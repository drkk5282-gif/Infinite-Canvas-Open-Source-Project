import unittest

import httpx
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

from backend.text_compression import TextCompressionMiddleware


class CompressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_is_compressed_but_media_and_ranges_are_untouched(self):
        body = b'const example = "canvas";\n' * 1000

        async def endpoint(request):
            if request.headers.get('range'):
                return Response(body[:128], status_code=206,
                                headers={'Content-Range': f'bytes 0-127/{len(body)}'})
            return Response(body, media_type='application/javascript')

        app = TextCompressionMiddleware(
            Starlette(routes=[Route('/{path:path}', endpoint)]),
            minimum_size=1024, compresslevel=4,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            for url in ['/static/js/app.js?v=test', '/api/canvas', '/api/workbench', '/api/tasks', '/']:
                response = await client.get(url, headers={'Accept-Encoding': 'gzip'})
                self.assertEqual(response.content, body)
                self.assertEqual(response.headers.get('content-encoding'), 'gzip')
                self.assertIn('Accept-Encoding', response.headers['vary'])
            media = await client.get('/uploads/test.mp4', headers={'Accept-Encoding': 'gzip'})
            self.assertNotIn('content-encoding', media.headers)
            self.assertEqual(media.content, body)
            for url in ['/uploads/test.mp4', '/static/js/app.js']:
                partial = await client.get(url, headers={'Range': 'bytes=0-127', 'Accept-Encoding': 'gzip'})
                self.assertEqual(partial.status_code, 206)
                self.assertEqual(partial.content, body[:128])
                self.assertNotIn('content-encoding', partial.headers)
            plain = await client.get('/api/canvas', headers={'Accept-Encoding': 'identity'})
            self.assertEqual(plain.content, body)
            self.assertNotIn('content-encoding', plain.headers)


if __name__ == '__main__':
    unittest.main()
