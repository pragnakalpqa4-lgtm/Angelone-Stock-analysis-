import json
import unittest
from datetime import datetime,timedelta,timezone
from expert_team import analyze_instrument,MASTER_URL

IST=timezone(timedelta(hours=5,minutes=30))


class Tests(unittest.TestCase):
    def setUp(self):
        self.clock=datetime(2026,10,30,10,0,10,tzinfo=IST).timestamp()
        self.record={'token':'99926000','name':'NIFTY','exch_seg':'NSE','instrumenttype':'AMXIDX',
            'exchange_type':1,'source':MASTER_URL,'downloaded_at_utc':datetime.fromtimestamp(self.clock,timezone.utc).isoformat()}
        self.tick={'token':'99926000','price':23030,'exchange_ts':self.clock-1,'received_ts':self.clock-.5,'volume':0,'oi':0}
        self.health=[{'component':'runtime','status':'RUNNING','updated_ts':self.clock-1},
                     {'component':'websocket','status':'CONNECTED','updated_ts':self.clock-60}]
        start=int(self.clock//60)*60-30*60
        self.candles=[{'minute':start+i*60,'open':23000+i,'high':23002+i,'low':22999+i,'close':23001+i,'volume':None} for i in range(30)]
    def report(self,**overrides):
        args=dict(record=self.record,tick=self.tick,candles=self.candles,health=self.health,clock=self.clock)
        args.update(overrides); return analyze_instrument(**args)
    def role(self,report,identity): return next(role for role in report['specialists'] if role['id']==identity)
    def test_ready_context_always_no_trade_and_no_fake_confidence(self):
        report=self.report(); self.assertEqual(report['readiness'],'RESEARCH_ONLY')
        self.assertEqual(report['decision'],'NO TRADE'); self.assertTrue(report['orders_disabled'])
        self.assertEqual(len(report['specialists']),8); self.assertFalse(report['llm_used'])
        self.assertEqual(self.role(report,'technical')['evidence']['rsi14'],100)
        self.assertNotIn('confidence',report); json.dumps(report,allow_nan=False)
    def test_stale_receive_or_exchange_and_future_rejected(self):
        for key,value in [('exchange_ts',self.clock-11),('received_ts',self.clock-11),('exchange_ts',self.clock+3),('received_ts',self.clock+1)]:
            with self.subTest(key=key,value=value):
                report=self.report(tick=dict(self.tick,**{key:value}))
                self.assertEqual(report['readiness'],'BLOCKED'); self.assertEqual(self.role(report,'technical')['stance'],'UNAVAILABLE')
    def test_disconnected_missing_or_old_runtime_blocks(self):
        for health in ([],[dict(row,status='DISCONNECTED') if row['component']=='websocket' else row for row in self.health],
                       [dict(row,updated_ts=self.clock-11) if row['component']=='runtime' else row for row in self.health]):
            with self.subTest(health=health): self.assertEqual(self.report(health=health)['readiness'],'BLOCKED')
    def test_reconnect_requires_tick_after_open(self):
        health=[dict(row,updated_ts=self.clock-.25) if row['component']=='websocket' else row for row in self.health]
        self.assertEqual(self.report(health=health)['readiness'],'BLOCKED')
    def test_partial_candle_does_not_fill_history_requirement(self):
        candles=self.candles[-20:]+[dict(self.candles[-1],minute=int(self.clock//60)*60,close=100000,high=100001)]
        report=self.report(candles=candles)
        self.assertEqual(report['readiness'],'WARMING_UP'); self.assertEqual(self.role(report,'regime')['status'],'READY')
        self.assertEqual(self.role(report,'data_quality')['evidence']['excluded_partial_minutes'],1)
    def test_gap_breaks_contiguous_window(self):
        report=self.report(candles=[c for i,c in enumerate(self.candles) if i!=20])
        self.assertEqual(report['readiness'],'WARMING_UP')
        self.assertEqual(self.role(report,'data_quality')['evidence']['contiguous_recent_minutes'],9)
    def test_missing_latest_minute_does_not_use_old_window(self):
        report=self.report(candles=self.candles[:-1]); self.assertEqual(report['readiness'],'WARMING_UP')
        self.assertEqual(self.role(report,'data_quality')['evidence']['contiguous_recent_minutes'],0)
    def test_previous_day_history_and_master_cannot_pass(self):
        old=[dict(c,minute=c['minute']-86400) for c in self.candles]
        self.assertEqual(self.report(candles=old)['readiness'],'WARMING_UP')
        oldrecord=dict(self.record,downloaded_at_utc=datetime.fromtimestamp(self.clock-86400,timezone.utc).isoformat())
        self.assertEqual(self.report(record=oldrecord)['readiness'],'BLOCKED')
    def test_future_same_day_master_provenance_cannot_pass(self):
        record=dict(self.record,downloaded_at_utc=datetime.fromtimestamp(self.clock+3600,timezone.utc).isoformat())
        self.assertEqual(self.report(record=record)['readiness'],'BLOCKED')
    def test_master_identity_scope_and_tick_token(self):
        for record in (dict(self.record,name='CRUDEOIL'),dict(self.record,token='573422'),dict(self.record,exch_seg='BSE'),dict(self.record,source='unverified')):
            with self.subTest(record=record): self.assertEqual(self.report(record=record)['readiness'],'BLOCKED')
        self.assertEqual(self.report(tick=dict(self.tick,token='99919000'))['readiness'],'BLOCKED')
    def test_sensex_same_rules_and_dict_health(self):
        record=dict(self.record,name='SENSEX',token='99919000',exch_seg='BSE',exchange_type=3)
        report=self.report(record=record,tick=dict(self.tick,token='99919000'),health={r['component']:r for r in self.health})
        self.assertEqual(report['readiness'],'RESEARCH_ONLY'); self.assertEqual(report['instrument'],'SENSEX')
    def test_derivatives_not_inferred_from_index_fields(self):
        report=self.report(tick=dict(self.tick,volume=999999999,oi=999999999))
        derivative=self.role(report,'derivatives')
        self.assertEqual(derivative['status'],'UNAVAILABLE'); self.assertFalse(derivative['evidence']['index_volume_used'])
        self.assertFalse(derivative['evidence']['vwap_available']); self.assertEqual(report['decision'],'NO TRADE')
    def test_kill_veto_and_candle_integrity(self):
        self.assertEqual(self.report(kill=True)['readiness'],'BLOCKED')
        candles=[dict(c) for c in self.candles]; candles[-1]['high']=1
        self.assertEqual(self.report(candles=candles)['readiness'],'BLOCKED')
        self.assertEqual(self.report(candles=self.candles+[dict(self.candles[-1])])['readiness'],'BLOCKED')
    def test_non_finite_tick_json_safe(self):
        report=self.report(tick=dict(self.tick,price=float('inf'),exchange_ts=float('nan')))
        self.assertEqual(report['readiness'],'BLOCKED'); json.dumps(report,allow_nan=False)
        report=self.report(record=dict(self.record,source=float('nan'),downloaded_at_utc=float('nan'),sha256=float('inf')))
        self.assertEqual(report['readiness'],'BLOCKED'); json.dumps(report,allow_nan=False)
    def test_malformed_candles_and_extreme_finite_values_fail_closed(self):
        for value in (float('nan'),float('inf'),'invalid',None):
            candles=[dict(c) for c in self.candles]; candles[-1]['high']=value
            report=self.report(candles=candles)
            self.assertEqual(report['readiness'],'BLOCKED'); json.dumps(report,allow_nan=False)
        candles=[dict(c,open=1e-308,high=1e-308,low=1e-308,close=1e-308) for c in self.candles]
        candles[-1].update(open=1e308,high=1e308,low=1e308,close=1e308)
        report=self.report(candles=candles)
        self.assertEqual(report['readiness'],'BLOCKED'); json.dumps(report,allow_nan=False)
    def test_outside_clock_window_and_holiday_never_claimed_open(self):
        outside=datetime(2026,10,30,16,0,tzinfo=IST).timestamp()
        self.assertEqual(self.report(clock=outside)['readiness'],'BLOCKED')
        report=self.report(); self.assertEqual(report['session_status'],'CLOCK_WINDOW_UNVERIFIED')
        self.assertEqual(self.role(report,'data_quality')['evidence']['exchange_holiday_calendar'],'NOT_VERIFIED')
    def test_no_tick_and_datetime_clock(self):
        report=self.report(tick=None,clock=datetime.fromtimestamp(self.clock,IST))
        self.assertEqual(report['readiness'],'BLOCKED'); self.assertEqual(report['decision'],'NO TRADE')

if __name__=='__main__': unittest.main()
