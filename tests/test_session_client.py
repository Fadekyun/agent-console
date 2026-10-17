import io
import json
import os
import unittest
import urllib.error
from unittest.mock import Mock, patch

from agent_console import session_client


class SessionClientErrorTests(unittest.TestCase):
    def rejection(self, body, *, status=403):
        stream = Mock(wraps=io.BytesIO(body)) if isinstance(body, bytes) else body
        stream.closed = False
        error = urllib.error.HTTPError('http://private-url.invalid', status,
            'PRIVATE-REASON', {'Private-Header': 'PRIVATE-HEADER'}, stream)
        opener = Mock()
        opener.open.side_effect = error
        with patch.dict(os.environ, {
                'AGENT_CONSOLE_REPORTING_URL': 'http://fixture.invalid',
                'AGENT_CONSOLE_SESSION_ID': 'fixture-id',
                'AGENT_CONSOLE_EVIDENCE_CAPABILITY': 'PRIVATE-CAPABILITY'}, clear=True), \
                patch('urllib.request.build_opener', return_value=opener), \
                self.assertRaises(ValueError) as rejected:
            session_client.request('read', {'route': ['session', 'list']})
        message = str(rejected.exception)
        for secret in ('private-url', 'PRIVATE-REASON', 'PRIVATE-HEADER', 'PRIVATE-CAPABILITY', 'PRIVATE-BODY'):
            self.assertNotIn(secret, message)
        return message, stream

    def test_known_fixed_reasons_include_status_and_safe_detail(self):
        for status, detail in [
                (403, 'valid session reporting capability required'),
                (403, 'read-only sessions cannot control other sessions'),
                (403, 'session is not an ancestor or descendant of the caller'),
                (403, 'this operation requires another session in the same tree'),
                (403, 'session is not in the same tree as the caller'),
                (404, 'session or required field not found'),
                (404, 'session not found')]:
            with self.subTest(detail=detail):
                message, stream = self.rejection(json.dumps({'detail': detail, 'extra': 'PRIVATE-BODY'}).encode(), status=status)
                self.assertEqual(message, f'Console session request rejected (HTTP {status}): {detail}')
                stream.read.assert_called_once_with(4097)

    def test_unknown_malformed_and_oversized_details_stay_generic(self):
        generic = 'Console session request rejected (HTTP 403); check session authorization and request constraints'
        bodies = [b'PRIVATE-BODY', b'\xff', b'[]', b'null', b'{"detail": []}',
            b'{"detail": {"secret": "PRIVATE-BODY"}}', b'{"detail": "PRIVATE-BODY"}',
            b'{"detail": "session not found PRIVATE-BODY"}',
            b'{"detail": "session not found"}' + b' ' * 4096]
        for body in bodies:
            with self.subTest(body_size=len(body)):
                message, stream = self.rejection(body)
                self.assertEqual(message, generic)
                stream.read.assert_called_once_with(4097)

    def test_error_body_read_failure_stays_generic(self):
        stream = Mock()
        stream.read.side_effect = OSError('PRIVATE-BODY')
        message, _ = self.rejection(stream)
        self.assertEqual(message, 'Console session request rejected (HTTP 403); check session authorization and request constraints')
        stream.read.assert_called_once_with(4097)


if __name__ == '__main__':
    unittest.main()
