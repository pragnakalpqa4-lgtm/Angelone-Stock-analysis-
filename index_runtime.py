"""Read-only Nifty 50 / Sensex collector with independent verification per index.

The transport follows Angel One's SmartWebSocketV2 snapshot wire format. No
broker or simulated order functions are imported or called. Default operation
never prompts for credentials; an explicitly private caller can supply an
in-memory session factory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import time
import urllib.request
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from discover_instruments import URL, normalize
from live_market_check import IST
from mcx_diagnostic import atomic_json, collect, private_session, snapshot
from stream_runtime import MarketStore, normalize_tick

HERE=Path(__file__).resolve().parent
SCOPE=('NIFTY','SENSEX')
EXPECTED={'NIFTY':('NSE',1,'NIFTY 50'),'SENSEX':('BSE',3,'SENSEX')}
EXPECTED_TOKENS={'NIFTY':'99926000','SENSEX':'99919000'}


def select_indices(rows):
    """Require canonical index records; reject option, alias and exchange matches."""
    selected=[]
    for name in SCOPE:
        exchange,exchange_type,symbol=EXPECTED[name]
        matches=[row for row in rows if str(row.get('name','')).strip().upper()==name
            and str(row.get('instrumenttype','')).strip().upper()=='AMXIDX'
            and str(row.get('exch_seg','')).strip().upper()==exchange
            and str(row.get('symbol','')).strip().upper()==symbol]
        if len(matches)!=1: raise ValueError('canonical index missing or ambiguous')
        record=normalize(matches[0])
        if not record['token'].isdigit() or int(record['token'])<=0 or record['expiry'].strip():
            raise ValueError('invalid index identity')
        if record['token']!=EXPECTED_TOKENS[name]:
            raise ValueError('canonical index token changed; independent verification required')
        record.update(index_id=name,display_name='NIFTY 50' if name=='NIFTY' else name,
            exchange_type=exchange_type,asset_type='INDEX',volume_supported=False,
            open_interest_supported=False,executable_depth_supported=False)
        selected.append(record)
    if len({r['token'] for r in selected})!=len(SCOPE):
        raise ValueError('index tokens must be distinct in the current store schema')
    return selected


def discover(path=None,opener=urllib.request.urlopen):
    request=urllib.request.Request(URL,headers={'User-Agent':'AngelIndexReadOnly/1.0','Cache-Control':'no-cache'})
    with opener(request,timeout=60) as response: content=response.read()
    rows=json.loads(content)
    if not isinstance(rows,list) or len(rows)<1000: raise ValueError('incomplete current instrument master')
    records=select_indices(rows)
    provenance={'source':URL,'downloaded_at_utc':datetime.now(timezone.utc).isoformat(),
        'sha256':hashlib.sha256(content).hexdigest(),'master_record_count':len(rows)}
    for record in records: record.update(provenance)
    payload=dict(provenance,scope=list(SCOPE),records=records)
    atomic_json(path or HERE/'verified_index_instruments.json',payload)
    return payload


def parse_packet(packet):
    if not isinstance(packet,(bytes,bytearray)) or len(packet)<379 or packet[0]!=3 or packet[1] not in (1,3):
        raise ValueError('unexpected index snapshot packet')
    token=bytes(packet[2:27]).split(b'\0',1)[0].decode('ascii')
    if not token or not token.isdigit(): raise ValueError('invalid index token')
    def number(offset): return struct.unpack_from('<q',packet,offset)[0]
    # These are index levels, not tradable contracts. Index volume/OI/depth is
    # deliberately not represented as executable liquidity or VWAP evidence.
    return {'token':token,'exchange_type':packet[1],'sequence_number':number(27),
        'exchange_timestamp':number(35),'last_traded_price':number(43),
        'day_open_price':number(91),'day_high_price':number(99),
        'day_low_price':number(107),'previous_close_price':number(115)}


def empty_stats():
    return {'received':0,'inserted':0,'duplicates':0,'fresh_at_receipt':0,
        'stale_at_receipt':0,'post_reconnect_ticks':0,'post_reconnect_fresh_ticks':0,
        'duplicate_replay_verified':False}


class Collector:
    """A single subscription covering exactly the two verified index identities."""
    def __init__(self,store,records):
        if len(records)!=2 or {r.get('index_id',r.get('name')) for r in records}!=set(SCOPE):
            raise ValueError('exactly NIFTY and SENSEX are required')
        self.records=[]
        for record in records:
            name=record.get('index_id',record.get('name')); expected=EXPECTED[name]
            if (record.get('exch_seg'),record.get('exchange_type'))!=expected[:2]:
                raise ValueError('invalid verified index exchange')
            if record.get('token')!=EXPECTED_TOKENS[name]: raise ValueError('invalid canonical index token')
            if record.get('instrumenttype')!='AMXIDX' or str(record.get('symbol','')).strip().upper()!=expected[2]:
                raise ValueError('invalid canonical index metadata')
            self.records.append(record)
        if len({r['token'] for r in self.records})!=2: raise ValueError('ambiguous index token')
        self.store=store
        self.allowed={(r['exchange_type'],r['token']):r for r in self.records}
        self.by_token={r['token']:empty_stats() for r in self.records}
        self.stats=dict(empty_stats(),connections=0,disconnects=0,forced_disconnects=0,rejected=0,
            by_token=self.by_token)
        self.first_connection_tick=None
        self.delays=[]; self.delays_by_token={r['token']:[] for r in self.records}
        self.observed_minutes={r['token']:set() for r in self.records}
        self.latest={}; self.last_tick=None

    def opened(self,sock):
        token_list=[{'exchangeType':r['exchange_type'],'tokens':[r['token']]}
            for r in sorted(self.records,key=lambda r:r['exchange_type'])]
        sock.send(json.dumps({'correlationID':'indexdiag1','action':1,
            'params':{'mode':3,'tokenList':token_list}}))
        self.stats['connections']+=1; self.first_connection_tick=None
        self.store.health('websocket','CONNECTED')

    def closed(self):
        self.stats['disconnects']+=1
        self.store.health('websocket','DISCONNECTED')

    def message(self,packet):
        self.stats['received']+=1
        try:
            message=parse_packet(packet)
            if (message['exchange_type'],message['token']) not in self.allowed:
                raise ValueError('unsubscribed exchange and token pair')
            tick=normalize_tick(message)
        except (ValueError,KeyError,TypeError,OverflowError,UnicodeError,struct.error):
            self.stats['rejected']+=1
            self.store.health('feed','INVALID','index packet rejected'); return
        token=tick['token']; counters=self.by_token[token]; counters['received']+=1
        inserted=self.store.ingest(tick)
        field='inserted' if inserted else 'duplicates'
        self.stats[field]+=1; counters[field]+=1
        if not inserted: return
        self.last_tick=tick
        if self.first_connection_tick is None: self.first_connection_tick=time.monotonic()
        self.observed_minutes[token].add(int(tick['exchange_ts']//60)*60)
        delay=tick['received_ts']-tick['exchange_ts']
        self.delays.append(delay); self.delays_by_token[token].append(delay)
        fresh=-2<=delay<=10
        field='fresh_at_receipt' if fresh else 'stale_at_receipt'
        self.stats[field]+=1; counters[field]+=1
        if self.stats['connections']>1:
            self.stats['post_reconnect_ticks']+=1; counters['post_reconnect_ticks']+=1
            if fresh:
                self.stats['post_reconnect_fresh_ticks']+=1; counters['post_reconnect_fresh_ticks']+=1
        self.store.health('feed:'+token,'OK' if fresh else 'STALE')
        key=(tick['exchange_ts'],tick['sequence'])
        if token not in self.latest or key>self.latest[token]['event_key']:
            quote={k:message[k]/100.0 if message[k]>0 else None for k in ('day_open_price','day_high_price','day_low_price','previous_close_price')}
            previous=quote['previous_close_price']
            quote['change_points']=tick['price']-previous if previous else None
            quote['change_percent']=(tick['price']/previous-1)*100 if previous else None
            self.store.index_snapshot(tick,quote)
            self.latest[token]={'event_key':key,'price':tick['price'],'exchange_ts':tick['exchange_ts'],
                'received_ts':tick['received_ts'],'feed_delay_seconds':delay,**quote}
        if not counters['duplicate_replay_verified']:
            counters['duplicate_replay_verified']=not self.store.ingest(dict(tick))
        self.stats['duplicate_replay_verified']=all(s['duplicate_replay_verified'] for s in self.by_token.values())


def per_index_results(store,records,collector=None,before=None,finished=None):
    """Never pool freshness or reconnect evidence between the two indices."""
    before=before or {}; finished=time.time() if finished is None else finished
    result={}
    for record in records:
        token=record['token']; stats=collector.by_token[token] if collector else empty_stats()
        persistence=snapshot(store.path,token)
        persistence['new_ticks_match_inserted']=persistence['ticks']-before.get(token,0)==stats['inserted']
        observed=collector.observed_minutes[token] if collector else set()
        completed=sum(r['minute'] in observed and r['minute']+60<=finished for r in persistence['candles'])
        checks={'live_freshness':stats['fresh_at_receipt']>0,
            'live_reconnect':bool(collector and collector.stats['forced_disconnects'] and stats['post_reconnect_fresh_ticks']>0),
            'live_duplicate_replay':stats['duplicate_replay_verified'],
            'live_candle_persistence':bool(completed and persistence['new_ticks_match_inserted'] and persistence['integrity_check']=='ok')}
        evidence={'instrument':record,'statistics':dict(stats),'persistence':persistence,
            'completed_observed_minutes':completed,'verification':{k:'PASS' if value else 'NOT_VERIFIED' for k,value in checks.items()},
            'live_verified':all(checks.values())}
        if collector and collector.delays_by_token[token]:
            delays=collector.delays_by_token[token]
            evidence['feed_delay_seconds']={'min':min(delays),'max':max(delays)}
        if collector and token in collector.latest:
            evidence['last_snapshot']={k:v for k,v in collector.latest[token].items() if k!='event_key'}
        result[record.get('index_id',record['name'])]=evidence
    return result


class CollectorAlreadyRunning(RuntimeError): pass


@contextmanager
def runtime_lock(db_path):
    """OS releases this lock on process exit; no stale-PID deletion is needed."""
    handle=open(str(db_path)+'.index.lock','a+b')
    locked=False
    try:
        if handle.seek(0,2)==0: handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
        except OSError: raise CollectorAlreadyRunning('COLLECTOR_ALREADY_RUNNING') from None
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        handle.close()


def run(seconds=180,disconnect_after=30,db_path=None,session_factory=None,verify_only=False):
    if verify_only: return _run(seconds,disconnect_after,db_path,session_factory,True)
    try:
        with runtime_lock(db_path or HERE/'market.db'):
            return _run(seconds,disconnect_after,db_path,session_factory,False)
    except CollectorAlreadyRunning:
        return {'scope':list(SCOPE),'orders_disabled':True,'live_result':'BLOCKED','reason':'COLLECTOR_ALREADY_RUNNING',
            'synthetic_prices_used':False,'note':'Existing collector and its report left unchanged.'}


def _run(seconds=180,disconnect_after=30,db_path=None,session_factory=None,verify_only=False):
    if not 10<=seconds<=3600 or not 0<=disconnect_after<seconds:
        raise ValueError('seconds must be 10..3600; disconnect-after must be 0..seconds-1')
    report={'checked_at_ist':datetime.now(IST).isoformat(),'scope':list(SCOPE),'orders_disabled':True,
        'live_result':'NOT_RUN','synthetic_prices_used':False,'requested_seconds':seconds,
        'metadata_only':bool(verify_only),'limitations':[
            'Index levels are not executable instruments. Volume, OI and executable depth are unsupported.',
            'Observed one-minute snapshot OHLC can contain partial minutes and gaps; no prices are backfilled.',
            'Feed freshness is not proof of an open exchange or a fresh underlying trade.',
            'Each index must independently pass freshness, replay, reconnect and candle-persistence checks.']}
    store=MarketStore(str(db_path or HERE/'market.db'))
    records=[]; collector=None; before={}
    if not verify_only:
        store.health('runtime','STARTING'); store.health('websocket','DISCONNECTED')
    try:
        metadata=discover(HERE/'verified_index_instruments.json')
        report['instrument_master']={k:v for k,v in metadata.items() if k!='records'}
        records=metadata['records']; report['instruments']=records
        for record in records:
            store.instrument(record)
            before[record['token']]=snapshot(store.path,record['token'])['ticks']
        report['persisted_ticks_before']=before
        if verify_only:
            report['live_result']='METADATA_ONLY'
        else:
            store.health('authentication','AWAITING_PRIVATE_LOGIN' if session_factory else 'CHECKING_EXISTING_SESSION')
            try: headers=(session_factory or private_session)()
            except RuntimeError as exc:
                if str(exc) in ('NO_EXISTING_NONINTERACTIVE_AUTH','EXISTING_STORE_HAS_NO_AUTOMATIC_TOTP'):
                    store.health('authentication','UNAVAILABLE')
                    report['live_result']='BLOCKED'; report['reason']=str(exc); return report
                raise
            store.health('authentication','AUTHENTICATED')
            collector=Collector(store,records)
            collect(collector,headers,seconds,disconnect_after)
            report['live_result']='INCOMPLETE'
    except (KeyboardInterrupt,EOFError): report['live_result']='CANCELLED'
    except Exception as exc:
        if not verify_only: store.health('authentication','ERROR',type(exc).__name__)
        report['live_result']='FAILED'; report['failure_type']=type(exc).__name__
    finally:
        if not verify_only:
            store.health('runtime','STOPPED'); store.health('websocket','DISCONNECTED')
        if collector: report['statistics']=collector.stats
        if records:
            report['per_index']=per_index_results(store,records,collector,before)
            if collector and report['live_result']=='INCOMPLETE' and all(r['live_verified'] for r in report['per_index'].values()):
                report['live_result']='VERIFIED'
        report['verification']={'current_master':'PASS' if records else 'NOT_VERIFIED',
            'both_indices_live_verified':bool(records and report.get('per_index') and all(r['live_verified'] for r in report['per_index'].values())),
            'live_ticks_received':collector.stats['inserted'] if collector else 0}
        report['completed_at_ist']=datetime.now(IST).isoformat()
        atomic_json(HERE/'index_diagnostic_report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=180)
    parser.add_argument('--disconnect-after',type=int,default=30)
    parser.add_argument('--verify-only',action='store_true',help='Verify public instrument metadata without authentication or streaming')
    args=parser.parse_args()
    if not 10<=args.seconds<=3600 or not 0<=args.disconnect_after<args.seconds:
        parser.error('seconds must be 10..3600; disconnect-after must be 0..seconds-1')
    result=run(args.seconds,args.disconnect_after,verify_only=args.verify_only)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['live_result'] in ('VERIFIED','METADATA_ONLY') else 2)
