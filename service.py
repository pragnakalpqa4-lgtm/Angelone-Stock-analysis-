"""Run the dashboard and collector supervisor as one restartable service."""
import argparse
import os
import signal
import time
from runtime_state import DATA_DIR, REPORT_FILE, write_json
import worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8081)
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not os.getenv('DASHBOARD_PASSWORD'):
        parser.error('Non-loopback binding requires DASHBOARD_PASSWORD and a private TLS proxy')
    import app
    from daily_report import generate, write_if_due
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with worker.runtime_lock(DATA_DIR / 'supervisor'):
        server = app.ThreadingHTTPServer((args.host, args.port), app.Handler)
        server.timeout = .5
        supervisor = worker.Supervisor()
        last_error = 0
        next_reporting_attempt = 0
        try:
            supervisor.step()
            generate()
            while not stopping:
                server.handle_request()
                supervisor.step()
                if time.time() >= next_reporting_attempt:
                    try:
                        write_if_due()
                    except Exception as exc:
                        supervisor.store.health('reporting', 'ERROR', type(exc).__name__)
                        next_reporting_attempt = time.time() + 60
        finally:
            supervisor.stop_child()
            server.server_close()
            write_json(REPORT_FILE, {'checked_at': time.time(), 'status': 'SUPERVISOR_STOPPED',
                'scope': ['NIFTY', 'SENSEX'], 'orders_disabled': True})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
