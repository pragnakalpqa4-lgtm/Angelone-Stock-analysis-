"""Archived MCX helpers and shared bounded read-only transport.

Wire layout follows Angel One's official SmartWebSocketV2 implementation.
Uses websocket-client directly to retain TLS verification and avoid SDK file logs.
"""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import struct
import time
import urllib.request
from contextlib import closing
from datetime import datetime, timezone

from connection_check import authenticate_session
from discover_instruments import URL, normalize
from live_market_check import IST, network_details
from secure_credentials import STORE, load, totp
from stream_runtime import MarketStore, normalize_tick

HERE=Path(__file__).resolve().parent
WS_URL='wss://smartapisocket.angelone.in/smart-stream'


def atomic_json(path,payload):
    path=Path(path); temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(payload,indent=2),encoding='utf-8'); temp.replace(path)


def select_contract(rows,clock):
    candidates=[]
    for raw in rows:
        if raw.get('exch_seg')!='MCX' or raw.get('name')!='CRUDEOIL' or raw.get('instrumenttype')!='FUTCOM': continue
        record=normalize(raw)
        # Conservatively avoid the expiry day; no claim about exchange cutoff.
        expiry=datetime.strptime(record['expiry'],'%d%b%Y').date()
        if expiry<=clock.astimezone(IST).date(): continue
        if not record['token'].isdigit() or int(record['lotsize'])<=0 or float(record['tick_size'])<=0: raise ValueError('invalid contract metadata')
        candidates.append((expiry,record))
    if not candidates: raise RuntimeError('no unexpired MCX CRUDEOIL future')
    candidates.sort(key=lambda item:item[0]); expiry,record=candidates[0]
    if sum(d==expiry for d,r in candidates)!=1: raise ValueError('ambiguous nearest contract')
    return dict(record,exchange_type=5,lot_size=int(record['lotsize']),tick_size_rupees=float(record['tick_size'])/100)


def discover(path,opener=urllib.request.urlopen):
    request=urllib.request.Request(URL,headers={'User-Agent':'AngelPaperReadOnlyDiagnostic/1.0','Cache-Control':'no-cache'})
    with opener(request,timeout=60) as response: content=response.read()
    rows=json.loads(content)
    if not isinstance(rows,list) or len(rows)<1000: raise ValueError('incomplete master')
    clock=datetime.now(timezone.utc); record=select_contract(rows,clock)
    provenance={'source':URL,'downloaded_at_utc':clock.isoformat(),'sha256':hashlib.sha256(content).hexdigest(),'master_record_count':len(rows)}
    record.update(provenance); atomic_json(path,record)
    return record


def private_session():
    """Only existing protected configuration; failures never include input values."""
    names=('ANGELONE_API_KEY','ANGELONE_CLIENT_CODE','ANGELONE_PIN','ANGELONE_TOTP_SECRET')
    if not any(os.getenv(n) for n in names):
        names=('ANGEL_API_KEY','ANGEL_CLIENT_ID','ANGEL_PIN','ANGEL_TOTP_SECRET')
    if all(os.getenv(n) for n in names):
        key,client,pin,seed=(os.environ[n] for n in names)
    elif os.path.isfile(STORE):
        saved=load(); key=saved['api_key']; client=saved['client_id']; pin=saved['pin']; seed=saved.get('totp_secret')
        if not seed: raise RuntimeError('EXISTING_STORE_HAS_NO_AUTOMATIC_TOTP')
    else: raise RuntimeError('NO_EXISTING_NONINTERACTIVE_AUTH')
    mac,local,public=network_details()
    headers,session=authenticate_session(key,client,pin,totp(seed),mac,local,public)
    return {'Authorization':session['jwtToken'],'x-api-key':headers['X-PrivateKey'],'x-client-code':client,'x-feed-token':session['feedToken']}


def parse_packet(packet):
    if not isinstance(packet,bytes) or len(packet)<379 or packet[0]!=3 or packet[1]!=5: raise ValueError('unexpected MCX snapshot packet')
    def number(offset): return struct.unpack_from('<q',packet,offset)[0]
    return {'token':packet[2:27].split(b'\0',1)[0].decode('ascii'),'exchange_type':packet[1],
        'sequence_number':number(27),'exchange_timestamp':number(35),'last_traded_price':number(43),
        'volume_trade_for_the_day':number(67),'open_interest':number(131)}


class Collector:
    def __init__(self,store,record):
        self.store=store; self.record=record
        self.stats={'received':0,'inserted':0,'duplicates':0,'rejected':0,'fresh_at_receipt':0,'stale_at_receipt':0,
            'connections':0,'disconnects':0,'forced_disconnects':0,'post_reconnect_ticks':0,'duplicate_replay_verified':False}
        self.delays=[]; self.last_tick=None; self.first_connection_tick=None; self.observed_minutes=set()
    def opened(self,sock):
        sock.send(json.dumps({'correlationID':'mcxdiag01','action':1,'params':{'mode':3,'tokenList':[{'exchangeType':5,'tokens':[self.record['token']]}]}}))
        self.stats['connections']+=1; self.first_connection_tick=None
        self.store.health('websocket','CONNECTED')
    def closed(self):
        self.stats['disconnects']+=1; self.store.health('websocket','DISCONNECTED')
    def message(self,packet):
        self.stats['received']+=1
        try:
            message=parse_packet(packet)
            if message['token']!=self.record['token']: raise ValueError('unsubscribed token')
            tick=normalize_tick(message)
        except (ValueError,KeyError,TypeError,OverflowError,UnicodeError,struct.error):
            self.stats['rejected']+=1; self.store.health('feed','INVALID','packet rejected'); return
        inserted=self.store.ingest(tick)
        self.stats['inserted' if inserted else 'duplicates']+=1
        if not inserted: return
        self.last_tick=tick
        self.observed_minutes.add(int(tick['exchange_ts']//60)*60)
        if self.first_connection_tick is None: self.first_connection_tick=time.monotonic()
        delay=tick['received_ts']-tick['exchange_ts']; self.delays.append(delay)
        self.stats['fresh_at_receipt' if -2<=delay<=10 else 'stale_at_receipt']+=1
        if self.stats['connections']>1: self.stats['post_reconnect_ticks']+=1
        self.store.health('feed','OK' if delay<=10 else 'STALE')
        # Replay exactly one real normalized event to verify durable idempotency.
        if not self.stats['duplicate_replay_verified']:
            self.stats['duplicate_replay_verified']=not self.store.ingest(dict(tick))


def collect(collector,headers,seconds,disconnect_after,connector=None):
    import sys
    if (HERE/'.deps').is_dir(): sys.path.insert(0,str(HERE/'.deps'))
    import websocket
    websocket.enableTrace(False); logging.getLogger('websocket').disabled=True
    connector=connector or websocket.create_connection
    deadline=time.monotonic()+seconds; retry=0; sock=None
    collector.store.health('runtime','RUNNING')
    try:
        while time.monotonic()<deadline:
            try:
                sock=connector(WS_URL,header=headers,timeout=min(10,max(.1,deadline-time.monotonic())))
                sock.settimeout(1); collector.opened(sock); last_ping=time.monotonic(); last_response=last_ping
                while time.monotonic()<deadline:
                    collector.store.health('runtime','RUNNING')
                    if time.monotonic()-last_ping>=10: sock.send('ping'); last_ping=time.monotonic()
                    first=collector.first_connection_tick
                    if disconnect_after and first is not None and time.monotonic()-first>=disconnect_after and not collector.stats['forced_disconnects']:
                        collector.stats['forced_disconnects']+=1; break
                    try: packet=sock.recv()
                    except websocket.WebSocketTimeoutException:
                        if time.monotonic()-last_response>=30:
                            collector.store.health('websocket','UNRESPONSIVE','No frames or heartbeat replies for 30 seconds')
                            break
                        continue
                    last_response=time.monotonic()
                    if packet in ('',b''): break
                    if packet=='pong': continue
                    if isinstance(packet,str): collector.store.health('feed','CONTROL_ERROR','broker control response'); continue
                    collector.message(packet)
            except Exception as exc:
                # Exception text may contain authentication headers; never retain it.
                collector.store.health('websocket','ERROR',type(exc).__name__)
            finally:
                if sock is not None:
                    try: sock.close()
                    except Exception: pass
                    sock=None
                collector.closed()
            remaining=deadline-time.monotonic()
            if remaining<=0: break
            retry+=1; time.sleep(min(2**min(retry,3),remaining))
    finally:
        collector.store.health('runtime','STOPPED')


def snapshot(path,token):
    # Fresh connection verifies committed state; no dependency on in-memory store.
    with closing(sqlite3.connect(path)) as c:
        count=c.execute('SELECT COUNT(*) FROM ticks WHERE token=?',(token,)).fetchone()[0]
        candles=[dict(zip(('minute','open','high','low','close','volume'),r)) for r in c.execute('SELECT minute,open,high,low,close,volume FROM candles WHERE token=? ORDER BY minute',(token,))]
        integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
    return {'ticks':count,'candles':candles,'integrity_check':integrity}


def run(seconds=180,disconnect_after=30,db_path=None,session_factory=None):
    report={'checked_at_ist':datetime.now(IST).isoformat(),'orders_disabled':True,'live_result':'NOT_RUN','synthetic_prices_used':False,
        'requested_seconds':seconds,'limitations':['OHLC uses observed feed snapshots; gaps are not backfilled.','Volume is observed cumulative delta; first partial minute/reset is unknown.','Feed freshness is not proof of last-trade freshness or a trading session.']}
    store=MarketStore(str(db_path or HERE/'market.db')); collector=None
    store.health('runtime','STARTING'); store.health('websocket','DISCONNECTED')
    try:
        record=discover(HERE/'verified_mcx_contract.json'); report['contract']=record; store.instrument(record)
        before=snapshot(store.path,record['token']); report['persisted_ticks_before']=before['ticks']
        try: headers=(session_factory or private_session)()
        except RuntimeError as exc:
            if str(exc) in ('NO_EXISTING_NONINTERACTIVE_AUTH','EXISTING_STORE_HAS_NO_AUTOMATIC_TOTP'):
                report['live_result']='BLOCKED'; report['reason']=str(exc); return report
            raise
        collector=Collector(store,record); collect(collector,headers,seconds,disconnect_after)
        report['live_result']='VERIFIED' if collector.stats['fresh_at_receipt'] and collector.stats['post_reconnect_ticks'] and collector.stats['forced_disconnects'] and collector.stats['duplicate_replay_verified'] else 'INCOMPLETE'
    except Exception as exc:
        report['live_result']='FAILED'; report['failure_type']=type(exc).__name__
    finally:
        store.health('runtime','STOPPED'); store.health('websocket','DISCONNECTED')
        if collector:
            report['statistics']=collector.stats
            if collector.delays: report['feed_delay_seconds']={'min':min(collector.delays),'max':max(collector.delays)}
        if report.get('contract'):
            report['persistence']=snapshot(store.path,report['contract']['token'])
            if collector:
                report['persistence']['new_ticks_match_inserted']=report['persistence']['ticks']-report['persisted_ticks_before']==collector.stats['inserted']
                completed=[r for r in report['persistence']['candles'] if r['minute'] in collector.observed_minutes and r['minute']+60<=time.time()]
                report['completed_observed_minutes']=len(completed)
                if not completed or not report['persistence']['new_ticks_match_inserted'] or report['persistence']['integrity_check']!='ok': report['live_result']='INCOMPLETE'
        report['verification']={'current_master':'PASS' if report.get('contract') else 'FAILED',
            'live_ticks_received':collector.stats['inserted'] if collector else 0,
            'live_freshness':'PASS' if collector and collector.stats['fresh_at_receipt'] else 'NOT_VERIFIED',
            'live_reconnect':'PASS' if collector and collector.stats['forced_disconnects'] and collector.stats['post_reconnect_ticks'] else 'NOT_VERIFIED',
            'live_duplicate_replay':'PASS' if collector and collector.stats['duplicate_replay_verified'] else 'NOT_VERIFIED',
            'live_candle_persistence':'PASS' if report.get('completed_observed_minutes',0) and report.get('persistence',{}).get('new_ticks_match_inserted') else 'NOT_VERIFIED'}
        atomic_json(HERE/'mcx_diagnostic_report.json',report)
    return report


if __name__=='__main__':
    raise SystemExit('MCX collection is retired from active scope. Run index_runtime.py for NIFTY and SENSEX only.')
