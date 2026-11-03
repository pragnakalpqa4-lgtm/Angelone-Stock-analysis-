"""Supervise one read-only index collector and write automatic daily reports."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from runtime_state import DATA_DIR, REPORT_FILE, is_enabled, write_json

DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault('MARKET_DB', str(DATA_DIR / 'market.db'))
os.environ.setdefault('PAPER_DB', str(DATA_DIR / 'paper.db'))
from collector_service import credentials_available
from index_runtime import runtime_lock
from stream_runtime import MarketStore
HERE = Path(__file__).resolve().parent


class Supervisor:
    def __init__(self, spawn=None, clock=time.time):
        self.spawn = spawn or self._spawn
        self.clock = clock
        self.child = None
        self.started = None
        self.next_retry = 0
        self.failures = 0
        self.restarts = 0
        self.store = MarketStore(os.environ['MARKET_DB'])

    def _spawn(self):
        return subprocess.Popen([sys.executable, str(HERE / 'collector_service.py')], cwd=HERE,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def stop_child(self):
        if self.child is not None:
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait(timeout=5)
            self.child = None
        self.store.health('runtime', 'STOPPED')
        self.store.health('websocket', 'DISCONNECTED')

    def _backoff(self, clock):
        self.failures += 1
        self.next_retry = clock + min(900, 30 * 2 ** min(self.failures - 1, 5))

    def step(self):
        clock = self.clock()
        enabled = is_enabled()
        available = credentials_available()
        if self.child is not None and self.child.poll() is not None:
            self.stop_child()
            self.restarts += 1
            self._backoff(clock)
        if not enabled:
            self.stop_child()
            self.next_retry = 0
            self.failures = 0
            state = 'STOPPED'
        elif not available:
            self.stop_child()
            self.store.health('authentication', 'UNAVAILABLE')
            state = 'BLOCKED_AUTHENTICATION'
        else:
            if self.child is not None and clock - self.started > 120:
                with self.store.connect() as c:
                    row = c.execute("SELECT updated_ts FROM health WHERE component='runtime'").fetchone()
                if row is None or clock - row[0] > 90:
                    self.stop_child()
                    self.restarts += 1
                    self._backoff(clock)
                elif clock - self.started > 300:
                    self.failures = 0
            if self.child is None and clock >= self.next_retry:
                try:
                    self.child = self.spawn()
                    self.started = clock
                except OSError:
                    self._backoff(clock)
            state = 'COLLECTOR_STARTED' if self.child is not None else 'RETRY_WAIT'
        payload = {'checked_at': clock, 'status': state, 'desired_enabled': enabled,
            'scope': ['NIFTY', 'SENSEX'], 'orders_disabled': True,
            'collector_pid': getattr(self.child, 'pid', None), 'restart_count': self.restarts,
            'next_retry_at': self.next_retry or None, 'authentication_configured': available}
        write_json(REPORT_FILE, payload)
        return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Report without starting a collector')
    args = parser.parse_args()
    if args.once:
        from daily_report import generate
        print(generate()['status'])
        return 0
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    from daily_report import write_if_due
    with runtime_lock(DATA_DIR / 'supervisor'):
        supervisor = Supervisor()
        try:
            while not stopping:
                supervisor.step()
                write_if_due()
                time.sleep(1)
        finally:
            supervisor.stop_child()
            write_json(REPORT_FILE, {'checked_at': time.time(), 'status': 'SUPERVISOR_STOPPED',
                'scope': ['NIFTY', 'SENSEX'], 'orders_disabled': True})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
