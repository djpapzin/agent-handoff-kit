"""Dependency-free WSGI entry point for managed Python hosting.

All child processes finish within the request; no background service is needed.
"""
import json
import threading
from http import HTTPStatus
from urllib.parse import urlsplit

from .web import ASSETS, ROOT, run_scenario

_slots = threading.BoundedSemaphore(2)


def application(environ, start_response):
    def reply(code, body, kind='application/json; charset=utf-8'):
        payload = json.dumps(body).encode() if isinstance(body, dict) else body
        start_response('{} {}'.format(code, HTTPStatus(code).phrase), [
            ('Content-Type', kind), ('Content-Length', str(len(payload))),
            ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
            ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"),
        ])
        return [payload]

    path = environ.get('PATH_INFO', '/')
    method = environ.get('REQUEST_METHOD', 'GET')
    if method == 'GET':
        if path == '/healthz':
            return reply(200, {'status': 'ok'})
        asset = ASSETS.get(path)
        if asset:
            name, kind = asset
            return reply(200, (ROOT / 'web' / name).read_bytes(), kind)
        return reply(404, {'error': 'Not found'})
    if method != 'POST':
        return reply(405, {'error': 'Method not allowed'})
    if path != '/api/run' or environ.get('QUERY_STRING'):
        return reply(404, {'error': 'Not found'})
    origin = environ.get('HTTP_ORIGIN')
    if origin and urlsplit(origin).netloc != environ.get('HTTP_HOST'):
        return reply(403, {'error': 'Please run the demo from this page.'})
    if environ.get('HTTP_TRANSFER_ENCODING') or environ.get('wsgi.input_terminated'):
        return reply(400, {'error': 'Request bodies are not supported.'})
    try:
        length = int(environ.get('CONTENT_LENGTH') or '0')
    except ValueError:
        return reply(400, {'error': 'Invalid request length.'})
    if length != 0:
        return reply(400, {'error': 'This demo uses a fixed synthetic fixture; send an empty request.'})
    if not _slots.acquire(blocking=False):
        return reply(429, {'error': 'Both demo slots are busy. Try again in a few seconds.'})
    try:
        result = run_scenario()
    except TimeoutError:
        return reply(504, {'error': 'The run timed out. Please try again.'})
    except Exception:
        return reply(500, {'error': 'The run could not be verified. Please try again.'})
    finally:
        _slots.release()
    return reply(200, result)
