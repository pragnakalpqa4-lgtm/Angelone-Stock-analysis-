"""SmartAPI WebSocket market-data runtime. Paper research only; no order API."""
import json,math,os,sqlite3,time
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from live_market_check import IST
from market_scope import INDEX_TOKENS

MARKET_DB=os.getenv('MARKET_DB',os.path.join(os.path.dirname(__file__),'market.db'))

@contextmanager
def connect_db(path=None):
    c=sqlite3.connect(path or MARKET_DB,timeout=10)
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS ticks(token TEXT, exchange_ts INTEGER, sequence INTEGER, received_ts REAL, price REAL, volume INTEGER, oi INTEGER, PRIMARY KEY(token,exchange_ts,sequence));
    CREATE TABLE IF NOT EXISTS candles(token TEXT, minute INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER, PRIMARY KEY(token,minute));
    CREATE TABLE IF NOT EXISTS health(component TEXT PRIMARY KEY, status TEXT, updated_ts REAL, detail TEXT);
    CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, ts REAL, token TEXT, regime TEXT, recommendation TEXT, evidence TEXT);
    CREATE TABLE IF NOT EXISTS instruments(token TEXT PRIMARY KEY, payload TEXT);
    CREATE TABLE IF NOT EXISTS expert_journal(id INTEGER PRIMARY KEY,ts REAL,token TEXT,minute INTEGER,version TEXT,payload TEXT,UNIQUE(token,minute,version));
    CREATE TABLE IF NOT EXISTS index_snapshots(token TEXT PRIMARY KEY,exchange_ts REAL,sequence INTEGER,payload TEXT);
    CREATE INDEX IF NOT EXISTS ticks_event_order ON ticks(token,exchange_ts,sequence);
    ''')
    try:
        with c: yield c
    finally: c.close()

def normalize_tick(message,received=None):
    received=time.time() if received is None else received
    token=str(message['token']); price=float(message['last_traded_price'])/100.0
    exchange_ms=int(message['exchange_timestamp']); exchange_ts=exchange_ms/1000.0
    seq=int(message.get('sequence_number',0)); volume=int(message.get('volume_trade_for_the_day',0)); oi=int(message.get('open_interest',0))
    if not token or not math.isfinite(price) or price<=0 or exchange_ts<=0 or seq<0 or volume<0 or oi<0 or not math.isfinite(received) or exchange_ts>received+2: raise ValueError('invalid tick')
    return {'token':token,'exchange_ts':exchange_ts,'sequence':seq,'received_ts':received,'price':price,'volume':volume,'oi':oi}

class MarketStore:
    def __init__(self,path=None):
        self.path=path or MARKET_DB
        with self.connect(): pass
    def connect(self): return connect_db(self.path)
    def instrument(self,record):
        with self.connect() as c: c.execute('INSERT OR REPLACE INTO instruments VALUES(?,?)',(record['token'],json.dumps(record)))
    def index_snapshot(self,tick,fields):
        if tick['token'] not in INDEX_TOKENS: raise ValueError('index outside active scope')
        with self.connect() as c:
            c.execute('''INSERT INTO index_snapshots VALUES(?,?,?,?) ON CONFLICT(token) DO UPDATE SET
                exchange_ts=excluded.exchange_ts,sequence=excluded.sequence,payload=excluded.payload
                WHERE excluded.exchange_ts>index_snapshots.exchange_ts OR
                (excluded.exchange_ts=index_snapshots.exchange_ts AND excluded.sequence>index_snapshots.sequence)''',
                (tick['token'],tick['exchange_ts'],tick['sequence'],json.dumps(fields)))
    def health(self,component,status,detail=''):
        with self.connect() as c: c.execute('INSERT OR REPLACE INTO health VALUES(?,?,?,?)',(component,status,time.time(),detail[:500]))
    def ingest(self,tick):
        minute=int(tick['exchange_ts']//60)*60
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            inserted=c.execute('INSERT OR IGNORE INTO ticks VALUES(?,?,?,?,?,?,?)',(tick['token'],tick['exchange_ts'],tick['sequence'],tick['received_ts'],tick['price'],tick['volume'],tick['oi'])).rowcount
            if not inserted: return False
            self._candle(c,tick['token'],minute)
            following=c.execute('SELECT MIN(minute) FROM candles WHERE token=? AND minute>?',(tick['token'],minute)).fetchone()[0]
            if following is not None: self._candle(c,tick['token'],following)
        self.observe(tick['token']); return True
    def _candle(self,c,token,minute):
        rows=c.execute('SELECT price,volume FROM ticks WHERE token=? AND exchange_ts>=? AND exchange_ts<? ORDER BY exchange_ts,sequence',(token,minute,minute+60)).fetchall()
        previous=c.execute('SELECT exchange_ts,volume FROM ticks WHERE token=? AND exchange_ts<? ORDER BY exchange_ts DESC,sequence DESC LIMIT 1',(token,minute)).fetchone()
        # Volume is observed cumulative delta, not exchange-certified minute volume.
        # The first partial minute and counter resets have unknown volume.
        volume=None
        if previous and datetime.fromtimestamp(previous[0],IST).date()==datetime.fromtimestamp(minute,IST).date():
            counters=[previous[1]]+[r[1] for r in rows]
            if all(b>=a for a,b in zip(counters,counters[1:])): volume=counters[-1]-counters[0]
        prices=[r[0] for r in rows]
        c.execute('INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?)',(token,minute,prices[0],max(prices),min(prices),prices[-1],volume))
    def observe(self,token):
        if token not in INDEX_TOKENS: return
        from expert_team import analyze_instrument,VERSION
        with self.connect() as c:
            clock=time.time(); minute=int(clock//60)*60
            if c.execute('SELECT 1 FROM expert_journal WHERE token=? AND minute=? AND version=?',(token,minute,VERSION)).fetchone(): return
            c.row_factory=sqlite3.Row
            instrument=c.execute('SELECT payload FROM instruments WHERE token=?',(token,)).fetchone()
            tick=c.execute('SELECT * FROM ticks WHERE token=? ORDER BY exchange_ts DESC,sequence DESC LIMIT 1',(token,)).fetchone()
            candles=[dict(r) for r in c.execute('SELECT * FROM candles WHERE token=? ORDER BY minute DESC LIMIT 120',(token,))][::-1]
            health=[dict(r) for r in c.execute('SELECT * FROM health')]
            report=analyze_instrument(json.loads(instrument[0]) if instrument else {},dict(tick) if tick else None,candles,health,clock=clock)
            report['journal_note']='Collector snapshot; emergency setting is not read here. Current API report is authoritative for current gates.'
            risk_role=next(r for r in report['specialists'] if r['id']=='risk')
            risk_role['evidence']['emergency_stop']=None
            risk_role['evidence']['emergency_stop_status']='NOT_OBSERVED_IN_COLLECTOR'
            inserted=c.execute('INSERT OR IGNORE INTO expert_journal(ts,token,minute,version,payload) VALUES(?,?,?,?,?)',(clock,token,minute,VERSION,json.dumps(report))).rowcount
            if inserted:
                c.execute('INSERT INTO observations(ts,token,regime,recommendation,evidence) VALUES(?,?,?,?,?)',(clock,token,report['readiness'],'NO TRADE',json.dumps({'team_report':True,'readiness':report['readiness'],'blockers':report['blockers']})))

def run():
    from index_runtime import run as run_indexes
    report=run_indexes()
    print(json.dumps(report,indent=2))
    return 0 if report['live_result']=='VERIFIED' else 2

if __name__=='__main__': raise SystemExit(run())
