"""Native Chromium desktop drops + protected FileList regressions, fictional APIs only."""
import argparse
import base64
import json
import mimetypes
import os
import tempfile
import wave
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / '.playwright-browsers')
PIXEL = base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--frontend-source', type=Path, default=ROOT / 'frontend')
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/desktop-drop-qa/after.json')
    args = parser.parse_args()
    frontend = args.frontend_source.resolve()
    board = dict(id='drop-board', name='Drop test', nodes=[], connections=[], viewport=dict(x=0, y=0, scale=1))
    wb = dict(id='drop-workbench', name='Drop test', assets=[], episodes=[])
    server = dict(canvas=dict(boards=[board], updatedAt='initial', storageEpoch='drop-test'),
                  workbench=dict(workbenches=[wb], updatedAt='initial', storageEpoch='drop-test'))
    uploads, errors, results = [], [], []
    media, fixtures = {}, {}

    def check(name, passed, **details):
        results.append(dict(name=name, passed=bool(passed), **details))

    def route_request(route):
        parsed = urlparse(route.request.url)
        if parsed.netloc != 'drop.test':
            route.abort()
            return
        path = unquote(parsed.path)
        if path in media:
            body, mime = media[path]
            route.fulfill(body=body, content_type=mime)
            return
        if path.startswith('/api/'):
            data = {}
            if path == '/api/auth/me':
                data = {'user': {'id': 'drop-test', 'username': 'drop-test'}}
            elif path == '/api/app-config':
                data = dict(disable_browser_channels=True, disable_doubao=True, points_enabled=False)
            elif path in ('/api/canvas', '/api/workbench'):
                kind = path.rsplit('/', 1)[1]
                data = server[kind]
                if route.request.method in ('PATCH', 'PUT'):
                    data.update(route.request.post_data_json, updatedAt='saved')
            elif path == '/api/accounts':
                data = []
            elif path == '/api/tasks':
                data = {'tasks': []}
            elif path == '/api/upload':
                content_type = route.request.headers['content-type']
                message = BytesParser(policy=default).parsebytes(
                    f'Content-Type: {content_type}\r\n\r\n'.encode() + route.request.post_data_buffer)
                parts = {part.get_param('name', header='content-disposition'): part for part in message.iter_parts()}
                part = parts['file']
                payload = part.get_payload(decode=True)
                filename = part.get_filename()
                # Chromium omits native file bytes from intercepted postData; use
                # our original fixture for the fictional upload's preview response.
                preview = fixtures.get(filename, payload)
                uploads.append(dict(filename=filename, capturedBytes=len(payload), fixtureBytes=len(preview),
                                    board=parts['board_id'].get_content() if 'board_id' in parts else None,
                                    workbench=parts['workbench_id'].get_content() if 'workbench_id' in parts else None))
                url = f'/fixtures/upload-{len(uploads)}{Path(filename).suffix}'
                media[url] = (preview, part.get_content_type())
                data = dict(url=url, kind=part.get_content_type().split('/')[0], width=64, height=64)
            route.fulfill(json=data)
            return
        relative = {'/canvas': 'canvas.html', '/workbench': 'workbench.html', '/': 'index.html'}.get(
            path, path.removeprefix('/static/'))
        target = (frontend / relative).resolve()
        if not target.is_file() and args.baseline:
            target = (ROOT / 'frontend' / relative).resolve()
        if target.is_relative_to(ROOT) and target.is_file():
            route.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream')
        else:
            route.fulfill(status=404, body='')

    with tempfile.TemporaryDirectory(prefix='canvas-desktop-drop-') as directory, sync_playwright() as p:
        sample = Path(directory) / 'desktop-image.gif'
        sample.write_bytes(PIXEL)
        sound = Path(directory) / 'desktop-audio.wav'
        with wave.open(str(sound), 'wb') as out:
            out.setparams((1, 2, 8000, 800, 'NONE', 'not compressed'))
            out.writeframes(b'\0\0' * 800)
        fixtures[sample.name] = sample.read_bytes()
        fixtures[sound.name] = sound.read_bytes()
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport=dict(width=1440, height=900))
        context.route('**/*', route_request)
        context.add_init_script("""
          localStorage.setItem('canvas_session_token', 'fictional-drop-test');
          window.__dropEvents = [];
          for (const type of ['dragenter', 'dragover', 'drop']) {
            window.addEventListener(type, e => __dropEvents.push({type, trusted:e.isTrusted,
              files:e.dataTransfer.files.length, items:e.dataTransfer.items.length,
              types:[...e.dataTransfer.types]}), true);
          }
        """)

        def new_page(kind, doc_id, selector):
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(error.stack))
            page.goto(f'http://drop.test/{kind}?id={doc_id}', wait_until='domcontentloaded')
            page.wait_for_selector(selector, state='visible')
            return page

        def native_drop(page, selector, files):
            box = page.locator(selector).first.bounding_box()
            session = context.new_cdp_session(page)
            position = dict(x=box['x'] + box['width'] / 2, y=box['y'] + box['height'] / 2)
            payload = dict(items=[], files=[str(f) for f in files], dragOperationsMask=1)
            for event in ('dragEnter', 'dragOver', 'drop'):
                session.send('Input.dispatchDragEvent', dict(type=event, **position, data=payload))
            session.detach()

        def wait_count(page, expression, count):
            try:
                page.wait_for_function(f"({expression}).filter(x => x.status === 'completed').length === {count}", timeout=4000)
                return True
            except PlaywrightTimeoutError:
                return False

        canvas = new_page('canvas', board['id'], '#viewport')
        # Don't wait for late engine setup: a visible board must accept files.
        native_drop(canvas, '#viewport', [sample])
        check('canvas-first-visible-native-drop', wait_count(canvas, 'CanvasStore.activeBoard().nodes', 1))
        canvas.wait_for_function("document.documentElement.dataset.mediaImportBound === '1'")
        events = canvas.evaluate('__dropEvents')
        check('native-dragover-protects-files-until-drop',
              any(e['type'] == 'dragover' and e['trusted'] and e['files'] == 0 and e['items'] for e in events)
              and any(e['type'] == 'drop' and e['trusted'] and e['files'] == 1 for e in events))

        encoded_video = canvas.evaluate("""async () => {
          const el = document.createElement('canvas'); el.width = el.height = 64;
          const stream = el.captureStream(10);
          const recorder = new MediaRecorder(stream, {mimeType:'video/webm'});
          const chunks = []; recorder.ondataavailable = e => chunks.push(e.data);
          const done = new Promise(resolve => recorder.onstop = resolve);
          recorder.start(); el.getContext('2d').fillRect(0,0,64,64);
          await new Promise(resolve => setTimeout(resolve, 250)); recorder.stop(); await done;
          stream.getTracks().forEach(track => track.stop());
          const bytes = new Uint8Array(await new Blob(chunks).arrayBuffer());
          return btoa(String.fromCharCode(...bytes));
        }""")
        video = Path(directory) / 'desktop-video.webm'
        video.write_bytes(base64.b64decode(encoded_video))
        fixtures[video.name] = video.read_bytes()
        before = canvas.evaluate('CanvasStore.activeBoard().nodes.length')
        start_uploads = len(uploads)
        native_drop(canvas, '#viewport', [sample, sound, video])
        check('canvas-native-image-audio-video-batch', wait_count(canvas, 'CanvasStore.activeBoard().nodes', before + 3)
              and len(uploads) == start_uploads + 3)
        check('canvas-upload-scope', all(u['board'] == board['id'] and not u['workbench'] for u in uploads))
        types = canvas.evaluate('CanvasStore.activeBoard().nodes.map(n=>n.type)')
        check('canvas-media-node-types', all(t in types for t in ['ref-image', 'ref-audio', 'ref-video']))
        try:
            canvas.wait_for_function("""() => [...document.querySelectorAll('#nodes-layer img')].some(v=>v.currentSrc.includes('/fixtures/') && v.naturalWidth > 0)
              && [...document.querySelectorAll('#nodes-layer video')].some(v=>v.currentSrc.includes('/fixtures/') && v.readyState >= 1)""", timeout=4000)
            check('canvas-imported-media-decodes', True)
        except PlaywrightTimeoutError:
            check('canvas-imported-media-decodes', False, videos=canvas.evaluate("[...document.querySelectorAll('#nodes-layer video')].map(v=>({src:v.currentSrc, state:v.readyState, error:v.error?.message}))"))

        bench = new_page('workbench', wb['id'], '.workbench-assets-panel')
        native_drop(bench, '.workbench-assets-panel', [sample, sound, video])
        check('workbench-native-image-audio-video-batch', wait_count(bench, 'WorkbenchStore.activeWorkbench().assets', 3))
        bench.wait_for_function('window.__wbExtrasReady')
        # Only DataTransfer.items are readable and a previous drag was interrupted.
        before = bench.evaluate('WorkbenchStore.activeWorkbench().assets.length')
        hover = bench.evaluate("""() => {
          window.__wbDraggingAssetId = 'previous-asset'; window.__wbNativeAssetDragging = true;
          window.__wbPointerDraggingAsset = true;
          const dt = new DataTransfer();
          let requestedEffect = 'none';
          Object.defineProperty(dt, 'dropEffect', {get:()=>requestedEffect, set:value=>requestedEffect=value});
          dt.items.add(new File([Uint8Array.from(atob('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'),c=>c.charCodeAt(0))],
            'items-only.gif', {type:''}));
          Object.defineProperty(dt, 'files', {value:new DataTransfer().files});
          const target = document.querySelector('#workbench-assets-list');
          const over = new DragEvent('dragover', {bubbles:true, cancelable:true, dataTransfer:dt});
          target.dispatchEvent(over);
          const result = {accepted:over.defaultPrevented, effect:dt.dropEffect};
          target.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:dt}));
          return result;
        }""")
        check('workbench-stale-drag-keeps-copy-cursor', hover == dict(accepted=True, effect='copy'), observed=hover)
        check('workbench-items-only-drop-after-interrupted-drag', wait_count(bench, 'WorkbenchStore.activeWorkbench().assets', before + 1))
        check('workbench-clears-interrupted-drag-state', bench.evaluate('!window.__wbDraggingAssetId && !window.__wbNativeAssetDragging && !window.__wbPointerDraggingAsset'))

        bench.evaluate("""() => {
          wbEndAssetDrag();
          const wb = WorkbenchStore.activeWorkbench(); const ep = WorkbenchStore.createEpisode(1);
          wb.episodes.push(ep); WorkbenchStore.activeEpisodeId = ep.id;
          WorkbenchEpisodeTab.mode = 'clips'; WorkbenchView.render();
        }""")
        bench.wait_for_selector('.workbench-clip-refs', state='visible')
        start_uploads = len(uploads)
        native_drop(bench, '.workbench-clip-refs', [sample])
        check('workbench-desktop-drop-into-episode-refs', wait_count(bench, 'WorkbenchStore.activeEpisode().assets', 1)
              and bench.evaluate('WorkbenchStore.activeEpisode().clips[0].softRefIds.includes(WorkbenchStore.activeEpisode().assets[0].id)'))
        check('workbench-drop-uploaded-once', len(uploads) == start_uploads + 1)
        start_uploads = len(uploads)
        internal = bench.evaluate("""() => {
          const wb = WorkbenchStore.activeWorkbench(); const id = wb.assets[0].id;
          const dt = new DataTransfer(); dt.setData('application/x-wb-asset-id',id); dt.setData('text/plain',id);
          wbBeginAssetDrag(id);
          const target = document.querySelector('.workbench-clip-refs');
          target.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt}));
          const overlay = document.documentElement.classList.contains('is-os-file-dragging');
          target.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));
          return !overlay && WorkbenchStore.activeEpisode().clips[0].softRefIds.includes(id);
        }""")
        check('workbench-internal-asset-drag-still-adds-reference', internal and len(uploads) == start_uploads)
        check('workbench-upload-scope', all(u['workbench'] == wb['id'] for u in uploads if u['workbench']))

        for page, kind, expression in [(canvas, 'canvas', 'CanvasStore.activeBoard().nodes'),
                                       (bench, 'workbench', 'WorkbenchStore.activeWorkbench().assets')]:
            page.evaluate("""() => {
              const dt = new DataTransfer(); dt.items.add(new File(['x'],'hover.png',{type:'image/png'}));
              document.body.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt}));
              window.dispatchEvent(new Event('blur'));
            }""")
            check(kind + '-cancel-clears-drop-cursor', page.evaluate("!document.documentElement.classList.contains('is-os-file-dragging') && document.documentElement.style.cursor !== 'copy'"))
            count = page.evaluate(f'({expression}).length')
            page.evaluate('CanvasStore.flushSave()' if kind == 'canvas' else 'WorkbenchStore.flushSave()')
            page.reload(wait_until='networkidle')
            check(kind + '-drop-persists-after-reload', wait_count(page, expression, count))
        check('no-page-errors', not errors, errors=errors)
        report = dict(checks=results, passed=sum(r['passed'] for r in results), total=len(results), uploads=uploads)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(passed=report['passed'], total=report['total'], failures=[r for r in results if not r['passed']],
                              output=str(args.output)), ensure_ascii=True))
        browser.close()
        if not args.baseline:
            assert all(r['passed'] for r in results), results


if __name__ == '__main__':
    main()
