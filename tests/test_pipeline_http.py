import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import app
import runtime_state


class PipelineHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.getcwd())
        folder = Path(self.tmp.name)
        self.patches = [patch.object(app, 'DB', str(folder/'paper.db')),
            patch.object(app, 'MARKET_DB', str(folder/'market.db')),
            patch.object(app, 'REPORT_FILE', folder/'worker.json'),
            patch.object(runtime_state, 'CONTROL_FILE', folder/'control.json'),
            patch.dict(os.environ, {'DASHBOARD_USER': 'operator', 'DASHBOARD_PASSWORD': ''})]
        for item in self.patches: item.start()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()
        for item in reversed(self.patches): item.stop()
        self.tmp.cleanup()

    def request(self, path, method='GET', headers=None):
        with self.opener.open(urllib.request.Request(self.url+path, method=method, headers=headers or {}), timeout=3) as response:
            return json.load(response)

    def test_http_control_persists_and_status_distinguishes_desired_from_actual(self):
        self.request('/api/pipeline/stop', 'POST')
        self.assertFalse(runtime_state.is_enabled())
        result = self.request('/api/status')
        self.assertFalse(result['pipeline']['desired_enabled'])
        self.assertFalse(result['market']['fresh'])
        self.request('/api/pipeline/start', 'POST')
        result = self.request('/api/status')
        self.assertTrue(result['pipeline']['desired_enabled'])
        self.assertEqual(result['pipeline']['status'], 'SUPERVISOR_UNAVAILABLE')
        self.assertTrue(result['orders_disabled'])

    def test_cross_origin_control_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/pipeline/stop', 'POST', {'Origin': 'https://unrelated.example'})
        self.assertEqual(caught.exception.code, 403)
        self.assertTrue(runtime_state.is_enabled())

    def test_authentication_is_required_on_status_and_control_when_configured(self):
        with patch.dict(os.environ, {'DASHBOARD_PASSWORD': 'fixture-password'}):
            for path, method in [('/api/status', 'GET'), ('/api/pipeline/stop', 'POST')]:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request(path, method)
                self.assertEqual(caught.exception.code, 401)
            headers = {'Authorization': 'Basic ' + base64.b64encode(b'operator:fixture-password').decode()}
            self.assertTrue(self.request('/api/status', headers=headers)['orders_disabled'])


if __name__ == '__main__':
    unittest.main()
