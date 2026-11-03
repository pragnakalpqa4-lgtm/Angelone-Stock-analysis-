import json
import os
import sqlite3
import struct
import tempfile
import time
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
import app
import mcx_diagnostic as d
from stream_runtime import MarketStore,normalize_tick


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.getcwd()); self.path=os.path.join(self.tmp.name,'market.db')
        self.store=MarketStore(self.path)
        self.record={'token':'123','symbol':'CRUDEOIL_TEST','exch_seg':'MCX'}
        self.store.instrument(self.record)
        self.collector=d.Collector(self.store,self.record)
        self.old=(app.DB,app.MARKET_DB); app.DB=os.path.join(self.tmp.name,'paper.db'); app.MARKET_DB=self.path
        self.minute=int(time.time()//60)*60-300
    def tearDown(self):
        app.DB,app.MARKET_DB=self.old; self.tmp.cleanup()
    def packet(self,seq=1,ts=None,price=600000,volume=100,token='123'):
        b=bytearray(379); b[0]=3; b[1]=5; b[2:2+len(token)]=token.encode()
        for offset,value in ((27,seq),(35,int((ts or time.time())*1000)),(43,price),(67,volume),(131,50)):
            struct.pack_into('<q',b,offset,value)
        return bytes(b)
    def tick(self,seq,ts,price=600000,volume=100):
        return normalize_tick(d.parse_packet(self.packet(seq,ts,price,volume)))
    def test_contract_filter_and_expiry(self):
        def row(exp,token='1',name='CRUDEOIL'):
            return dict(token=token,symbol='CRUDEOIL'+exp+'FUT',name=name,expiry=exp,strike='0',lotsize='100',instrumenttype='FUTCOM',exch_seg='MCX',tick_size='100')
        clock=datetime(2026,10,29,tzinfo=timezone.utc)
        selected=d.select_contract([row('29OCT2026'),row('19NOV2026'),row('18DEC2026'),row('01NOV2026',name='CRUDEOILM')],clock)
        self.assertEqual(selected['expiry'],'19NOV2026'); self.assertEqual(selected['exchange_type'],5); self.assertEqual(selected['tick_size_rupees'],1)
        with self.assertRaises(ValueError): d.select_contract([row('19NOV2026'),row('19NOV2026','2')],clock)
    def test_parser_milliseconds_and_invalid(self):
        ts=time.time(); tick=self.tick(1,ts)
        self.assertAlmostEqual(tick['exchange_ts'],int(ts*1000)/1000)
        self.assertEqual(tick['price'],6000)
        for packet in (b'bad',self.packet(price=0),self.packet(ts=time.time()+10),self.packet(token='999')):
            self.collector.message(packet)
        self.assertEqual(self.collector.stats['rejected'],4)
    def test_duplicate_reopen_and_atomic_candles(self):
        t=self.tick(1,self.minute+1)
        self.assertTrue(self.store.ingest(t)); self.assertFalse(self.store.ingest(dict(t)))
        restarted=MarketStore(self.path)
        self.assertFalse(restarted.ingest(dict(t)))
        self.assertEqual(d.snapshot(self.path,'123')['ticks'],1)
        self.assertEqual(d.snapshot(self.path,'123')['integrity_check'],'ok')
    def test_out_of_order_ohlc_and_observed_volume(self):
        for seq,ts,price,volume in [(1,self.minute-1,599900,90),(3,self.minute+40,601000,110),(2,self.minute+10,600000,100),(4,self.minute+61,602000,115)]:
            self.store.ingest(self.tick(seq,ts,price,volume))
        candles=d.snapshot(self.path,'123')['candles']
        self.assertIsNone(candles[0]['volume'])
        self.assertEqual((candles[1]['open'],candles[1]['close'],candles[1]['volume']),(6000,6010,20))
        self.assertEqual(candles[2]['volume'],5)
        self.store.ingest(self.tick(5,self.minute+59,603000,112))
        candles=d.snapshot(self.path,'123')['candles']
        self.assertEqual(candles[1]['close'],6030); self.assertEqual(candles[2]['volume'],3)
    def test_volume_reset_unknown_and_no_gap_fill(self):
        self.store.ingest(self.tick(1,self.minute,volume=100))
        self.store.ingest(self.tick(2,self.minute+180,volume=1))
        candles=d.snapshot(self.path,'123')['candles']
        self.assertEqual(len(candles),2); self.assertIsNone(candles[-1]['volume'])
    def test_fresh_stale_future_disconnect_reconnect(self):
        class Sock:
            def send(self,payload): self.payload=json.loads(payload)
        sock=Sock(); self.collector.opened(sock); self.store.health('runtime','RUNNING')
        self.collector.message(self.packet())
        self.assertFalse(app.status()['market']['fresh'])  # Historical MCX is outside active scope.
        self.assertEqual(sock.payload['params']['tokenList'],[{'exchangeType':5,'tokens':['123']}])
        self.collector.closed(); self.assertFalse(app.status()['market']['fresh'])
        self.collector.opened(sock); self.assertFalse(app.status()['market']['fresh'])
        self.collector.message(self.packet(2)); self.assertFalse(app.status()['market']['fresh'])
        self.assertEqual(self.collector.stats['post_reconnect_ticks'],1)
        with patch('app.now',return_value=time.time()+11): self.assertFalse(app.status()['market']['fresh'])
        self.store.health('runtime','STOPPED'); self.assertFalse(app.status()['market']['fresh'])
    def test_newly_received_stale_exchange_time(self):
        class Sock:
            def send(self,p): pass
        self.collector.opened(Sock()); self.store.health('runtime','RUNNING')
        self.collector.message(self.packet(ts=time.time()-100))
        self.assertFalse(app.status()['market']['fresh']); self.assertEqual(self.collector.stats['stale_at_receipt'],1)
    def test_latest_tick_event_order_not_arrival(self):
        self.store.ingest(self.tick(2,self.minute+40,601000))
        self.store.ingest(self.tick(1,self.minute+10,600000))
        self.assertEqual(app.status()['market']['ticks'],[])  # History persists, dashboard excludes MCX.
        self.assertEqual(d.snapshot(self.path,'123')['candles'][-1]['close'],6010)
    def test_no_orders_even_simulated(self):
        self.assertTrue(app.ORDERS_DISABLED)
        q=app.Quote('123',5999,6000,1000,1000,100,time.time(),time.time(),'SYNTHETIC_DEMO')
        self.assertFalse(app.PaperEngine().execute('id','BUY',q,1,1,'diagnostic',5000,7000)[0])
        self.assertEqual(app.status()['trades'],[])
    def test_no_auth_never_prompts_or_prints(self):
        with patch.dict(os.environ,{},clear=True),patch('mcx_diagnostic.os.path.isfile',return_value=False),patch('getpass.getpass',side_effect=AssertionError('must not prompt')):
            with self.assertRaisesRegex(RuntimeError,'NO_EXISTING_NONINTERACTIVE_AUTH'): d.private_session()
    def test_store_isolation(self):
        other=MarketStore(os.path.join(self.tmp.name,'other.db'))
        self.store.ingest(self.tick(1,self.minute)); self.assertEqual(d.snapshot(other.path,'123')['ticks'],0)
    def test_transport_forced_disconnect_resubscribe_and_error_redaction(self):
        clock=[0.0]; sockets=[]; packets=[self.packet(seq=i) for i in range(1,20)]
        class Sock:
            def __init__(self): self.sent=[]; self.closed=False
            def settimeout(self,value): pass
            def send(self,payload): self.sent.append(json.loads(payload))
            def recv(self): clock[0]+=.25; return packets.pop(0)
            def close(self): self.closed=True
        def connector(*args,**kwargs):
            sock=Sock(); sockets.append(sock); return sock
        with patch('mcx_diagnostic.time.monotonic',side_effect=lambda:clock[0]),patch('mcx_diagnostic.time.sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            d.collect(self.collector,{},4,.5,connector)
        self.assertEqual(self.collector.stats['forced_disconnects'],1)
        self.assertEqual(len(sockets),2)
        self.assertTrue(all(s.closed and len(s.sent)==1 for s in sockets))
        self.assertGreater(self.collector.stats['post_reconnect_ticks'],0)
        self.assertFalse(app.status()['market']['fresh'])
        self.assertTrue(self.collector.stats['duplicate_replay_verified'])
        self.assertEqual(d.snapshot(self.path,'123')['ticks'],self.collector.stats['inserted'])
        clock[0]=0
        with patch('mcx_diagnostic.time.monotonic',side_effect=lambda:clock[0]),patch('mcx_diagnostic.time.sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            d.collect(self.collector,{},1,0,lambda *a,**k:(_ for _ in ()).throw(RuntimeError('secret sentinel')))
        with self.store.connect() as c:
            self.assertFalse(any('secret sentinel' in str(row) for row in c.execute('SELECT * FROM health')))
    def test_http_dashboard_and_reset_blocked(self):
        import threading,urllib.request,urllib.error
        from http.server import ThreadingHTTPServer
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with urllib.request.urlopen(base+'/api/status') as response: status=json.load(response)
            self.assertTrue(status['orders_disabled']); self.assertFalse(status['market']['fresh'])
            with urllib.request.urlopen(base) as response: self.assertIn(b'id="expertTeam"',response.read())
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(base+'/api/reset-kill',method='POST'))
            self.assertEqual(error.exception.code,403); error.exception.close()
        finally: server.shutdown(); server.server_close(); worker.join()

if __name__=='__main__': unittest.main()
