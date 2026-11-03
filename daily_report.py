"""Dated reports remain useful when broker access or feeds are blocked."""
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, time
from runtime_state import DATA_DIR, REPORT_FILE, is_enabled, read_json, write_json
from live_market_check import IST
from market_scope import INDEX_SCOPE


def generate(clock=None):
    clock = (clock or datetime.now(IST)).astimezone(IST)
    start = datetime.combine(clock.date(), time.min, IST).timestamp()
    end = start + 86400
    worker = read_json(REPORT_FILE, {})
    timestamp = clock.timestamp()
    heartbeat_fresh = isinstance(worker.get('checked_at'), (int, float)) and 0 <= timestamp - worker['checked_at'] <= 10
    report = {'checked_at_ist': clock.isoformat(), 'scope': ['NIFTY', 'SENSEX'],
        'orders_disabled': True, 'desired_enabled': is_enabled(),
        'supervisor_status': worker.get('status') if heartbeat_fresh else 'UNAVAILABLE',
        'supervisor_heartbeat_fresh': heartbeat_fresh, 'status': 'BLOCKED', 'indices': {}, 'errors': [],
        'note': 'Observed broker data only. Report creation does not prove message delivery or cloud deployment.'}
    path = Path(os.getenv('MARKET_DB', str(DATA_DIR / 'market.db')))
    db = None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        db.execute('BEGIN')
        report['database_integrity'] = db.execute('PRAGMA quick_check').fetchone()[0]
        health = {r[0]: {'status': r[1], 'updated_ts': r[2]} for r in db.execute('SELECT component,status,updated_ts FROM health')}
        active = health.get('runtime', {}).get('status') == 'RUNNING' and health.get('websocket', {}).get('status') == 'CONNECTED'
        active = active and 0 <= timestamp - health.get('runtime', {}).get('updated_ts', 0) <= 10
        for record in INDEX_SCOPE:
            token = record['token']
            ticks = db.execute('SELECT COUNT(*) FROM ticks WHERE token=? AND exchange_ts>=? AND exchange_ts<?', (token, start, end)).fetchone()[0]
            candles = db.execute('SELECT COUNT(*) FROM candles WHERE token=? AND minute>=? AND minute+60<=?', (token, start, min(end, timestamp))).fetchone()[0]
            latest = db.execute('SELECT exchange_ts,received_ts,price FROM ticks WHERE token=? ORDER BY exchange_ts DESC,sequence DESC LIMIT 1', (token,)).fetchone()
            fresh = bool(report['desired_enabled'] and active and latest and latest[1] >= health.get('websocket', {}).get('updated_ts', 0)
                and -2 <= timestamp - latest[0] <= 10 and 0 <= timestamp - latest[1] <= 10)
            report['indices'][record['name']] = {'ticks_today': ticks, 'closed_observed_candles_today': candles,
                'feed_fresh': fresh, 'latest_exchange_ts': latest[0] if latest else None,
                'latest_observed_price': latest[2] if latest else None}
        report['authentication'] = health.get('authentication', {}).get('status', 'UNAVAILABLE')
        if report['database_integrity'] != 'ok':
            report['errors'].append('DATABASE_INTEGRITY_FAILED')
        if not is_enabled():
            report['status'] = 'STOPPED'
        elif report['database_integrity'] == 'ok' and heartbeat_fresh and all(i['feed_fresh'] for i in report['indices'].values()):
            report['status'] = 'LIVE_OBSERVATIONS'
        elif report['database_integrity'] == 'ok' and all(i['ticks_today'] for i in report['indices'].values()):
            report['status'] = 'OBSERVATIONS_RECORDED_FEEDS_NOT_CURRENTLY_VERIFIED'
    except (sqlite3.Error, OSError):
        report['errors'].append('DATABASE_UNAVAILABLE')
    finally:
        if db is not None:
            db.close()
    if not report['supervisor_heartbeat_fresh']:
        report['errors'].append('SUPERVISOR_NOT_VERIFIED')
    reports = DATA_DIR / 'reports'
    write_json(reports / (clock.date().isoformat() + '.json'), report)
    write_json(reports / 'latest.json', report)
    lines = [f"# NIFTY/SENSEX daily report — {clock.date()}", '',
        f"Checked: {clock.isoformat()}", f"Status: **{report['status']}**", '',
        '| Index | Ticks today | Closed observed candles | Feed fresh |', '|---|---:|---:|---|']
    for name in ('NIFTY', 'SENSEX'):
        item = report['indices'].get(name, {})
        lines.append(f"| {name} | {item.get('ticks_today', 0)} | {item.get('closed_observed_candles_today', 0)} | {item.get('feed_fresh', False)} |")
    lines += ['', 'Errors: ' + (', '.join(report['errors']) or 'None recorded'), '',
        'All orders disabled. Intraday continuity, exchange calendar and actual analysis readiness require separate verification.']
    markdown = '\n'.join(lines) + '\n'
    (reports / (clock.date().isoformat() + '.md')).write_text(markdown, encoding='utf-8')
    (reports / 'latest.md').write_text(markdown, encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
            output.write(markdown)
    return report


def write_if_due(clock=None):
    clock = (clock or datetime.now(IST)).astimezone(IST)
    if clock.time() < time(15, 40):
        return False
    marker = DATA_DIR / 'reports' / (clock.date().isoformat() + '.complete')
    if marker.exists():
        return False
    generate(clock)
    # SQLite's backup API includes committed WAL data consistently.
    source_path = Path(os.getenv('MARKET_DB', str(DATA_DIR / 'market.db')))
    if source_path.exists():
        backup_dir = DATA_DIR / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(source_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        destination = sqlite3.connect(backup_dir / (clock.date().isoformat() + '.db'))
        try:
            source.backup(destination)
            if destination.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('BACKUP_VERIFICATION_FAILED')
        finally:
            destination.close()
            source.close()
    marker.write_text(clock.isoformat(), encoding='utf-8')
    return True


if __name__ == '__main__':
    print(json.dumps(generate(), indent=2))
