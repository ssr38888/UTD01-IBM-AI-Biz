"""Isolated browser check: trained scoring, saved claim linkage and real API evidence."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = os.environ.get("E2E_FRONTEND") == "1"
tmp = Path(tempfile.mkdtemp(prefix="claimclock-integration-"))
env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp / 'test.db'}",
       "LOCAL_STORAGE_DIR": str(tmp / 'objects'), "FALLWATCH_ENV_FILE": str(tmp / 'none.env'),
       "DEVICE_KEYS": "integration-device-01234567890123456789",
       "VIEWER_KEYS": "integration-viewer-01234567890123456789",
       "CLAIMCLOCK_MODEL_PATH": str(ROOT / 'data/claimclock/model.joblib'),
       "CLAIMCLOCK_POPULATION_PATH": str(ROOT / 'data/claimclock/population_baseline.json')}
base = "http://127.0.0.1:8012"
with (tmp / 'server.log').open('w') as log:
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8012'],
                            cwd=ROOT / 'backend', env=env, stdout=log, stderr=log)
    try:
        for _ in range(100):
            try:
                if httpx.get(base + '/healthz').status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        else:
            raise RuntimeError((tmp / 'server.log').read_text())
        ids = []
        for number in range(2):
            frames = [{'name': f'f{i}.jpg', 'role': role, 't': 1760000000000 + i * 500}
                      for i, role in enumerate(['pre', 'standing', 'burst', 'laid', 'post'])]
            meta = {'device_id': 'SYNTHETIC-INTEGRATION-TEST', 'client_event_id': str(uuid.uuid4()),
                    'detected_at_ms': 1760000004000 + number * 10000,
                    'descriptor': {'standT': 1760000000500, 'lieT': 1760000001500,
                                   'confirmedT': 1760000001800, 'descentMs': 1000,
                                   'hipDropBodyHeights': .42, 'hipSpeedBodyHeightsPerSec': .42,
                                   'landingSide': 'unclear', 'floorMs': 5000, 'recovered': False},
                    'frames': frames}
            buf = io.BytesIO(); Image.new('RGB', (128, 96), 'navy').save(buf, 'JPEG')
            start = meta['descriptor']['standT']
            poses = [{'t': start + i * 100, 'h': .4 + i * .03,
                      'p': 'standing' if i == 0 else 'lying' if i == 10 else 'other',
                      'k': [[.5, .5, .9] for _ in range(9)]} for i in range(11)]
            if number == 0:
                poses = []  # An archived event without observable descent.
            response = httpx.post(base + '/api/events', data={'meta': json.dumps(meta)},
                                  files=[('images', (f['name'], buf.getvalue(), 'image/jpeg')) for f in frames]
                                  + [('keypoints', ('keypoints.json', json.dumps(poses).encode(), 'application/json'))],
                                  headers={'X-Device-Key': env['DEVICE_KEYS']})
            assert response.status_code == 200, response.text
            ids.append(response.json()['id'])
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('CHROME', 'C:/Program Files/Google/Chrome/Application/chrome.exe'), headless=True)
            page = browser.new_page(viewport={'width': 1200, 'height': 1000})
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + ('/' if FRONTEND else '/viewer/'))
            if FRONTEND:
                assert page.url.endswith('/frontend/')
                expect(page.locator('#chain .card')).to_have_count(2)
                expect(page.frame_locator('#cameraFrame').locator('#consent')).to_be_visible()
            page.locator('#claimPanel').wait_for(state='visible')
            expect(page.locator('#baselineScore')).to_have_text('5.56%', timeout=30000)
            expect(page.locator('#adjustedScore')).to_have_text('4.17–5.00%')
            expect(page.locator('#relativeEffect')).to_have_text('10–25%')
            expect(page.locator('#beforeIndex')).to_have_text('100%')
            expect(page.locator('#afterIndex')).to_have_text('75–90%')
            expect(page.locator('#riskIndex')).to_have_text('Relative risk index: 100 → 75–90 (not a probability)')
            expect(page.locator('#fallIntensity')).to_have_text('Medium fall intensity')
            assert page.locator('#claimPanel input').count() == 0
            assert page.locator('#claimForm').count() == 0
            before = page.locator('#baselineScore').inner_text()
            after = page.locator('#adjustedScore').inner_text()
            assert before != '—' and after != '—'
            assert 'Assumption-based' in page.locator('#claimPanel').inner_text()
            page.reload()
            expect(page.locator('#baselineScore')).to_have_text(before)
            expect(page.locator('#adjustedScore')).to_have_text(after)
            page.locator(f'[data-event-id="{ids[0]}"]').click() if FRONTEND else page.locator('#eventSelect').select_option(ids[0])
            expect(page.locator('#claimIncident')).to_contain_text('Event #1')
            expect(page.locator('#baselineScore')).to_have_text('5.56%')
            expect(page.locator('#adjustedScore')).to_have_text('5.00–5.56%')
            expect(page.locator('#relativeEffect')).to_have_text('0–10%')
            expect(page.locator('#adjustmentStatus')).to_contain_text('photo evidence only')
            expect(page.locator('#fallIntensity')).to_have_text('Photo evidence available')
            page.locator(f'[data-event-id="{ids[1]}"]').click() if FRONTEND else page.locator('#eventSelect').select_option(ids[1])
            expect(page.locator('#claimIncident')).to_contain_text('Event #2')
            expect(page.locator('#adjustedScore')).to_have_text('4.17–5.00%')
            expect(page.locator('#beforeRing')).to_have_css('stroke-dashoffset', '0px')
            expect(page.locator('#afterRing')).to_have_css('stroke-dashoffset', '25px')
            page.locator('#claimPanel').scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT / ('.tools/frontend-integration.png' if FRONTEND else '.tools/claim-integration.png')))
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            browser.close()
        print(json.dumps({'historical_baseline': before, 'assumption_based_estimate': after,
                          'persistence': True, 'event_isolation': True, 'mobile_layout': True,
                          'browser_errors': errors, 'test_database': str(tmp)}))
    finally:
        proc.terminate()
        proc.wait(timeout=20)
