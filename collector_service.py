"""Long-running dual-index process, owned and stopped by worker.py."""
import os
from runtime_state import DATA_DIR, is_enabled

DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault('MARKET_DB', str(DATA_DIR / 'market.db'))

from index_runtime import Collector, discover, runtime_lock
from mcx_diagnostic import collect, private_session
from stream_runtime import MarketStore


def credentials_available():
    from secure_credentials import STORE
    groups = (
        ('ANGELONE_API_KEY', 'ANGELONE_CLIENT_CODE', 'ANGELONE_PIN', 'ANGELONE_TOTP_SECRET'),
        ('ANGEL_API_KEY', 'ANGEL_CLIENT_ID', 'ANGEL_PIN', 'ANGEL_TOTP_SECRET'),
    )
    selected = groups[0] if any(os.getenv(n) for n in groups[0]) else groups[1]
    return all(os.getenv(n) for n in selected) or (os.name == 'nt' and os.path.isfile(STORE))


def main():
    store = MarketStore(os.environ['MARKET_DB'])
    try:
        with runtime_lock(store.path):
            while is_enabled():
                store.health('runtime', 'STARTING')
                store.health('websocket', 'DISCONNECTED')
                if not credentials_available():
                    store.health('authentication', 'UNAVAILABLE', 'Configure protected Angel One authentication')
                    return 2
                metadata = discover(DATA_DIR / 'verified_index_instruments.json')
                for record in metadata['records']:
                    store.instrument(record)
                headers = private_session()
                store.health('authentication', 'AUTHENTICATED')
                collect(Collector(store, metadata['records']), headers, 3600, 0)
                del headers
    except Exception as exc:
        store.health('collector_error', 'ERROR', type(exc).__name__)
        return 3
    finally:
        store.health('runtime', 'STOPPED')
        store.health('websocket', 'DISCONNECTED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
