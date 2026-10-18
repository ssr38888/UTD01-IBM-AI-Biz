# FallWatch — fall evidence capture for ClaimClock

A phone mounted on a wall watches a work area. When someone goes from **standing** to **lying on the floor** fast and stays down, the phone sends a short picture sequence to the server on LinuxONE. That covers 10 s before, the fall itself, and 10 s after. The server stores it with tamper-evident fingerprints. Only JPEG evidence photos are saved for confirmed fall transitions; no new videos are recorded or uploaded.

```
phone (browser)                                   LinuxONE
┌──────────────────────────────┐   HTTPS   ┌────────────────────────────────────┐
│ camera → MediaPipe Pose      │  ───────▶ │ Caddy (TLS) → FastAPI              │
│ → fall detector (detector.js)│  only on  │   ├─ SHA-256 every picture         │
│ → JPEG ring buffer, last ~16 s│  a fall   │   ├─ manifest + hash chain         │
│ → outbox (IndexedDB, retries)│           │   ├─ PostgreSQL (event records)    │
└──────────────────────────────┘           │   └─ picture storage (volume / S3) │
                                           │ /viewer  evidence page             │
                                           └────────────────────────────────────┘
```

## How detection works (`phone/detector.js`)

The pose model gives 33 body points about 5 times a second. Side-on poses can use a single confidently visible shoulder/hip pair when the far side is occluded. Each frame is classified:

| Posture  | Rule |
|----------|------|
| standing | torso within 30° of vertical **and** legs extended (ankles ≥ 1.25 torso-lengths below hips) |
| lying    | torso within 30° of horizontal **and** body box wider than tall |
| other    | sitting, crouching, bending, mid-fall |

A **fall event** is all of the following:
- steady standing for at least 0.3 s,
- then lying within **2 s**,
- then still lying **0.3 s** later, with a hip descent of at least 0.15 visible body heights.

Detection drop-outs up to 1.5 s while the person is down are tolerated. These cases do not count as a fall:
- sitting, then lying down on purpose,
- bending over,
- a tumble followed by getting straight back up.

Each event comes with a **descriptor**:
- **Descent time:** last standing picture to first lying picture.
- **Hip drop speed:** in body heights per second.
- **Landing side:** a 2D estimate (left / right / unclear), voted over the lying frames.
- **Time on the floor:** until the person stands up, or "≥ X s" if they are still down when the event is sent.

The viewer labels these numbers as descriptive, not a measure of injury. That matches the "fall descriptor card" decision in the spec.

## Pictures and storage (`phone/evidence.js`)

The phone keeps pictures in a fixed-size memory buffer:
- It takes a picture every **750 ms** and every **300 ms** while someone is going down.
- Each picture is 640 px wide at JPEG quality 0.7.
- The buffer holds about 11 s, so phone memory stays flat (about 1–3 MB).
- Nothing leaves the phone unless a fall is confirmed.

Each event is labelled with these picture roles:
- `standing` (key frame 1: last steady standing view, taken at the exact frame)
- `laid` (key frame 2: taken the instant the person is first seen lying)
- `pre`, `burst` and `post`

If an event exceeds 150 pictures, `pre` and `post` are thinned evenly. Key and burst pictures are never dropped. A pose-point log (~5×/s, a few KB) is sent along with the pictures, so the motion is recorded more finely than the pictures.

Event size depends on the scene and image compression. With no confirmed fall, no photos are uploaded. The camera does not send heartbeat pictures.

## Evidence integrity (backend)

- Every picture is SHA-256 hashed on arrival.
- Each event gets a `manifest.json` containing:
  - every picture's hash, role and time
  - the descriptor
  - the server receive time
  - the previous event's hash.
- Each event's chain hash is `sha256(previous chain hash + manifest hash)`. Deleting or editing any past event breaks every link after it.
- Storage is write-once: the API refuses to overwrite.
- With S3 storage and `S3_RETENTION_DAYS>0`, buckets use Object Lock in compliance mode, so not even an admin can delete evidence early.
- **Verify** in the viewer, or `GET /api/events/{id}/verify`, re-hashes everything. **Check all**, or `GET /api/chain/verify`, walks the whole chain.
- Uploads are idempotent per phone event ID. A phone that loses connection keeps events in its outbox and retries with backoff.

## Run it on LinuxONE

The only way to use the camera in a phone browser is over **HTTPS**. Caddy handles that.

```sh
cd deploy
python3 ../scripts/make_keys.py --deploy   # creates deploy/.env with random keys + DB password (owner-only)
# then set SITE_ADDRESS in deploy/.env
docker compose up -d --build   # or: podman-compose up -d --build
```

All images (python:3.12-slim, postgres:16, caddy:2) have s390x builds. The Python packages are pure Python, or they use the system `libpq`.

Pages:
- **Phone:** open `https://<site>/capture/`, accept the suggested device name, tick consent, then **Start watching**. Mount the phone so the whole person is in view, ideally from the side at chest height.
- **Evidence viewer:** `https://<site>/viewer/` (opens automatically using the browser session).

If phones can't reach jsdelivr or Google Storage, run `deploy/vendor-mediapipe.sh` once and switch the three URLs at the top of `phone/app.js` to `./vendor/...`.

### Test on your phone from a laptop (no Docker)

```sh
pip install -r backend/requirements.txt
python3 scripts/make_keys.py                 # prints your phone key + viewer key once, saves them to .env
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000
# second terminal: public HTTPS link for the phone (no account needed)
cloudflared tunnel --url http://localhost:8000   # prints https://<random>.trycloudflare.com
```

Phone: `https://<random>.trycloudflare.com/capture/`. The browser automatically receives an HttpOnly camera and log session; no API key entry is needed. Laptop: `.../viewer/` opens without a viewer key.
Stop the tunnel when you're done testing; the link dies with it.

### Settings (env, sent to phones at start-up)

`SNAPSHOT_INTERVAL_MS` 750 · `BURST_INTERVAL_MS` 300 · `PRE_EVENT_MS` 5000 · `POST_EVENT_MS` 5000 · `HEARTBEAT_SEC` 0 · `JPEG_QUALITY` 0.7 · `FRAME_WIDTH` 640 · `MAX_IMAGES_PER_EVENT` 150.

## Security

- **No keys in the code.** `scripts/make_keys.py` makes 256-bit random keys on your machine and saves them to `.env` (owner-only, git-ignored). The server **refuses to start** if keys are missing, short, placeholders, or if the phone key and viewer key are the same.
- **Two separate keys.** The phone key can only upload; it cannot view anything. The viewer key can only view; it cannot upload. Each phone can have its own key (comma-separated) so one can be revoked by removing it and restarting.
- **Keys only travel in headers over HTTPS.** They are never put in URLs, so they don't end up in logs, browser history or proxies. Keys are checked in constant time.
- **On the phone**, the capture link provisions a capture and log session cookie lasting seven days. Server API keys are never sent to the browser. Camera requests renew expired sessions automatically; Logs renew the same session automatically and require no key entry. Treat the testing link as permission to connect a camera and read saved evidence.
- **Pages are locked down:** strict Content-Security-Policy, no framing, no referrer, API responses not cached, no public API docs page.
- **Evidence:** SHA-256 fingerprints, hash chain, write-once storage (see above).
- Never paste `.env` or your keys into a chat, an AI tool, a screenshot or a repo. If a key leaks: `python3 scripts/make_keys.py --force`, restart, re-enter the new key on the phone.

## Tests

```sh
node --test tests/*.test.mjs                 # detector + picture buffer (15 tests)
python3 -m pytest backend/tests              # API, keys, headers, hashing, tamper detection, S3 Object Lock path
TEST_DATABASE_URL=postgresql+psycopg://... python3 -m pytest backend/tests   # same, on Postgres
python3 tests/e2e_browser.py                 # headless Chromium: phone page → server → viewer
```

The browser test uses Chromium's fake camera and a scripted pose sequence in place of MediaPipe. Everything else runs for real.

## Accuracy check on real falls (UR Fall Detection Dataset)

Run this on a laptop, because MediaPipe Python has no s390x build:

```sh
pip install mediapipe opencv-python
python eval/download_urfall.py --small        # or all 30 falls + 40 everyday clips
python eval/extract_keypoints.py data/urfall data/keypoints
node eval/evaluate.mjs data/keypoints         # recall on falls, false alarms on everyday clips
```

UR Fall is CC BY-NC-SA 4.0, for non-commercial academic use. Cite Kwolek & Kępski (2014). Many of its clips end only 1–3 s after landing, which is shorter than the 3 s stays-down check. By default, `evaluate.mjs` therefore counts a fall as caught when it reaches the "possible fall" stage; use `--strict` to count only confirmed events.

## Limits to state in the pitch

- Detection is single-person (`numPoses: 1`). It is tuned for a side or front view, and a camera looking straight down won't work well.
- Landing side is a 2D estimate from one camera.
- Thresholds come from body geometry and have not yet been tuned on real data. Run the UR Fall eval and adjust `DEFAULTS` in `detector.js`.
- People are being recorded: get consent and keep the "Monitoring" indicator visible.

## Camera monitoring interface

The camera view uses the available screen and shows monitoring, posture, and event status. Buffer size, memory usage, upload counts, and processing speed are omitted; the future log page is outside this change. The browser still requires consent, an initial Start action, camera permission, and an open foreground page.

Confirmed fall transitions upload automatically as photos only. Existing older event clips remain readable in history. Stopping or ending a test video saves any already-confirmed event using available photo evidence and clears the temporary buffer.

### Persistent event history

Logs now open to All history, including records from previous days. Each saved row shows its event number, type, detection date/time, camera, photo count, clip availability, and storage size. Select a row to inspect its evidence and fingerprint. Today filters the list using local midnight boundaries; today's overview metrics and chart remain daily in either view. Older records load in pages of 50, and saved events remain available after reload. History and photos open automatically using the signed browser session; no viewer key entry is required.

### Current capture policy: observed falls, photos only

Camera startup, ordinary presence, and a person already lying down do not create events. The camera requires steady standing followed by a horizontal torso within two seconds, a hip descent of at least 0.15 visible body heights, and a 300 ms confirmation. Only confirmed transitions are saved. The temporary JPEG buffer is bounded and discarded on Stop or video end; no-event footage is not written to the server or IndexedDB.

New events contain JPEG photos and small pose/manifest metadata, with five seconds before the last standing frame through five seconds after the first lying frame. Normal photos are 750 ms apart, with 300 ms burst photos and exact key frames during the transition. Video recording and camera video uploads are disabled. The server ignores video attachments from older clients while preserving their photos. Existing historical videos remain viewable; no saved evidence is deleted.

### Fresh person check before a fall event

Before a tracked horizontal pose can become an event, the camera rechecks that frame with independent IMAGE-mode pose detection. This avoids carrying a tracked skeleton into an empty room when a person exits. A failed fresh check cannot confirm an event. A short missed frame uses the existing dropout grace; persistent absence expires the standing/fall transition. JPEG-only evidence, timing, and existing history are unchanged.
