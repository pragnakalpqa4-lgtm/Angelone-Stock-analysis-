"""Interactive connection diagnostic. No credentials, tokens or prices are saved.

Run directly in a private terminal, not through an agent-controlled console.
This diagnostic cannot place broker orders.
"""
import getpass, json, os, sys, urllib.error, urllib.request

LOGIN='https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword'
QUOTE='https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/'

def post(url, data, headers, opener=urllib.request.urlopen):
    if url not in (LOGIN, QUOTE): raise ValueError('Endpoint is not allowed')
    request=urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method='POST')
    with opener(request,timeout=10) as response: return json.load(response)

def authenticate_session(key, client, pin, totp, mac, local_ip, public_ip, opener=urllib.request.urlopen):
    h={'Content-Type':'application/json','Accept':'application/json','X-PrivateKey':key,'X-UserType':'USER','X-SourceID':'WEB','X-MACAddress':mac,'X-ClientLocalIP':local_ip,'X-ClientPublicIP':public_ip}
    result=post(LOGIN,{'clientcode':client,'password':pin,'totp':totp},h,opener)
    data=result.get('data') or {}
    if result.get('status') is not True or not all(data.get(k) for k in ('jwtToken','feedToken')): raise RuntimeError('Authentication failed; check details and broker access')
    h['Authorization']='Bearer '+data['jwtToken']
    return h,data

def authenticate(key, client, pin, totp, mac, local_ip, public_ip, opener=urllib.request.urlopen):
    return authenticate_session(key,client,pin,totp,mac,local_ip,public_ip,opener)[0]

def quote(headers, exchange, token, opener=urllib.request.urlopen):
    result=post(QUOTE,{'mode':'FULL','exchangeTokens':{exchange:[token]}},headers,opener)
    data=result.get('data') or {}
    if result.get('status') is not True or data.get('unfetched') or not data.get('fetched'): raise RuntimeError('Quote request failed or returned incomplete data')
    return data['fetched']

def main():
    print('PAPER ONLY: broker connection diagnostic. No orders will be sent.')
    print('Run this yourself in a private terminal. Nothing is saved; network access is required.')
    key=getpass.getpass('SmartAPI API key: '); client=getpass.getpass('Client ID: ')
    pin=getpass.getpass('Angel One PIN: '); totp=getpass.getpass('Authenticator TOTP: ')
    mac=input('Device MAC address: ').strip(); local=input('Device local IP address: ').strip(); public=input('Public IP address: ').strip()
    try:
        headers=authenticate(key,client,pin,totp,mac,local,public)
        print('Authentication succeeded. Session credentials remain in memory only.')
        print('A quote check requires an exchange and a token independently verified in Angel One instrument master.')
        exchange=input('Exchange (or Enter to skip): ').strip().upper()
        if exchange:
            token=input('Verified instrument token: ').strip()
            rows=quote(headers,exchange,token)
            print('Broker returned',len(rows),'quote(s). Freshness, entitlement and tradability are not yet validated.')
    except (RuntimeError,urllib.error.URLError,TimeoutError,ValueError,KeyError):
        print('Connection check failed. Verify access, network and inputs without sharing credentials in chat.',file=sys.stderr)
        return 1
    finally:
        key=client=pin=totp=''
    return 0
if __name__=='__main__': sys.exit(main())
