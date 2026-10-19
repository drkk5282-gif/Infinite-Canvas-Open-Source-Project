"""Canvas/home performance and video interaction checks; all APIs/media are fictional.

Run with Python + Playwright. Reads frontend directly without a live server.
Optional --screenshots DIRECTORY writes UI QA images to that directory.
"""
import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / '.playwright-browsers')
ORIGIN = 'http://smoothness.test'
PIXEL = base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    requests, errors = [], []
    server_canvas = {'boards': [], 'updatedAt': 'initial', 'storageEpoch': 'smoothness-test'}
    server_workbench = {'workbenches': [], 'updatedAt': 'initial', 'storageEpoch': 'smoothness-test'}

    def route_request(route):
        parsed = urlparse(route.request.url)
        if parsed.netloc != 'smoothness.test':
            route.abort()
            return
        path = unquote(parsed.path)
        if path.startswith('/fixtures/'):
            requests.append(path)
            route.fulfill(body=PIXEL, content_type='image/gif')
            return
        if path.startswith('/api/'):
            data = {}
            if path == '/api/auth/me':
                data = {'user': {'id': 'smoothness-test', 'username': 'smoothness-test', 'is_admin': False}}
            elif path == '/api/app-config':
                data = {'disable_browser_channels': True, 'disable_doubao': True, 'points_enabled': False}
            elif path == '/api/canvas':
                data = server_canvas
            elif path == '/api/workbench':
                data = server_workbench
            elif path == '/api/accounts':
                data = []
            elif path == '/api/tasks':
                data = {'tasks': []}
            route.fulfill(json=data)
            return
        relative = {'/': 'index.html', '/canvas': 'canvas.html', '/workbench': 'workbench.html'}.get(path, path.removeprefix('/static/'))
        target = (ROOT / 'frontend' / relative).resolve()
        if target.is_relative_to(ROOT / 'frontend') and target.is_file():
            route.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream')
        else:
            route.fulfill(status=404, body='')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 900})
        context.route('**/*', route_request)
        context.add_init_script("localStorage.setItem('canvas_session_token', 'fictional-smoothness-test');")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(error.stack or str(error)))
        page.goto(ORIGIN, wait_until='networkidle')
        page.wait_for_function('CanvasStore._persistence?.ready && WorkbenchStore._persistence?.ready')
        page.wait_for_function("!document.documentElement.classList.contains('is-workspace-booting')")
        page.evaluate("""() => {
          clearTaskPolling(); CanvasStore.stopSync();
          CanvasStore.boards = Array.from({length: 120}, (_, i) => ({
            id: `board-${i}`, name: `分镜画板 ${i + 1}`, connections: [],
            nodes: [{id: `image-${i}`, type: 'ref-image', imageUrl: `/fixtures/cover-${i}.gif`, x: 0, y: 0}]
          }));
          WorkspaceHome.render();
        }""")
        page.wait_for_function("document.querySelector('#workspace-board-grid img.is-media-loaded')")
        page.wait_for_timeout(250)
        initial_covers = len(set(requests))
        assert 0 < initial_covers < 40, initial_covers
        home = page.evaluate("""() => {
          const root = document.getElementById('workspace-board-grid');
          const first = root.querySelector('[data-id]');
          first.focus();
          const obs = new MutationObserver(() => {});
          obs.observe(root, {childList: true, subtree: true});
          const start = performance.now();
          for (let i = 0; i < 20; i++) WorkspaceHome.render();
          const ms = performance.now() - start;
          const mutations = obs.takeRecords().length; obs.disconnect();
          if (first !== root.querySelector('[data-id]') || document.activeElement !== first || mutations) {
            throw new Error('Unchanged home refresh lost DOM, loaded covers or keyboard focus');
          }
          const wb = WorkbenchStore.createWorkbench('视频素材', false);
          wb.assets = [{id: 'poster-test', type: 'video', videoUrl: '/must-not-load.mp4', coverUrl: '/fixtures/poster.gif'}];
          WorkbenchHome.render();
          const card = document.querySelector('.workspace-workbench-card');
          if (!card.querySelector('img') || card.querySelector('video')) throw new Error('Poster was used as a video source');
          WorkbenchHome.render();
          if (card !== document.querySelector('.workspace-workbench-card')) throw new Error('Workbench home was rebuilt');
          CanvasStore.boards[0].name = '已改名';
          WorkspaceHome.render();
          if (!root.querySelector('[data-id="board-0"]').textContent.includes('已改名')) throw new Error('Home rename lost');
          return {unchangedRefreshes: 20, childMutations: mutations, refreshMs: Math.round(ms)};
        }""")
        last = page.locator('#workspace-board-grid .workspace-board-card[data-id]').last
        last.scroll_into_view_if_needed()
        page.wait_for_function("document.querySelector('#workspace-board-grid .workspace-board-card[data-id]:last-child img')?.classList.contains('is-media-loaded')")
        assert len(set(requests)) > initial_covers

        # Home, canvas and workbench now have separate entry pages. Exercise
        # their real scripts and boot sequence instead of the former single page.
        server_workbench['workbenches'] = page.evaluate('WorkbenchStore.workbenches')
        server_canvas['boards'] = [{'id': 'test-board', 'name': '流畅性验证', 'nodes': [], 'connections': []}]
        page.goto(ORIGIN + '/canvas?id=test-board', wait_until='networkidle')
        page.wait_for_function("initBrowserZoomGuard._bound && CanvasStore.activeBoard()?.id === 'test-board'")
        page.evaluate("""async () => {
          const canvas = document.createElement('canvas'); canvas.width = 320; canvas.height = 180;
          const ctx = canvas.getContext('2d'), stream = canvas.captureStream(20), chunks = [];
          const recorder = new MediaRecorder(stream, {mimeType: 'video/webm'});
          recorder.ondataavailable = e => chunks.push(e.data);
          const stopped = new Promise(resolve => recorder.onstop = resolve);
          recorder.start();
          for (let i = 0; i < 20; i++) {
            ctx.fillStyle = i % 2 ? '#253a50' : '#30485f'; ctx.fillRect(0, 0, 320, 180);
            await new Promise(resolve => setTimeout(resolve, 50));
          }
          recorder.stop(); await stopped; stream.getTracks().forEach(track => track.stop());
          const blob = new Blob(chunks, {type: 'video/webm'});
          window.testVideoUrl = URL.createObjectURL(blob);
          window.testVideoDataUrl = await new Promise(resolve => {
            const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.readAsDataURL(blob);
          });
          const nodes = [
            {id: 'video-node', type: 'ref-video', name: '视频预览', videoUrl: testVideoUrl, x: 100, y: 80, mediaWidth: 320, mediaHeight: 180},
            {id: 'drag-node', type: 'ref-image', name: '拖动测试', imageUrl: '/fixtures/drag.gif', x: 520, y: 80},
          ];
          CanvasStore.boards = [{id: 'test-board', name: '流畅性验证', nodes, connections: []}];
          CanvasStore.activeBoardId = 'test-board'; CanvasStore.viewport = {x: 40, y: 80, scale: 1};
          clearTaskPolling(); CanvasStore.stopSync(); applyViewport();
          renderNodes({immediate: true});
        }""")
        page.wait_for_function("document.querySelector('[data-video-node=video-node] video')?.readyState >= 2")
        drag = page.evaluate("""async () => {
          const node = CanvasStore.activeBoard().nodes.find(n => n.id === 'drag-node');
          const wrap = document.querySelector('.node-wrap[data-id="drag-node"]');
          const rect = getViewportEl().getBoundingClientRect(), v = CanvasStore.viewport;
          const drag = {type: 'nodes', anchorId: node.id, nodeIds: [node.id], offsetX: 0, offsetY: 0,
            startPositions: {[node.id]: {x: node.x, y: node.y}}, wraps: {[node.id]: wrap}, moved: false};
          CanvasStore.drag = drag; CanvasStore.isDragging = true;
          prepareNodeDragCaches(drag);
          const originalUpdate = updateDragConnectionPaths;
          let updates = 0; updateDragConnectionPaths = d => {updates++; originalUpdate(d);};
          for (let i = 1; i <= 120; i++) {
            window.dispatchEvent(new PointerEvent('pointermove', {clientX: rect.left + v.x + (520+i)*v.scale,
              clientY: rect.top + v.y + (80+i)*v.scale, buttons: 1}));
          }
          await new Promise(requestAnimationFrame);
          if (updates !== 1) throw new Error(`Drag updated ${updates} times for one frame`);
          window.dispatchEvent(new PointerEvent('pointerup', {clientX: rect.left + v.x + 660*v.scale,
            clientY: rect.top + v.y + 220*v.scale, button: 0}));
          updateDragConnectionPaths = originalUpdate;
          if (Math.abs(node.x - 660) > 0.01 || Math.abs(node.y - 220) > 0.01 || CanvasStore.drag) {
            throw new Error(`Release position lost: ${node.x}, ${node.y}`);
          }
          return {pointerSamples: 120, frameUpdates: 1, finalPosition: [node.x, node.y]};
        }""")
        preview = page.locator('.node-preview[data-video-node="video-node"]')
        page.evaluate("""() => {
          const preview = document.querySelector('[data-video-node=video-node]');
          preview.dataset.userPaused = '1'; syncNodeVideoPlayback(preview);
        }""")
        preview.hover()
        play = preview.locator('[data-action="node-video-toggle"]')
        play.click()
        page.wait_for_function("!document.querySelector('[data-video-node=video-node] video').paused")
        play.click()
        page.mouse.move(1400, 860)
        preview.hover()
        page.wait_for_timeout(200)
        assert preview.locator('video').evaluate('(v) => v.paused'), 'Hover ignored manual pause'
        scrub = preview.get_by_role('slider', name='视频进度')
        scrub.focus()
        scrub.press('End')
        page.wait_for_timeout(100)
        assert preview.locator('video').evaluate('(v) => v.currentTime > 0.5'), 'Keyboard seek failed'
        assert scrub.get_attribute('aria-valuetext')
        play.click()
        page.wait_for_function("!document.querySelector('[data-video-node=video-node] video').paused")
        page.mouse.move(1150, 600)
        page.mouse.down(button='middle')
        page.evaluate("""() => {
          if (CanvasStore.drag?.type !== 'pan') throw new Error('Middle-button pan did not start');
          const preview = document.querySelector('[data-video-node=video-node]');
          preview.querySelector('video').dispatchEvent(new Event('canplay'));
          if (!preview.querySelector('video').paused) throw new Error('Buffer event restarted video during pan');
        }""")
        page.wait_for_timeout(250)
        assert page.evaluate('_viewportInteracting'), 'Pan settled before pointer release'
        page.mouse.up(button='middle')
        page.wait_for_function("!document.querySelector('[data-video-node=video-node] video').paused")
        page.evaluate("""() => {
          Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
          document.dispatchEvent(new Event('visibilitychange'));
          const preview = document.querySelector('[data-video-node=video-node]');
          preview.querySelector('video').dispatchEvent(new Event('canplay'));
          if (!preview.querySelector('video').paused) throw new Error('Hidden video restarted');
          delete document.visibilityState;
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        page.wait_for_function("!document.querySelector('[data-video-node=video-node] video').paused")
        play.click()
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            scrub.focus()
            page.screenshot(path=str(args.screenshots / 'canvas-video-controls.png'))
            page.set_viewport_size({'width': 800, 'height': 700})
            preview.scroll_into_view_if_needed()
            page.screenshot(path=str(args.screenshots / 'canvas-compact.png'))
        clip_data_url = page.evaluate('testVideoDataUrl')
        wb_id = server_workbench['workbenches'][0]['id']
        page.goto(ORIGIN + '/workbench?id=' + wb_id, wait_until='networkidle')
        page.wait_for_function("typeof bindWorkbenchClipVideo === 'function' && WorkbenchStore.activeWorkbench()")
        workbench = page.evaluate("""async (testVideoUrl) => {
          const host = document.createElement('div');
          host.innerHTML = renderVideoPreviewHtml({id: 'wb-controls-test', type: 'video'}, testVideoUrl);
          document.body.append(host);
          const preview = host.firstElementChild, video = preview.querySelector('video');
          bindWorkbenchClipVideo(preview);
          let loads = 0;
          const originalLoad = video.load.bind(video);
          video.load = () => {loads++; originalLoad();};
          for (let i = 0; i < 20; i++) wbPlayClipVideo(preview, {pinned: true});
          await Promise.race([preview._wbPlayRequest, new Promise((_, reject) => setTimeout(() => reject(new Error('Workbench playback timeout')), 5000))]);
          if (video.paused || loads !== 1) throw new Error(`Workbench playback/duplicate load: ${loads}`);
          wbPauseClipVideo(preview);
          preview.dispatchEvent(new MouseEvent('mouseenter'));
          await new Promise(resolve => setTimeout(resolve, 200));
          if (!video.paused) throw new Error('Workbench hover ignored manual pause');
          const scrub = preview.querySelector('.node-vid-scrub');
          scrub.dispatchEvent(new KeyboardEvent('keydown', {key: 'End', bubbles: true}));
          if (video.currentTime < 0.5 || !scrub.getAttribute('aria-valuetext')) throw new Error('Workbench keyboard seek failed');
          Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
          wbPlayClipVideo(preview, {pinned: true});
          if (!video.paused) throw new Error('Workbench started in a hidden page');
          delete document.visibilityState;
          video.pause(); host.remove();
          return {playRequests: 20, actualLoads: loads, manualPause: true, keyboardSeek: true, hiddenPage: true};
        }""", clip_data_url)
        assert not errors, errors
        print(json.dumps({'home': {**home, 'totalCovers': 120, 'initialRequests': initial_covers}, 'drag': drag,
                          'video': 'manual pause, keyboard seek, active pan, hidden page, resume passed',
                          'workbench': workbench, 'pageErrors': errors}, ensure_ascii=False))
        browser.close()


if __name__ == '__main__':
    main()
