import io
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="fallwatch-test-"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{TMP / 'test.db'}")
os.environ["LOCAL_STORAGE_DIR"] = str(TMP / "objects")
DKEY = "test-device-key-0123456789abcdef"
VKEY = "test-viewer-key-0123456789abcdef"
os.environ["DEVICE_KEYS"] = DKEY
os.environ["VIEWER_KEYS"] = VKEY
os.environ["FALLWATCH_ENV_FILE"] = str(TMP / "none.env")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image as PILImage  # noqa: E402

from app import main  # noqa: E402

D = {"X-Device-Key": DKEY}
V = {"X-Viewer-Key": VKEY}


def jpeg(color=(120, 120, 120)) -> bytes:
    b = io.BytesIO()
    PILImage.new("RGB", (64, 48), color).save(b, "JPEG", quality=70)
    return b.getvalue()


def make_event(n=6, client_id=None):
    t0 = 1_760_000_000_000
    names = [f"f{t0 + i * 500}.jpg" for i in range(n)]
    roles = ["pre", "standing", "burst", "laid", "post", "post"][:n]
    frames = [{"name": nm, "t": t0 + i * 500, "role": r} for i, (nm, r) in enumerate(zip(names, roles))]
    meta = {
        "device_id": "phone-1",
        "client_event_id": client_id or str(uuid.uuid4()),
        "detected_at_ms": t0 + 4000,
        "descriptor": {"standT": t0 + 500, "lieT": t0 + 1500, "confirmedT": t0 + 4500, "descentMs": 1000,
                       "hipDropBodyHeights": 0.42, "hipSpeedBodyHeightsPerSec": 0.42,
                       "landingSide": "left", "floorMs": 9000, "recovered": False},
        "frames": frames,
    }
    files = [("images", (nm, jpeg((i * 30, 80, 80)), "image/jpeg")) for i, nm in enumerate(names)]
    files.append(("keypoints", ("keypoints.json", json.dumps([{"t": t0, "p": "standing"}]).encode(),
                                "application/json")))
    return meta, files


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def post(client, meta, files, headers=D):
    return client.post("/api/events", data={"meta": json.dumps(meta)}, files=files, headers=headers)


def test_auth_required(client):
    meta, files = make_event()
    assert post(client, meta, files, headers={}).status_code == 401
    assert client.get("/api/events").status_code == 401


def test_config(client):
    r = client.get("/api/config", headers=D).json()
    assert r["snapshot_interval_ms"] == 750 and r["burst_interval_ms"] == 300
    assert r["pre_event_ms"] == 5000 and r["post_event_ms"] == 5000
    assert r["heartbeat_sec"] == 0


def test_history_is_protected_persistent_and_paginates_without_duplicates(client):
    assert client.get("/api/history").status_code == 401
    ids = [post(client, *make_event()).json()["id"] for _ in range(3)]
    first = client.get("/api/history?limit=2", headers=V)
    assert first.status_code == 200
    first = first.json()
    assert [event["id"] for event in first["events"]] == ids[::-1][:2]
    second = client.get(f"/api/history?limit=2&before_seq={first['next_cursor']}", headers=V).json()
    assert ids[0] in [event["id"] for event in second["events"]]
    assert not set(e["id"] for e in first["events"]) & set(e["id"] for e in second["events"])
    t = make_event()[0]["detected_at_ms"]
    assert client.get(f"/api/history?start_ms={t+1}&end_ms={t+2}", headers=V).json()["total"] == 0
    assert client.get("/api/history?start_ms=1", headers=V).status_code == 422
    assert client.get("/api/history?limit=0", headers=V).status_code == 422
    # Loading history does not discard the saved evidence or change its hash.
    event = client.get(f"/api/events/{ids[0]}", headers=V).json()
    assert event["images"] and event["manifest_sha256"]


def test_rejects_timestamp_that_browser_history_cannot_display(client):
    meta, files = make_event()
    meta["detected_at_ms"] = 8640000000000001
    assert post(client, meta, files).status_code == 422


def test_person_down_event_is_saved_and_classified_in_logs(client):
    meta, files = make_event()
    meta["descriptor"].update(eventType="person_down", observedFall=False,
                              standT=meta["descriptor"]["lieT"], descentMs=0,
                              hipDropBodyHeights=0, hipSpeedBodyHeightsPerSec=0)
    event_id = post(client, meta, files).json()["id"]
    ev = client.get(f"/api/events/{event_id}", headers=V).json()
    assert ev["event_type"] == "person_down" and ev["descent_ms"] == 0
    t = meta["detected_at_ms"]
    logs = client.get(f"/api/logs?start_ms={t}&end_ms={t+1}", headers=V).json()
    assert next(e for e in logs["events"] if e["id"] == event_id)["event_type"] == "person_down"


def test_home_is_camera_and_log_summary_is_complete_and_protected(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/capture/"
    assert client.get("/api/logs?start_ms=1&end_ms=1000").status_code == 401
    meta, files = make_event()
    t = meta["detected_at_ms"]
    result = post(client, meta, files)
    event_id = result.json()["id"]
    response = client.get(f"/api/logs?start_ms={t}&end_ms={t+1}", headers=V)
    assert response.status_code == 200
    event = next(e for e in response.json()["events"] if e["id"] == event_id)
    assert event["total_bytes"] == sum(len(f[1][1]) for f in files if f[0] == "images")
    outside = client.get(f"/api/logs?start_ms={t-1}&end_ms={t}", headers=V).json()
    assert event_id not in [e["id"] for e in outside["events"]]
    assert client.get(f"/api/logs?start_ms={t}&end_ms={t}", headers=V).status_code == 422
    assert client.get(f"/api/logs?start_ms={t}&end_ms={t+28*3600000}", headers=V).status_code == 422


def test_camera_session_without_manual_keys(client):
    with TestClient(main.app, base_url="https://testserver") as camera:
        assert camera.post("/api/camera/session", headers={"Origin": "https://evil.example"}).status_code == 403
        r = camera.post("/api/camera/session", headers={"Origin": "https://testserver"})
        assert r.status_code == 200
        assert "HttpOnly" in r.headers["set-cookie"] and "Secure" in r.headers["set-cookie"]
        assert DKEY not in r.text
        assert camera.get("/api/config").status_code == 200
        meta, files = make_event()
        assert post(camera, meta, files, headers={"Origin": "https://testserver"}).status_code == 200
        assert camera.get("/api/events").status_code == 200
        assert camera.get("/api/history").status_code == 200
        assert camera.get("/api/logs?start_ms=1&end_ms=1000").status_code == 200
        event_id = camera.get("/api/events").json()[0]["id"]
        assert camera.get(f"/api/events/{event_id}").status_code == 200
        assert camera.get(f"/api/events/{event_id}/verify").status_code == 200
        assert camera.post("/api/snapshots", headers={"Origin": "https://evil.example"}).status_code == 403
        camera.cookies.set("fw_camera", "tampered", domain="testserver.local", path="/api")
        assert camera.get("/api/config").status_code == 401


def test_upload_view_verify(client):
    meta, files = make_event()
    r = post(client, meta, files)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["n_images"] == 6 and not rec["duplicate"]

    ev = client.get(f"/api/events/{rec['id']}", headers=V).json()
    assert [i["role"] for i in ev["images"]] == ["pre", "standing", "burst", "laid", "post", "post"]
    assert ev["landing_side"] == "left"

    img = client.get(f"/api/events/{rec['id']}/files/{ev['images'][0]['name']}", headers=V)
    assert img.status_code == 200 and img.content.startswith(b"\xff\xd8")
    # Keys in the URL are not accepted (they would leak into logs and history).
    leak = client.get(f"/api/events/{rec['id']}/files/{ev['images'][0]['name']}?key={VKEY}")
    assert leak.status_code == 401

    v = client.get(f"/api/events/{rec['id']}/verify", headers=V).json()
    assert v["ok"], v


def test_retry_is_idempotent(client):
    cid = str(uuid.uuid4())
    meta, files = make_event(client_id=cid)
    a = post(client, meta, files).json()
    meta, files = make_event(client_id=cid)
    b = post(client, meta, files).json()
    assert a["id"] == b["id"] and b["duplicate"]


def test_legacy_video_attachment_is_not_saved_but_photos_are_preserved(client):
    meta, files = make_event()
    files.append(("clip", ("event.webm", b"\x1a\x45\xdf\xa3test-webm", "video/webm")))
    result = post(client, meta, files)
    assert result.status_code == 200, result.text
    event_id = result.json()["id"]
    event = client.get(f"/api/events/{event_id}", headers=V).json()
    assert event["clip"] is None
    assert len(event["images"]) == 6
    assert client.get(f"/api/events/{event_id}/files/event.webm", headers=V).status_code == 404
    assert client.get(f"/api/events/{event_id}/verify", headers=V).json()["ok"]


def test_rejects_bad_input(client):
    meta, files = make_event()
    files[0] = ("images", (files[0][1][0], b"not a jpeg", "image/jpeg"))
    assert post(client, meta, files).status_code == 415

    meta, files = make_event()
    files[0] = ("images", ("../../etc/passwd", jpeg(), "image/jpeg"))
    assert post(client, meta, files).status_code == 422


def test_chain_and_tamper_detection(client):
    ids = [post(client, *make_event()).json()["id"] for _ in range(3)]
    assert client.get("/api/chain/verify", headers=V).json()["ok"]

    # Tamper with one stored picture behind the API's back.
    ev = client.get(f"/api/events/{ids[1]}", headers=V).json()
    p = TMP / "objects" / "events" / ids[1] / ev["images"][2]["name"]
    p.write_bytes(jpeg((255, 0, 0)))
    v = client.get(f"/api/events/{ids[1]}/verify", headers=V).json()
    assert not v["ok"] and any("changed" in x for x in v["problems"])
    chain = client.get("/api/chain/verify", headers=V).json()
    assert not chain["ok"] and ids[1] in chain["bad"]


def test_s3_backend_with_object_lock():
    """Same code path MinIO uses on LinuxONE, against an in-process S3 mock."""
    from moto import mock_aws
    from app import config, storage

    with mock_aws():
        import boto3
        config.S3_RETENTION_DAYS = 30
        config.S3_BUCKET = "evidence-test"
        s3 = boto3.client("s3", region_name="us-east-1")
        st = storage.S3Storage(client=s3)
        st.put("events/x/a.jpg", jpeg(), "image/jpeg")
        assert st.get("events/x/a.jpg").startswith(b"\xff\xd8")
        head = s3.head_object(Bucket="evidence-test", Key="events/x/a.jpg")
        assert head["ObjectLockMode"] == "COMPLIANCE"
        config.S3_RETENTION_DAYS = 0


def test_wrong_kind_of_key_rejected(client):
    assert client.get("/api/events", headers={"X-Viewer-Key": DKEY}).status_code == 401
    assert client.get("/api/config", headers={"X-Device-Key": VKEY}).status_code == 401


def test_security_headers_and_no_public_docs(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    r = client.get("/viewer/")
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    camera = client.get("/capture/")
    assert "frame-ancestors 'none'" in camera.headers["content-security-policy"]
    assert camera.headers["x-frame-options"] == "DENY"
    r = client.get("/api/events", headers=V)
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("dev,view", [
    ([], [VKEY]),                                   # missing
    (["short"], [VKEY]),                            # too short
    (["change-me-phone-1-xxxxxxxxxxxxxxxx"], [VKEY]),  # placeholder
    ([VKEY], [VKEY]),                               # same key for both roles
])
def test_server_refuses_weak_keys(dev, view, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DEVICE_KEYS", dev)
    monkeypatch.setattr(config, "VIEWER_KEYS", view)
    with pytest.raises(RuntimeError):
        config.check_keys()
