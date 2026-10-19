import tempfile
import unittest
import socket
import threading
import time
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.routing import Route
from backend.media.range_response import media_file_response
from backend.text_compression import TextCompressionMiddleware


class MediaRangeTests(unittest.IsolatedAsyncioTestCase):
    async def test_range_and_download_compatibility(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'video.mp4'
            data = bytes(range(256)) * 4096
            path.write_bytes(data)
            async def endpoint(request):
                return media_file_response(path, request)
            app = TextCompressionMiddleware(Starlette(routes=[Route('/uploads/video.mp4', endpoint)]))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                full = await client.get('/uploads/video.mp4')
                self.assertEqual(full.content, data)
                self.assertEqual(full.headers['accept-ranges'], 'bytes')
                for value, start, end in [('bytes=0-1023', 0, 1023), ('bytes=-512', len(data)-512, len(data)-1),
                                          ('bytes=1000000-', 1000000, len(data)-1), ('bytes=1048570-9999999',1048570,len(data)-1)]:
                    res = await client.get('/uploads/video.mp4', headers={'Range': value})
                    self.assertEqual(res.status_code, 206)
                    self.assertEqual(res.content, data[start:end+1])
                    self.assertEqual(res.headers['content-range'], f'bytes {start}-{end}/{len(data)}')
                    self.assertEqual(int(res.headers['content-length']), end-start+1)
                    self.assertNotIn('content-encoding', res.headers)
                for value in ['bytes=9999999-', 'bytes=10-5', 'bytes=-0']:
                    res = await client.get('/uploads/video.mp4', headers={'Range': value})
                    self.assertEqual(res.status_code, 416)
                    self.assertEqual(res.content, b'')
                for value in ['bytes=abc', 'bytes=0-1,4-5']:
                    res = await client.get('/uploads/video.mp4', headers={'Range': value})
                    self.assertEqual(res.status_code, 200)
                    self.assertEqual(res.content, data)
                for validator, status in [(full.headers['etag'], 206), (full.headers['last-modified'], 206), ('"stale"',200)]:
                    res = await client.get('/uploads/video.mp4', headers={'Range':'bytes=0-1023','If-Range':validator})
                    self.assertEqual(res.status_code,status)
                head = await client.head('/uploads/video.mp4')
                self.assertEqual(head.content,b'')
                self.assertEqual(int(head.headers['content-length']),len(data))

    async def test_real_problem_files_over_http(self):
        import uvicorn
        root = Path(__file__).resolve().parents[2]
        folder = root / 'data/users/admin/wmduut9i/assets'
        names = ['b902b29afd2241a085a7617090f8a923.mp4', 'dcdef3c18f9f4680bc9a3d9dc3b3d80e.mp4',
                 '8ab834bde9444b998af3546c222efa5d.mp4', 'cd172d8afb9448c9926c8d366fc1a827.mp4',
                 'eee413f0adbe4702a7f2f4b709ea371f.mp4', 'a278d9459bd54e1fb82e1a9bb66759b2.mp4']
        if not all((folder / name).is_file() for name in names):
            self.skipTest('Local read-only regression fixtures are not installed')
        paths = {name: folder / name for name in names}
        async def endpoint(request):
            return media_file_response(paths[request.path_params['name']], request)
        app = Starlette(routes=[Route('/{name}', endpoint)])
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started: break
                import asyncio
                await asyncio.sleep(.02)
            self.assertTrue(server.started)
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
                import asyncio
                started = time.perf_counter()
                responses = await asyncio.gather(*(client.get('/'+name, headers={'Range':'bytes=0-1023'}) for name in names))
                for name, res in zip(names, responses):
                    self.assertEqual(res.status_code,206)
                    with paths[name].open('rb') as source:
                        self.assertEqual(res.content,source.read(1024))
                    self.assertEqual(len(res.content),1024)
                print(f'Real HTTP: {len(responses)} files, {sum(len(r.content) for r in responses)} bytes, {time.perf_counter()-started:.3f}s')
        finally:
            server.should_exit = True
            await asyncio.to_thread(thread.join, 5)
            sock.close()


if __name__ == '__main__':
    unittest.main()
