"""Live frontend -> embedded capture -> upload -> ledger -> server scores, isolated data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import io
import uuid
from PIL import Image

import httpx
from playwright.sync_api import sync_playwright, expect
from e2e_browser import FAKE_MP

ROOT = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp(prefix='fallwatch-frontend-camera-'))
env = {**os.environ, 'DATABASE_URL': f'sqlite:///{tmp / "test.db"}',
       'LOCAL_STORAGE_DIR': str(tmp / 'objects'), 'FALLWATCH_ENV_FILE': str(tmp / 'none.env'),
       'DEVICE_KEYS': 'test-camera-01234567890123456789', 'VIEWER_KEYS': 'test-viewer-01234567890123456789'}
base = 'http://127.0.0.1:8014'
with (tmp / 'server.log').open('w') as log:
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8014'],
                              cwd=ROOT / 'backend', env=env, stdout=log, stderr=log)
    try:
        for _ in range(80):
            try:
                if httpx.get(base + '/healthz').status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        else:
            raise RuntimeError((tmp / 'server.log').read_text())
        seeded_ids=[]
        seed_count=10 if os.environ.get('E2E_SEEDED') else 0
        for i in range(seed_count):
            timestamp=1760000000000+i*10000
            meta={'device_id':'ARCHIVED-TEST','client_event_id':str(uuid.uuid4()),'detected_at_ms':timestamp,
                  'descriptor':{'standT':timestamp,'lieT':timestamp+1000,'confirmedT':timestamp+1300,
                    'descentMs':1000,'hipDropBodyHeights':.4,'hipSpeedBodyHeightsPerSec':.4,
                    'landingSide':'unclear','floorMs':5000,'recovered':False},
                  'frames':[{'name':'photo.jpg','t':timestamp,'role':'laid'}]}
            buf=io.BytesIO();Image.new('RGB',(64,48),'navy').save(buf,'JPEG')
            response=httpx.post(base+'/api/events',data={'meta':json.dumps(meta)},
                                files={'images':('photo.jpg',buf.getvalue(),'image/jpeg')},headers={'X-Device-Key':env['DEVICE_KEYS']})
            assert response.status_code==200,response.text
            seeded_ids.append(response.json()['id'])
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, permissions=['camera'])
            source = FAKE_MP.replace('__FRESH_EMPTY__', 'false').replace('__FRESH_GLITCH__', 'false')
            if os.environ.get('E2E_TILTED'):
                source=source.replace('  return { landmarks: [lms] };', '''
                  if(s>=4.6){ lms[11].y-=.185; lms[12].y-=.185; }
                  for(const point of lms) point.x/=(16/9);
                  return { landmarks: [lms] };''')
            context.route('https://cdn.jsdelivr.net/**/vision_bundle.mjs', lambda route: route.fulfill(body=source, content_type='text/javascript', headers={'Access-Control-Allow-Origin':'*'}))
            context.route('https://cdn.jsdelivr.net/fallwatch-test/synthetic.js', lambda route: route.fulfill(body=(ROOT / 'tests/synthetic.js').read_text(), content_type='text/javascript', headers={'Access-Control-Allow-Origin':'*'}))
            context.add_init_script('''
              window.recordingAttempts=0;
              window.MediaRecorder=class {constructor(){window.recordingAttempts++;throw Error('No video recording allowed')}};
              navigator.mediaDevices.getUserMedia=async()=>{
                const canvas=document.createElement('canvas');canvas.width=640;canvas.height=360;
                const ctx=canvas.getContext('2d');
                const draw=()=>{ctx.fillStyle='#132235';ctx.fillRect(0,0,640,360);ctx.fillStyle='#67d8c0';ctx.font='20px monospace';ctx.fillText('SYNTHETIC CAMERA TEST '+Date.now(),30,180)};
                draw();setInterval(draw,100);return canvas.captureStream(10);
              };
            ''')
            page = context.new_page(); errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base + '/')
            assert page.url.endswith('/frontend/')
            if seed_count:
                expect(page.locator('#chain .card')).to_have_count(seed_count)

                page.locator('.chain-scroll').evaluate('el=>el.scrollLeft=el.scrollWidth')
            else:
                expect(page.locator('#chain')).to_contain_text('No saved incidents')
            assert page.locator('iframe').count()==0
            frame=context.new_page()
            frame.goto(base+'/capture/')
            assert frame.locator('input, select').count()==0, 'Camera still requires a setup form'
            expect(frame.locator('#startBtn')).to_be_enabled()
            page.wait_for_timeout(800)
            frame.locator('#startBtn').click()
            expect(frame.locator('#live')).to_be_visible(timeout=15000)
            expect(frame.locator('#stopBtn')).to_have_text('Stop monitoring')
            assert frame.locator('#stopBtn').bounding_box()['y'] < frame.locator('.stage').bounding_box()['y']
            page.wait_for_timeout(800)
            page.wait_for_timeout(1000)
            assert len(context.request.get(base+'/api/events',headers={'X-Viewer-Key':env['VIEWER_KEYS']}).json())==seed_count
            # Keep the stream running while reviewing another section of the parent page.
            page.get_by_role('link',name='Dispute score',exact=True).click()
            expect(frame.locator('#liveMsg')).to_contain_text('Event #',timeout=30000)
            page.locator('#refreshEvents').click()
            expect(page.locator('#chain .card')).to_have_count(seed_count+1,timeout=30000)
            expect(page.locator('#claimIncident')).to_contain_text(f'Event #{seed_count+1}')
            assert page.locator('#chain .card').first.evaluate('el=>{const card=el.getBoundingClientRect(),scroll=el.closest(".chain-scroll").getBoundingClientRect();return card.left>=scroll.left-2&&card.right<=scroll.right+2}'), 'New saved event remains outside the visible ledger'
            expect(page.locator('#claimPanel')).to_be_visible()
            expect(page.locator('#baselineScore')).to_have_text('5.56%')
            expect(page.locator('#evidencePhoto')).to_have_attribute('src', __import__('re').compile('^blob:'))
            assert frame.locator('#video').evaluate('v=>v.srcObject.active&&!v.paused')
            assert frame.locator('body').evaluate('()=>window.recordingAttempts')==0
            saved=context.request.get(base+'/api/events',headers={'X-Viewer-Key':env['VIEWER_KEYS']}).json()
            assert len(saved)==seed_count+1
            detail=context.request.get(base+'/api/events/'+saved[0]['id'],headers={'X-Viewer-Key':env['VIEWER_KEYS']}).json()
            assert detail['clip'] is None
            assert {'standing','laid'} <= {image['role'] for image in detail['images']}
            if os.environ.get('E2E_TILTED'):
                poses=context.request.get(base+'/api/events/'+saved[0]['id']+'/files/keypoints.json',headers={'X-Viewer-Key':env['VIEWER_KEYS']}).json()
                assert any(row.get('p')=='other' for row in poses), 'Other-pose trigger missing from evidence'
            page.locator('#verifySelected').click()
            expect(page.locator('#verifyResult')).to_have_text('Evidence intact · verified')
            page.locator('#verifyAll').click()
            expect(page.locator('#chainStatus')).to_contain_text('chain intact')
            page.locator('#top').scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT/'.tools/new-frontend-desktop.png'))
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ROOT/'.tools/new-frontend-mobile.png'))
            assert not errors,errors
            frame.locator('#stopBtn').click()
            expect(frame.locator('#setup')).to_be_visible()
            page.wait_for_timeout(800)
            expect(frame.locator('#startBtn')).to_be_enabled()
            assert frame.locator('#video').evaluate('v=>v.srcObject===null')
            if seed_count:
                # Another camera/device uploads without an iframe postMessage.
                page.locator('.chain-scroll').evaluate('el=>el.scrollLeft=el.scrollWidth')
                external={**meta,'client_event_id':str(uuid.uuid4())}
                response=context.request.post(base+'/api/events',headers={'X-Device-Key':env['DEVICE_KEYS']},multipart={
                    'meta':json.dumps(external),'images':{'name':'photo.jpg','mimeType':'image/jpeg','buffer':buf.getvalue()}})
                assert response.status==200,response.text()
                page.locator('#refreshEvents').click()
                expect(page.locator('#claimIncident')).to_contain_text(f'Event #{seed_count+2}')
                page.wait_for_function('()=>{const el=document.querySelector("#chain .card"),card=el.getBoundingClientRect(),scroll=el.closest(".chain-scroll").getBoundingClientRect();return card.left>=scroll.left-2&&card.right<=scroll.right+2}')
            browser.close()
        print(json.dumps({'camera_to_frontend_saved_events':len(saved),'photos':len(detail['images']),
                          'video_saved':False,'stream_continues':True,'verification':True,'mobile_layout':True,'errors':errors}))
    finally:
        server.terminate();server.wait(timeout=20)
