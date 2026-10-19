"""Large-board regression/benchmark with fictional APIs; never touches user data."""
import argparse
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from canvas_smoothness_browser import ORIGIN, PIXEL, ROOT
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--benchmark-only', action='store_true')
    parser.add_argument('--canvas-source', type=Path, help='Read a saved canvas.js for a comparable baseline')
    args = parser.parse_args()
    errors = []
    server_canvas = {'boards': [], 'updatedAt': 'initial', 'storageEpoch': 'large-board-test'}
    writes = []

    def route_request(route):
        parsed = urlparse(route.request.url)
        if parsed.netloc != 'smoothness.test':
            route.abort()
            return
        path = unquote(parsed.path)
        if path.startswith('/fixtures/'):
            route.fulfill(body=PIXEL, content_type='image/gif')
            return
        if path.startswith('/api/'):
            data = {}
            if path == '/api/auth/me':
                data = {'user': {'id': 'large-board-test', 'username': 'large-board-test'}}
            elif path == '/api/app-config':
                data = {'disable_browser_channels': True, 'disable_doubao': True, 'points_enabled': False}
            elif path == '/api/canvas':
                if route.request.method in ('PUT', 'PATCH'):
                    payload = route.request.post_data_json
                    writes.append(payload)
                    rows = {b['id']: b for b in server_canvas['boards']} if route.request.method == 'PATCH' else {}
                    rows.update({b['id']: b for b in payload.get('boards', [])})
                    server_canvas.update(boards=list(rows.values()), updatedAt=f'saved-{len(writes)}')
                data = server_canvas
            elif path == '/api/workbench':
                data = {'workbenches': [], 'updatedAt': 'initial', 'storageEpoch': 'large-board-test'}
            elif path == '/api/accounts':
                data = []
            elif path == '/api/tasks':
                data = {'tasks': []}
            route.fulfill(json=data)
            return
        relative = 'index.html' if path == '/' else path.removeprefix('/static/')
        target = (ROOT / 'frontend' / relative).resolve()
        if relative == 'js/canvas.js' and args.canvas_source:
            route.fulfill(body=args.canvas_source.read_bytes(), content_type='application/javascript')
            return
        if target.is_relative_to(ROOT / 'frontend') and target.is_file():
            route.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream')
        else:
            route.fulfill(status=404, body='')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 900})
        context.route('**/*', route_request)
        context.add_init_script("localStorage.setItem('canvas_session_token', 'fictional-large-board-test');")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(ORIGIN, wait_until='networkidle')
        page.wait_for_function('CanvasStore._persistence?.ready && WorkbenchStore._persistence?.ready')
        page.wait_for_function("initBrowserZoomGuard._bound && !document.documentElement.classList.contains('is-workspace-booting')")
        page.evaluate("""async () => {
          clearTaskPolling(); CanvasStore.stopSync();
          const nodes = Array.from({length: 600}, (_, i) => ({id: `large-${i}`, type: 'image',
            customName: `节点 ${i}`, prompt: `描述 ${i}`, status: 'idle', ratio: '16:9',
            x: (i % 30) * 360, y: Math.floor(i / 30) * 280}));
          const connections = nodes.slice(1).map((n, i) => ({id: `wire-${i}`, from: nodes[i].id, to: n.id}));
          CanvasStore.boards = [{id: 'large-board', name: '大画布测试', nodes, connections}];
          CanvasStore.activeBoardId = 'large-board';
          enterCanvasMode('large-board'); CanvasStore.stopSync();
          CanvasStore.viewport = {x: 60, y: 90, scale: 1};
          applyViewport(); renderNodes({immediate: true});
          CanvasStore.save(); await CanvasStore.flushSave();
        }""")
        page.wait_for_timeout(600)
        assert page.locator('#viewport').is_visible(), 'Benchmark must run on a visible canvas after boot'
        metrics = page.evaluate("""() => {
          const measure = (fn, iterations = 5) => {
            const samples = [];
            for (let i = 0; i < iterations; i++) {
              const start = performance.now(); fn(); samples.push(performance.now() - start);
            }
            samples.sort((a,b) => a-b);
            return Math.round(samples[Math.floor(samples.length / 2)] * 100) / 100;
          };
          return {nodes: 600, connections: 599,
            unchangedRefreshMs: measure(() => renderNodes({immediate: true})),
            viewportCullMs: measure(() => syncNodeViewportCull({skipHydrate: true})),
            connectionsMs: measure(() => renderStaticConnections()),
            zoomChromeMs: measure(() => {
              CanvasStore.viewport.scale = CanvasStore.viewport.scale === 2 ? 3 : 2;
              applyChromeCounterScale();
            })};
        }""")
        if not args.benchmark_only:
            check_interactions(page)
            # Exercise real persistence adoption with a changed remote board.
            page.evaluate("""async () => {
              document.activeElement?.blur(); clearSelection();
              CanvasStore.save(); await CanvasStore.flushSave();
            }""")
            page.wait_for_timeout(450)
            remote = server_canvas['boards'][0]
            remote['nodes'][2]['customName'] = '远端改名已同步'
            remote['nodes'][2]['status'] = 'failed'
            remote['nodes'][2]['error'] = '模拟失败'
            remote['nodes'][2]['x'] += 37
            server_canvas['updatedAt'] = 'remote-change'
            page.evaluate('CanvasStore.syncFromServer({force: true})')
            page.wait_for_function("CanvasStore.activeBoard().nodes[2].customName === '远端改名已同步'")
            page.wait_for_function("document.querySelector('.node-wrap[data-id=large-2]').textContent.includes('远端改名已同步')")
            page.evaluate("""() => {
              const node = CanvasStore.activeBoard().nodes[2];
              const wrap = document.querySelector('.node-wrap[data-id=large-2]');
              if (parseFloat(wrap.style.left) !== node.x || node.status !== 'failed') throw new Error('Remote update lost');
              window.beforeReload = {prompt: CanvasStore.activeBoard().nodes[0].prompt, x: node.x};
            }""")
            saved = page.evaluate('beforeReload')
            page.reload(wait_until='networkidle')
            page.wait_for_function("CanvasStore._persistence?.ready && CanvasStore.boards.some(b => b.id === 'large-board')")
            restored = page.evaluate("""() => {
              const board = CanvasStore.boards.find(b => b.id === 'large-board');
              return {prompt: board.nodes[0].prompt, x: board.nodes[2].x};
            }""")
            assert restored == saved, (restored, saved)
        assert not errors, errors
        result = {'benchmark': metrics, 'interactions': 'skipped' if args.benchmark_only else 'passed', 'pageErrors': errors}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
        browser.close()


def check_interactions(page):
    page.evaluate("""() => {
      CanvasStore.viewport = {x: 60, y: 90, scale: 1}; applyViewport();
      renderNodes({immediate: true}); renderStaticConnections();
      const board = CanvasStore.activeBoard();
      const incoming = buildIncomingConnectionKeys(board);
      for (const node of board.nodes) {
        if (nodeStructuralFingerprint(node, incoming) !== nodeStructuralFingerprint(node)) {
          throw new Error('Connection fingerprint changed');
        }
      }
      const root = document.getElementById('nodes-layer');
      const wraps = [...root.children];
      const svg = document.getElementById('connections-layer');
      const groups = [...svg.querySelectorAll('.conn-group')];
      const observer = new MutationObserver(() => {});
      observer.observe(svg, {childList: true, subtree: true});
      for (let i = 0; i < 5; i++) {renderNodes({immediate: true}); renderStaticConnections();}
      if (observer.takeRecords().length || groups.some(g => !g.isConnected)) throw new Error('Unchanged wires remounted');
      observer.disconnect();
      if (wraps.some((w, i) => root.children[i] !== w)) throw new Error('Unchanged cards remounted');
      const path = svg.querySelector('.conn-line[data-id="wire-0"]');
      const before = path.getAttribute('d');
      board.nodes[1].x += 25;
      renderNodes({immediate: true}); renderStaticConnections();
      if (!path.isConnected || path.getAttribute('d') === before) throw new Error('Moved wire failed to patch');
      board.nodes[1].x -= 25;
      const pending = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      pending.classList.add('conn-pending'); svg.append(pending);
      board.connections.reverse(); renderStaticConnections();
      const expected = board.connections.filter(c => isConnectionInViewport(c)).map(c => c.id);
      const actual = [...svg.querySelectorAll('.conn-group')].map(g => g.dataset.id);
      if (JSON.stringify(expected) !== JSON.stringify(actual) || svg.lastElementChild !== pending) {
        throw new Error('Wire order or pending wire lost');
      }
      pending.remove(); board.connections.reverse();
      const conn = board.connections.shift(); renderStaticConnections();
      if (path.isConnected || document.querySelector('.port-out[data-node=large-0]').classList.contains('port-linked')) {
        throw new Error('Deleted wire/port remains linked');
      }
      board.connections.unshift(conn); renderStaticConnections();
      if (!document.querySelector('.port-out[data-node=large-0]').classList.contains('port-linked')) throw new Error('Reconnected port missing');
      for (const scale of [0.3, 1, 2, 4]) {
        CanvasStore.viewport.scale = scale; applyChromeCounterScale(); renderStaticConnections();
        const model = getPortCenterModel('large-0', 'out');
        // Apply the world transform before comparing world-space ports.
        applyViewport();
        const aligned = getPortCenter('large-0', 'out');
        if (Math.abs(model.y - aligned.y) > 1) throw new Error(`Port/title alignment changed at ${scale}: ${JSON.stringify({model, aligned, app: document.getElementById('app-root').className, route: document.documentElement.className, viewport: getViewportEl().getBoundingClientRect().toJSON(), body: document.querySelector('.node-wrap[data-id=large-0] .node-body').getBoundingClientRect().toJSON()})}`);
      }
      CanvasStore.viewport = {x: 60, y: 90, scale: 1}; applyViewport(); clearSelection();
    }""")
    page.wait_for_timeout(450)
    # Real keyboard events: navigation, zoom, rename, duplicate, undo/redo/delete.
    before_x = page.evaluate('CanvasStore.viewport.x')
    page.keyboard.down('ArrowRight')
    page.wait_for_timeout(100)
    page.keyboard.up('ArrowRight')
    assert page.evaluate('CanvasStore.viewport.x') != before_x
    page.keyboard.press('Control+=')
    page.wait_for_timeout(200)
    assert page.evaluate('CanvasStore.viewport.scale') > 1
    page.evaluate("CanvasStore.viewport = {x: 60, y: 90, scale: 1}; applyViewport(); selectSingle('large-0'); CanvasHistory.reset();")
    page.wait_for_timeout(450)
    page.keyboard.press('F2')
    page.locator('#modal-rename-node').wait_for(state='visible')
    page.locator('#input-rename-node').fill('快捷键改名')
    page.locator('#input-rename-node').press('Enter')
    page.wait_for_function("CanvasStore.activeBoard().nodes[0].customName === '快捷键改名'")
    page.locator('#modal-rename-node').wait_for(state='hidden')
    page.evaluate('document.activeElement?.blur()')
    assert page.evaluate('canUseCanvasShortcuts()'), page.evaluate("({active: isCanvasActive(), floating: isFloatingCanvasUiOpen(), focus: document.activeElement.outerHTML.slice(0,200)})")
    page.keyboard.press('Control+d')
    assert page.evaluate('CanvasStore.activeBoard().nodes.length') == 601, page.evaluate("({count: CanvasStore.activeBoard().nodes.length, selected: getSelectedIds(), focus: document.activeElement.outerHTML.slice(0,200)})")
    page.wait_for_function('CanvasStore.activeBoard().nodes.length === 601')
    page.keyboard.press('Control+z')
    page.wait_for_function('CanvasStore.activeBoard().nodes.length === 600')
    page.keyboard.press('Control+y')
    page.wait_for_function('CanvasStore.activeBoard().nodes.length === 601')
    page.keyboard.press('Delete')
    page.wait_for_function('CanvasStore.activeBoard().nodes.length === 600')
    page.keyboard.press('Control+z')
    page.wait_for_function('CanvasStore.activeBoard().nodes.length === 601')
    page.keyboard.press('Control+y')
    page.wait_for_function('CanvasStore.activeBoard().nodes.length === 600')
    page.evaluate("selectSingle('large-0'); CanvasStore.viewport = {x: 60, y: 90, scale: 1}; applyViewport();")
    page.wait_for_timeout(450)
    editor = page.locator('.prompt-editor[data-node="large-0"]')
    editor.fill('大画布输入不丢失')
    page.evaluate("""() => {
      const editor = document.activeElement;
      const html = editor.innerHTML;
      const selection = window.getSelection();
      const anchor = selection.anchorNode, offset = selection.anchorOffset;
      const viewport = JSON.stringify(CanvasStore.viewport);
      CanvasStore.activeBoard().nodes[0].status = 'pending';
      renderNodes({immediate: true});
      if (document.activeElement !== editor || editor.innerHTML !== html || selection.anchorNode !== anchor || selection.anchorOffset !== offset) {
        throw new Error('Refresh disrupted text/caret');
      }
      editor.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
      if (viewport !== JSON.stringify(CanvasStore.viewport)) throw new Error('Editing shortcut moved canvas');
      CanvasStore.activeBoard().nodes[0].status = 'idle'; editor.blur(); clearSelection();
      CanvasStore.viewport = {x: 60, y: 90, scale: 1}; applyViewport(); renderNodes({immediate: true});
    }""")
    page.wait_for_timeout(450)
    # Selected offscreen cards remain operable; cold cards appear on re-entry.
    page.evaluate("""() => {
      const far = document.querySelector('.node-wrap[data-id=large-599]');
      syncNodeViewportCull({skipHydrate: true});
      if (!far.classList.contains('is-offscreen')) throw new Error('Cold card was not culled');
      setSelection(['large-598', 'large-599'], null);
      if (far.classList.contains('is-offscreen')) throw new Error('Selected cold card hidden');
      clearSelection(); const node = CanvasStore.activeBoard().nodes[599];
      CanvasStore.viewport.scale = 2; applyChromeCounterScale();
      CanvasStore.viewport.x = 100 - node.x * 2; CanvasStore.viewport.y = 100 - node.y * 2;
      applyViewport(); syncNodeViewportCull({skipHydrate: true});
      if (far.classList.contains('is-offscreen')) throw new Error('Re-entering cold card stayed hidden');
      const model = getPortCenterModel(node.id, 'out'), port = getPortCenter(node.id, 'out');
      if (Math.abs(model.y - port.y) > 1) throw new Error('Re-entered title/port scale stale');
      CanvasStore.viewport = {x: 60, y: 90, scale: 1}; applyViewport();
    }""")
    page.wait_for_timeout(450)
    # Marquee selection and a real drag keep final node coordinates and wires.
    page.mouse.move(50, 85)
    page.mouse.down()
    page.mouse.move(665, 240, steps=8)
    page.mouse.up()
    page.wait_for_function("getSelectedIds().includes('large-0') && getSelectedIds().includes('large-1')")
    page.evaluate("selectSingle('large-0');")
    page.wait_for_timeout(450)
    start = page.locator('.node-wrap[data-id="large-0"] .node-preview').bounding_box()
    page.mouse.move(start['x'] + 80, start['y'] + 60)
    page.mouse.down()
    page.mouse.move(start['x'] + 180, start['y'] + 125, steps=8)
    page.mouse.up()
    page.wait_for_function('!CanvasStore.drag')
    assert page.evaluate('CanvasStore.activeBoard().nodes[0].x') > 50
    page.evaluate("""() => {
      renderStaticConnections();
      const conn = CanvasStore.activeBoard().connections.find(c => c.id === 'wire-0');
      const a = getPortCenterModel(conn.from, 'out'), b = getPortCenterModel(conn.to, 'in');
      const path = document.querySelector('.conn-line[data-id=wire-0]');
      if (path.getAttribute('d') !== connectionPath(a.x, a.y, b.x, b.y)) throw new Error('Drag wire stale');
    }""")


if __name__ == '__main__':
    main()
