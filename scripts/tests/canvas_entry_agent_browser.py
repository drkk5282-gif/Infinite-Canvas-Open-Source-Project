"""Real canvas route, fictional APIs: startup, deferred cards and agent edits."""
import argparse
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import sync_playwright
from canvas_smoothness_browser import PIXEL, ROOT
from canvas_large_board_browser import check_interactions
from canvas_interaction_load import check_interaction_load, preview_fixture
from canvas_motion_profile import check_motion_profile
from canvas_drag_paint_profile import check_drag_paint_profile
from canvas_response_checks import check_responsiveness


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--responsiveness', action='store_true')
    parser.add_argument('--overview', action='store_true', help='Enter at the minimum supported zoom')
    parser.add_argument('--interaction-load', action='store_true', help='Drag, pan and zoom while media and task updates arrive')
    parser.add_argument('--motion-profile', action='store_true', help='Dense, empty and separated-region gesture measurements')
    parser.add_argument('--paint-profile', action='store_true', help='Stationary viewport, 600 loaded overlapping cards')
    parser.add_argument('--paint-cpu-rate', type=float, default=4, help='CPU throttle for paint profile; use 1 for normal speed')
    parser.add_argument('--paint-diagnose', action='store_true', help='Record style invalidation and isolate drag subsystems')
    parser.add_argument('--frontend-source', type=Path, help='Read a complete saved frontend, including CSS, for comparison')
    parser.add_argument('--canvas-source', type=Path, help='Read another canvas.js for comparison; never overwrites source')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/canvas-agent-qa/after.json')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    nodes = [dict(id=f'large-{i}', type='image', customName=f'节点 {i}', prompt=f'描述 {i}',
                  status='idle', ratio='16:9', channel='myapi', model='myapi_image',
                  x=(i % 30) * 360, y=(i // 30) * 280) for i in range(600)]
    if args.interaction_load or args.motion_profile or args.paint_profile:
        for i, node in enumerate(nodes):
            node.update(imageUrl=f'/fixtures/preview-{i}.png', status='completed', mediaWidth=1024, mediaHeight=576)
    fixture = preview_fixture() if args.interaction_load or args.motion_profile or args.paint_profile else PIXEL
    board = dict(id='large-board', name='加载与智能体验证', nodes=nodes,
                 viewport=dict(x=60, y=90, scale=0.3 if args.overview else 1),
                 connections=[dict(id=f'wire-{i}', **{'from': nodes[i]['id'], 'to': n['id']}) for i, n in enumerate(nodes[1:])])
    server = dict(boards=[board], updatedAt='initial', storageEpoch='entry-test')
    errors, jobs = [], []

    def route_request(route):
        parsed = urlparse(route.request.url)
        if parsed.netloc != 'smoothness.test':
            route.abort()
            return
        path = unquote(parsed.path)
        if path.startswith('/fixtures/'):
            route.fulfill(body=fixture, content_type='image/png' if args.interaction_load or args.motion_profile or args.paint_profile else 'image/gif')
            return
        if path.startswith('/api/'):
            data = {}
            if path == '/api/auth/me':
                data = {'user': {'id': 'entry-test', 'username': 'entry-test'}}
            elif path == '/api/app-config':
                data = {'disable_browser_channels': True, 'disable_doubao': True, 'points_enabled': False}
            elif path == '/api/canvas':
                if route.request.method in ('PATCH', 'PUT'):
                    payload = route.request.post_data_json
                    rows = {b['id']: b for b in server['boards']} if route.request.method == 'PATCH' else {}
                    rows.update({b['id']: b for b in payload.get('boards', [])})
                    server.update(boards=list(rows.values()), updatedAt='saved')
                data = server
            elif path == '/api/workbench':
                data = dict(workbenches=[], updatedAt='initial', storageEpoch='entry-test')
            elif path == '/api/accounts':
                data = []
            elif path == '/api/tasks':
                data = {'tasks': []}
            elif path == '/api/image/generate':
                jobs.append(route.request.post_data_json)
                data = {'id': f'job-{len(jobs)}', 'status': 'pending'}
            route.fulfill(json=data)
            return
        relative = {'/': 'index.html', '/canvas': 'canvas.html'}.get(path, path.removeprefix('/static/'))
        target = (ROOT / 'frontend' / relative).resolve()
        if args.frontend_source:
            target = (args.frontend_source / relative).resolve()
        saved = ROOT / 'backups/before-canvas-agent-adaptation-20260914/frontend' / relative
        if args.baseline and not args.canvas_source and not args.frontend_source and relative in ('js/canvas.js', 'js/agent/panel.js', 'js/agent/index.js'):
            target = saved
        if args.canvas_source and relative == 'js/canvas.js':
            target = args.canvas_source.resolve()
        if target.is_file() and (target.is_relative_to(ROOT / 'frontend') or target.is_relative_to(ROOT / 'backups')):
            route.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream')
        else:
            route.fulfill(status=404, body='')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 900})
        context.route('**/*', route_request)
        context.add_init_script("""
          localStorage.setItem('canvas_session_token', 'fictional-entry-test');
          window.__entry = {renders: [], longTasks: []};
          new PerformanceObserver(list => { for (const e of list.getEntries()) __entry.longTasks.push(e.duration); }).observe({type:'longtask', buffered:true});
          document.addEventListener('DOMContentLoaded', () => {
            const render = renderNodesImpl;
            renderNodesImpl = function(opts) { const t = performance.now(); const out = render(opts);
              __entry.renders.push({ms: performance.now()-t, cards: document.querySelectorAll('.node-preview-col').length}); return out; };
            window.syncAllNodesFromTasks = () => new Promise(resolve => setTimeout(resolve, 1800));
          }, {once:true});
        """)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://smoothness.test/canvas?id=large-board', wait_until='domcontentloaded')
        page.wait_for_function("!document.documentElement.classList.contains('is-canvas-booting') && document.querySelector('#nodes-layer')?.children.length === 600")
        metrics = page.evaluate("""() => ({revealMs: performance.now(), firstRender: __entry.renders[0],
          deferred: document.querySelectorAll('[data-deferred-node="1"]').length,
          chatMountedAtEntry: !!document.getElementById('agent-panel'), longTasks: __entry.longTasks})""")
        page.wait_for_function('initBrowserZoomGuard._bound')
        page.evaluate('clearTaskPolling(); CanvasStore.stopSync();')
        page.wait_for_timeout(600)
        if args.responsiveness:
            metrics['responsiveness'] = check_responsiveness(page, verify=not args.baseline)
        if args.paint_profile:
            metrics['paint'] = check_drag_paint_profile(page, args.output, verify=not args.baseline, diagnose=args.paint_diagnose, cpu_rate=args.paint_cpu_rate)
        elif args.motion_profile:
            metrics['motion'] = check_motion_profile(page, verify=not args.baseline)
        elif args.interaction_load:
            metrics['interaction'] = check_interaction_load(page, verify=not args.baseline)
        if not args.baseline and not args.interaction_load and not args.motion_profile and not args.paint_profile and not args.responsiveness:
            assert metrics['firstRender']['cards'] < 200, metrics
            if not args.overview:
                assert metrics['deferred'] > 400, metrics
            # Current entry intentionally hydrates hidden chat to resume in-flight jobs.
            assert page.evaluate("!AgentPanel.isOpen()"), metrics
            if args.overview:
                page.wait_for_function("""() => CanvasStore.activeBoard().nodes.filter(n => isNodeInViewport(n, 0))
                  .every(n => document.querySelector(`.node-wrap[data-id="${n.id}"]`)?.dataset.deferredNode !== '1')""")
            page.evaluate("CanvasStore.viewport = {x: 60, y: 90, scale: 1}; applyViewport(); renderNodes({immediate:true});")
            check_interactions(page)
            page.evaluate(r"""async () => {
              clearTaskPolling(); CanvasStore.stopSync();
              selectSingle('large-0');
              AgentOps.updateNode('large-0', {prompt:'第一行：春天\n第二行：保留原文。'});
              AgentOps.replaceNodeText('large-0', {field:'prompt', find:'春天', replace:'夏天'});
              const node = CanvasStore.activeBoard().nodes.find(n => n.id === 'large-0');
              if (node.prompt !== '第一行：夏天\n第二行：保留原文。') throw Error('Unrelated text changed');
              const ed = document.querySelector('.prompt-editor[data-node="large-0"]');
              if (PromptEditor.getValue(ed) !== node.prompt) throw Error('Editor still has stale prompt');
              const far = document.querySelector('[data-id="large-570"]');
              CanvasStore.updateNode('large-570', {prompt:'屏外更新'}); renderNodes({immediate:true}); selectSingle('large-570');
              if (document.querySelector('[data-id="large-570"]') !== far || far.dataset.deferredNode) throw Error('Deferred selection identity lost');
              AgentPanel.open();
              if (!document.querySelector('#agent-panel')) throw Error('Lazy chat failed to open');
              if (getComputedStyle(document.querySelector('.bottom-chrome-fabs')).visibility !== 'hidden') throw Error('Chat opener still covers open panel');
              AgentPanel.close();
              if (getComputedStyle(document.querySelector('.bottom-chrome-fabs')).visibility === 'hidden') throw Error('Chat opener not restored after closing panel');
              CanvasStore.updateNode('large-0', {imageUrl:'/fixtures/original.png', status:'completed', channel:'myapi', model:'myapi_image'});
              selectSingle('large-0');
              window.ensureReferencedAiMarksBaked = async () => [];
              const result = await generateForNode('large-0', {imageEdit:{sourceUrl:'/fixtures/original.png', instruction:'把招牌文字改成夏天，其余保持不变'}});
              if (!result?.submitted) throw Error('Image edit was not queued');
              const live = CanvasStore.activeBoard().nodes.find(n => n.id === 'large-0');
              if (live.imageUrl !== '/fixtures/original.png') throw Error('Original removed before result');
              if (live._retryPayload.referenceImages[0] !== '/fixtures/original.png') throw Error('Retry lost source');
              if (!live.genHistory.some(h => h.imageUrl === '/fixtures/original.png')) throw Error('Original missing from history');
              if (PromptEditor.getValue(document.querySelector('.prompt-editor[data-node="large-0"]')) !== live.prompt) throw Error('Image edit prompt not visible');
              await CanvasStore.flushSave();
              CanvasStore.viewport = {x:60,y:90,scale:1}; applyViewport();
            }""")
            page.wait_for_timeout(500)
            assert jobs, 'No image task submitted'
            assert jobs[-1]['node_id'] == 'large-0'
            assert jobs[-1]['reference_images'][0] == '/fixtures/original.png'
            assert jobs[-1]['references'][0]['url'] == '/fixtures/original.png'
            assert '招牌文字改成夏天' in jobs[-1]['prompt']
            page.evaluate("""async () => {
              const node = CanvasStore.activeBoard().nodes.find(n => n.id === 'large-0');
              syncNodeFromTask({id:node.taskId, board_id:'large-board', node_id:'large-0', status:'completed', image_url:'/fixtures/edited.png'});
              const done = CanvasStore.activeBoard().nodes.find(n => n.id === 'large-0');
              if (done.imageUrl !== '/fixtures/edited.png') throw Error('Edited image did not replace target');
              if (!done.genHistory.some(h => h.imageUrl === '/fixtures/original.png')) throw Error('Completion lost original');
              await CanvasStore.flushSave();
            }""")
            page.screenshot(path=str(args.output.with_suffix('.png')))
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function("!document.documentElement.classList.contains('is-canvas-booting') && CanvasStore.activeBoard()?.nodes?.length === 600")
            assert page.evaluate("CanvasStore.activeBoard().nodes.find(n=>n.id==='large-0').imageUrl") == '/fixtures/edited.png'
            assert page.evaluate("CanvasStore.activeBoard().nodes.find(n=>n.id==='large-570').prompt") == '屏外更新'
        metrics.update(errors=errors, imageJobs=len(jobs))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
        assert not errors, errors
        print(json.dumps(metrics, ensure_ascii=False))
        browser.close()


if __name__ == '__main__':
    main()
