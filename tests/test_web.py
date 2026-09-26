"""HTTP contract and real-process integration for the web demo."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from handoff_kit.web import DemoServer, run_scenario


class WebTests(unittest.TestCase):
    def setUp(self):
        self.server = DemoServer(('127.0.0.1', 0), runner=lambda: {'status':'DONE'})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, method='GET', body=None, headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=25)
        try:
            conn.request(method,path,body=body,headers=headers or {})
            response=conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def test_assets_and_health(self):
        for path, content in [('/',b'One task. Two workers. One receipt.'),('/style.css',b'--teal'),('/app.js',b'/api/run'),('/healthz',b'"ok"')]:
            status, body, headers = self.request(path)
            self.assertEqual(status,200)
            self.assertIn(content,body)
            self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        self.assertEqual(self.request('/../AGENTS.md')[0],404)

    def test_only_fixed_empty_post_can_execute(self):
        self.assertEqual(self.request('/api/run')[0],404)
        self.assertEqual(self.request('/api/run','POST',body='{"command":"anything"}')[0],400)
        self.assertEqual(self.request('/api/run','POST',headers={'Origin':'https://different.example'})[0],403)
        status,body,_ = self.request('/api/run','POST')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['status'],'DONE')

    def test_busy_capacity_rejects_without_running(self):
        self.server.slots.acquire();self.server.slots.acquire()
        try:
            self.assertEqual(self.request('/api/run','POST')[0],429)
        finally:
            self.server.slots.release();self.server.slots.release()

    def test_failure_releases_capacity_and_hides_internal_details(self):
        def fail(): raise RuntimeError('/private/path/or/internal/detail')
        self.server.runner=fail
        status,body,_=self.request('/api/run','POST')
        self.assertEqual(status,500)
        self.assertNotIn(b'/private',body)
        self.assertTrue(self.server.slots.acquire(False))
        self.assertTrue(self.server.slots.acquire(False))
        self.server.slots.release();self.server.slots.release()

    def test_real_scenario_repeated_isolation(self):
        self.server.runner=run_scenario
        reports=[]
        for _ in range(2):
            status,body,_=self.request('/api/run','POST')
            self.assertEqual(status,200,body)
            report=json.loads(body)
            self.assertEqual(report['status'],'DONE')
            self.assertEqual(report['generation'],2)
            self.assertEqual(report['receipt_count'],1)
            self.assertTrue(report['worker_a_killed'])
            self.assertTrue(report['replay_unchanged'])
            self.assertEqual(len(report['history']),9)
            self.assertEqual(report['completed_steps'],['validate','create_receipt','summarize'])
            reports.append(report)
        self.assertNotEqual(reports[0]['run_id'],reports[1]['run_id'])

    def test_timeout_kills_process_group(self):
        import subprocess
        with patch('handoff_kit.web.subprocess.Popen') as popen, patch('handoff_kit.web.os.killpg') as kill:
            proc=popen.return_value
            proc.pid=123
            proc.communicate.side_effect=[subprocess.TimeoutExpired('demo',20),('','')]
            with self.assertRaises(TimeoutError): run_scenario()
            kill.assert_called_once()
            self.assertEqual(kill.call_args[0][0],123)
