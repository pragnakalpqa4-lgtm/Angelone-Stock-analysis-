import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest.mock import patch

import app
import daily_report
import runtime_state
import worker
import workflow_report
from live_market_check import IST
from stream_runtime import MarketStore


class SupervisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.getcwd())
        self.folder = Path(self.temp.name)
        self.clock = datetime(2026, 11, 3, 16, 0, tzinfo=IST)
        self.patches = [
            patch.dict(os.environ, {'MARKET_DB': str(self.folder/'market.db'), 'PAPER_DB': str(self.folder/'paper.db')}),
            patch.object(runtime_state, 'CONTROL_FILE', self.folder/'control.json'),
            patch.object(worker, 'REPORT_FILE', self.folder/'latest_report.json'),
            patch.object(daily_report, 'REPORT_FILE', self.folder/'latest_report.json'),
            patch.object(daily_report, 'DATA_DIR', self.folder),
            patch.object(workflow_report, 'DATA_DIR', self.folder),
            patch.object(app, 'DB', str(self.folder/'paper.db')),
            patch.object(app, 'MARKET_DB', str(self.folder/'market.db')),
        ]
        for item in self.patches: item.start()
        self.store = MarketStore(str(self.folder/'market.db'))
        self.supervisor = None

    def tearDown(self):
        if self.supervisor is not None: self.supervisor.stop_child()
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def make_supervisor(self):
        def spawn():
            return subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.supervisor = worker.Supervisor(spawn=spawn, clock=lambda: self.clock.timestamp())
        return self.supervisor

    def test_missing_auth_does_not_spawn_or_claim_running(self):
        with patch('worker.credentials_available', return_value=False):
            supervisor = self.make_supervisor()
            result = supervisor.step()
            self.assertIsNone(supervisor.child)
            self.assertEqual(result['status'], 'BLOCKED_AUTHENTICATION')

    def test_stop_terminates_actual_process_and_restart_obeys_control(self):
        with patch('worker.credentials_available', return_value=True):
            supervisor = self.make_supervisor()
            supervisor.step()
            child = supervisor.child
            self.assertIsNone(child.poll())
            runtime_state.set_enabled(False)
            self.assertEqual(supervisor.step()['status'], 'STOPPED')
            self.assertIsNotNone(child.poll())
            runtime_state.set_enabled(True)
            supervisor.step()
            self.assertIsNotNone(supervisor.child)
            self.assertNotEqual(supervisor.child.pid, child.pid)

    def test_crash_backs_off_then_restarts(self):
        with patch('worker.credentials_available', return_value=True):
            supervisor = self.make_supervisor()
            supervisor.step()
            supervisor.child.kill(); supervisor.child.wait()
            self.assertEqual(supervisor.step()['status'], 'RETRY_WAIT')
            self.assertEqual(supervisor.restarts, 1)
            from datetime import timedelta
            self.clock += timedelta(seconds=31)
            self.assertEqual(supervisor.step()['status'], 'COLLECTOR_STARTED')

    def test_corrupt_control_fails_closed(self):
        runtime_state.CONTROL_FILE.write_text('{broken', encoding='utf-8')
        self.assertFalse(runtime_state.is_enabled())

    def test_hung_collector_is_reaped(self):
        with patch('worker.credentials_available', return_value=True):
            supervisor = self.make_supervisor()
            supervisor.step()
            child = supervisor.child
            supervisor.started -= 121
            self.assertEqual(supervisor.step()['status'], 'RETRY_WAIT')
            self.assertIsNotNone(child.poll())

    def test_empty_database_creates_dated_blocked_report(self):
        report = daily_report.generate(self.clock)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['indices']['SENSEX']['ticks_today'], 0)
        self.assertTrue((self.folder/'reports'/'2026-11-03.md').exists())
        self.assertIn('SUPERVISOR_NOT_VERIFIED', report['errors'])

    def test_both_indices_and_reconnect_epoch_required_for_report_freshness(self):
        stamp = self.clock.timestamp()
        runtime_state.write_json(self.folder/'latest_report.json', {'checked_at': stamp, 'status': 'COLLECTOR_STARTED'})
        with self.store.connect() as c:
            for key, state, updated in [('runtime','RUNNING',stamp),('websocket','CONNECTED',stamp-5)]:
                c.execute('INSERT OR REPLACE INTO health VALUES(?,?,?,?)',(key,state,updated,''))
            for token in ('99926000','99919000'):
                c.execute('INSERT INTO ticks VALUES(?,?,?,?,?,?,?)',(token,stamp-1,1,stamp-1,23000,0,0))
        self.assertEqual(daily_report.generate(self.clock)['status'], 'LIVE_OBSERVATIONS')
        with self.store.connect() as c:
            c.execute("UPDATE health SET updated_ts=? WHERE component='websocket'",(stamp,))
        report = daily_report.generate(self.clock)
        self.assertNotEqual(report['status'], 'LIVE_OBSERVATIONS')
        self.assertFalse(report['indices']['NIFTY']['feed_fresh'])
        runtime_state.set_enabled(False)
        stopped = daily_report.generate(self.clock)
        self.assertEqual(stopped['status'], 'STOPPED')
        self.assertFalse(stopped['indices']['NIFTY']['feed_fresh'])

    def test_daily_backup_is_consistent_and_due_once(self):
        with self.store.connect() as c:
            c.execute('INSERT INTO ticks VALUES(?,?,?,?,?,?,?)',('99926000',self.clock.timestamp()-1,1,self.clock.timestamp(),23000,0,0))
        self.assertTrue(daily_report.write_if_due(self.clock))
        self.assertFalse(daily_report.write_if_due(self.clock))
        backup = MarketStore(str(self.folder/'backups'/'2026-11-03.db'))
        with backup.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ticks').fetchone()[0], 1)

    def test_scheduled_report_explains_missing_deployment(self):
        with patch.dict(os.environ, {'ANGELONE_REPORT_URL': ''}):
            report = workflow_report.fetch_report()
        self.assertEqual(report['status'], 'DEPLOYMENT_NOT_CONFIGURED')
        self.assertEqual(report['scope'], ['NIFTY', 'SENSEX'])

    def test_scheduled_report_sanitizes_errors_and_requires_https(self):
        with patch.dict(os.environ, {'ANGELONE_REPORT_URL': 'http://example.com/private-token'}):
            report = workflow_report.fetch_report()
        self.assertEqual(report['status'], 'DEPLOYED_REPORT_UNAVAILABLE')
        self.assertNotIn('private-token', json.dumps(report))


if __name__ == '__main__':
    unittest.main()
