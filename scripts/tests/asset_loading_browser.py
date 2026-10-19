"""Loading regression using local UI files and fictional, intercepted APIs only."""
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse, unquote

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / '.playwright-browsers')
ORIGIN = 'http://loading.test'


def main():
    def route_request(route):
        parsed = urlparse(route.request.url)
        if parsed.netloc != 'loading.test':
            route.abort()
            return
        path = unquote(parsed.path)
        if path.startswith('/api/'):
            data = {}
            if path == '/api/auth/me':
                data = {'user': {'id': 'loading-test', 'username': 'loading-test', 'is_admin': False}}
            elif path == '/api/app-config':
                data = {'disable_browser_channels': True, 'disable_doubao': True, 'points_enabled': False}
            elif path == '/api/canvas':
                data = {'boards': [], 'updatedAt': 'initial', 'storageEpoch': 'loading-test'}
            elif path == '/api/workbench':
                data = {'workbenches': [], 'updatedAt': 'initial', 'storageEpoch': 'loading-test'}
            elif path == '/api/accounts':
                data = []
            elif path == '/api/tasks':
                data = {'tasks': []}
            route.fulfill(json=data)
            return
        relative = 'index.html' if path == '/' else path.removeprefix('/static/')
        target = (ROOT / 'frontend' / relative).resolve()
        if target.is_relative_to(ROOT / 'frontend') and target.is_file():
            route.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream')
        else:
            route.fulfill(status=404, body='')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.route('**/*', route_request)
        context.add_init_script("localStorage.setItem('canvas_session_token', 'fictional-loading-test');")
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(ORIGIN, wait_until='networkidle')
        page.wait_for_function('CanvasStore._persistence?.ready && WorkbenchStore._persistence?.ready')
        result = page.evaluate("""() => {
          clearTaskPolling(); CanvasStore.stopSync();
          const wb = WorkbenchStore.createWorkbench('Loading regression', false);
          WorkbenchStore.activeWorkbenchId = wb.id;
          WorkbenchStore.activeEpisodeId = null;
          const pixel = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
          wb.assetTags = Array.from({length: 12}, (_, i) => ({id: `tag-${i}`, name: `Tag ${i}`, order: i}));
          wb.assets = Array.from({length: 240}, (_, i) => ({
            ...WorkbenchStore.createAsset('image'), id: `asset-${i}`, name: `Asset ${i}`,
            thumbUrl: pixel, imageUrl: pixel, tagId: `tag-${i % 12}`
          }));
          const list = document.getElementById('workbench-assets-list');
          WorkbenchView.renderAssets(wb);
          const first = list.querySelector('.workbench-asset-item');
          const image = first.querySelector('img');
          const observer = new MutationObserver(() => {});
          observer.observe(list, {childList: true, subtree: true});
          const start = performance.now();
          for (let i = 0; i < 10; i++) WorkbenchView.renderAssets(wb);
          const cachedMs = performance.now() - start;
          const mutations = observer.takeRecords().length;
          observer.disconnect();
          if (mutations || first !== list.querySelector('.workbench-asset-item') || image !== first.querySelector('img')) {
            throw new Error('Unchanged refresh recreated assets/media');
          }
          let previews = 0;
          const originalOpen = WorkbenchAssetPreview.open;
          WorkbenchAssetPreview.open = () => previews++;
          first.dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));
          WorkbenchAssetPreview.open = originalOpen;
          if (previews !== 1) throw new Error('Preview handlers missing or duplicated');
          first.dispatchEvent(new MouseEvent('click', {bubbles: true, ctrlKey: true}));
          if (!WorkbenchAssetPick.has(first.dataset.id)) throw new Error('Ctrl selection failed');
          WorkbenchView.renderAssets(wb);
          if (!list.querySelector('.is-picked')) throw new Error('Selection was lost');
          const picked = list.querySelector('.is-picked');
          const transfer = new DataTransfer();
          picked.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: transfer}));
          if (transfer.getData('application/x-wb-asset-id') !== picked.dataset.id) throw new Error('Drag payload lost');
          picked.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: transfer}));
          wb.assets[0].name = 'Renamed asset';
          wb.assets[0].tagId = 'tag-1';
          WorkbenchView.renderAssets(wb);
          const renamed = list.querySelector('.workbench-asset-item[data-id="asset-0"]');
          if (!renamed.textContent.includes('Renamed asset') || renamed.closest('[data-asset-drop-tag]').dataset.assetDropTag !== 'tag-1') {
            throw new Error('Rename/tag change was skipped');
          }
          wb.assets[0].status = 'generating';
          WorkbenchView.renderAssets(wb);
          if (!list.querySelector('[data-id="asset-0"].is-busy')) throw new Error('Task status was skipped');
          wb.assets.splice(0, 1);
          WorkbenchView.renderAssets(wb);
          if (list.querySelector('.workbench-asset-item[data-id="asset-0"]')) throw new Error('Deleted asset remains');
          const rebuildStart = performance.now();
          for (let i = 0; i < 10; i++) {
            delete list._wbAssetsHtml;
            WorkbenchView.renderAssets(wb);
          }
          return {assets: 240, refreshes: 10, unchangedChildMutations: mutations,
            cachedMs: Math.round(cachedMs), forcedRebuildMs: Math.round(performance.now() - rebuildStart),
            checks: 'preview, selection, drag payload, rename, tags, task status, deletion'};
        }""")
        video_result = page.evaluate("""async () => {
          const canvas = document.createElement('canvas');
          canvas.width = 64; canvas.height = 64;
          const ctx = canvas.getContext('2d');
          const stream = canvas.captureStream(10);
          const recorder = new MediaRecorder(stream, {mimeType: 'video/webm'});
          const chunks = [];
          recorder.ondataavailable = e => chunks.push(e.data);
          const stopped = new Promise(resolve => recorder.onstop = resolve);
          recorder.start();
          ctx.fillStyle = '#22aaff'; ctx.fillRect(0, 0, 64, 64);
          await new Promise(resolve => setTimeout(resolve, 250));
          recorder.stop(); await stopped;
          stream.getTracks().forEach(track => track.stop());
          const url = URL.createObjectURL(new Blob(chunks, {type: 'video/webm'}));
          const preview = document.createElement('div');
          preview.className = 'node-preview has-video is-frame-preview';
          preview.dataset.wbPreview = '1';
          const video = document.createElement('video');
          video.className = 'node-preview-vid-el';
          video.dataset.src = url;
          video.muted = true;
          preview.append(video); document.body.append(preview);
          let loads = 0;
          const nativeLoad = video.load.bind(video);
          video.load = () => { loads++; nativeLoad(); };
          const ready = new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(new Error('Video never became playable')), 5000);
            video.addEventListener('loadeddata', () => {clearTimeout(timer); resolve();}, {once: true});
          });
          for (let i = 0; i < 20; i++) {
            ensureLazyVideoEl(video);
            wbForceClipVideoSrc(preview);
          }
          await ready;
          if (loads !== 1) throw new Error(`Video restarted ${loads} times`);
          video.loop = true; video.currentTime = 0;
          await video.play(); video.pause();
          if (!video.videoWidth) throw new Error('Video frame was not decoded');
          preview.remove(); URL.revokeObjectURL(url);
          return {hydrateCalls: 20, actualLoads: loads, playback: 'passed'};
        }""")
        assert not errors, errors
        print(json.dumps(result, ensure_ascii=False))
        print(json.dumps(video_result))
        browser.close()


if __name__ == '__main__':
    main()
