"""Serve the actual NIFTY/SENSEX dashboard and pipeline control API."""
import argparse
import os
from runtime_state import DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8081)
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not os.getenv('DASHBOARD_PASSWORD'):
        parser.error('Non-loopback listening requires DASHBOARD_PASSWORD and a private TLS proxy')
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('MARKET_DB', str(DATA_DIR / 'market.db'))
    os.environ.setdefault('PAPER_DB', str(DATA_DIR / 'paper.db'))
    import app
    app.ThreadingHTTPServer((args.host, args.port), app.Handler).serve_forever()


if __name__ == '__main__':
    main()
