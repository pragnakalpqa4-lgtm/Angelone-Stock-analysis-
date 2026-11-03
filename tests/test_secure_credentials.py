import os,tempfile,unittest
import secure_credentials as s

class Tests(unittest.TestCase):
    def test_rfc_totp_vector(self):
        self.assertEqual(s.totp('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ',59,digits=8),'94287082')
    def test_seed_validation(self):
        with self.assertRaises(ValueError): s.normalize_seed('!invalid!')
    @unittest.skipUnless(os.name=='nt','Windows DPAPI only')
    def test_dpapi_roundtrip(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as d:
            p=os.path.join(d,'c.bin'); original={'api_key':'a','client_id':'b','pin':'c','totp_secret':''}
            s.save(original,p)
            with open(p,'rb') as f: encrypted=f.read()
            self.assertNotIn(b'api_key',encrypted); self.assertEqual(s.load(p),original)

if __name__=='__main__': unittest.main()
