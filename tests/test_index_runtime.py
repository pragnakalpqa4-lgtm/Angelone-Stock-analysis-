import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

import index_runtime as index
from stream_runtime import MarketStore,normalize_tick


def master_row(name='NIFTY',token=None,exchange=None,symbol=None,kind='AMXIDX'):
    return {'token':token or ('99926000' if name=='NIFTY' else '99919000'),
        'name':name,'symbol':symbol or ('Nifty 50' if name=='NIFTY' else 'SENSEX'),
        'exch_seg':exchange or ('NSE' if name=='NIFTY' else 'BSE'),'instrumenttype':kind,
        'expiry':'','lotsize':'1','tick_size':'0.000000','strike':'0.000000'}


class Sock:
    def __init__(self): self.sent=[]; self.closed=False
    def send(self,payload): self.sent.append(json.loads(payload))
    def close(self): self.closed=True


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.getcwd())
        self.here=Path(self.tmp.name); self.path=str(self.here/'indices.db')
        self.store=MarketStore(self.path)
        self.records=index.select_indices([master_row(),master_row('SENSEX')])
        self.collector=index.Collector(self.store,self.records)

    def tearDown(self): self.tmp.cleanup()

    def packet(self,name='NIFTY',seq=1,ts=None,exchange=None,token=None,price=2300000):
        b=bytearray(379); b[0]=3
        b[1]=exchange if exchange is not None else (1 if name=='NIFTY' else 3)
        token=token or ('99926000' if name=='NIFTY' else '99919000')
        b[2:2+len(token)]=token.encode()
        for offset,value in ((27,seq),(35,int((time.time() if ts is None else ts)*1000)),
            (43,price),(91,2290000),(99,2310000),(107,2280000),(115,2285000)):
            struct.pack_into('<q',b,offset,value)
        return bytes(b)

    def metadata(self):
        return {'source':'fixture://official-master','downloaded_at_utc':'2026-10-30T03:00:00+00:00',
            'sha256':'fixture-hash','master_record_count':1000,'scope':['NIFTY','SENSEX'],'records':self.records}

    def test_exact_canonical_identity_rejects_alias_and_ambiguity(self):
        rows=[master_row(),master_row('SENSEX'),master_row(token='26000',symbol='NIFTY',kind=''),
            master_row(token='777',exchange='BSE'),master_row('SENSEX',token='888',exchange='NSE')]
        records=index.select_indices(rows)
        self.assertEqual([(r['token'],r['exchange_type']) for r in records],[('99926000',1),('99919000',3)])
        self.assertTrue(all(not r['volume_supported'] and not r['open_interest_supported'] for r in records))
        with self.assertRaises(ValueError): index.select_indices(rows+[master_row(token='999')])
        with self.assertRaises(ValueError): index.select_indices([master_row(symbol='NIFTY'),master_row('SENSEX')])
        with self.assertRaises(ValueError): index.select_indices([master_row(token='26000'),master_row('SENSEX')])
        wrong=[dict(r) for r in records]; wrong[0]['instrumenttype']='OPTIDX'
        with self.assertRaises(ValueError): index.Collector(self.store,wrong)

    def test_fresh_master_provenance_and_metadata_file(self):
        rows=[master_row(),master_row('SENSEX')]+[{'name':'OTHER'} for i in range(998)]
        content=json.dumps(rows).encode()
        def opener(request,timeout): return io.BytesIO(content)
        target=self.here/'verified.json'; payload=index.discover(target,opener)
        self.assertEqual(payload['sha256'],hashlib.sha256(content).hexdigest())
        self.assertEqual(payload['master_record_count'],1000)
        self.assertTrue(all(r['source']==payload['source'] and r['downloaded_at_utc']==payload['downloaded_at_utc'] and r['sha256']==payload['sha256'] for r in payload['records']))
        self.assertEqual(json.loads(target.read_text())['records'][1]['exch_seg'],'BSE')
        with self.assertRaises(ValueError): index.discover(target,lambda *a,**k:io.BytesIO(b'[]'))

    def test_parser_milliseconds_and_no_executable_index_volume(self):
        stamp=time.time(); parsed=index.parse_packet(self.packet(ts=stamp))
        tick=normalize_tick(parsed)
        self.assertAlmostEqual(tick['exchange_ts'],int(stamp*1000)/1000)
        self.assertEqual(tick['price'],23000)
        self.assertEqual((tick['volume'],tick['oi']),(0,0))
        self.assertEqual(parsed['day_open_price'],2290000)

    def test_foreign_mcx_and_cross_exchange_spoof_rejected(self):
        for packet in (self.packet(exchange=5),self.packet(exchange=3),self.packet('SENSEX',exchange=1),
            self.packet(token='12345'),b'invalid'):
            self.collector.message(packet)
        self.assertEqual(self.collector.stats['rejected'],5)
        self.assertEqual(self.collector.stats['inserted'],0)
        self.assertEqual(index.snapshot(self.path,'99926000')['ticks'],0)

    def test_future_and_invalid_price_rejected(self):
        self.collector.message(self.packet(ts=time.time()+10))
        self.collector.message(self.packet(price=0))
        self.assertEqual(self.collector.stats['rejected'],2)

    def test_each_index_tracks_its_own_freshness(self):
        self.collector.opened(Sock())
        self.collector.message(self.packet())
        self.collector.message(self.packet('SENSEX',ts=time.time()-100))
        counters=self.collector.by_token
        self.assertEqual(counters['99926000']['fresh_at_receipt'],1)
        self.assertEqual(counters['99919000']['fresh_at_receipt'],0)
        self.assertEqual(counters['99919000']['stale_at_receipt'],1)
        evidence=index.per_index_results(self.store,self.records,self.collector)
        self.assertEqual(evidence['NIFTY']['verification']['live_freshness'],'PASS')
        self.assertEqual(evidence['SENSEX']['verification']['live_freshness'],'NOT_VERIFIED')
        self.assertFalse(all(e['live_verified'] for e in evidence.values()))

    def test_duplicate_after_reopening_store_keeps_candles_unchanged(self):
        packet=self.packet(); self.collector.message(packet)
        before=index.snapshot(self.path,'99926000')
        reopened=index.Collector(MarketStore(self.path),self.records); reopened.message(packet)
        after=index.snapshot(self.path,'99926000')
        self.assertEqual(before,after)
        self.assertEqual(reopened.stats['duplicates'],1)
        self.assertEqual(reopened.stats['inserted'],0)

    def test_each_connection_subscribes_both_indices_once(self):
        first=Sock(); second=Sock(); self.collector.opened(first); self.collector.closed(); self.collector.opened(second)
        for sock in (first,second):
            self.assertEqual(len(sock.sent),1)
            self.assertEqual(sock.sent[0]['params']['tokenList'],[
                {'exchangeType':1,'tokens':['99926000']},{'exchangeType':3,'tokens':['99919000']}])
        self.assertEqual(self.collector.stats['connections'],2)

    def test_real_transport_loop_forced_disconnect_resubscribes_both(self):
        clock=[0.0]; sockets=[]; test=self; seq=[0]
        class TransportSock(Sock):
            def settimeout(self,value): pass
            def recv(self):
                clock[0]+=.2; seq[0]+=1
                return test.packet('NIFTY' if seq[0]%2 else 'SENSEX',seq[0])
        def connector(*args,**kwargs):
            sock=TransportSock(); sockets.append(sock); return sock
        with patch('mcx_diagnostic.time.monotonic',side_effect=lambda:clock[0]),patch('mcx_diagnostic.time.sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            index.collect(self.collector,{},5,.5,connector)
        self.assertEqual(self.collector.stats['forced_disconnects'],1)
        self.assertEqual(len(sockets),2)
        self.assertTrue(all(s.closed and len(s.sent)==1 for s in sockets))
        self.assertTrue(all(s['post_reconnect_fresh_ticks']>0 for s in self.collector.by_token.values()))
        self.assertTrue(self.collector.stats['duplicate_replay_verified'])

    def test_metadata_only_never_authenticates_or_verifies_live(self):
        with patch('index_runtime.HERE',self.here),patch('index_runtime.discover',return_value=self.metadata()),patch('index_runtime.private_session',side_effect=AssertionError('must not authenticate')),patch('index_runtime.collect',side_effect=AssertionError('must not connect')):
            result=index.run(db_path=self.path,verify_only=True)
        self.assertEqual(result['live_result'],'METADATA_ONLY')
        self.assertFalse(result['verification']['both_indices_live_verified'])
        self.assertEqual(result['verification']['live_ticks_received'],0)
        self.assertTrue((self.here/'index_diagnostic_report.json').is_file())

    def test_auth_unavailable_saves_sanitized_blocked_result(self):
        with patch('index_runtime.HERE',self.here),patch('index_runtime.discover',return_value=self.metadata()),patch('index_runtime.private_session',side_effect=RuntimeError('NO_EXISTING_NONINTERACTIVE_AUTH')):
            result=index.run(db_path=self.path)
        self.assertEqual(result['live_result'],'BLOCKED')
        self.assertEqual(set(result['per_index']),{'NIFTY','SENSEX'})
        self.assertFalse(result['verification']['both_indices_live_verified'])

    def populate_live_check(self,collector,only_nifty=False):
        collector.opened(Sock())
        names=['NIFTY'] if only_nifty else ['NIFTY','SENSEX']
        for name in names: collector.message(self.packet(name,1,time.time()-70))
        collector.closed(); collector.stats['forced_disconnects']=1; collector.opened(Sock())
        for name in names: collector.message(self.packet(name,2,time.time()))

    def test_complete_run_requires_both_indices_independently(self):
        def fake_collect(collector,*args): self.populate_live_check(collector)
        with patch('index_runtime.HERE',self.here),patch('index_runtime.discover',return_value=self.metadata()),patch('index_runtime.collect',side_effect=fake_collect):
            result=index.run(db_path=self.path,session_factory=lambda:{})
        self.assertEqual(result['live_result'],'VERIFIED')
        self.assertTrue(result['verification']['both_indices_live_verified'])
        self.assertTrue(all(r['live_verified'] for r in result['per_index'].values()))

    def test_one_live_index_cannot_cover_missing_other_index(self):
        def fake_collect(collector,*args): self.populate_live_check(collector,only_nifty=True)
        with patch('index_runtime.HERE',self.here),patch('index_runtime.discover',return_value=self.metadata()),patch('index_runtime.collect',side_effect=fake_collect):
            result=index.run(db_path=self.path,session_factory=lambda:{})
        self.assertEqual(result['live_result'],'INCOMPLETE')
        self.assertTrue(result['per_index']['NIFTY']['live_verified'])
        self.assertFalse(result['per_index']['SENSEX']['live_verified'])
        self.assertEqual(result['per_index']['SENSEX']['statistics']['inserted'],0)

    def test_auth_failure_does_not_emit_exception_text(self):
        def auth(): raise RuntimeError('sensitive fixture value')
        with patch('index_runtime.HERE',self.here),patch('index_runtime.discover',return_value=self.metadata()):
            result=index.run(db_path=self.path,session_factory=auth)
        self.assertEqual(result['live_result'],'FAILED')
        self.assertNotIn('sensitive fixture value',json.dumps(result))

    def test_concurrent_collector_is_blocked_before_auth_and_lock_released(self):
        with index.runtime_lock(self.path),patch('index_runtime.discover',side_effect=AssertionError('must not start second collector')):
            report=index.run(db_path=self.path)
        self.assertEqual(report['reason'],'COLLECTOR_ALREADY_RUNNING')
        self.assertTrue(report['orders_disabled'])
        with index.runtime_lock(self.path): pass  # OS lock is reusable after release.

    def test_silent_socket_reconnects_after_heartbeat_deadline(self):
        clock=[0.0]; sockets=[]
        class IdleSock:
            def __init__(self): self.closed=False
            def settimeout(self,value): pass
            def send(self,payload): pass
            def recv(self):
                import websocket
                clock[0]+=1
                raise websocket.WebSocketTimeoutException('fixture timeout')
            def close(self): self.closed=True
        def connector(*args,**kwargs):
            sock=IdleSock(); sockets.append(sock); return sock
        with patch('mcx_diagnostic.time.monotonic',side_effect=lambda:clock[0]),patch('mcx_diagnostic.time.sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            index.collect(self.collector,{},35,0,connector)
        self.assertEqual(len(sockets),2); self.assertTrue(all(s.closed for s in sockets))
        self.assertEqual(self.collector.stats['inserted'],0)


if __name__=='__main__': unittest.main()
