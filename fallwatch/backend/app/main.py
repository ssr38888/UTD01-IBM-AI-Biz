"""FallWatch API: receives fall events from phones, stores evidence write-once, and serves the viewer."""
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .db import ChainHead, Event, Image, SessionLocal, Snapshot, init_db
from .storage import make_storage

@asynccontextmanager
async def lifespan(_app):
    config.check_keys()  # refuse to run without strong keys
    init_db()
    yield


# No public /docs or /openapi.json: nothing about the API is advertised to strangers.
app = FastAPI(title="FallWatch", version="0.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

# Page-level protections. The capture page must load MediaPipe from jsdelivr and the
# pose model from Google Storage; everything else is same-origin only.
_CSP_CAPTURE = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval' 'unsafe-eval'; "
                "connect-src 'self' https://cdn.jsdelivr.net https://storage.googleapis.com; "
                "img-src 'self' blob: data:; media-src 'self' blob: mediastream:; worker-src 'self' blob:; "
                "style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
_CSP_DEFAULT = ("default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    path = request.url.path
    h = resp.headers
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "no-referrer"
    # Only the same-origin log viewer can be embedded in the camera dashboard.
    h["X-Frame-Options"] = "SAMEORIGIN" if path.startswith("/viewer/") else "DENY"
    h["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    h["Content-Security-Policy"] = (_CSP_CAPTURE if path.startswith("/capture") else
                                    _CSP_DEFAULT.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
                                    if path.startswith("/viewer/") else _CSP_DEFAULT)
    if path.startswith("/api/") and "Cache-Control" not in h:
        h["Cache-Control"] = "no-store"
    elif path.startswith(("/capture", "/viewer")):
        h["Cache-Control"] = "no-cache"
    return resp
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ROLES = {"standing", "laid", "pre", "burst", "post"}
_storage = None


def storage():
    global _storage
    if _storage is None:
        _storage = make_storage()
    return _storage


def set_storage(s) -> None:  # used by tests
    global _storage
    _storage = s


def get_db():
    with SessionLocal() as s:
        yield s


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _key_ok(given: Optional[str], allowed: list[str]) -> bool:
    """Constant-time comparison against every allowed key (no early exit, no timing leak)."""
    if not given:
        return False
    g = given.encode()
    ok = False
    for k in allowed:
        ok |= hmac.compare_digest(g, k.encode())
    return ok


CAMERA_SESSION_SECONDS = 7 * 24 * 3600


def _camera_signature(payload: str) -> str:
    return hmac.new(config.DEVICE_KEYS[0].encode(), ("camera-session:" + payload).encode(), hashlib.sha256).hexdigest()


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    # Match the public Host rather than the localhost upstream behind the tunnel.
    return origin in {f"https://{request.headers.get('host')}", f"http://{request.headers.get('host')}"}


def device_auth(request: Request, x_device_key: Optional[str] = Header(None)) -> None:
    if _key_ok(x_device_key, config.DEVICE_KEYS):
        return
    token = request.cookies.get("fw_camera", "")
    try:
        expiry, nonce, signature = token.split(".")
        payload = f"{expiry}.{nonce}"
        valid = int(expiry) > time.time() and hmac.compare_digest(signature, _camera_signature(payload))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(401, "camera session required")
    if request.method not in {"GET", "HEAD"} and not _same_origin(request):
        raise HTTPException(403, "same-origin camera request required")


def viewer_auth(request: Request, x_viewer_key: Optional[str] = Header(None)) -> None:
    if _key_ok(x_viewer_key, config.VIEWER_KEYS):
        return
    # Browser logs share the signed camera session; API clients may still use a key.
    device_auth(request, None)


# --------------------------------------------------------------------------- phone side

@app.post("/api/camera/session")
def camera_session(request: Request):
    """Provision a capture and log browser session without exposing server API keys.
    """
    if not _same_origin(request):
        raise HTTPException(403, "same-origin camera request required")
    payload = f"{int(time.time()) + CAMERA_SESSION_SECONDS}.{secrets.token_urlsafe(24)}"
    response = JSONResponse({"ok": True})
    response.set_cookie("fw_camera", f"{payload}.{_camera_signature(payload)}",
                        httponly=True, secure=request.url.scheme == "https" or request.headers.get("origin", "").startswith("https://"),
                        samesite="strict", path="/api", max_age=CAMERA_SESSION_SECONDS)
    return response

@app.get("/api/config")
def phone_config(_: None = Depends(device_auth)):
    """Capture settings live on the server so they can be tuned without touching phones."""
    return {
        "snapshot_interval_ms": config.SNAPSHOT_INTERVAL_MS,
        "burst_interval_ms": config.BURST_INTERVAL_MS,
        "pre_event_ms": config.PRE_EVENT_MS,
        "post_event_ms": config.POST_EVENT_MS,
        "heartbeat_sec": 0,
        "jpeg_quality": config.JPEG_QUALITY,
        "frame_width": config.FRAME_WIDTH,
        "max_images": config.MAX_IMAGES_PER_EVENT,
    }


@app.post("/api/events")
async def upload_event(
    meta: str = Form(...),
    images: list[UploadFile] = File(...),
    keypoints: Optional[UploadFile] = File(None),
    clip: Optional[UploadFile] = File(None),
    _: None = Depends(device_auth),
    db: Session = Depends(get_db),
):
    """One fall = metadata + JPEG frames + keypoint log. Safe to retry (idempotent per client_event_id)."""
    try:
        m = json.loads(meta)
        device_id = str(m["device_id"])[:64]
        client_event_id = str(m["client_event_id"])[:64]
        d = m["descriptor"]
        detected_at_ms = int(m.get("detected_at_ms", d.get("confirmedT", 0)))
        if not 0 <= detected_at_ms <= 8640000000000000:
            raise ValueError("event timestamp is outside the supported date range")
        frames = {f["name"]: f for f in m["frames"]}
    except (ValueError, KeyError, TypeError, OverflowError) as e:
        raise HTTPException(422, f"bad meta: {e}")

    existing = db.scalar(select(Event).where(Event.device_id == device_id,
                                             Event.client_event_id == client_event_id))
    if existing:
        return _receipt(existing, duplicate=True)

    if not images or len(images) > config.MAX_IMAGES_PER_EVENT:
        raise HTTPException(413, "too many or no images")

    # Read + check every file before writing anything.
    files = []
    for up in images:
        name = up.filename or ""
        if not NAME_RE.match(name) or name not in frames:
            raise HTTPException(422, f"image {name!r} missing from meta.frames")
        data = await up.read(config.MAX_IMAGE_BYTES + 1)
        if len(data) > config.MAX_IMAGE_BYTES:
            raise HTTPException(413, f"{name} too large")
        if not data.startswith(b"\xff\xd8\xff"):
            raise HTTPException(415, f"{name} is not a JPEG")
        f = frames[name]
        role = f.get("role", "pre")
        if role not in ROLES:
            raise HTTPException(422, f"bad role {role}")
        files.append({"name": name, "role": role, "captured_at_ms": int(f["t"]),
                      "sha256": sha256(data), "bytes": len(data), "data": data})
    if len({f["name"] for f in files}) != len(files):
        raise HTTPException(422, "duplicate image names")

    kp_bytes = b""
    if keypoints is not None:
        kp_bytes = await keypoints.read(config.MAX_KEYPOINTS_BYTES + 1)
        if len(kp_bytes) > config.MAX_KEYPOINTS_BYTES:
            raise HTTPException(413, "keypoint log too large")

    # New evidence is photos only. Ignore video attachments from older phones
    # while still accepting their photos; historical saved clips remain readable.
    clip_info = None
    if clip is not None:
        await clip.close()

    event_id = str(uuid.uuid4())
    prefix = f"events/{event_id}"
    files.sort(key=lambda f: f["captured_at_ms"])

    # Lock the chain head so events are appended one at a time (row lock on Postgres).
    head = db.execute(select(ChainHead).where(ChainHead.id == 1).with_for_update()).scalar_one()
    seq = head.seq + 1

    received_at = datetime.now(timezone.utc)
    manifest = {
        "schema": "fallwatch.manifest/1",
        "event_id": event_id,
        "seq": seq,
        "device_id": device_id,
        "client_event_id": client_event_id,
        "received_at": received_at.isoformat(),
        "detected_at_ms": detected_at_ms,
        "descriptor": d,
        "files": [{k: f[k] for k in ("name", "role", "captured_at_ms", "sha256", "bytes")} for f in files],
        "keypoints_sha256": sha256(kp_bytes) if kp_bytes else None,
        "prev_chain_hash": head.chain_hash,
    }
    manifest_bytes = canonical(manifest)
    manifest_hash = sha256(manifest_bytes)
    chain_hash = sha256((head.chain_hash + manifest_hash).encode())

    st = storage()
    for f in files:
        st.put(f"{prefix}/{f['name']}", f["data"], "image/jpeg")
    if kp_bytes:
        st.put(f"{prefix}/keypoints.json", kp_bytes, "application/json")
    st.put(f"{prefix}/manifest.json", manifest_bytes, "application/json")

    ev = Event(
        id=event_id, device_id=device_id, client_event_id=client_event_id, received_at=received_at,
        detected_at_ms=manifest["detected_at_ms"],
        stand_t_ms=int(d["standT"]), lie_t_ms=int(d["lieT"]),
        descent_ms=int(d["descentMs"]), hip_drop=float(d["hipDropBodyHeights"]),
        hip_speed=float(d["hipSpeedBodyHeightsPerSec"]),
        landing_side=str(d.get("landingSide", "unclear"))[:16],
        floor_ms=int(d.get("floorMs", 0)), recovered=bool(d.get("recovered", False)),
        n_images=len(files), meta_json=json.dumps({**m, "clip": clip_info}), manifest_sha256=manifest_hash,
        prev_chain_hash=head.chain_hash, chain_hash=chain_hash, seq=seq,
    )
    db.add(ev)
    for f in files:
        db.add(Image(event_id=event_id, name=f["name"], role=f["role"], captured_at_ms=f["captured_at_ms"],
                     sha256=f["sha256"], bytes=f["bytes"], object_key=f"{prefix}/{f['name']}"))
    head.seq, head.chain_hash = seq, chain_hash
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # a retry of the same event raced us; return the stored one
        existing = db.scalar(select(Event).where(Event.device_id == device_id,
                                                 Event.client_event_id == client_event_id))
        if existing:
            return _receipt(existing, duplicate=True)
        raise
    return _receipt(ev)


def _receipt(ev: Event, duplicate: bool = False):
    return {"id": ev.id, "seq": ev.seq, "n_images": ev.n_images, "manifest_sha256": ev.manifest_sha256,
            "chain_hash": ev.chain_hash, "duplicate": duplicate}


@app.post("/api/snapshots")
async def upload_snapshot(
    device_id: str = Form(...), captured_at_ms: int = Form(...), posture: str = Form("unknown"),
    image: UploadFile = File(...), _: None = Depends(device_auth), db: Session = Depends(get_db),
):
    """Low-rate heartbeat picture, only used when HEARTBEAT_SEC > 0."""
    if config.HEARTBEAT_SEC <= 0:
        raise HTTPException(403, "heartbeat snapshots are turned off")
    data = await image.read(config.MAX_IMAGE_BYTES + 1)
    if len(data) > config.MAX_IMAGE_BYTES or not data.startswith(b"\xff\xd8\xff"):
        raise HTTPException(415, "need a JPEG under the size limit")
    key = f"snapshots/{device_id[:64]}/{int(captured_at_ms)}-{uuid.uuid4().hex[:8]}.jpg"
    storage().put(key, data, "image/jpeg")
    db.add(Snapshot(device_id=device_id[:64], captured_at_ms=captured_at_ms, posture=posture[:16],
                    sha256=sha256(data), object_key=key))
    db.commit()
    return {"ok": True, "sha256": sha256(data)}


# --------------------------------------------------------------------------- viewer side

@app.get("/api/history")
def event_history(limit: int = 50, before_seq: Optional[int] = None,
                  start_ms: Optional[int] = None, end_ms: Optional[int] = None,
                  _: None = Depends(viewer_auth), db: Session = Depends(get_db)):
    if not 1 <= limit <= 100 or (before_seq is not None and before_seq < 1):
        raise HTTPException(422, "invalid history page")
    if (start_ms is None) != (end_ms is None):
        raise HTTPException(422, "provide both day boundaries")
    filters = []
    if start_ms is not None:
        if start_ms < 0 or not 0 < end_ms - start_ms <= 27 * 3600000:
            raise HTTPException(422, "invalid day range")
        filters = [Event.detected_at_ms >= start_ms, Event.detected_at_ms < end_ms]
    total = db.scalar(select(func.count()).select_from(Event).where(*filters))
    image_sizes = select(Image.event_id, func.sum(Image.bytes).label("size")).group_by(Image.event_id).subquery()
    query = (select(Event, func.coalesce(image_sizes.c.size, 0))
             .outerjoin(image_sizes, Event.id == image_sizes.c.event_id).where(*filters))
    if before_seq is not None:
        query = query.where(Event.seq < before_seq)
    rows = db.execute(query.order_by(Event.seq.desc()).limit(limit + 1)).all()
    events = []
    for ev, image_bytes in rows[:limit]:
        clip = json.loads(ev.meta_json).get("clip") or {}
        events.append({**_event_summary(ev), "total_bytes": image_bytes + clip.get("bytes", 0), "has_clip": bool(clip)})
    return {"events": events, "total": total,
            "next_cursor": events[-1]["seq"] if len(rows) > limit else None}

@app.get("/api/logs")
def daily_logs(start_ms: int, end_ms: int, _: None = Depends(viewer_auth), db: Session = Depends(get_db)):
    """A complete local-day slice, without the event-list pagination cap.

    The browser supplies local-midnight boundaries, including daylight-saving
    changes. Storage counts photo and video bytes, not browser RAM.
    """
    if start_ms < 0 or not 0 < end_ms - start_ms <= 27 * 3600 * 1000:
        raise HTTPException(422, "provide a day range of at most 27 hours")
    image_sizes = select(Image.event_id, func.sum(Image.bytes).label("size")).group_by(Image.event_id).subquery()
    rows = db.execute(select(Event, func.coalesce(image_sizes.c.size, 0))
                      .outerjoin(image_sizes, Event.id == image_sizes.c.event_id)
                      .where(Event.detected_at_ms >= start_ms, Event.detected_at_ms < end_ms)
                      .order_by(Event.detected_at_ms.desc(), Event.seq.desc())).all()
    events = []
    for ev, image_bytes in rows:
        clip = json.loads(ev.meta_json).get("clip") or {}
        events.append({**_event_summary(ev), "total_bytes": image_bytes + clip.get("bytes", 0)})
    return {"events": events, "start_ms": start_ms, "end_ms": end_ms}

def _event_summary(ev: Event) -> dict:
    descriptor = json.loads(ev.meta_json).get("descriptor", {})
    return {
        "event_type": descriptor.get("eventType", "fall"),
        "id": ev.id, "seq": ev.seq, "device_id": ev.device_id,
        "received_at": ev.received_at.replace(tzinfo=ev.received_at.tzinfo or timezone.utc).isoformat(),
        "detected_at_ms": ev.detected_at_ms, "descent_ms": ev.descent_ms, "hip_drop": ev.hip_drop,
        "hip_speed": ev.hip_speed, "landing_side": ev.landing_side, "floor_ms": ev.floor_ms,
        "recovered": ev.recovered, "n_images": ev.n_images,
        "manifest_sha256": ev.manifest_sha256, "chain_hash": ev.chain_hash,
    }


@app.get("/api/events")
def list_events(limit: int = 50, _: None = Depends(viewer_auth), db: Session = Depends(get_db)):
    rows = db.scalars(select(Event).order_by(Event.seq.desc()).limit(min(limit, 500))).all()
    return [_event_summary(e) for e in rows]


@app.get("/api/events/{event_id}")
def get_event(event_id: str, _: None = Depends(viewer_auth), db: Session = Depends(get_db)):
    ev = db.get(Event, event_id)
    if not ev:
        raise HTTPException(404)
    out = _event_summary(ev)
    out["stand_t_ms"], out["lie_t_ms"] = ev.stand_t_ms, ev.lie_t_ms
    out["prev_chain_hash"] = ev.prev_chain_hash
    out["meta"] = json.loads(ev.meta_json)
    out["clip"] = out["meta"].get("clip")
    out["images"] = [{"name": i.name, "role": i.role, "captured_at_ms": i.captured_at_ms,
                      "sha256": i.sha256, "bytes": i.bytes} for i in ev.images]
    return out


@app.get("/api/events/{event_id}/files/{name}")
def get_file(event_id: str, name: str, _: None = Depends(viewer_auth), db: Session = Depends(get_db)):
    if not NAME_RE.match(name):
        raise HTTPException(400)
    ev = db.get(Event, event_id)
    if not ev:
        raise HTTPException(404)
    try:
        data = storage().get(f"events/{event_id}/{name}")
    except Exception:
        raise HTTPException(404)
    ctype = ("video/webm" if name.endswith(".webm") else "video/mp4" if name.endswith(".mp4")
             else "image/jpeg" if name.endswith(".jpg") else "application/json")
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, no-store"})


@app.get("/api/events/{event_id}/verify")
def verify_event(event_id: str, _: None = Depends(viewer_auth), db: Session = Depends(get_db)):
    """Re-hash every stored file and the manifest; check the chain link to the previous event."""
    ev = db.get(Event, event_id)
    if not ev:
        raise HTTPException(404)
    return _verify(ev, db)


def _verify(ev: Event, db: Session) -> dict:
    st = storage()
    problems = []
    try:
        manifest_bytes = st.get(f"events/{ev.id}/manifest.json")
    except Exception:
        return {"ok": False, "problems": ["manifest missing"]}
    if sha256(manifest_bytes) != ev.manifest_sha256:
        problems.append("manifest hash does not match database")
    manifest = json.loads(manifest_bytes)
    for f in manifest["files"]:
        try:
            if sha256(st.get(f"events/{ev.id}/{f['name']}")) != f["sha256"]:
                problems.append(f"{f['name']} changed")
        except Exception:
            problems.append(f"{f['name']} missing")
    if manifest.get("keypoints_sha256"):
        try:
            if sha256(st.get(f"events/{ev.id}/keypoints.json")) != manifest["keypoints_sha256"]:
                problems.append("keypoints changed")
        except Exception:
            problems.append("keypoints missing")
    if manifest.get("clip"):
        clip_info = manifest["clip"]
        try:
            if sha256(st.get(f"events/{ev.id}/{clip_info['name']}")) != clip_info["sha256"]:
                problems.append("event clip changed")
        except Exception:
            problems.append("event clip missing")
    if sha256((ev.prev_chain_hash + ev.manifest_sha256).encode()) != ev.chain_hash:
        problems.append("chain hash does not match")
    prev = db.scalar(select(Event).where(Event.seq == ev.seq - 1))
    expected_prev = prev.chain_hash if prev else "0" * 64
    if ev.prev_chain_hash != expected_prev:
        problems.append("link to previous event broken")
    return {"ok": not problems, "problems": problems, "files_checked": len(manifest["files"]),
            "manifest_sha256": ev.manifest_sha256, "chain_hash": ev.chain_hash}


@app.get("/api/chain/verify")
def verify_chain(_: None = Depends(viewer_auth), db: Session = Depends(get_db)):
    bad = {}
    n = 0
    for ev in db.scalars(select(Event).order_by(Event.seq)):
        n += 1
        r = _verify(ev, db)
        if not r["ok"]:
            bad[ev.id] = r["problems"]
    head = db.get(ChainHead, 1)
    return {"ok": not bad, "events_checked": n, "head": head.chain_hash if head else None, "bad": bad}


@app.get("/healthz")
def healthz():
    return {"ok": True}


# --------------------------------------------------------------------------- pages

@app.get("/")
def root():
    return RedirectResponse("/capture/")


app.mount("/capture", StaticFiles(directory=config.ROOT / "phone", html=True), name="capture")
app.mount("/viewer", StaticFiles(directory=config.ROOT / "viewer", html=True), name="viewer")


@app.exception_handler(FileExistsError)
def _exists(_: Request, exc: FileExistsError):
    return JSONResponse({"detail": "object already exists (write-once)"}, status_code=409)

