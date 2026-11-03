import unittest
from datetime import datetime,timezone,timedelta
import live_market_check as m

def row(name,expiry,strike,kind,token):
    return {'name':name,'expiry':expiry,'strike':str(strike*100),'symbol':f'{name}{strike}{kind}','token':token,'lotsize':'20','tick_size':'5','instrumenttype':'OPTIDX','exch_seg':'BFO'}

class Tests(unittest.TestCase):
    def test_expiry_after_cutoff(self):
        rows=[row('SENSEX','29OCT2026',80000,'CE','1'),row('SENSEX','05NOV2026',80000,'CE','2')]
        z=timezone(timedelta(hours=5,minutes=30),'IST')
        self.assertEqual(str(m.active_expiry(rows,'SENSEX',datetime(2026,10,29,14,tzinfo=z))),'2026-10-29')
        self.assertEqual(str(m.active_expiry(rows,'SENSEX',datetime(2026,10,29,16,tzinfo=z))),'2026-11-05')
    def test_atm_complete_pair(self):
        rows=[row('NIFTY','03NOV2026',25000,'CE','1'),row('NIFTY','03NOV2026',25000,'PE','2'),row('NIFTY','03NOV2026',25100,'CE','3')]
        ce,pe,strike=m.option_pair(rows,'NIFTY',m.parse_date('03NOV2026'),25040)
        self.assertEqual((ce['token'],pe['token'],strike),('1','2',25000))
    def test_index_unique(self):
        r={'name':'NIFTY','instrumenttype':'AMXIDX','token':'99926000'}
        self.assertEqual(m.index_record([r],'NIFTY')['token'],'99926000')

if __name__=='__main__': unittest.main()
