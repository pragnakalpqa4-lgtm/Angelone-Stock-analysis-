"""Retrieve the deployed daily report; never label an ephemeral runner live."""
import base64
from datetime import datetime
import json
import os
import urllib.request
from urllib.parse import urlsplit
from runtime_state import DATA_DIR, write_json
from live_market_check import IST


def fetch_report():
    report = {'checked_at_ist': datetime.now(IST).isoformat(), 'scope': ['NIFTY', 'SENSEX'],
        'status': 'DEPLOYMENT_NOT_CONFIGURED', 'orders_disabled': True,
        'reason': 'Configure ANGELONE_REPORT_URL for the continuously running private service.'}
    url = os.getenv('ANGELONE_REPORT_URL')
    if url:
        try:
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.username or parsed.password or not parsed.hostname:
                raise ValueError('HTTPS URL required')
            headers = {'Accept': 'application/json'}
            password = os.getenv('DASHBOARD_PASSWORD')
            if password:
                credentials = os.getenv('DASHBOARD_USER') or 'operator'
                headers['Authorization'] = 'Basic ' + base64.b64encode((credentials+':'+password).encode()).decode()
            # Refuse redirects to avoid forwarding authentication to another host.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            opener = urllib.request.build_opener(NoRedirect())
            with opener.open(urllib.request.Request(url, headers=headers), timeout=20) as response:
                incoming = json.loads(response.read(1024 * 1024))
            if not isinstance(incoming, dict) or incoming.get('scope') != ['NIFTY', 'SENSEX'] or incoming.get('orders_disabled') is not True:
                raise ValueError('Wrong report scope')
            dated = datetime.fromisoformat(incoming['checked_at_ist']).astimezone(IST)
            if dated.date() != datetime.now(IST).date():
                raise ValueError('Daily report is stale')
            report = incoming
        except Exception as exc:
            report.update(status='DEPLOYED_REPORT_UNAVAILABLE', reason=type(exc).__name__)
    folder = DATA_DIR / 'reports'
    write_json(folder / 'latest.json', report)
    markdown = '# NIFTY/SENSEX daily status\n\nStatus: **' + report['status'] + '**\n\n'
    markdown += 'Source: deployed report endpoint when configured; GitHub Actions is not the continuous worker.\n\n'
    markdown += '```json\n' + json.dumps(report, indent=2) + '\n```\n'
    (folder / 'latest.md').write_text(markdown, encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
            output.write(markdown)
    return report


if __name__ == '__main__':
    print(fetch_report()['status'])
