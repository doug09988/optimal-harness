"""Real local HTTP supervision tests; no shared model server used."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from optimal_harness.model import LocalChatClient, ModelQueue, ModelError
from optimal_harness.server_config import discover_cache_configuration

BASE = Path(__file__).resolve().parents[1]


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=BASE / 'runs')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.payloads = []
        self.mode = 'ok'
        self.delay = 0
        case = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.payloads.append(payload)
                time.sleep(case.delay)
                usage = {'prompt_tokens': 40, 'completion_tokens':10, 'prompt_tokens_details':{'cached_tokens':30}}
                if case.mode == 'negative': usage['prompt_tokens']=-1
                if case.mode == 'cache': usage['prompt_tokens_details']['cached_tokens']=50
                data = {'choices':[{'message':{'content':'{"action":"finish","summary":"lane ready"}'}}], 'usage':usage}
                if case.mode == 'no_usage': del data['usage']
                raw = json.dumps(data).encode()
                try:
                    self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self,*args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.client = LocalChatClient(f'http://127.0.0.1:{self.server.server_address[1]}/v1','test')

    def test_missing_usage_cannot_be_invented(self):
        self.mode='no_usage'
        with self.assertRaisesRegex(ModelError,'omitted usage'): self.client.complete('prompt',20,2)

    def test_invalid_server_usage_cannot_undercount(self):
        for mode in ('negative','cache'):
            self.mode=mode
            with self.subTest(mode=mode), self.assertRaises(ModelError): self.client.complete('prompt',20,2)

    def test_fresh_context_no_prior_chat_replayed(self):
        self.client.complete('first secret observation',20,2)
        self.client.complete('second independent brief',20,2)
        self.assertEqual(len(self.payloads[1]['messages']),2)
        self.assertNotIn('first secret',json.dumps(self.payloads[1]))
        self.assertEqual(self.payloads[0]['messages'][0], self.payloads[1]['messages'][0])

    def test_deadline_kills_wedged_http_generation(self):
        self.delay=3
        q=ModelQueue(self.client,self.base/'lock')
        async def run():
            await q.start()
            try:
                with self.assertRaisesRegex(ModelError,'deadline'):
                    await q.request('wedged',20,0.5)
            finally: await q.stop()
        started=time.monotonic(); asyncio.run(run())
        self.assertLess(time.monotonic()-started,2)

    def test_expired_queued_request_never_executes_later(self):
        self.delay=0.3
        q=ModelQueue(self.client,self.base/'lock')
        async def run():
            await q.start()
            try:
                first=asyncio.create_task(q.request('first',20,3))
                await asyncio.sleep(0.02)
                second=asyncio.create_task(q.request('expired',20,0.05))
                results=await asyncio.gather(first,second,return_exceptions=True)
                self.assertIsInstance(results[1], ModelError)
                self.assertIn('queue_timeout',str(results[1]))
            finally: await q.stop()
        asyncio.run(run())
        self.assertEqual(len(self.payloads),1)

    def test_recovery_requires_hook_and_real_completion_not_models_list(self):
        q=ModelQueue(self.client,self.base/'lock')
        marker=self.base/'restarted'
        async def run():
            await q.start()
            try:
                with self.assertRaises(ModelError): await q.recover(None)
                result=await q.recover(lambda:marker.write_text('restart-hook-called'),timeout=3)
                self.assertEqual(result.fresh_tokens,20)
            finally: await q.stop()
        asyncio.run(run())
        self.assertEqual(marker.read_text(),'restart-hook-called')
        self.assertEqual(len(self.payloads),1)
        self.assertIn('lane ready',self.payloads[0]['messages'][1]['content'])

    def test_cache_config_discovery_records_actual_flags_not_assumptions(self):
        class Result:
            returncode=0
            stdout='42 /opt/python /opt/mlx_lm.server --port 8080 --prompt-cache-bytes 268435456 --prompt-cache-size 1 --decode-concurrency 1 --prompt-concurrency 1\n'
        with patch('optimal_harness.server_config.subprocess.run',return_value=Result()):
            config=discover_cache_configuration()
        self.assertTrue(config['verified'])
        self.assertEqual(config['prompt_cache_bytes'],268435456)
        Result.stdout=Result.stdout.replace('268435456','536870912')
        with patch('optimal_harness.server_config.subprocess.run',return_value=Result()):
            self.assertFalse(discover_cache_configuration()['verified'])
