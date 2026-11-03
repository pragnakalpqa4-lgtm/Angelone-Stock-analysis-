"""Explicit private-console, one-session diagnostic. No credentials are saved.

Launch in a separate user-owned Windows console, never an agent-observed terminal.
The normal index_runtime CLI remains strictly noninteractive.
"""
import getpass
import os
import sys
import warnings
import argparse

from connection_check import authenticate_session
from live_market_check import network_details
from index_runtime import run
from secure_credentials import STORE,load


def hidden(prompt):
    # Refuse getpass's echoing fallback if a private console is unavailable.
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        value=getpass.getpass(prompt).strip()
    if not value: raise ValueError('required private input unavailable')
    return value


def one_session():
    if not sys.stdin.isatty(): raise RuntimeError('private console required')
    print('Private Angel One sign-in: input is hidden. Nothing entered here is saved.')
    print('Use your current six-digit authenticator code, not the TOTP setup secret.')
    print('All real and simulated orders remain disabled.')
    saved=load() if os.path.isfile(STORE) else {}
    key=client=pin=code=''
    try:
        key=saved.get('api_key') or hidden('SmartAPI API key (hidden): ')
        client=saved.get('client_id') or hidden('Client ID (hidden): ')
        pin=saved.get('pin') or hidden('Angel One PIN (hidden): ')
        # Resolve network details first so the short-lived code is entered last.
        mac,local,public=network_details()
        code=hidden('Current authenticator code (hidden): ')
        if len(code)!=6 or not code.isascii() or not code.isdigit(): raise ValueError('six-digit code required')
        headers,session=authenticate_session(key,client,pin,code,mac,local,public)
        print('Authentication succeeded. Starting the bounded read-only feed.')
        print('Dashboard: http://127.0.0.1:8081/ (refreshes every two seconds).')
        return {'Authorization':session['jwtToken'],'x-api-key':headers['X-PrivateKey'],
                'x-client-code':client,'x-feed-token':session['feedToken']}
    finally:
        saved.clear(); key=client=pin=code=''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=180)
    args=parser.parse_args()
    if not 31<=args.seconds<=3600: parser.error('seconds must be 31..3600')
    if not sys.stdin.isatty():
        print('Launch this utility in its separate private Windows console.'); return 2
    print('Read-only NIFTY and SENSEX monitor; stops after',args.seconds,'seconds.')
    print('Downloading the current instrument master before private sign-in...')
    try:
        result=run(seconds=args.seconds,disconnect_after=30,session_factory=one_session)
        print('Diagnostic outcome:',result['live_result'])
        print('Actual new live ticks:',result.get('statistics',{}).get('inserted',0))
        print('Report saved as index_diagnostic_report.json. No credentials are included.')
        if result.get('failure_type'): print('Failure category:',result['failure_type'])
        if result.get('reason'): print('Reason:',result['reason'])
        status=0 if result['live_result']=='VERIFIED' else 2
    except (KeyboardInterrupt,EOFError):
        print('Private diagnostic cancelled.'); status=2
    except Exception as exc:
        print('Private diagnostic stopped:',type(exc).__name__); status=2
    try: input('Press Enter to close this private window...')
    except (EOFError,KeyboardInterrupt): pass
    return status


if __name__=='__main__': raise SystemExit(main())
