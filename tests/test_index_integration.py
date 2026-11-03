import json
import os
import tempfile
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
import app
from expert_team import MASTER_URL
from live_market_check import IST
from market_scope import INDEX_SCOPE
from stream_runtime import MarketStore


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.getcwd())
        self.old=(app.DB,app.MARKET_DB); app.DB=os.path.join(self.tmp.name,'paper.db'); app.MARKET_DB=os.path.join(self.tmp.name,'market.db')
        self.store=MarketStore(app.MARKET_DB); self.clock=datetime(2026,10,30,10,0,10,tzinfo=IST).timestamp()
        for record in INDEX_SCOPE:
            record=dict(record,source=MASTER_URL,downloaded_at_utc=datetime.fromtimestamp(self.clock-100,timezone.utc).isoformat())
            self.store.instrument(record)
        with self.store.connect() as c:
            c.execute('INSERT OR REPLACE INTO health VALUES(?,?,?,?)',('runtime','RUNNING',self.clock,''))
            c.execute('INSERT OR REPLACE INTO health VALUES(?,?,?,?)',('websocket','CONNECTED',self.clock-100,''))
    def tearDown(self): app.DB,app.MARKET_DB=self.old; self.tmp.cleanup()
    def tick(self,token,seq=1,ts=None,price=23000):
        return dict(token=token,exchange_ts=self.clock-1 if ts is None else ts,sequence=seq,received_ts=self.clock-.5,price=price,volume=0,oi=0)
    def status(self):
        with patch('app.now',return_value=self.clock): return app.status()
    def test_both_indices_required_and_experts_exist_without_feed(self):
        d=self.status(); self.assertEqual(len(d['expert_team']['reports']),2)
        self.assertTrue(all(len(r['specialists'])==8 and r['decision']=='NO TRADE' for r in d['expert_team']['reports']))
        self.store.ingest(self.tick('99926000'))
        d=self.status(); self.assertFalse(d['market']['fresh']); self.assertIn('99919000',d['market']['missing_tokens'])
        self.store.ingest(self.tick('99919000'))
        d=self.status(); self.assertTrue(d['market']['fresh']); self.assertEqual(d['expert_team']['readiness'],'WARMING_UP')
        self.assertTrue(d['orders_disabled'])
    def test_actual_kill_and_disconnect_override_saved_reports(self):
        for token in ('99926000','99919000'): self.store.ingest(self.tick(token))
        with app.db() as c: c.execute("INSERT OR REPLACE INTO settings VALUES('kill','1')")
        d=self.status(); self.assertEqual(d['expert_team']['readiness'],'BLOCKED')
        self.assertTrue(all(next(s for s in r['specialists'] if s['id']=='risk')['evidence']['emergency_stop'] for r in d['expert_team']['reports']))
        with self.store.connect() as c: c.execute("UPDATE health SET status='DISCONNECTED' WHERE component='websocket'")
        self.assertFalse(self.status()['market']['fresh'])
    def test_out_of_scope_history_is_preserved_and_hidden(self):
        self.store.instrument({'token':'573422','name':'CRUDEOIL','exch_seg':'MCX','instrumenttype':'FUTCOM'})
        self.store.ingest(self.tick('573422'))
        d=self.status(); self.assertEqual(d['market']['ticks'],[])
        self.assertTrue(all(r['name'] in ('NIFTY','SENSEX') for r in d['market']['instruments']))
        with self.store.connect() as c: self.assertEqual(c.execute("SELECT COUNT(*) FROM ticks WHERE token='573422'").fetchone()[0],1)
    def test_journal_is_throttled_versioned_and_does_not_claim_kill_state(self):
        with patch('stream_runtime.time.time',return_value=self.clock):
            self.store.ingest(self.tick('99926000')); self.store.ingest(self.tick('99926000',2))
        with self.store.connect() as c:
            rows=c.execute('SELECT version,payload FROM expert_journal').fetchall()
            self.assertEqual(len(rows),1); report=json.loads(rows[0][1]); self.assertEqual(rows[0][0],report['version'])
            self.assertIsNone(next(r for r in report['specialists'] if r['id']=='risk')['evidence']['emergency_stop'])
    def test_daily_detail_persistence_and_late_tick_does_not_overwrite(self):
        tick=self.tick('99926000'); self.store.ingest(tick)
        fields={'day_open_price':22900,'previous_close_price':22800,'change_points':200}
        self.store.index_snapshot(tick,fields)
        older=self.tick('99926000',0,self.clock-3)
        self.store.index_snapshot(older,{'day_open_price':1})
        self.assertEqual(self.status()['market']['index_details']['99926000'],fields)
        self.store.ingest(self.tick('99926000',2,self.clock-.75))
        self.assertNotIn('99926000',self.status()['market']['index_details'])  # No mismatched older details.
    def test_closed_current_session_history_enables_context_only(self):
        start=int(self.clock//60)*60-25*60
        with patch('stream_runtime.time.time',return_value=self.clock):
            for token in ('99926000','99919000'):
                for i in range(25): self.store.ingest(self.tick(token,i+1,start+i*60+10,23000+i))
                self.store.ingest(self.tick(token,26))
        d=self.status(); self.assertEqual(d['expert_team']['readiness'],'RESEARCH_ONLY')
        self.assertEqual(d['expert_team']['decision'],'NO TRADE'); self.assertEqual(d['trades'],[])
        with patch('app.now',return_value=self.clock+11):
            d=app.status(); self.assertEqual(d['expert_team']['readiness'],'BLOCKED'); self.assertFalse(d['market']['fresh'])

if __name__=='__main__': unittest.main()
