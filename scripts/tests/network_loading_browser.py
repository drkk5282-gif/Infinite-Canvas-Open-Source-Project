"""Real browser regression with fictional data and intercepted APIs."""
import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / '.playwright-browsers')
ORIGIN = 'http://127.0.0.1:10027'


async def main():
    gate = asyncio.Event()
    arrived = asyncio.Event()
    delayed = {'path': None, 'count': 0}

    async def route_request(route):
        url = route.request.url
        if not url.startswith(ORIGIN + '/'):
            await route.abort()
            return
        path = urlparse(url).path
        if not path.startswith('/api/'):
            await route.continue_()
            return
        if delayed['path'] == path:
            delayed['count'] += 1
            arrived.set()
            await gate.wait()
        data = {}
        if path == '/api/auth/me':
            data = {'user': {'id': 'network-test', 'username': 'network-test', 'is_admin': False}}
        elif path == '/api/app-config':
            data = {'disable_browser_channels': True, 'disable_doubao': True, 'points_enabled': False}
        elif path == '/api/canvas':
            data = {'boards': [], 'storageEpoch': 'network-test', 'updatedAt': 'initial'}
        elif path == '/api/workbench':
            data = {'workbenches': [], 'storageEpoch': 'network-test', 'updatedAt': 'initial'}
        elif path == '/api/accounts':
            data = []
        elif path == '/api/tasks':
            data = {'tasks': []}
        await route.fulfill(json=data)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route('**/*', route_request)
        await context.add_init_script("localStorage.setItem('canvas_session_token', 'fictional-test');")
        page = await context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(ORIGIN, wait_until='networkidle')
        await page.wait_for_function('CanvasStore._persistence?.ready && WorkbenchStore._persistence?.ready')
        await page.evaluate('clearTaskPolling(); CanvasStore.stopSync();')
        delayed['path'] = '/api/tasks'
        await page.evaluate('for (let i = 0; i < 20; i++) startPolling();')
        await asyncio.wait_for(arrived.wait(), timeout=5)
        await page.wait_for_timeout(300)
        assert delayed['count'] == 1, delayed
        assert await page.evaluate('isTaskPollingActive()')
        gate.set()
        await page.wait_for_function('!isTaskPollingActive()')

        delayed.update(path='/api/canvas', count=0)
        gate.clear()
        arrived.clear()
        await page.evaluate('CanvasStore.stopSync(); for (let i = 0; i < 20; i++) void CanvasStore.syncFromServer();')
        await asyncio.wait_for(arrived.wait(), timeout=5)
        await page.wait_for_timeout(300)
        assert delayed['count'] == 1, delayed
        gate.set()
        await page.wait_for_function('!CanvasStore._syncInFlight')
        assert not errors, errors
        print('PASS: 20 task polling starts -> 1 request; 20 canvas sync calls -> 1 request; page errors: 0')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
