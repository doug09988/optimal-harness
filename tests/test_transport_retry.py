"""Scripted HTTP transport tests; no sockets or model server."""
from http.client import RemoteDisconnected
import io
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from optimal_harness.model import LocalChatClient, ModelError, _single_http_request


class TransportRetryTests(unittest.TestCase):
    def response(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.read.return_value = json.dumps({
            'choices': [{'message': {'content': 'done'}}],
            'usage': {'prompt_tokens': 40, 'completion_tokens': 10,
                      'prompt_tokens_details': {'cached_tokens': 30}},
        }).encode()
        return response

    def call(self, script):
        opener = MagicMock()
        opener.open.side_effect = script
        events = []
        client = LocalChatClient('http://127.0.0.1:8080', 'scripted')
        return client, opener, events

    def test_twice_transient_then_success_same_request_and_usage(self):
        client, opener, events = self.call([
            RemoteDisconnected('Remote end closed connection without response'),
            URLError(ConnectionResetError('reset')), self.response()])
        with patch('urllib.request.build_opener', return_value=opener), \
                patch('optimal_harness.model.time.sleep') as sleep:
            result = client.complete('same prompt', 123, 60, events.append)
        self.assertEqual(result.transport_retries, 2)
        self.assertTrue(result.usage_unknown_for_failed_request)
        self.assertEqual(result.fresh_tokens, 20)
        self.assertEqual([e['attempt'] for e in events], [2, 3])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [5, 15])
        requests = [c.args[0] for c in opener.open.call_args_list]
        self.assertTrue(all(r is requests[0] for r in requests))
        self.assertEqual(json.loads(requests[0].data)['max_tokens'], 123)

    def test_transient_exhaustion_preserves_error_and_records_two_retries(self):
        client, opener, events = self.call([BrokenPipeError('broken')] * 3)
        with patch('urllib.request.build_opener', return_value=opener), \
                patch('optimal_harness.model.time.sleep') as sleep:
            with self.assertRaisesRegex(ModelError, '^local model request failed: broken$') as caught:
                client.complete('prompt', 123, 60, events.append)
        self.assertEqual(caught.exception.transport_retries, 2)
        self.assertEqual(len(events), 2)
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_non_transient_errors_do_not_retry(self):
        errors = [TimeoutError('deadline'), URLError(TimeoutError('timeout')),
                  HTTPError('scripted', 500, 'server error', {}, io.BytesIO()),
                  OSError('other transport failure')]
        for error in errors:
            with self.subTest(error=error):
                client, opener, events = self.call([error])
                with patch('urllib.request.build_opener', return_value=opener), \
                        patch('optimal_harness.model.time.sleep') as sleep:
                    with self.assertRaises(ModelError):
                        client.complete('prompt', 123, 60, events.append)
                self.assertEqual(opener.open.call_count, 1)
                self.assertEqual(events, [])
                sleep.assert_not_called()

    def test_refused_and_aborted_are_transient(self):
        for error in (ConnectionRefusedError(), ConnectionAbortedError()):
            self.assertTrue(LocalChatClient._transient(URLError(error)))

    def test_malformed_response_does_not_retry(self):
        response = self.response()
        response.read.return_value = b'not json'
        client, opener, events = self.call([response])
        with patch('urllib.request.build_opener', return_value=opener), \
                patch('optimal_harness.model.time.sleep') as sleep:
            with self.assertRaises(ModelError):
                client.complete('prompt', 123, 60, events.append)
        sleep.assert_not_called()
        self.assertEqual(events, [])

    def test_child_sends_retry_evidence_before_terminal_error(self):
        client, opener, _ = self.call([ConnectionResetError('reset')] * 3)
        conn = MagicMock()
        with patch('urllib.request.build_opener', return_value=opener), \
                patch('optimal_harness.model.time.sleep'):
            _single_http_request(conn, client, 'prompt', 123, 60)
        messages = [c.args[0] for c in conn.send.call_args_list]
        self.assertEqual([m[0] for m in messages],
                         ['transport_retry', 'transport_retry', 'error'])
        self.assertEqual(messages[1][1]['transport_retries'], 2)
        conn.close.assert_called_once()
