import io, json, unittest
import connection_check as c

class Response:
    def __init__(self,data): self.data=json.dumps(data).encode()
    def __enter__(self): return io.BytesIO(self.data)
    def __exit__(self,*a): pass

class ConnectionTests(unittest.TestCase):
    def test_auth_and_quote(self):
        requests=[]
        def opener(req,timeout):
            requests.append((req.full_url,json.loads(req.data)))
            return Response({'status':True,'data':{'jwtToken':'test','feedToken':'feed','refreshToken':'refresh'}} if req.full_url==c.LOGIN else {'status':True,'data':{'fetched':[{'symbolToken':'1'}],'unfetched':[]}})
        headers=c.authenticate('key','client','pin','123456','mac','local','public',opener)
        self.assertEqual(len(c.quote(headers,'NSE','1',opener)),1)
        self.assertEqual([r[0] for r in requests],[c.LOGIN,c.QUOTE])
    def test_unfetched_fails(self):
        def opener(req,timeout): return Response({'status':True,'data':{'fetched':[],'unfetched':[{}]}})
        with self.assertRaises(RuntimeError): c.quote({},'NSE','1',opener)
    def test_endpoint_allowlist(self):
        with self.assertRaises(ValueError): c.post('https://example.com',{}, {})

if __name__=='__main__': unittest.main()
