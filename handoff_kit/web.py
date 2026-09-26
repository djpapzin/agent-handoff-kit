"""Bounded HTTP wrapper for the fixed, verified synthetic recovery demo."""
import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}


def run_scenario(timeout=20):
    """Run unchanged CLI scenario in a fresh process group and disposable DB."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='handoff-web-') as directory:
        db = str(Path(directory) / 'demo.db')
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / 'demo.py'), '--db', db],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise TimeoutError('The demo exceeded its time limit. Please try again.')
        if proc.returncode != 0:
            raise RuntimeError('The demo did not finish successfully. Please try again.')
        # The subprocess verifies real process death, single receipt, and replay.
        if 'PASS: real worker kill, expired-lease recovery, single receipt, read-only replay' not in stdout:
            raise RuntimeError('The demo did not produce verified completion evidence.')
        conn = sqlite3.connect(Path(db).as_uri() + '?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('BEGIN')
            job = dict(conn.execute('SELECT * FROM jobs').fetchone())
            receipts = [dict(r) for r in conn.execute('SELECT * FROM receipts')]
            history = [dict(r) for r in conn.execute('SELECT * FROM history ORDER BY seq')]
        finally:
            conn.close()
        if job['status'] != 'DONE' or len(receipts) != 1:
            raise RuntimeError('Final state failed verification.')
        claims = [r for r in history if r['event'] == 'claim']
        if len(claims) != 2 or claims[1]['generation'] != claims[0]['generation'] + 1:
            raise RuntimeError('Worker handoff failed verification.')
        return {
            'run_id': uuid.uuid4().hex[:12], 'status': job['status'],
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'receipt_count': len(receipts), 'generation': job['generation'],
            'replay_unchanged': True, 'worker_a_killed': True,
            'completed_steps': json.loads(job['checkpoint_json'])['completed_steps'],
            'receipt': json.loads(receipts[0]['result_json']),
            'history': [{k: r[k] for k in ('seq','old_status','new_status','generation','event','step')} for r in history],
        }


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, runner=run_scenario):
        super().__init__(address, Handler)
        self.runner = runner
        self.slots = threading.BoundedSemaphore(2)


class Handler(BaseHTTPRequestHandler):
    server_version = 'HandoffDemo/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(25)

    def reply(self, code, body, kind='application/json; charset=utf-8'):
        payload = json.dumps(body).encode() if isinstance(body, dict) else body
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/healthz':
            return self.reply(200, {'status': 'ok'})
        asset = ASSETS.get(path)
        if not asset:
            return self.reply(404, {'error': 'Not found'})
        name, mime = asset
        self.reply(200, (ROOT / 'web' / name).read_bytes(), mime)

    def do_POST(self):
        if self.path != '/api/run':
            return self.reply(404, {'error': 'Not found'})
        # No arbitrary commands, paths, task data, or URLs are accepted.
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            return self.reply(403, {'error': 'Please run the demo from this page.'})
        if self.headers.get('Transfer-Encoding'):
            return self.reply(400, {'error': 'Request bodies are not supported.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.reply(400, {'error': 'Invalid request length.'})
        if length != 0:
            return self.reply(400, {'error': 'This demo uses a fixed synthetic fixture; send an empty request.'})
        if not self.server.slots.acquire(blocking=False):
            return self.reply(429, {'error': 'Both demo slots are busy. Try again in a few seconds.'})
        try:
            self.reply(200, self.server.runner())
        except TimeoutError:
            self.reply(504, {'error': 'The run timed out. Please try again.'})
        except Exception:
            self.reply(500, {'error': 'The run could not be verified. Please try again.'})
        finally:
            self.server.slots.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8080')))
    args = parser.parse_args()
    with DemoServer((args.host, args.port)) as server:
        print('Agent Handoff Kit: http://{}:{}'.format(*server.server_address), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
