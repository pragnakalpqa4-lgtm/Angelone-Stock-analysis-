import os,tempfile,unittest
import stream_runtime as s

class Tests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(dir=os.getcwd()); self.db=os.path.join(self.tmp.name,'m.db'); self.store=s.MarketStore(self.db)
    def tearDown(self): self.tmp.cleanup()
    def tick(self,seq=1,price=123450,ts=1800000000000): return s.normalize_tick({'token':'x','last_traded_price':price,'exchange_timestamp':ts,'sequence_number':seq,'volume_trade_for_the_day':10},received=ts/1000+1)
    def test_normalize_paise(self): self.assertEqual(self.tick()['price'],1234.5)
    def test_deduplicate_and_candle(self):
        t=self.tick(); self.assertTrue(self.store.ingest(t)); self.assertFalse(self.store.ingest(t)); self.assertTrue(self.store.ingest(self.tick(2,123550,1800000030000)))
        with self.store.connect() as c:
            candle=c.execute('SELECT open,high,low,close FROM candles').fetchone(); ticks=c.execute('SELECT COUNT(*) FROM ticks').fetchone()[0]
        self.assertEqual(candle,(1234.5,1235.5,1234.5,1235.5)); self.assertEqual(ticks,2)
    def test_invalid(self):
        with self.assertRaises(ValueError): self.tick(price=0)

if __name__=='__main__': unittest.main()
