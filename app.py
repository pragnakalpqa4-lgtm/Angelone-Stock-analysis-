"""Paper-only research prototype. No broker order API exists in this codebase."""
import argparse, csv, io, json, math, os, sqlite3, threading, time, urllib.request
import base64, hmac
from urllib.parse import urlsplit
from runtime_state import DATA_DIR, REPORT_FILE, is_enabled, read_json, set_enabled
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from market_scope import INDEX_SCOPE,INDEX_TOKENS,active_record
from expert_team import analyze_instrument

MODE = 'NIFTY / SENSEX RESEARCH — ALL ORDERS DISABLED'
ORDERS_DISABLED = True
DB = os.getenv('PAPER_DB', str(DATA_DIR / 'paper.db'))
MARKET_DB = os.getenv('MARKET_DB', str(DATA_DIR / 'market.db'))
DASHBOARD = os.path.join(os.path.dirname(__file__), 'dashboard.html')

def now(): return datetime.now(timezone.utc).timestamp()
@contextmanager
def db():
    os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, ts REAL, payload TEXT);
    CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, event_id TEXT UNIQUE, ts REAL, side TEXT, token TEXT, qty INTEGER, price REAL, fee REAL, reason TEXT);
    CREATE TABLE IF NOT EXISTS positions(token TEXT PRIMARY KEY, qty INTEGER, entry REAL, entry_fee REAL, stop REAL, target REAL);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);''')
    try:
        with c: yield c
    finally: c.close()

@dataclass(frozen=True)
class Quote:
    token: str
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    volume: int
    exchange_ts: float
    processed_ts: float
    source: str

def validate(q, max_age=10):
    if q.source not in ('LIVE','SYNTHETIC_DEMO'): return 'unknown data source'
    if not all(math.isfinite(x) for x in (q.bid,q.ask,q.exchange_ts,q.processed_ts)): return 'non-finite data'
    if q.bid <= 0 or q.ask < q.bid or q.bid_qty <= 0 or q.ask_qty <= 0: return 'invalid depth'
    if q.exchange_ts > q.processed_ts + 2 or q.processed_ts-q.exchange_ts > max_age or now()-q.processed_ts > max_age: return 'stale or future data'
    return None

class Risk:
    # Demonstration defaults only; live mode cannot be enabled.
    def __init__(self, capital=100000, risk=500, max_spread=.03, min_volume=100, max_positions=1, max_trades=2, max_daily_loss=1000):
        self.capital,self.risk,self.max_spread,self.min_volume,self.max_positions,self.max_trades,self.max_daily_loss=capital,risk,max_spread,min_volume,max_positions,max_trades,max_daily_loss
    def check(self,q,lot,stop,positions,trades,cash,market_open,kill,loss=0):
        err=validate(q)
        if err: return 0,err
        if kill or not market_open: return 0,'kill switch or market status uncertain'
        if lot <= 0 or not isinstance(lot,int) or stop <= 0 or stop >= q.ask: return 0,'invalid lot or stop'
        if (q.ask-q.bid)/q.ask > self.max_spread or q.volume < self.min_volume: return 0,'spread or liquidity limit'
        if len(positions)>=self.max_positions or trades>=self.max_trades or loss>=self.max_daily_loss: return 0,'position, frequency or daily loss limit'
        # Option premium can be lost in full; stops do not guarantee a fill.
        n=min(int(self.risk//(q.ask*lot)),int(cash//(q.ask*lot)),int(q.ask_qty//lot))
        return (n*lot,'approved') if n else (0,'insufficient capital, risk budget or depth')

def fee(price,qty,rate=.002): return round(price*qty*rate,2)

class PaperEngine:
    def __init__(self,capital=100000,slippage=.001,cost_rate=.002): self.capital,self.slippage,self.cost_rate=capital,slippage,cost_rate
    def positions(self):
        with db() as c: return c.execute('SELECT token,qty,entry,entry_fee,stop,target FROM positions').fetchall()
    def cash(self):
        with db() as c:
            rows=c.execute('SELECT side,qty,price,fee FROM trades').fetchall()
        return self.capital+sum((p*q if s=='SELL' else -p*q)-f for s,q,p,f in rows)
    def execute(self,event_id,side,q,qty,lot,reason,stop=0,target=0):
        if ORDERS_DISABLED: return False,'all orders disabled during read-only diagnostic'
        if validate(q): return False,'invalid quote'
        if side not in ('BUY','SELL') or qty<=0 or lot<=0 or qty%lot: return False,'invalid order'
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM trades WHERE event_id=?',(event_id,)).fetchone(): return False,'duplicate'
            pos=c.execute('SELECT qty FROM positions WHERE token=?',(q.token,)).fetchone()
            if side=='BUY' and pos: return False,'existing position'
            if side=='SELL' and (not pos or pos[0]!=qty): return False,'no matching long position'
            if (q.ask_qty if side=='BUY' else q.bid_qty)<qty: return False,'insufficient displayed depth'
            price=round(q.ask*(1+self.slippage) if side=='BUY' else q.bid*(1-self.slippage),4)
            cost=fee(price,qty,self.cost_rate)
            if side=='BUY':
                rows=c.execute('SELECT side,qty,price,fee FROM trades').fetchall()
                cash=self.capital+sum((p*n if s=='SELL' else -p*n)-f for s,n,p,f in rows)
                if price*qty+cost>cash: return False,'insufficient cash'
                if stop<=0 or target<=price or stop>=price: return False,'invalid exit levels'
                c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)',(q.token,qty,price,cost,stop,target))
            else: c.execute('DELETE FROM positions WHERE token=?',(q.token,))
            c.execute('INSERT INTO trades(event_id,ts,side,token,qty,price,fee,reason) VALUES(?,?,?,?,?,?,?,?)',(event_id,now(),side,q.token,qty,price,cost,reason))
        return True,'filled'
    def monitor(self,q,lot,exit_now=False):
        for token,qty,entry,entry_fee,stop,target in self.positions():
            if token==q.token and (q.bid<=stop or q.bid>=target or exit_now):
                return self.execute('exit:'+token+':'+str(q.exchange_ts),'SELL',q,qty,lot,'stop/target/manual exit')
        return False,'hold'

class Orchestrator:
    def __init__(self,engine,risk): self.engine,self.risk=engine,risk
    def decide(self,q,lot,trend,market_open=False,instrument_verified=False):
        # Explicitly illustrative rules; not a validated strategy.
        evidence={'data_agent':validate(q) or 'valid','regime_agent':'trending' if abs(trend)>=.01 else 'uncertain',
                  'technical_agent':'up' if trend>.01 else 'down' if trend<-.01 else 'no signal',
                  'validation_agent':'research-only rule; independent confirmation unavailable'}
        reason='instrument unverified or no independently validated strategy'
        decision='NO TRADE'
        if q.source=='SYNTHETIC_DEMO' and instrument_verified and abs(trend)>.01:
            qty,reason=self.risk.check(q,lot,q.ask*.8,self.engine.positions(),self.trade_count(),self.engine.cash(),market_open,self.killed())
            if qty:
                ok,reason=self.engine.execute('entry:'+q.token+':'+str(q.exchange_ts),'BUY',q,qty,lot,'synthetic demonstration',q.ask*.8,q.ask*1.3)
                if ok: decision='SIMULATED BUY'
        record={'timestamp':datetime.now(timezone.utc).isoformat(),'mode':MODE,'source':q.source,'instrument':q.token,'decision':decision,'evidence':evidence,'reason':reason,'quote':asdict(q)}
        with db() as c: c.execute('INSERT INTO decisions(ts,payload) VALUES(?,?)',(now(),json.dumps(record)))
        return record
    def trade_count(self):
        with db() as c: return c.execute("SELECT COUNT(*) FROM trades WHERE side='BUY' AND ts>=?",(now()-86400,)).fetchone()[0]
    def killed(self):
        with db() as c: return bool(c.execute("SELECT value FROM settings WHERE key='kill' AND value='1'").fetchone())

class AngelReadOnly:
    """FULL quote adapter. Session obtained independently; no login or order functions."""
    URL='https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/'
    def quote(self,exchange,tokens):
        key=os.environ['ANGEL_API_KEY']; token=os.environ['ANGEL_JWT']
        payload=json.dumps({'mode':'FULL','exchangeTokens':{exchange:tokens}}).encode()
        headers={'Content-Type':'application/json','Accept':'application/json','Authorization':'Bearer '+token,'X-PrivateKey':key,'X-UserType':'USER','X-SourceID':'WEB','X-ClientLocalIP':os.environ['ANGEL_LOCAL_IP'],'X-ClientPublicIP':os.environ['ANGEL_PUBLIC_IP'],'X-MACAddress':os.environ['ANGEL_MAC']}
        req=urllib.request.Request(self.URL,data=payload,headers=headers,method='POST')
        with urllib.request.urlopen(req,timeout=10) as response: result=json.load(response)
        if not result.get('status') or result.get('data',{}).get('unfetched'): raise RuntimeError('quote failed or incomplete')
        return result['data']['fetched']

def status():
    clock=now()
    with db() as c:
        decisions=[json.loads(x[0]) for x in c.execute('SELECT payload FROM decisions ORDER BY id DESC LIMIT 30')]
        trades=c.execute('SELECT ts,side,token,qty,price,fee,reason FROM trades ORDER BY id DESC LIMIT 100').fetchall()
        decisions=[r for r in decisions if r.get('instrument') in INDEX_TOKENS]
        trades=[r for r in trades if r[2] in INDEX_TOKENS]
    eng=PaperEngine()
    market={'scope':['NIFTY','SENSEX'],'ticks':[],'candles':{},'index_details':{},'health':[],'observations':[],'instruments':[],'latest_received_ts':None,'fresh':False,'missing_tokens':list(INDEX_TOKENS)}
    if os.path.exists(MARKET_DB):
        mc=None
        try:
            mc=sqlite3.connect(MARKET_DB,timeout=3)
            mc.row_factory=sqlite3.Row
            mc.execute('BEGIN')
            market['ticks']=[dict(r) for r in mc.execute('''SELECT t.* FROM ticks t WHERE rowid=(SELECT rowid FROM ticks x
                WHERE x.token=t.token ORDER BY exchange_ts DESC,sequence DESC LIMIT 1) ORDER BY token''')]
            market['ticks']=[r for r in market['ticks'] if r['token'] in INDEX_TOKENS]
            for token in [r['token'] for r in market['ticks']]:
                market['candles'][token]=[dict(r) for r in mc.execute('SELECT minute,open,high,low,close,volume FROM candles WHERE token=? ORDER BY minute DESC LIMIT 120',(token,))][::-1]
            market['health']=[dict(r) for r in mc.execute('SELECT component,status,updated_ts,detail FROM health ORDER BY component')]
            market['observations']=[dict(r) for r in mc.execute('SELECT ts,token,regime,recommendation,evidence FROM observations WHERE token IN (?,?) ORDER BY id DESC LIMIT 20',INDEX_TOKENS)]
            if mc.execute("SELECT 1 FROM sqlite_master WHERE name='instruments'").fetchone():
                market['instruments']=[record for r in mc.execute('SELECT payload FROM instruments') if active_record(record:=json.loads(r[0]))]
            if mc.execute("SELECT 1 FROM sqlite_master WHERE name='index_snapshots'").fetchone():
                for snapshot_row in mc.execute('SELECT * FROM index_snapshots WHERE token IN (?,?)',INDEX_TOKENS):
                    tick=next((r for r in market['ticks'] if r['token']==snapshot_row['token']),None)
                    if tick and tick['exchange_ts']==snapshot_row['exchange_ts'] and tick['sequence']==snapshot_row['sequence']:
                        market['index_details'][tick['token']]=json.loads(snapshot_row['payload'])
            mc.close()
            if market['ticks']:
                market['latest_received_ts']=max(r['received_ts'] for r in market['ticks'])
                health={r['component']:r for r in market['health']}
                ws=health.get('websocket',{}); runtime=health.get('runtime',{})
                active=ws.get('status')=='CONNECTED' and runtime.get('status')=='RUNNING' and 0<=clock-runtime.get('updated_ts',0)<=10
                for tick in market['ticks']:
                    tick['receive_age_seconds']=clock-tick['received_ts']
                    tick['exchange_age_seconds']=clock-tick['exchange_ts']
                    tick['fresh']=bool(active and tick['received_ts']>=ws.get('updated_ts',0) and
                        0<=tick['receive_age_seconds']<=10 and -2<=tick['exchange_age_seconds']<=10)
                market['missing_tokens']=[token for token in INDEX_TOKENS if token not in {r['token'] for r in market['ticks']}]
                market['fresh']=not market['missing_tokens'] and all(t['fresh'] for t in market['ticks'])
        except (sqlite3.Error,KeyError,ValueError,TypeError) as exc:
            market['fresh']=False; market['ticks']=[]; market['candles']={}
            market['health'].append({'component':'dashboard','status':'ERROR','updated_ts':now(),'detail':type(exc).__name__})
        finally:
            if mc is not None: mc.close()
    eng=PaperEngine(); risk=Risk(); kill=Orchestrator(eng,risk).killed() or not is_enabled()
    if not is_enabled():
        market['fresh']=False
        for tick in market['ticks']: tick['fresh']=False
    worker=read_json(REPORT_FILE,{})
    heartbeat=worker.get('checked_at',0)
    supervisor_fresh=isinstance(heartbeat,(int,float)) and 0<=clock-heartbeat<=10
    pipeline={'desired_enabled':is_enabled(),'supervisor_fresh':supervisor_fresh,
        'status':worker.get('status','UNAVAILABLE') if supervisor_fresh else 'SUPERVISOR_UNAVAILABLE',
        'collector_pid':worker.get('collector_pid') if supervisor_fresh else None,
        'restart_count':worker.get('restart_count',0)}
    instruments={r['token']:r for r in market['instruments']}; latest={r['token']:r for r in market['ticks']}
    # Reports are recalculated from the same snapshot on every request so a saved
    # positive observation cannot survive a disconnect, stale tick or kill gate.
    team=[analyze_instrument(instruments.get(r['token'],dict(r)),latest.get(r['token']),market['candles'].get(r['token'],[]),market['health'],clock=clock,kill=kill) for r in INDEX_SCOPE]
    team_summary={'mode':'RULE_BASED_RESEARCH','decision':'NO TRADE','orders_disabled':True,
        'readiness':'BLOCKED' if any(r['readiness']=='BLOCKED' for r in team) else 'WARMING_UP' if any(r['readiness']=='WARMING_UP' for r in team) else 'RESEARCH_ONLY',
        'reason':'Specialist checks are research evidence. No strategy or execution is approved.',
        'reports':team}
    positions=[]
    last={r['token']:r['price'] for r in market['ticks'] if r.get('fresh')}
    unrealized=0.0
    for token,qty,entry,entry_fee,stop,target in eng.positions():
        if token not in INDEX_TOKENS: continue
        mark=last.get(token); pnl=(mark-entry)*qty-entry_fee if mark is not None else None
        if pnl is not None: unrealized+=pnl
        positions.append({'token':token,'qty':qty,'entry':entry,'entry_fee':entry_fee,'stop':stop,'target':target,'mark':mark,'unrealized_pnl':pnl})
    return {'mode':MODE,'version':'index-expert-team-v1','orders_disabled':ORDERS_DISABLED,'warning':'Nifty and Sensex reference-data research. All orders disabled. Specialist rules are unvalidated.',
        'server_ts':clock,'kill':kill,'cash':eng.cash(),'unrealized_pnl':round(unrealized,2),
        'positions':positions,'decisions':decisions,'trades':trades,'market':market,'expert_team':team_summary,'pipeline':pipeline,
        'risk_limits':{'starting_capital':risk.capital,'max_risk_per_trade':risk.risk,'max_bid_ask_spread':risk.max_spread,
        'minimum_volume':risk.min_volume,'max_concurrent_positions':risk.max_positions,'max_trades_per_day':risk.max_trades,
        'max_daily_loss':risk.max_daily_loss,'max_data_delay_seconds':10}}

class Handler(BaseHTTPRequestHandler):
    def authorized(self):
        password=os.getenv('DASHBOARD_PASSWORD')
        if not password: return True
        expected='Basic '+base64.b64encode((os.getenv('DASHBOARD_USER','operator')+':'+password).encode()).decode()
        if hmac.compare_digest(self.headers.get('Authorization',''),expected): return True
        self.send_response(401); self.send_header('WWW-Authenticate','Basic realm="Index dashboard"'); self.end_headers()
        return False
    def do_GET(self):
        if not self.authorized(): return
        if self.path=='/api/status': body=json.dumps(status(),indent=2).encode(); typ='application/json'
        elif self.path=='/api/report': body=json.dumps(read_json(DATA_DIR/'reports'/'latest.json',{'status':'NOT_GENERATED'})).encode(); typ='application/json'
        elif self.path=='/trades.csv':
            out=io.StringIO(); w=csv.writer(out); w.writerow(['timestamp_utc_epoch','side','token','quantity','price','estimated_fee','reason']); w.writerows(status()['trades']); body=out.getvalue().encode(); typ='text/csv'
        elif self.path=='/':
            try:
                with open(DASHBOARD,'rb') as page: body=page.read()
                typ='text/html; charset=utf-8'
            except OSError: self.send_error(503,'dashboard.html missing'); return
        else: self.send_error(404); return
        self.send_response(200); self.send_header('Content-Type',typ); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass
    def do_POST(self):
        if not self.authorized(): return
        if self.headers.get('Origin') and urlsplit(self.headers['Origin']).netloc!=self.headers.get('Host'):
            self.send_error(403,'Origin does not match'); return
        if self.path in ('/api/pipeline/start','/api/pipeline/stop'):
            enabled=self.path.endswith('/start'); set_enabled(enabled)
            body=json.dumps({'desired_enabled':enabled,'orders_disabled':True}).encode()
            self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body); return
        if self.path not in ('/api/kill','/api/reset-kill'): self.send_error(404); return
        if self.path=='/api/reset-kill' and ORDERS_DISABLED: self.send_error(403,'All orders are disabled'); return
        value='1' if self.path=='/api/kill' else '0'
        with db() as c: c.execute("INSERT OR REPLACE INTO settings VALUES('kill',?)",(value,))
        body=json.dumps({'ok':True,'kill':value=='1','mode':MODE}).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body)

def demo():
    e=PaperEngine(); o=Orchestrator(e,Risk()); t=now(); q=Quote('DEMO_CALL',99,100,1000,1000,5000,t,t,'SYNTHETIC_DEMO')
    result=o.decide(q,1,.02,True,True); print(json.dumps(result,indent=2))
    if result['decision']=='SIMULATED BUY':
        t=now(); exitq=Quote('DEMO_CALL',131,132,1000,1000,6000,t,t,'SYNTHETIC_DEMO'); print('Demo exit:',e.monitor(exitq,1))

def main():
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['demo','serve','kill','reset-kill','quote']); p.add_argument('--exchange'); p.add_argument('--token'); p.add_argument('--port',type=int,default=8080); a=p.parse_args()
    if os.getenv('MODE','PAPER')!='PAPER': raise SystemExit('LIVE mode is unavailable')
    if a.command=='demo': demo()
    elif a.command=='serve': print('Local dashboard http://127.0.0.1:%s' % a.port,flush=True); ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
    elif a.command in ('kill','reset-kill'):
        with db() as c: c.execute("INSERT OR REPLACE INTO settings VALUES('kill',?)",('1' if a.command=='kill' else '0',))
        print('Kill switch',a.command)
    elif a.command=='quote':
        if not a.exchange or not a.token: p.error('quote requires --exchange and --token')
        print(json.dumps(AngelReadOnly().quote(a.exchange,[a.token]),indent=2))
if __name__=='__main__': main()
