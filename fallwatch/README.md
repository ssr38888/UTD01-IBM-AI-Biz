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

### Automatic dispute scores

Logs shows scores automatically for each selected incident. There is no employee form and no invented claim profile. The baseline is the historical recorded-dispute rate in the 2023 training split: 11,470 / 206,460 = 5.56%. It is a population rate, not an employee-specific prediction or denial probability.

Build the baseline artifact with `python scripts/build_population_baseline.py PATH/TO/train.csv.gz data/claimclock/population_baseline.json`. The source must be ClaimClock's training split, never validation/test labels. The artifact contains counts and a source SHA-256; `CLAIMCLOCK_POPULATION_PATH` can override its location. The automatic endpoint is `GET /api/events/{event_id}/dispute-score`, protected by the existing browser session or viewer key.

The second value is labeled **Evidence-adjusted estimate / Assumption-based**. Intensity is estimated from the verified descriptor and hashed pose log using actual elapsed timestamps and normalized hip descent. It is image-plane motion intensity, not injury severity, impact force, or a medically validated danger category. Sparse sampling does not imply a high-intensity fall.

The versioned `pose_descent_v2` policy requires intact evidence, standing/floor key photos, a hashed pose log, at least three visible observations covering half of the recorded poses, visible standing-to-lying boundaries, no visible-pose gaps over 500 ms or 75% of the descent, and consistent finite drop/speed/duration. Duration must be 100–2000 ms and drop 0.15–2 body heights. Unreliable observations show **Insufficient visibility**. The estimate card displays the unchanged historical baseline with **No adjustment applied**; the API keeps `adjusted_probability: null` to distinguish this fallback from an actual adjustment.

Heuristic bands and unvalidated relative dispute-rate reductions:
- High: at least 1 body height/sec; assumed reduction 35%.
- Medium: at least 0.4 body heights/sec; assumed reduction 25%.
- Low: other measurable transitions; assumed reduction 10%.

Adjusted estimate = historical baseline × (1 − assumed reduction). The thresholds and reductions are explicit policy assumptions, not learned from dispute outcomes. Integrity verifies stored bytes, not claim truth or causality. Automatic scores use the current baseline artifact and incident evidence; no employee input or assessment writes are required. Older incidents lacking sufficient pose coverage remain unclassified. Camera detection and capture behavior are unchanged.

The original trained tabular model and optional claim-link API remain available for future use with actual claim metadata. Automatic Logs scores do not use those saved assessments. Immutable event evidence remains unchanged.

Run `python -m pytest backend/tests`. With the population artifact installed, `python tests/claim_integration.py` checks automatic scores, no form, reload, incident switching, and mobile layout using an isolated database and synthetic evidence.

Brief occlusions no longer suppress measurable transitions: partially visible falls are labeled Estimated with limited visibility. Speed is normalized hip descent divided by actual elapsed descent time. Continuous coverage uses all poses with gaps at most 250 ms; partial coverage is explicitly marked.

### Uploaded frontend, live integration

The supplied `fallwatch-frontend.zip` design is integrated under `frontend/`. The app root now redirects to `/frontend/`. Its design stylesheet is retained as `frontend/theme.css`; `live.css` adapts it for actual camera controls, a persistent event ledger, authenticated photo loading, and phone navigation. The supplied simulator, fake scenarios, sample ledger, and record-tampering action are not part of the live app.

`frontend/live-app.js` uses the existing same-origin session and backend history, daily summary, event, file, integrity, and dispute-score endpoints. Scoring continues to use the shared viewer module and the backend intensity policy; it is not recalculated using the uploaded simulation's JavaScript. No employee claim form or API key entry is required. Historic videos remain stored, but the new evidence view displays photos.

The existing capture engine is embedded at `/capture/?embedded=1` with same-origin-only framing and camera permission delegation. Regular `/capture/` retains its framing restriction. Detection stays in the capture page; a saved-event notification refreshes the parent ledger and scores. Root frontend scripts and styles are external files, preserving the Content Security Policy; system fallback fonts avoid requiring external font services. The Docker image now includes `frontend/`.

Verification: `node --test tests/*.test.mjs`, `python -m pytest backend/tests`, `E2E_FRONTEND=1 python tests/claim_integration.py`, and `python tests/frontend_camera_e2e.py`. Browser tests use isolated databases and synthetic camera input, checking actual capture, photos-only upload, live ledger refresh, evidence integrity, scoring, event selection, reload, and mobile layout.


### Photo-only evidence path

Automatic scoring distinguishes measured-motion evidence from photo-only evidence. If saved photos pass integrity verification but a descent cannot be measured, the UI shows **Photo evidence available** and **Assumption-based · photo evidence only**. The backend retains the unavailable intensity state instead of inventing a motion level. A separate, unvalidated 10% relative photo-presence adjustment is applied (`score_basis: verified_photos_policy`), giving 5.00% from the current 5.56% population baseline. Measurable falls retain the high/medium/low policy. Photos failing integrity verification receive no adjustment. Photo integrity is not proof of fall severity, claim truth, or a demonstrated reduction in disputes.


### Assumed reduction ranges (version 21)

All automatic evidence-adjusted estimates now use scenario intervals: photo-only / low intensity 0–10%, medium 10–25%, high 25–35% relative reduction. The endpoint returns `assumed_relative_reduction_range` and `adjusted_probability_range`, and no exact adjusted probability or reduction point. Probability bounds are baseline × (1 − upper reduction) through baseline × (1 − lower reduction). These are configurable assumption boundaries, not learned uncertainty estimates, statistical confidence intervals, or randomized per-event predictions. Charts shade the interval, and the numeric risk index and probability display both bounds. Evidence failing verification receives no adjustment.

### Perspective-tolerant horizontal posture (version 23)

The camera can corroborate a tilted torso with the visible whole-body axis. The original 60-degree torso rule remains. A second camera-only path accepts torso angle at least 45 degrees when shoulder-to-ankle and hip-to-ankle axes are each at least 60 degrees from vertical, the visible body box is wider than tall, and the hip-to-ankle length is at least 0.8 torso lengths. Shoulder and ankle vectors from the hip must point in opposing directions to exclude seated forward folds. It requires visible ankles and does not infer floor contact or injury from geometry alone.

This addresses a projected lying pose previously classified Other at roughly 50 degrees of torso tilt. It does not relax person visibility or the independent IMAGE-mode person check. Steady standing, descent timing/drop, confirmation, and one-event-per-fall behavior remain. Pose logs include body-axis angle (`ba`) and horizontal criterion (`hb`, torso or whole_body) for diagnosis. Tests cover perspective in square/wide frames, bent/upright-leg negatives, startup lying, and a full captured tilted-pose event reaching an existing ledger.

### Direct camera controls and Full pose model (version 24)

Capture now offers Start and Stop without device-name, camera-selection, or checkbox fields. A stable browser-local device identifier is generated automatically; camera access prefers the rear camera and permits the browser to select an available camera. Browser camera permission still applies. Stop releases the stream and enables Start again.

Both VIDEO tracking and independent IMAGE person verification use the official pretrained MediaPipe Pose Landmarker Full float16 v1 model instead of Lite. This is a pretrained-model upgrade, not custom training or measured lying-position accuracy. Existing posture geometry and event confirmation rules remain. A real-model browser smoke test verified loading, inference startup and stream shutdown; the synthetic tilted-pose integration test verified photo-only upload and ledger updates. Labeled representative examples are still needed to evaluate lying-position accuracy.

### Direct Other-pose events and torch (version 30)

At user request, live capture enables otherConfirmsFall: a person classified Other immediately confirms one suspected_fall event without a standing or lying transition. A fresh IMAGE person check remains required. Empty frames do not trigger, and standing recovery rearms detection. This intentionally includes sitting/bending poses classified Other. These events retain observedFall=false and zero unmeasured descent/speed. A confirmation photo is taken immediately.

Torch on/off appears only when the video track reports torch capability. Browser tests simulated torch hardware; physical support depends on the phone camera and browser.
