"""Authenticated quote diagnostic with verified master contracts; never places orders."""
import getpass,json,os,socket,sys,urllib.request,uuid
from datetime import datetime,time,timezone,timedelta
from connection_check import authenticate,quote
from secure_credentials import STORE as CREDENTIAL_STORE,load as load_credentials,totp as make_totp

HERE=os.path.dirname(__file__)
MASTER=os.path.join(HERE,'verified_instruments.json')
REPORT=os.path.join(HERE,'live_check_report.json')
IST=timezone(timedelta(hours=5,minutes=30),'IST')

def network_details():
    mac='-'.join(f'{(uuid.getnode() >> i) & 0xff:02X}' for i in range(40,-1,-8))
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try: s.connect(('8.8.8.8',80)); local=s.getsockname()[0]
    finally: s.close()
    with urllib.request.urlopen('https://api.ipify.org',timeout=10) as r: public=r.read().decode().strip()
    return mac,local,public

def parse_date(s): return datetime.strptime(s,'%d%b%Y').date()
def strike_rupees(row): return float(row['strike'])/100.0

def active_expiry(rows,name,clock):
    cutoff=time(15,15)
    dates=sorted({parse_date(r['expiry']) for r in rows if r['name'].upper()==name and r['instrumenttype'].upper()=='OPTIDX' and r['expiry']})
    valid=[d for d in dates if d>clock.date() or (d==clock.date() and clock.time()<cutoff)]
    if not valid: raise RuntimeError('no unexpired contract')
    return valid[0]

def index_record(rows,name):
    candidates=[r for r in rows if r['name'].upper()==name and r['instrumenttype'].upper()=='AMXIDX']
    if len(candidates)!=1: raise RuntimeError('index record is missing or ambiguous')
    return candidates[0]

def option_pair(rows,name,expiry,spot):
    expiry_text=expiry.strftime('%d%b%Y').upper()
    candidates=[r for r in rows if r['name'].upper()==name and r['instrumenttype'].upper()=='OPTIDX' and r['expiry'].upper()==expiry_text]
    by_strike={}
    for r in candidates:
        symbol=r['symbol'].upper(); kind='CE' if symbol.endswith('CE') else 'PE' if symbol.endswith('PE') else None
        if kind: by_strike.setdefault(strike_rupees(r),{})[kind]=r
    complete=[s for s,pair in by_strike.items() if set(pair)=={'CE','PE'}]
    if not complete: raise RuntimeError('no complete CE/PE pair')
    strike=min(complete,key=lambda s:(abs(s-spot),s))
    return by_strike[strike]['CE'],by_strike[strike]['PE'],strike

def field(row,*names):
    for n in names:
        if row.get(n) not in (None,''): return row[n]
    return None

def sanitized_quote(row):
    return {k:field(row,*v) for k,v in {
        'exchange':('exchange',),'symbol':('tradingSymbol','tradingsymbol'),'token':('symbolToken','symboltoken'),
        'ltp':('ltp',),'feed_time':('exchFeedTime','exchange_timestamp'),'trade_time':('exchTradeTime','last_trade_time'),
        'volume':('tradeVolume','volume'),'open_interest':('opnInterest','open_interest')}.items()}

def run(headers,rows,clock):
    indexes={}
    for name in ('NIFTY','SENSEX'):
        rec=index_record(rows,name); raw=quote(headers,rec['exch_seg'],rec['token'])[0]; spot=float(field(raw,'ltp'))
        exp=active_expiry(rows,name,clock); ce,pe,strike=option_pair(rows,name,exp,spot)
        # connection_check.quote accepts one token; preserve conservative request behavior.
        ceq=quote(headers,ce['exch_seg'],ce['token'])[0]; peq=quote(headers,pe['exch_seg'],pe['token'])[0]
        indexes[name]={'index':sanitized_quote(raw),'selected_expiry':exp.isoformat(),'selected_strike':strike,
                       'lot_size':int(ce['lotsize']),'tick_size_rupees':float(ce['tick_size'])/100.0,
                       'call':sanitized_quote(ceq),'put':sanitized_quote(peq)}
    market_window=time(9,15)<=clock.time()<=time(15,30) and clock.weekday()<5
    return {'mode':'PAPER — LIVE MARKET DATA CHECK — NO ORDERS','checked_at_ist':clock.isoformat(),
            'market_hours_by_clock_only':market_window,'freshness_verdict':'requires feed timestamp validation during market hours',
            'instruments':indexes}

def main():
    if not os.path.exists(MASTER): print('Run discover_instruments.py first.',file=sys.stderr); return 1
    rows=json.load(open(MASTER,encoding='utf-8'))['records']
    print('PAPER ONLY: authenticated market-data diagnostic. There is no order endpoint.')
    if os.path.exists(CREDENTIAL_STORE):
        saved=load_credentials(); key=saved['api_key']; client=saved['client_id']; pin=saved['pin']
        totp=make_totp(saved['totp_secret']) if saved.get('totp_secret') else getpass.getpass('Current authenticator TOTP: ')
        print('Using Windows-encrypted SmartAPI credentials. Automatic TOTP:',bool(saved.get('totp_secret')))
    else:
        key=getpass.getpass('SmartAPI API key: '); client=getpass.getpass('Client ID: '); pin=getpass.getpass('Angel One PIN: '); totp=getpass.getpass('Current authenticator TOTP: ')
    try:
        try: mac,local,public=network_details()
        except Exception:
            mac=input('Device MAC address: ').strip(); local=input('Local IPv4: ').strip(); public=input('Public IPv4: ').strip()
        headers=authenticate(key,client,pin,totp,mac,local,public)
        report=run(headers,rows,datetime.now(IST))
        with open(REPORT,'w',encoding='utf-8') as f: json.dump(report,f,indent=2)
        print('Quote check completed. Sanitized report saved to:',REPORT)
        print('Market-hours freshness verified:',report['market_hours_by_clock_only'] and report['freshness_verdict']=='verified')
        return 0
    except Exception as exc:
        print('Quote check failed:',type(exc).__name__,str(exc)[:160],file=sys.stderr); return 1
    finally: key=client=pin=totp=''
if __name__=='__main__': sys.exit(main())
