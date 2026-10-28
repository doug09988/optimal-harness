"""Loopback board API: role-separated bearer auth over the durable Store.

The local runtime is a trusted board client; the MODEL never receives credentials,
raw SQL access, argv selection, scheduler operations, or grade authority.
"""
from __future__ import annotations
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from .engine import Engine
from .store import LeaseLost


class BoardAPI:
    def __init__(self, engine, credentials):
        self.engine = engine
        self.credentials = {}
        for identity, config in credentials.items():
            if config['role'] not in ('admin', 'verifier', 'worker'):
                raise ValueError('unknown credential role')
            token = config['token']
            if not isinstance(token, str) or len(token) < 16:
                raise ValueError('token must contain at least 16 characters')
            self.credentials[identity] = (config['role'], hashlib.sha256(token.encode()).hexdigest())

    def authenticate(self, header):
        if not header.startswith('Bearer '):
            raise PermissionError('bearer token required')
        token_hash = hashlib.sha256(header[7:].encode()).hexdigest()
        for identity, (role, expected) in self.credentials.items():
            if hmac.compare_digest(expected, token_hash):
                return identity, role
        raise PermissionError('invalid token')

    def dispatch(self, identity, role, path, data):
        store = self.engine.store
        allowed = {'/submit': {'admin'}, '/grade': {'verifier'},
                   '/claim': {'worker'}, '/heartbeat': {'worker'}, '/handoff': {'worker'},
                   '/status': {'admin', 'verifier'}, '/audit': {'admin', 'verifier'}}
        if role not in allowed.get(path, set()):
            raise PermissionError('role forbidden for route')
        if path == '/submit':
            return {'project': self.engine.submit(Path(data['root']), data['plan'], Path(data['grader']))}
        if path == '/grade':
            if type(data['approved']) is not bool:
                raise ValueError('approved must be boolean')
            self.engine.coordinator_verdict(data['project'], data['approved'], data['notes'])
            return {'status': store.project(data['project'])['status']}
        if path == '/claim':
            return {'task': store.claim(identity, 1800)}
        if path in ('/heartbeat', '/handoff'):
            task = data['task']
            if task.get('lease_owner') != identity:
                raise PermissionError('only lease owner may act')
            if path == '/heartbeat':
                usage = data.get("usage_delta")
                status = store.meter(task, **usage) if usage is not None else store.meter(task, calls=0)
                store.heartbeat(task, 1800)
                return {"ok": True, **status}
            # Accept-and-flag reports are not a stage-pass capability.
            body = data['report']
            if not isinstance(body, dict):
                raise ValueError('report must be object')
            store.handoff(task, body)
            return {'accepted': True, 'requires_runtime_verification': True}
        if path == '/status':
            return {'projects': store.projects()}
        return store.audit()

    def server(self, port=0):
        app = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    identity, role = app.authenticate(self.headers.get('Authorization', ''))
                    n = int(self.headers.get('Content-Length', '0'))
                    if not 0 < n <= 1048576:
                        raise ValueError('invalid body length')
                    data = json.loads(self.rfile.read(n))
                    result = app.dispatch(identity, role, self.path, data)
                    status = 200
                except PermissionError as exc:
                    status, result = 403, {'error': str(exc)}
                except LeaseLost as exc:
                    status, result = 409, {'error': str(exc)}
                except Exception as exc:
                    status, result = 400, {'error': str(exc)}
                raw = json.dumps(result).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass
        return ThreadingHTTPServer(('127.0.0.1', port), Handler)
