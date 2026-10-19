"""Browser regression against the local UI, with every API request intercepted.

Uses a fresh browser context and fictional accounts/data; never writes real data.
Run with the project's Python environment while the UI server is running.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".playwright-browsers")
ORIGIN = "http://127.0.0.1:10027"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--canvas-source', type=Path, help='Compare a saved canvas.js without replacing live files')
    args = parser.parse_args()
    state = {
        "canvas": {"updatedAt": "initial", "storageEpoch": "epoch-1", "boards": []},
        "workbench": {"updatedAt": "initial", "storageEpoch": "epoch-1", "workbenches": []},
    }
    fail_writes = {"value": False}
    fail_reads = {"value": False}
    legacy_backend = {"value": True}
    errors = []
    writes = []
    recovery_tasks = []
    cancelled_tasks = []

    def route_request(route):
        request = route.request
        parsed = urlparse(request.url)
        if not request.url.startswith(ORIGIN + "/"):
            route.abort()
            return
        url = parsed.path
        if url == '/static/js/canvas.js' and args.canvas_source:
            route.fulfill(body=args.canvas_source.read_bytes(), content_type='application/javascript')
            return
        if not url.startswith("/api/"):
            route.continue_()
            return
        if url in ("/api/canvas", "/api/workbench"):
            kind = url.rsplit("/", 1)[-1]
            if request.method == "GET" and fail_reads["value"]:
                route.fulfill(status=503, json={"detail": "simulated offline load"})
                return
            if request.method == "PATCH" and legacy_backend["value"]:
                route.fulfill(status=405, json={"detail": "legacy backend"})
                return
            if request.method in ("PUT", "PATCH"):
                writes.append(kind)
                if fail_writes["value"]:
                    route.fulfill(status=503, json={"detail": "simulated offline"})
                    return
                if request.method == "PATCH":
                    field = "boards" if kind == "canvas" else "workbenches"
                    rows = {row["id"]: row for row in state[kind][field]}
                    rows.update({row["id"]: row for row in request.post_data_json[field]})
                    state[kind] = {field: list(rows.values()), "updatedAt": f"saved-{len(writes)}", "storageEpoch": state[kind]["storageEpoch"]}
                    route.fulfill(json={"updatedAt": state[kind]["updatedAt"], "storageEpoch": state[kind]["storageEpoch"], "ok": True,
                                        "savedIds": [row["id"] for row in request.post_data_json[field]]})
                    return
                state[kind] = {**request.post_data_json, "updatedAt": f"saved-{len(writes)}", "storageEpoch": state[kind]["storageEpoch"]}
            data = state[kind]
        elif url == "/api/auth/me":
            data = {"user": {"id": "autosave-test", "username": "autosave-test", "is_admin": False}}
        elif url == "/api/app-config":
            data = {"disable_browser_channels": True, "disable_doubao": True, "points_enabled": False}
        elif url == "/api/tasks":
            data = {"tasks": recovery_tasks}
        elif url.startswith('/api/tasks/') and url.endswith('/cancel'):
            cancelled_tasks.append(url)
            data = {"ok": True}
        elif url == "/api/accounts":
            data = []
        else:
            data = {}
        route.fulfill(json=data)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        context.route("**/*", route_request)
        context.add_init_script("localStorage.setItem('canvas_session_token', 'fictional-autosave-test');")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(ORIGIN, wait_until="networkidle")
        page.wait_for_function("typeof CanvasStore !== 'undefined' && WorkbenchStore._persistence?.ready")
        # Storage becomes ready before the workspace boot transition finishes.
        # Wait for navigation bindings so its finally block cannot hide our view.
        page.wait_for_function("initBrowserZoomGuard._bound && !document.documentElement.classList.contains('is-workspace-booting')")

        page.evaluate("""async () => {
          const wb = WorkbenchStore.createWorkbench('Autosave test');
          WorkbenchStore.activeWorkbenchId = wb.id;
          WorkbenchStore.activeEpisodeId = wb.episodes[0].id;
          await WorkbenchStore.saveNow();
          await enterWorkbench(wb.id, wb.episodes[0].id, {skipReload: true, noLoader: true});
        }""")
        assert page.evaluate("WorkbenchStore._supportsPartialSave === false")
        legacy_backend["value"] = False
        page.locator("#btn-add-clip").click()
        page.wait_for_function("WorkbenchStore.activeEpisode()?.clips.length === 2")
        page.wait_for_function("!WorkbenchStore._persistence.pending")
        assert len(state["workbench"]["workbenches"][0]["episodes"][0]["clips"]) == 2

        # A failed PUT followed by an immediate reload must retain the new clip.
        fail_writes["value"] = True
        page.locator("#btn-add-clip").click()
        page.wait_for_function("WorkbenchStore.activeEpisode()?.clips.length === 3")
        page.reload(wait_until="networkidle")
        page.wait_for_function("WorkbenchStore.workbenches[0]?.episodes[0]?.clips.length === 3")
        # Refresh again while document reads and writes are both unavailable.
        # Recovery must retain its pending state and validate the server on return.
        fail_reads["value"] = True
        page.reload(wait_until="networkidle")
        page.wait_for_function("WorkbenchStore._persistence?.ready && WorkbenchStore.workbenches[0]?.episodes[0]?.clips.length === 3")
        assert page.evaluate("WorkbenchStore._persistence.pending")
        page.evaluate("""() => {
          const wb = WorkbenchStore.workbenches[0];
          wb.episodes[0].clips[2].prompt = 'edited after offline refresh';
          WorkbenchStore.save(wb.id);
        }""")
        fail_reads["value"] = False
        fail_writes["value"] = False
        page.evaluate("window.dispatchEvent(new Event('online'))")
        page.wait_for_function("!WorkbenchStore._persistence.pending")
        assert len(state["workbench"]["workbenches"][0]["episodes"][0]["clips"]) == 3
        assert state["workbench"]["workbenches"][0]["episodes"][0]["clips"][2]["prompt"] == 'edited after offline refresh'

        # Actual browser request: UTF-8 payload > 64 KiB must save normally.
        page.evaluate("""async () => {
          WorkbenchStore.workbenches[0].episodes[0].clips[0].prompt = '中'.repeat(30000);
          await WorkbenchStore.saveNow();
        }""")
        assert len(state["workbench"]["workbenches"][0]["episodes"][0]["clips"][0]["prompt"]) == 30000

        # A blank prompt accidentally persisted by an old recovery cache can
        # be rebuilt from the generation task on the next load.
        recover_clip = state["workbench"]["workbenches"][0]["episodes"][0]["clips"][0]
        recover_clip["prompt"] = ""
        recover_clip["taskId"] = "recover-task"
        recover_clip["genHistory"] = []
        recovery_tasks[:] = [{
            "id": "recover-task", "workbench_id": state["workbench"]["workbenches"][0]["id"],
            "node_id": recover_clip["id"], "prompt": "Recovered task prompt",
        }]
        page.reload(wait_until="networkidle")
        page.wait_for_function("WorkbenchStore.workbenches[0]?.episodes[0]?.clips[0]?.prompt === 'Recovered task prompt'")
        page.wait_for_function("!WorkbenchStore._persistence.pending")
        assert state["workbench"]["workbenches"][0]["episodes"][0]["clips"][0]["prompt"] == "Recovered task prompt"

        page.evaluate("""async () => {
          const board = CanvasStore.createBoard('Viewport test');
          CanvasStore.activeBoardId = board.id;
          CanvasStore.viewport = {x: 222, y: -91, scale: 1.3};
          debouncedSaveViewport();
          await CanvasStore.flushSave();
        }""")
        fail_writes["value"] = True
        page.evaluate("""() => {
          CanvasStore.viewport = {x: 777, y: -345, scale: 0.8};
          debouncedSaveViewport();
        }""")
        page.reload(wait_until="networkidle")
        page.wait_for_function("CanvasStore.boards[0]?.viewport?.x === 777")
        assert page.evaluate("CanvasStore.boards[0].viewport.scale") == 0.8
        fail_writes["value"] = False
        page.evaluate("window.dispatchEvent(new Event('online'))")
        page.wait_for_function("!CanvasStore._persistence.pending")
        assert state["canvas"]["boards"][0]["viewport"]["x"] == 777

        # The provider can already be running when a refresh lands before the
        # returned task id was persisted. Rebind by the durable board/node ids
        # and immediately save the recovered generating state.
        active_binding = page.evaluate("""async () => {
          const board = CanvasStore.boards[0];
          const node = CanvasStore.addNode('video', 160, 120);
          CanvasStore.updateNode(node.id, {
            status: 'idle', taskId: null, prompt: 'refresh recovery',
            channel: 'bendi', model: 'seedance-2.5', ratio: '16:9'
          });
          await CanvasStore.flushSave();
          return {boardId: board.id, nodeId: node.id};
        }""")
        recovery_tasks[:] = [{
            "id": "refresh-active-task", "kind": "video", "status": "generating",
            "board_id": active_binding["boardId"], "node_id": active_binding["nodeId"],
            "account_id": "bendi", "account_name": "BenDi",
            "prompt": "refresh recovery", "ratio": "16:9",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }]
        page.reload(wait_until="networkidle")
        node_id = json.dumps(active_binding["nodeId"])
        page.wait_for_function(
            f"""() => {{
              const node = CanvasStore.boards[0]?.nodes.find((item) => item.id === {node_id});
              return node?.taskId === 'refresh-active-task' && node?.status === 'generating';
            }}"""
        )
        page.wait_for_function("!CanvasStore._persistence.pending")
        saved_node = next(
            node for node in state["canvas"]["boards"][0]["nodes"]
            if node["id"] == active_binding["nodeId"]
        )
        assert saved_node["taskId"] == "refresh-active-task"
        assert saved_node["status"] == "generating"

        # An explicit browser-channel stop survives refresh even when a stale
        # task list still reports generation. All cancellation requests are mocked.
        page.evaluate("""async (id) => {
          CanvasStore.updateNode(id, {channel: 'doubao', model: defaultModelForChannel('doubao', 'video'), promptSent: false});
          const confirm = AppDialog.confirm;
          try {
            AppDialog.confirm = async () => true;
            await pauseGeneration(id);
          } finally { AppDialog.confirm = confirm; }
          const node = findNodeAnywhere(id);
          if (node.status !== 'idle' || !node.generationStopped) throw new Error('Explicit stop was not recorded');
          await CanvasStore.flushSave();
        }""", active_binding["nodeId"])
        assert cancelled_tasks == ['/api/tasks/refresh-active-task/cancel']
        page.reload(wait_until="networkidle")
        page.wait_for_function("CanvasStore._persistence?.ready && initBrowserZoomGuard._bound")
        assert page.evaluate("""task => {
          const changed = syncNodeFromTask(task);
          const node = findNodeAnywhere(task.node_id);
          return !changed && node.status === 'idle' && node.taskId === null && node.generationStopped;
        }""", recovery_tasks[0])

        state["canvas"]["boards"][0]["name"] = "Remote rename"
        state["canvas"]["updatedAt"] = "remote-update"
        page.evaluate("""async () => {
          window.__boardRenders = 0;
          const original = renderBoardDropdown;
          renderBoardDropdown = (...args) => { window.__boardRenders++; return original(...args); };
          document.activeElement?.blur();
          await CanvasStore.syncFromServer({force: true});
        }""")
        assert page.evaluate("CanvasStore.boards[0].name") == "Remote rename"
        assert page.evaluate("window.__boardRenders") > 0

        # Recreating the server data directory changes its epoch. Pending IDB,
        # emergency journals and legacy localStorage must all stay deleted.
        state["canvas"] = {"updatedAt": None, "storageEpoch": "epoch-2", "boards": []}
        state["workbench"] = {"updatedAt": None, "storageEpoch": "epoch-2", "workbenches": []}
        page.reload(wait_until="networkidle")
        page.wait_for_function("CanvasStore._persistence?.ready && WorkbenchStore._persistence?.ready")
        assert page.evaluate("CanvasStore.boards.length") == 0
        assert page.evaluate("WorkbenchStore.workbenches.length") == 0
        assert state["canvas"]["boards"] == []
        assert state["workbench"]["workbenches"] == []

        # Large synthetic workspace: keep operations representative without
        # touching account data or starting generation. Measure hot-path time.
        performance = page.evaluate("""async () => {
          CanvasStore.stopSync();
          const boards = Array.from({length: 25}, (_, b) => ({
            id: 'perf-b-' + b, name: 'Board ' + b, connections: [],
            nodes: Array.from({length: 80}, (_, n) => ({
              id: 'perf-n-' + b + '-' + n, type: 'text', prompt: '测试内容'.repeat(300),
              x: n * 20, y: n * 10, resultText: '', status: 'idle'
            }))
          }));
          CanvasStore.boards = boards;
          CanvasStore.activeBoardId = boards[0].id;
          await CanvasStore.flushSave();
          const bytes = new Blob([JSON.stringify(boards)]).size;
          let syncCacheWrites = 0;
          const nativeSet = Storage.prototype.setItem;
          Storage.prototype.setItem = function(...args) { syncCacheWrites++; return nativeSet.apply(this, args); };
          const start = performance.now();
          for (let i = 0; i < 500; i++) {
            CanvasStore.viewport.x = i;
            CanvasStore.save();
          }
          const inputMs = performance.now() - start;
          Storage.prototype.setItem = nativeSet;
          let maxFrameGap = 0, lastFrame = performance.now(), measuring = true;
          const frame = (now) => { maxFrameGap = Math.max(maxFrameGap, now - lastFrame); lastFrame = now; if (measuring) requestAnimationFrame(frame); };
          requestAnimationFrame(frame);
          const saveStart = performance.now();
          await CanvasStore.flushSave();
          const saveMs = performance.now() - saveStart;
          measuring = false;
          const loadStart = performance.now();
          await Promise.all([CanvasStore.loadFromServer(), CanvasStore.loadFromServer(), CanvasStore.loadFromServer()]);
          const loadMs = performance.now() - loadStart;
          const reenterStart = performance.now();
          await CanvasStore.loadFromServer();
          return { bytes, boards: boards.length, nodes: 2000, operations: 500,
            inputMs, syncCacheWrites, saveMs, maxFrameGap, loadMs,
            reenterMs: performance.now() - reenterStart };
        }""")
        assert performance["syncCacheWrites"] == 0, performance
        assert performance["inputMs"] < 100, performance
        assert not errors, errors
        print(json.dumps({"passed": ["add clip", "legacy server fallback", "immediate refresh after failed save", "repeated offline refresh and editing", "online retry", "large UTF-8 save", "blank prompt task-history recovery", "viewport refresh recovery", "active generation task rebind after refresh", "manual stop survives refresh and stale polling", "remote refresh rendering", "storage reset invalidates old cache"], "performance": performance, "pageErrors": errors, "mockedWrites": len(writes)}))
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
