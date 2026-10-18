"""End-to-end check in a real (headless) Chromium: phone page -> server -> viewer.

The camera is Chromium's fake camera and MediaPipe is replaced by a stub that
plays a scripted pose sequence (stand 4 s, fall, lie down), so everything else
runs for real: snapshots, ring buffer, key frames, IndexedDB outbox, upload,
hashing, storage, viewer, verification.

    python tests/e2e_browser.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("E2E_OUT", tempfile.mkdtemp(prefix="fw-e2e-out-")))
CHROME = os.environ.get("CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")

FAKE_MP = """
import { pose, falling } from "https://cdn.jsdelivr.net/fallwatch-test/synthetic.js";
let t0 = null, freshCalls = 0;
function nextPose() {
  if (t0 === null) t0 = performance.now();
  const s = (performance.now() - t0) / 1000;
  let lms;
  if (s < 4) lms = pose("standing");
  else if (s < 4.6) lms = falling((s - 4) / 0.6, { side: "left" });
  else lms = pose("lying", { side: "left" });
  return { landmarks: [lms] };
}
export const FilesetResolver = { forVisionTasks: async () => ({}) };
export const PoseLandmarker = { createFromOptions: async () => ({
  detectForVideo: nextPose,
  detect: () => (__FRESH_EMPTY__ || (__FRESH_GLITCH__ && freshCalls++ === 0)) ? { landmarks: [] } : nextPose(),
  close() {},
}) };
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fw-e2e-"))
    port = free_port()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp / 'e2e.db'}", "LOCAL_STORAGE_DIR": str(tmp / "obj"),
           "FALLWATCH_ENV_FILE": str(tmp / "none.env"), "DEVICE_KEYS": "e2e-phone-key-0123456789abcdefgh", "VIEWER_KEYS": "e2e-viewer-key-0123456789abcdefgh", "PRE_EVENT_MS": "5000", "POST_EVENT_MS": "5000"}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                           cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/healthz")
                break
            except Exception:
                time.sleep(0.2)
        OUT.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=CHROME, args=[
                "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
            ctx = b.new_context(viewport={"width": 420, "height": 900}, permissions=["camera"])
            no_event = os.environ.get("E2E_NO_EVENT")
            source = FAKE_MP.replace("__FRESH_EMPTY__", "true" if no_event == "exit" else "false").replace("__FRESH_GLITCH__", "true" if os.environ.get("E2E_FRESH_GLITCH") else "false")
            if no_event == "lying":
                source = source.replace("if (s < 4)", "if (false)").replace("else if (s < 4.6)", "else if (false)")
            elif no_event == "standing":
                source = source.replace("if (s < 4)", "if (true)")
            ctx.route("https://cdn.jsdelivr.net/**/vision_bundle.mjs", lambda r: r.fulfill(
                body=source, content_type="text/javascript", headers={"Access-Control-Allow-Origin": "*"}))
            ctx.route("https://cdn.jsdelivr.net/fallwatch-test/synthetic.js", lambda r: r.fulfill(
                body=(ROOT / "tests" / "synthetic.js").read_text(), content_type="text/javascript",
                headers={"Access-Control-Allow-Origin": "*"}))
            ctx.add_init_script("""
              // A live canvas stream is deterministic on Windows, where Chrome's
              // built-in fake camera can end its tracks immediately after startup.
              window.recordingAttempts = 0;
              window.MediaRecorder = class { constructor() { window.recordingAttempts++; throw Error('Video recording must stay disabled'); } static isTypeSupported() { return true; } };
              navigator.mediaDevices.getUserMedia = async () => {
                const canvas = document.createElement('canvas');
                canvas.width = 640; canvas.height = 360;
                const g = canvas.getContext('2d');
                const draw = () => {
                  g.fillStyle = '#202934'; g.fillRect(0, 0, 640, 360);
                  g.fillStyle = '#364354'; g.fillRect(0, 250, 640, 110);
                  g.fillStyle = '#738496'; g.fillRect(70, 50, 90, 130);
                  g.fillStyle = '#b1bfcb';
                  g.beginPath(); g.arc(315, 115, 20, 0, Math.PI * 2); g.fill();
                  g.fillRect(301, 137, 28, 75); g.fillRect(301, 212, 10, 60); g.fillRect(319, 212, 10, 60);
                  g.fillStyle = '#67d8c0'; g.font = '14px monospace';
                  g.fillText('TEST CAMERA  ' + new Date().toISOString(), 16, 28);
                };
                draw(); setInterval(draw, 100);
                return canvas.captureStream(10);
              };
            """)
            page = ctx.new_page()
            logs = []
            page.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
            page.goto(base + "/capture/")
            assert page.url.endswith("/capture/")
            page.fill("#deviceId", "e2e-phone")
            assert page.locator("#deviceKey").count() == 0
            assert page.locator("#sBuffer, #sMem").count() == 0
            page.check("#consent")
            page.click("#startBtn")
            page.wait_for_selector("#live:not([hidden])", timeout=15000)
            if no_event:
                page.wait_for_timeout(11000)
                assert page.evaluate("window.recordingAttempts") == 0
                page.click("#stopBtn")
                saved = ctx.request.get(base + "/api/events", headers={"X-Viewer-Key": "e2e-viewer-key-0123456789abcdefgh"}).json()
                assert saved == [], saved
                assert page.evaluate("window.recordingAttempts") == 0
                assert not list((tmp / "obj").rglob("*")), "No-fall footage was persisted"
                print(json.dumps({"no_event_scenario": no_event, "saved_events": 0, "recording_attempts": 0}))
                b.close()
                return 0
            page.wait_for_timeout(1000)
            # Opening logs must not unload or stop the monitoring camera.
            page.click("#menuToggle")
            page.click("[data-view='logs']")
            frame = page.frame_locator("#logsFrame")
            frame.locator("#eventCount").get_by_text("0", exact=True).wait_for()
            assert page.locator("#video").evaluate("video => video.srcObject.active && !video.paused"), page.locator("#video").evaluate("v => ({active:v.srcObject?.active,paused:v.paused,time:v.currentTime,ready:v.readyState})")
            # Wait for a complete automatic upload while the log covers the camera.
            for _ in range(45):
                check = urllib.request.Request(base + "/api/events", headers={"X-Viewer-Key": "e2e-viewer-key-0123456789abcdefgh"})
                if json.load(urllib.request.urlopen(check)):
                    break
                page.wait_for_timeout(500)
            frame.locator("#eventCount").get_by_text("1", exact=True).wait_for()
            page.click("#menuToggle")
            page.click("[data-view='camera']")
            assert page.locator("#video").evaluate("video => video.srcObject.active && !video.paused"), page.locator("#video").evaluate("v => ({active:v.srcObject?.active,paused:v.paused,time:v.currentTime,ready:v.readyState})")
            page.screenshot(path=str(OUT / "phone-down.png"))
            finish = os.environ.get("E2E_FINISH", "normal")
            if finish in {"stop", "eof"}:
                page.wait_for_selector("#banner.down", timeout=10000)
                if finish == "stop":
                    page.click("#stopBtn")
                else:
                    page.evaluate("Object.defineProperty(document.querySelector('#video'), 'ended', {get: () => true})")
            # Wait for the confirmed fall + "after" pictures + upload.
            deadline = time.time() + 25
            events = []
            while time.time() < deadline:
                req = urllib.request.Request(base + "/api/events", headers={"X-Viewer-Key": "e2e-viewer-key-0123456789abcdefgh"})
                events = json.load(urllib.request.urlopen(req))
                if events:
                    break
                page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / "phone-sent.png"))
            assert len(events) == 1, f"events={events} logs={logs[-10:]}"
            assert not [m for m in logs if m.startswith("error:") and "401" not in m], logs
            stage = page.locator(".stage").bounding_box()
            if finish != "stop":
                assert stage["height"] >= 600, stage
            ev_id = events[0]["id"]
            req = urllib.request.Request(f"{base}/api/events/{ev_id}", headers={"X-Viewer-Key": "e2e-viewer-key-0123456789abcdefgh"})
            ev = json.load(urllib.request.urlopen(req))
            roles = [i["role"] for i in ev["images"]]
            req = urllib.request.Request(f"{base}/api/events/{ev_id}/verify", headers={"X-Viewer-Key": "e2e-viewer-key-0123456789abcdefgh"})
            ver = json.load(urllib.request.urlopen(req))

            manifest_req = urllib.request.Request(f"{base}/api/events/{ev_id}/files/manifest.json", headers={"X-Viewer-Key": "e2e-viewer-key-0123456789abcdefgh"})
            manifest = json.load(urllib.request.urlopen(manifest_req))
            assert "clip" not in manifest and ev["clip"] is None
            assert not list((tmp / "obj").rglob("*.webm")) and not list((tmp / "obj").rglob("*.mp4"))
            assert page.evaluate("window.recordingAttempts") == 0
            assert all(ev["stand_t_ms"] - 5000 <= image["captured_at_ms"] <= ev["lie_t_ms"] + 5000 for image in ev["images"])
            v = ctx.new_page()
            v.on("console", lambda m: logs.append(f"viewer {m.type}: {m.text}"))
            v.on("pageerror", lambda e: logs.append(f"viewer error: {e}"))
            v.set_viewport_size({"width": 1280, "height": 1500})
            v.goto(base + "/viewer/")
            v.wait_for_selector("#verifyBtn", timeout=10000)
            assert v.locator("#eventClip").count() == 0
            v.click("#verifyBtn")
            try:
                v.wait_for_selector(".badge.ok", timeout=10000)
            except Exception:
                v.screenshot(path=str(OUT / "viewer-error.png"), full_page=True)
                print("Viewer verification:", v.locator("#verifyOut").inner_text(), logs[-15:])
                raise
            assert v.locator("#eventCount").inner_text() == "1"
            assert v.locator("#eventHash").inner_text() == ev["manifest_sha256"]
            assert v.locator("#eventTime").get_attribute("datetime")
            assert v.locator("#dailyChart svg").count() == 1
            v.wait_for_function("() => document.querySelector('#mainPhoto')?.naturalWidth > 0")
            assert "laid" in v.locator("#mainPhoto").get_attribute("alt")
            position = int(v.locator("#photoPosition").inner_text().split("/")[0])
            v.click("#nextPhoto")
            assert int(v.locator("#photoPosition").inner_text().split("/")[0]) == position + 1
            v.wait_for_timeout(500)
            v.screenshot(path=str(OUT / "viewer.png"), full_page=True)
            # Seed a real earlier-day record in the isolated test server, then
            # verify history, filtering, selection and persistence across reload.
            assert v.locator(".history-row").count() == 1
            first_image = ev["images"][0]
            image_req = urllib.request.Request(f"{base}/api/events/{ev_id}/files/{first_image['name']}", headers={"X-Viewer-Key": "e2e-viewer-key-0123456789abcdefgh"})
            image_bytes = urllib.request.urlopen(image_req).read()
            archive_meta = {**ev["meta"], "client_event_id": "archive-history-event", "detected_at_ms": ev["detected_at_ms"] - 86400000,
                            "frames": [{"name": "archive.jpg", "t": ev["detected_at_ms"] - 86400000, "role": "laid"}]}
            archive = ctx.request.post(base + "/api/events", headers={"X-Device-Key": "e2e-phone-key-0123456789abcdefgh"}, multipart={
                "meta": json.dumps(archive_meta), "images": {"name": "archive.jpg", "mimeType": "image/jpeg", "buffer": image_bytes}})
            assert archive.status == 200, archive.text()
            archive_id = archive.json()["id"]
            v.click("#refresh")
            v.locator(f"[data-event-id='{archive_id}']").wait_for()
            assert v.locator(".history-row").count() == 2
            assert v.locator("#eventCount").inner_text() == "1", "Today's count must exclude history"
            v.click(f"[data-event-id='{archive_id}']")
            v.wait_for_function("() => document.querySelector('#photoPosition')?.textContent === '1 / 1'")
            v.wait_for_function("() => document.querySelector('#mainPhoto')?.naturalWidth > 0")
            v.click("#historyToday")
            v.wait_for_function("() => document.querySelectorAll('.history-row').length === 1")
            assert v.locator(f"[data-event-id='{archive_id}']").count() == 0
            v.click("#historyAll")
            v.locator(f"[data-event-id='{archive_id}']").wait_for()
            v.reload()
            v.locator(f"[data-event-id='{archive_id}']").wait_for()
            assert v.locator(".history-row").count() == 2
            v.click(f"[data-event-id='{ev_id}']")
            v.wait_for_function("() => document.querySelector('#mainPhoto')?.naturalWidth > 0")
            v.set_viewport_size({"width": 390, "height": 844})
            v.wait_for_timeout(300)
            assert v.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile overflow"
            v.screenshot(path=str(OUT / "logs-mobile.png"), full_page=True)
            v.set_viewport_size({"width": 1440, "height": 1100})
            v.screenshot(path=str(OUT / "logs-desktop.png"), full_page=True)
            b.close()

        summary = {
            "uploaded_events": len(events), "images": len(roles),
            "roles": {r: roles.count(r) for r in dict.fromkeys(roles)},
            "bytes_total": sum(i["bytes"] for i in ev["images"]),
            "confirmation_ms": ev["meta"]["descriptor"]["confirmedT"] - ev["meta"]["descriptor"]["lieT"],
            "descent_ms": ev["descent_ms"], "landing_side": ev["landing_side"], "floor_ms": ev["floor_ms"],
            "verify": ver["ok"], "clip_bytes": 0, "camera_height": stage["height"] if stage else None,
            "screenshots": str(OUT),
        }
        print(json.dumps(summary, indent=2))
        assert roles.count("standing") == 1 and roles.count("laid") == 1, roles
        assert ev["event_type"] == "fall", ev["event_type"]
        assert ver["ok"], ver
        assert ev["landing_side"] == "left", ev["landing_side"]
        delay = ev["meta"]["descriptor"]["confirmedT"] - ev["meta"]["descriptor"]["lieT"]
        assert 300 <= delay < 1000, delay
        print("E2E OK")
        return 0
    finally:
        srv.terminate()
        srv.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
