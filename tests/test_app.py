import os, sqlite3, tempfile, unittest
import app

class Tests(unittest.TestCase):
    def setUp(self):
        self.disabled=app.ORDERS_DISABLED; app.ORDERS_DISABLED=False
        self.tmp=tempfile.TemporaryDirectory(dir=os.getcwd()); app.DB=os.path.join(self.tmp.name,'test.db'); app.MARKET_DB=os.path.join(self.tmp.name,'market.db'); self.e=app.PaperEngine(); self.r=app.Risk()
        t=app.now(); self.q=app.Quote('test',99,100,100,100,1000,t,t,'SYNTHETIC_DEMO')
    def tearDown(self): app.ORDERS_DISABLED=self.disabled; self.tmp.cleanup()
    def test_invalid_and_stale_quotes(self):
        from dataclasses import replace
        self.assertIsNotNone(app.validate(replace(self.q,exchange_ts=app.now()-100)))
        self.assertIsNotNone(app.validate(replace(self.q,ask=98)))
    def test_risk_and_sizing(self):
        qty,reason=self.r.check(self.q,1,80,[],0,100000,True,False)
        self.assertEqual(qty,5); self.assertEqual(reason,'approved')
        self.assertEqual(self.r.check(self.q,1,80,[],0,100000,False,False)[0],0)
        self.assertEqual(self.r.check(self.q,1,80,[],0,100000,True,True)[0],0)
    def test_entry_exit_persistence_duplicate(self):
        self.assertTrue(self.e.execute('entry','BUY',self.q,2,1,'test',80,130)[0])
        self.assertFalse(self.e.execute('entry','BUY',self.q,2,1,'test',80,130)[0])
        self.assertEqual(len(app.PaperEngine().positions()),1)
        from dataclasses import replace
        exitq=replace(self.q,bid=131,ask=132)
        self.assertTrue(self.e.monitor(exitq,1)[0]); self.assertEqual(len(self.e.positions()),0)
        self.assertGreater(self.e.cash(),100000)
    def test_rejections(self):
        self.assertFalse(self.e.execute('x','SELL',self.q,1,1,'short')[0])
        self.assertFalse(self.e.execute('x','BUY',self.q,3,2,'bad lot',80,130)[0])
        self.assertFalse(self.e.execute('x','BUY',self.q,101,1,'depth',80,130)[0])
    def test_live_never_trades(self):
        from dataclasses import replace
        q=replace(self.q,source='LIVE')
        d=app.Orchestrator(self.e,self.r).decide(q,1,.1,True,True)
        self.assertEqual(d['decision'],'NO TRADE'); self.assertEqual(self.e.positions(),[])
    def test_fees_and_slippage(self):
        self.assertEqual(app.fee(100,5),1)
        self.e.execute('x','BUY',self.q,1,1,'test',80,130)
        self.assertAlmostEqual(self.e.positions()[0][2],100.1)
    def test_kill_persists(self):
        with app.db() as c: c.execute("INSERT INTO settings VALUES('kill','1')")
        self.assertTrue(app.Orchestrator(self.e,self.r).killed())
    def test_mode_isolation(self):
        import inspect
        self.assertFalse(any(s in inspect.getsource(app) for s in ('placeOrder','modifyOrder','cancelOrder')))
        self.assertFalse(hasattr(app.AngelReadOnly,'order'))
    def test_dashboard_market_and_freshness(self):
        c=sqlite3.connect(app.MARKET_DB)
        c.executescript('''CREATE TABLE ticks(token TEXT, exchange_ts INTEGER, sequence INTEGER, received_ts REAL, price REAL, volume INTEGER, oi INTEGER);
        CREATE TABLE candles(token TEXT, minute INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER);
        CREATE TABLE health(component TEXT,status TEXT,updated_ts REAL,detail TEXT);
        CREATE TABLE observations(id INTEGER PRIMARY KEY,ts REAL,token TEXT,regime TEXT,recommendation TEXT,evidence TEXT);''')
        t=app.now(); c.execute('INSERT INTO ticks VALUES(?,?,?,?,?,?,?)',('99926000',int(t),1,t,22936.75,100,0)); c.execute('INSERT INTO candles VALUES(?,?,?,?,?,?,?)',('99926000',int(t)//60*60,22900,22950,22890,22936.75,100)); c.commit(); c.close()
        s=app.status(); self.assertFalse(s['market']['fresh']); self.assertEqual(s['market']['ticks'][0]['price'],22936.75); self.assertIn('max_daily_loss',s['risk_limits']); self.assertTrue(os.path.exists(app.DASHBOARD))

if __name__=='__main__': unittest.main()
