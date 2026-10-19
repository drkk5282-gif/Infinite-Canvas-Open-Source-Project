"""Browser regression for canvas/workbench video lightbox recovery.

All API calls use fictional in-memory data. A real local MP4 is only read as a
media fixture; the first request is deliberately aborted to exercise retry.
"""

import os
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:10027"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".playwright-browsers")


def main():
    fixture = min((ROOT / "data" / "users" / "admin").rglob("*.mp4"), key=lambda path: path.stat().st_size)
    media = fixture.read_bytes()
    media_requests = {"count": 0}
    document_requests = {"count": 0}
    errors = []

    def route_request(route):
        request = route.request
        parsed = urlparse(request.url)
        if parsed.path == "/" and request.resource_type == "document":
            document_requests["count"] += 1
        if parsed.path == "/test-lightbox.mp4":
            media_requests["count"] += 1
            if media_requests["count"] == 1:
                route.abort("connectionreset")
            else:
                range_header = request.headers.get("range", "")
                start = 0
                end = len(media) - 1
                status = 200
                if range_header.startswith("bytes="):
                    bounds = range_header[6:].split("-", 1)
                    start = int(bounds[0] or 0)
                    if bounds[1]:
                        end = min(int(bounds[1]), end)
                    status = 206
                body = media[start : end + 1]
                headers = {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(body)),
                    "Accept-Ranges": "bytes",
                    "Cache-Control": "no-store",
                }
                if status == 206:
                    headers["Content-Range"] = f"bytes {start}-{end}/{len(media)}"
                route.fulfill(
                    status=status,
                    body=body,
                    headers=headers,
                )
            return
        if not request.url.startswith(ORIGIN + "/"):
            route.abort()
            return
        if not parsed.path.startswith("/api/"):
            relative = 'index.html' if parsed.path == '/' else parsed.path.removeprefix('/static/')
            target = (ROOT / 'frontend' / relative).resolve()
            if target.is_relative_to(ROOT / 'frontend') and target.is_file():
                route.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream')
            else:
                route.fulfill(status=404, body='')
            return
        if parsed.path == "/api/auth/me":
            data = {"user": {"id": "video-test", "username": "video-test", "is_admin": False}}
        elif parsed.path == "/api/app-config":
            data = {"disable_browser_channels": True, "disable_doubao": True, "points_enabled": False}
        elif parsed.path == "/api/canvas":
            data = {"boards": [], "updatedAt": "initial", "storageEpoch": "video-test"}
        elif parsed.path == "/api/workbench":
            data = {"workbenches": [], "updatedAt": "initial", "storageEpoch": "video-test"}
        elif parsed.path in ("/api/tasks", "/api/accounts"):
            data = {"tasks": []} if parsed.path == "/api/tasks" else []
        else:
            data = {}
        route.fulfill(json=data)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        context.route("**/*", route_request)
        context.add_init_script("localStorage.setItem('canvas_session_token', 'fictional-video-test');")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(ORIGIN, wait_until="networkidle")
        page.wait_for_function(
            "typeof VideoLightbox !== 'undefined' && CanvasStore._persistence?.ready && WorkbenchStore._persistence?.ready"
        )

        opened = page.evaluate("""() => {
          CanvasStore.stopSync?.();
          const node = {id: 'canvas-video', type: 'video', status: 'completed', videoUrl: '/test-lightbox.mp4', x: 0, y: 0};
          CanvasStore.boards = [{id: 'video-board', name: 'Video board', nodes: [node], connections: []}];
          CanvasStore.activeBoardId = 'video-board';
          VideoLightbox.open(node.id);
          return {open: VideoLightbox.isOpen(), node: VideoLightbox.currentNode()?.id,
            attempt: VideoLightbox._loadAttempt, src: document.querySelector('#vl-video')?.src};
        }""")
        assert opened["open"] and opened["node"] == "canvas-video", opened
        try:
            page.wait_for_function("document.querySelector('#vl-video').readyState >= 1", timeout=15_000)
        except Exception:
            print({
                "mediaRequests": media_requests["count"],
                "documentRequests": document_requests["count"],
                "url": page.url,
                "lightbox": page.evaluate("""() => {
                  const video = document.querySelector('#vl-video');
                  return {src: video?.currentSrc || video?.src, readyState: video?.readyState,
                    networkState: video?.networkState, error: video?.error?.code || 0,
                    open: VideoLightbox.isOpen(), attempt: VideoLightbox._loadAttempt,
                    ready: VideoLightbox._loadReady, node: VideoLightbox.currentNode()?.id};
                }"""),
                "pageErrors": errors,
            })
            raise
        assert media_requests["count"] >= 2
        assert page.evaluate("VideoLightbox.currentNode()?.id") == "canvas-video"
        page.wait_for_function("VideoLightbox._loadReady && !document.querySelector('#vl-video').paused")
        page.evaluate("""() => {
          const video = VideoLightbox.video();
          window.__modalLoads = 0;
          const nativeLoad = video.load.bind(video);
          video.load = () => { window.__modalLoads++; nativeLoad(); };
          VideoLightbox.close();
          VideoLightbox.open('canvas-video');
        }""")
        page.wait_for_function("VideoLightbox._loadReady")
        assert page.evaluate("window.__modalLoads") == 0, 'Quick reopen discarded buffered video'
        page.evaluate("VideoLightbox.close()")
        page.wait_for_function("!VideoLightbox.isOpen()")
        page.evaluate("""() => {
          for (let i = 0; i < 20; i++) warmVideoUrl('/test-lightbox.mp4?warm=1');
        }""")
        assert page.evaluate("window.__modalLoads") == 1, page.evaluate("({loads: window.__modalLoads, state: VideoLightbox.video().networkState})")
        page.evaluate("""() => {
          CanvasStore.boards[0].nodes[0].videoUrl = '/test-lightbox.mp4?warm=1';
          VideoLightbox.open('canvas-video');
        }""")
        page.wait_for_function("VideoLightbox._loadReady")
        assert page.evaluate("window.__modalLoads") == 1, 'Opening restarted the warmed player'
        page.evaluate("""async () => {
          await VideoLightbox.nudge(0.1);
          const originalCapture = FrameCapture.run;
          let captureId = null;
          FrameCapture.run = id => {captureId = id;};
          VideoLightbox.captureCurrentFrame();
          FrameCapture.run = originalCapture;
          if (captureId !== 'canvas-video') throw new Error('Capture target changed');
          const node = {...CanvasStore.boards[0].nodes[0], id: 'next-video', videoUrl: '/test-lightbox.mp4?next=1'};
          CanvasStore.boards[0].nodes.push(node);
          VideoLightbox.setNodes(CanvasStore.boards[0].nodes, 'canvas-video');
          VideoLightbox.next();
        }""")
        page.wait_for_function("VideoLightbox._loadReady && VideoLightbox.currentNode()?.id === 'next-video'")
        page.evaluate("VideoLightbox.prev()")
        page.wait_for_function("VideoLightbox._loadReady && VideoLightbox.currentNode()?.id === 'canvas-video'")
        page.evaluate("VideoLightbox.close()")

        page.evaluate("""() => {
          const wb = WorkbenchStore.createWorkbench('Video workbench', false);
          WorkbenchStore.workbenches = [wb];
          WorkbenchStore.activeWorkbenchId = wb.id;
          WorkbenchStore.activeEpisodeId = wb.episodes[0].id;
          const clip = wb.episodes[0].clips[0];
          clip.videoUrl = '/test-lightbox.mp4';
          clip.status = 'completed';
          window.__wbVideoNodeId = 'wbclip-' + clip.id;
          VideoLightbox.open(window.__wbVideoNodeId);
        }""")
        page.wait_for_function("document.querySelector('#vl-video').readyState >= 1", timeout=15_000)
        assert page.evaluate("VideoLightbox.currentNode()?.id === window.__wbVideoNodeId")
        assert page.evaluate("!document.querySelector('#vl-locate').classList.contains('hidden')") is False
        page.evaluate("VideoLightbox.close()")

        thumb = page.evaluate("""() => {
          const wb = WorkbenchStore.activeWorkbench();
          const ep = WorkbenchStore.activeEpisode(wb);
          const asset = WorkbenchStore.createAsset('image');
          asset.name = '即时缩略图';
          asset.status = 'completed';
          asset.imageUrl = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==';
          ep.assets = [asset];
          const editor = document.createElement('div');
          editor.className = 'prompt-editor';
          editor.contentEditable = 'true';
          editor.dataset.node = window.__wbVideoNodeId;
          document.body.appendChild(editor);
          editor.focus();
          const inserted = WorkbenchClipMention.insertFromAsset(asset.id, editor);
          const img = editor.querySelector('img.media-thumb-lazy');
          return {inserted, connected: img?.isConnected, hydrated: img?.dataset.thumbHydrated,
            src: img?.getAttribute('src') || '', dataSrc: img?.dataset.src || ''};
        }""")
        assert thumb["inserted"] and thumb["connected"], thumb
        assert thumb["hydrated"] == "1" and thumb["src"], thumb
        page.wait_for_function("document.body.lastElementChild.querySelector('img')?.complete")
        assert not errors, errors
        print({"passed": ["canvas retry after aborted media", "quick reopen without load", "20 warmups -> 1 load; open -> 0 extra loads", "autoplay, seek, capture target, previous/next", "workbench async node lifetime", "new mention thumbnail loads before refresh"], "mediaRequests": media_requests["count"]})
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
