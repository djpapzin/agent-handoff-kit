"""Check the managed-host entry point with WSGI validation and real workers."""
import json
import unittest
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

from handoff_kit.wsgi import application


class WSGITests(unittest.TestCase):
    def request(self, path='/', method='GET', **extra):
        env = {}
        setup_testing_defaults(env)
        env.update(PATH_INFO=path, REQUEST_METHOD=method, QUERY_STRING='',
                   HTTP_HOST='demo.pythonanywhere.com', **extra)
        captured = []
        result = validator(application)(env, lambda status, headers: captured.append((status, headers)))
        try:
            body = b''.join(result)
        finally:
            result.close()
        return int(captured[0][0].split()[0]), body

    def test_assets_health_and_methods(self):
        for path in ('/', '/app.js', '/style.css', '/healthz'):
            self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.request('/../AGENTS.md')[0], 404)
        self.assertEqual(self.request('/', 'DELETE')[0], 405)

    def test_input_rejection_never_starts_worker(self):
        with patch('handoff_kit.wsgi.run_scenario') as run:
            for headers, expected in [({'CONTENT_LENGTH':'1'},400),
                ({'HTTP_TRANSFER_ENCODING':'chunked'},400),
                ({'HTTP_ORIGIN':'https://other.example'},403)]:
                self.assertEqual(self.request('/api/run','POST',**headers)[0],expected)
            run.assert_not_called()

    def test_failure_is_redacted_and_capacity_released(self):
        with patch('handoff_kit.wsgi.run_scenario',side_effect=RuntimeError('/private')):
            status, body = self.request('/api/run','POST')
            self.assertEqual(status,500)
            self.assertNotIn(b'/private',body)
        with patch('handoff_kit.wsgi.run_scenario',return_value={'status':'DONE'}):
            self.assertEqual(self.request('/api/run','POST')[0],200)

    def test_real_recovery_through_wsgi(self):
        status, body = self.request('/api/run','POST',HTTP_ORIGIN='https://demo.pythonanywhere.com')
        self.assertEqual(status,200,body)
        result=json.loads(body)
        self.assertEqual((result['status'],result['generation'],result['receipt_count']),('DONE',2,1))
        self.assertTrue(result['worker_a_killed'])
        self.assertTrue(result['replay_unchanged'])
        self.assertEqual(len(result['history']),9)
