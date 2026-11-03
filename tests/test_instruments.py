import io,json,os,tempfile,unittest
import discover_instruments as d

class Resp:
    def __init__(self,x): self.x=json.dumps(x).encode()
    def __enter__(self): return io.BytesIO(self.x)
    def __exit__(self,*a): pass

class Tests(unittest.TestCase):
    def test_select(self):
        base={'token':'1','symbol':'NIFTY26OCT25000CE','name':'NIFTY','expiry':'29OCT2026','strike':'2500000','lotsize':'1','instrumenttype':'OPTIDX','exch_seg':'NFO','tick_size':'5'}
        self.assertEqual(d.select([base])[0]['token'],'1')
        self.assertEqual(d.select([{**base,'name':'BANKNIFTY'}]),[])
    def test_schema(self):
        with self.assertRaises(ValueError): d.normalize({})
    def test_incomplete(self):
        with self.assertRaises(RuntimeError): d.download(lambda req,timeout:Resp([]))

if __name__=='__main__': unittest.main()
