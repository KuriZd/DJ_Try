import io
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import requests

from scripts.smoke_video_s3 import SmokeFailure, check_range, run


def response(status=200, *, data=None, headers=None, body=b''):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    result.raw = io.BytesIO(body)
    if data is not None:
        import json
        result._content = json.dumps(data).encode()
    return result


class SmokeVideoTests(unittest.TestCase):
    def test_200_is_failure_without_reading_body(self):
        result = response(body=b'large full object')
        with self.assertRaisesRegex(SmokeFailure, '200 OK en vez de 206'):
            check_range(result, 2048, b'x' * 1024)
        self.assertEqual(result.raw.tell(), 0)

    def test_incorrect_range_or_bytes_fail(self):
        for content_range, body in [('bytes 0-1023/9999', b'x' * 1024),
                                    ('bytes 0-1023/2048', b'y' * 1024)]:
            with self.subTest(content_range=content_range), self.assertRaises(SmokeFailure):
                check_range(response(206, headers={'Content-Range': content_range,
                            'Content-Length': '1024'}, body=body), 2048, b'x' * 1024)

    @patch.dict('os.environ', {'VIDEO_SMOKE_TOKEN': 'test-jwt'})
    @patch('scripts.smoke_video_s3.requests.request')
    def test_complete_flow_and_token_is_not_sent_to_s3(self, send):
        video_id = '12345678-1234-1234-1234-123456789abc'
        prefix = b'\x00\x00\x00\x20ftyp' + b'x' * 1016
        origin = 'https://frontend.example'
        cors = {'Access-Control-Allow-Origin': origin,
                'Access-Control-Allow-Methods': 'PUT, GET',
                'Access-Control-Allow-Headers': 'Content-Type, If-None-Match, Range'}
        send.side_effect = [
            response(201, data={'video': {'id': video_id}, 'upload_url': 'https://s3.example/put',
                               'method': 'PUT', 'headers': {'Content-Type': 'video/mp4', 'If-None-Match': '*'}}),
            response(headers=cors), response(headers=cors),
            response(data={'status': 'uploaded', 'renditions': [
                {'profile': 'original', 'status': 'uploaded', 'file_size': 2048}]}),
            response(data={'url': 'https://s3.example/get'}), response(headers=cors),
            response(206, headers={**cors, 'Content-Range': 'bytes 0-1023/2048', 'Content-Length': '1024'}, body=prefix),
        ]
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'test.mp4'
            file.write_bytes(prefix + b'x' * 1024)
            run(Namespace(api_base='https://api.example/api/videos/', origin=origin, file=file))
        self.assertEqual([call.args[0] for call in send.call_args_list],
                         ['POST', 'OPTIONS', 'PUT', 'POST', 'GET', 'OPTIONS', 'GET'])
        for call in send.call_args_list:
            self.assertFalse(call.kwargs['allow_redirects'])
            if 's3.example' in call.args[1]:
                self.assertNotIn('Authorization', call.kwargs['headers'])
        self.assertEqual(send.call_args.kwargs['headers']['Range'], 'bytes=0-1023')
        self.assertTrue(send.call_args.kwargs['stream'])


if __name__ == '__main__':
    unittest.main()
