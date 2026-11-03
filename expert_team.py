"""Deterministic, read-only NIFTY/SENSEX research specialists.

These are transparent local rule functions, not LLM agents or a validated
trading strategy. Each role receives the same snapshot; no role can place an
order. Completed bars mean closed *observed snapshot* minutes, not certified
exchange trade candles. Missing data never becomes a confidence score.
"""
import math
import time
from datetime import datetime, timedelta, timezone

IST=timezone(timedelta(hours=5,minutes=30))
MASTER_URL='https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json'
VERSION='index-expert-team-v1'
IDENTITIES={
    '99926000': {'name':'NIFTY','label':'NIFTY 50','exchange':'NSE','exchange_type':1},
    '99919000': {'name':'SENSEX','label':'SENSEX','exchange':'BSE','exchange_type':3},
}


def _number(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (ValueError,TypeError,OverflowError): return None


def _epoch(value):
    if isinstance(value,datetime):
        return value.replace(tzinfo=IST).timestamp() if value.tzinfo is None else value.timestamp()
    return _number(value)


def _provenance_text(value):
    return value if isinstance(value,str) else value.isoformat() if isinstance(value,datetime) else None


def _verified_date(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if parsed.tzinfo is None: return None
        return parsed.astimezone(IST).date()
    except (TypeError,ValueError,OverflowError): return None


def _verified_epoch(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (TypeError,ValueError,OverflowError,OSError): return None


def _health_rows(health):
    if isinstance(health,list): return {r.get('component'):r for r in health if isinstance(r,dict)}
    if isinstance(health,dict):
        return {key:(value if isinstance(value,dict) else {'status':value}) for key,value in health.items()}
    return {}


def _role(identity,name,status,stance,evidence=None,blockers=None):
    return {'id':identity,'name':name,'status':status,'stance':stance,
            'evidence':evidence or {},'blockers':list(blockers or [])}


def _session_bars(candles,token,clock):
    local=datetime.fromtimestamp(clock,IST)
    start=local.replace(hour=9,minute=15,second=0,microsecond=0).timestamp()
    end=local.replace(hour=15,minute=30,second=0,microsecond=0).timestamp()
    expected=int(min(clock//60*60-60,end-60))
    valid={}; invalid=[]; partial=0; other_session=0
    for candle in candles or []:
        if not isinstance(candle,dict): invalid.append('non-object candle'); continue
        minute=_number(candle.get('minute'))
        if minute is None or minute%60: invalid.append('invalid minute boundary'); continue
        if minute<start or minute>=end: other_session+=1; continue
        if minute+60>clock: partial+=1; continue
        if candle.get('token') is not None and str(candle['token'])!=token:
            invalid.append('candle token mismatch'); continue
        values={key:_number(candle.get(key)) for key in ('open','high','low','close')}
        if any(value is None or value<=0 for value in values.values()):
            invalid.append('invalid candle OHLC'); continue
        if not (values['low']<=min(values['open'],values['close'])<=max(values['open'],values['close'])<=values['high']):
            invalid.append('inconsistent candle OHLC'); continue
        if int(minute) in valid: invalid.append('duplicate candle minute'); continue
        valid[int(minute)]=dict(values,minute=int(minute))
    tail=[]; cursor=expected
    while cursor in valid:
        tail.append(valid[cursor]); cursor-=60
    tail.reverse()
    return tail,{'closed_observed_minutes':len(valid),'contiguous_recent_minutes':len(tail),
        'excluded_partial_minutes':partial,'excluded_other_session_minutes':other_session,
        'expected_latest_closed_minute':expected if expected>=start else None,
        'latest_available_closed_minute':max(valid) if valid else None,
        'coverage':'observed snapshots; within-minute coverage and holidays unverified',
        'invalid_candle_reasons':sorted(set(invalid))}


def _wilder(values,length=14):
    average=_mean(values[:length])
    for value in values[length:]: average=average*((length-1)/length)+value/length
    return average


def _mean(values):
    scale=max(values)
    return (sum(value/scale for value in values)/len(values))*scale if scale else 0.0


def _technical(bars):
    closes=[bar['close'] for bar in bars]
    changes=[b-a for a,b in zip(closes,closes[1:])]
    gain=_wilder([max(value,0) for value in changes])
    loss=_wilder([max(-value,0) for value in changes])
    scale=max(gain,loss)
    rsi=50.0 if not scale else 100*(gain/scale)/(gain/scale+loss/scale)
    ranges=[max(bar['high']-bar['low'],abs(bar['high']-previous['close']),abs(bar['low']-previous['close']))
            for previous,bar in zip(bars,bars[1:])]
    sma5=_mean(closes[-5:]); sma20=_mean(closes[-20:])
    stance='UPWARD_PRICE_CONTEXT' if sma5>sma20 and rsi>50 else 'DOWNWARD_PRICE_CONTEXT' if sma5<sma20 and rsi<50 else 'MIXED_PRICE_CONTEXT'
    return stance,{'sma5':round(sma5,6),'sma20':round(sma20,6),'rsi14':round(rsi,6),
        'atr14_points':round(_wilder(ranges),6),'closed_observed_minutes_used':len(bars),
        'indicator_method':'RSI/ATR Wilder averages initialized from first 14 changes/ranges in supplied contiguous current-session window',
        'signal_validation':'descriptive context only; thresholds and profitability not validated'}


def analyze_instrument(record,tick,candles,health,clock=None,kill=False):
    """Return a JSON-safe research report from one immutable caller snapshot.

    clock accepts Unix seconds or datetime. A naive datetime is treated as IST.
    Missing or stale evidence blocks research readiness; execution is always
    NO TRADE regardless of readiness or agreement between specialists.
    """
    timestamp=_epoch(clock) if clock is not None else time.time()
    if timestamp is None: raise ValueError('clock must be a finite timestamp')
    local=datetime.fromtimestamp(timestamp,IST)
    record=record if isinstance(record,dict) else {}
    tick=tick if isinstance(tick,dict) else {}
    token=str(record.get('token','')); expected=IDENTITIES.get(token)
    label=expected['label'] if expected else 'UNSUPPORTED INSTRUMENT'
    blockers=[]; specialists=[]
    valid_name=str(record.get('name','')).upper().replace(' ','')
    names=('NIFTY','NIFTY50') if expected and expected['name']=='NIFTY' else ('SENSEX',)
    identity_ok=bool(expected and valid_name in names and str(record.get('exch_seg','')).upper()==expected['exchange']
                     and str(record.get('instrumenttype','')).upper()=='AMXIDX'
                     and (record.get('exchange_type') is None or record['exchange_type']==expected['exchange_type']))
    if not identity_ok: blockers.append('Exact NIFTY/SENSEX index identity is not verified')
    verified_at=record.get('downloaded_at_utc') or record.get('verified_at_utc')
    verification_epoch=_verified_epoch(verified_at)
    today_verified=bool(_verified_date(verified_at)==local.date() and record.get('source')==MASTER_URL
                        and verification_epoch is not None and 0<verification_epoch<=timestamp+2)
    if not today_verified: blockers.append('Official instrument-master provenance is missing, future-dated or not verified for today IST')
    in_window=local.weekday()<5 and (9,15)<=(local.hour,local.minute)<(15,30)
    if not in_window: blockers.append('Outside the weekday 09:15–15:30 IST clock window')
    price=_number(tick.get('price')); exchange_ts=_number(tick.get('exchange_ts')); received_ts=_number(tick.get('received_ts'))
    exchange_age=timestamp-exchange_ts if exchange_ts is not None else None
    receive_age=timestamp-received_ts if received_ts is not None else None
    if not tick: blockers.append('No live tick available')
    elif str(tick.get('token',''))!=token: blockers.append('Tick token does not match the verified index')
    if tick.get('source') not in (None,'LIVE'): blockers.append('Tick source is not live market data')
    if price is None or price<=0: blockers.append('Valid positive index price unavailable')
    if exchange_age is None or not -2<=exchange_age<=10: blockers.append('Exchange timestamp is stale, invalid or too far in the future')
    if receive_age is None or not 0<=receive_age<=10: blockers.append('Receive timestamp is stale, invalid or in the future')
    states=_health_rows(health); runtime=states.get('runtime',{}); websocket=states.get('websocket',{})
    runtime_ts=_number(runtime.get('updated_ts')); ws_ts=_number(websocket.get('updated_ts'))
    operations=[]
    if runtime.get('status')!='RUNNING': operations.append('Collector runtime is not RUNNING')
    if runtime_ts is None or not 0<=timestamp-runtime_ts<=10: operations.append('Runtime heartbeat is missing or older than ten seconds')
    if websocket.get('status')!='CONNECTED': operations.append('WebSocket is not CONNECTED')
    if ws_ts is None or ws_ts>timestamp+2: operations.append('Connection generation timestamp is missing or invalid')
    elif received_ts is None or received_ts<ws_ts: operations.append('A fresh tick has not arrived after this connection opened')
    bars,bar_evidence=_session_bars(candles,token,timestamp)
    if bar_evidence['invalid_candle_reasons']: blockers.append('Candle integrity failed: '+', '.join(bar_evidence['invalid_candle_reasons']))
    data_blockers=list(blockers)+operations
    session_status='CLOCK_WINDOW_UNVERIFIED' if in_window else 'OUTSIDE_CLOCK_WINDOW'
    specialists.append(_role('data_quality','Data quality specialist','BLOCKED' if data_blockers else 'PASS',
        'UNAVAILABLE' if data_blockers else 'FRESH_OBSERVED_FEED',
        {'identity_verified':identity_ok,'master_verified_today':today_verified,'index_price':price if price and price>0 else None,
         'exchange_age_seconds':round(exchange_age,3) if exchange_age is not None else None,
         'receive_age_seconds':round(receive_age,3) if receive_age is not None else None,
         'session_status':session_status,'exchange_holiday_calendar':'NOT_VERIFIED',**bar_evidence},data_blockers))
    specialists.append(_role('operations','Operations specialist','BLOCKED' if operations else 'PASS',
        'DISCONNECTED_OR_UNHEALTHY' if operations else 'COLLECTOR_HEALTHY',
        {'runtime_status':str(runtime.get('status','UNKNOWN')),'websocket_status':str(websocket.get('status','UNKNOWN')),
         'heartbeat_age_seconds':round(timestamp-runtime_ts,3) if runtime_ts is not None else None,
         'connection_opened_ts':ws_ts},operations))
    history_needed=max(0,21-len(bars))
    analysis_blockers=[]; regime_blockers=list(data_blockers)
    if len(bars)<20: regime_blockers.append('Need 20 contiguous recent closed current-session candles; have '+str(len(bars)))
    regime_evidence={'required_closed_minutes':20,'available_contiguous_closed_minutes':len(bars)}
    regime_stance='UNAVAILABLE'
    if not regime_blockers:
        closes=[bar['close'] for bar in bars[-20:]]
        scale=max(closes); normalized=[value/scale for value in closes]
        movement=sum(abs(b-a) for a,b in zip(normalized,normalized[1:]))
        change=(closes[-1]-closes[0])/closes[0]*100
        efficiency=abs(normalized[-1]-normalized[0])/movement if movement else 0
        if not math.isfinite(change) or not math.isfinite(efficiency):
            reason='Numerical range prevents reliable price-context calculation'
            analysis_blockers.append(reason); regime_blockers.append(reason)
        else:
            regime_stance='DIRECTIONAL_UP_CONTEXT' if efficiency>=.5 and change>0 else 'DIRECTIONAL_DOWN_CONTEXT' if efficiency>=.5 and change<0 else 'RANGING_OR_UNCERTAIN'
            regime_evidence.update({'observed_return_percent':round(change,6),'path_efficiency':round(efficiency,6),
                'rule':'Endpoint direction with path efficiency >=0.5; illustrative unvalidated context classifier'})
    specialists.append(_role('regime','Market regime specialist','BLOCKED' if data_blockers or analysis_blockers else 'WARMING_UP' if regime_blockers else 'READY',regime_stance,regime_evidence,regime_blockers))
    technical_blockers=list(data_blockers)
    if len(bars)<21: technical_blockers.append('Need 21 contiguous recent closed current-session candles; have '+str(len(bars)))
    technical_stance='UNAVAILABLE'; technical_evidence={'required_closed_minutes':21,'available_contiguous_closed_minutes':len(bars)}
    if not technical_blockers:
        try:
            technical_stance,technical_evidence=_technical(bars)
            if any(isinstance(value,float) and not math.isfinite(value) for value in technical_evidence.values()):
                raise ArithmeticError('non-finite indicator')
        except (ArithmeticError,ValueError):
            reason='Numerical range prevents reliable technical-indicator calculation'
            analysis_blockers.append(reason); technical_blockers.append(reason)
            technical_stance='UNAVAILABLE'; technical_evidence={'calculation':'unavailable'}
    specialists.append(_role('technical','Technical context specialist','BLOCKED' if data_blockers or analysis_blockers else 'WARMING_UP' if technical_blockers else 'READY',technical_stance,technical_evidence,technical_blockers))
    missing_derivatives=['No verified live option contracts, bid/ask depth, option-chain coverage or Greeks in this index snapshot',
                         'Index volume/open interest cannot establish executable derivatives liquidity']
    specialists.append(_role('derivatives','Derivatives evidence specialist','UNAVAILABLE','NO_DERIVATIVES_ASSESSMENT',
        {'index_volume_used':False,'index_oi_used':False,'vwap_available':False,'options_depth_available':False,'greeks_available':False},missing_derivatives))
    risk_blockers=['All real and simulated orders are disabled','No independently validated strategy or execution-risk evidence']
    if kill: risk_blockers.insert(0,'Emergency stop is active')
    specialists.append(_role('risk','Risk veto specialist','VETO','NO TRADE',
        {'orders_disabled':True,'emergency_stop':bool(kill),'sizing_performed':False,'exposure_inputs_available':False},risk_blockers))
    critical=list(dict.fromkeys(data_blockers+analysis_blockers+(['Emergency stop is active'] if kill else [])))
    readiness='BLOCKED' if critical else 'WARMING_UP' if len(bars)<21 else 'RESEARCH_ONLY'
    reviewer_blockers=critical+(['Insufficient contiguous closed-candle history'] if len(bars)<21 else [])
    specialists.append(_role('reviewer','Cross-check reviewer',readiness,'NO TRADE',
        {'same_snapshot_review':True,'separate_rule_checks':True,'independent_external_validation':False,
         'can_override_data_or_risk_veto':False,'strategy_validated':False},reviewer_blockers+missing_derivatives))
    explanation=('Research blocked: '+ '; '.join(critical)) if critical else (
        'Waiting for '+str(history_needed)+' additional contiguous closed observed minute(s).' if history_needed else
        'Descriptive index context is available. Derivatives, holiday/session validation and strategy evidence remain insufficient for any trade.')
    specialists.append(_role('coordinator','Research coordinator',readiness,'NO TRADE',
        {'aggregation':'Evidence gates; no majority vote can override a blocker','remaining_closed_minutes_needed':history_needed,
         'explanation':explanation},critical))
    return {'version':VERSION,'team_type':'DETERMINISTIC_RULE_BASED','llm_used':False,'generated_ts':timestamp,
        'instrument':expected['name'] if expected else 'UNSUPPORTED','name':label,'token':token,
        'readiness':readiness,'decision':'NO TRADE','orders_disabled':True,'strategy_validated':False,
        'session_status':session_status,'explanation':explanation,'blockers':critical,
        'limitations':['Clock window does not verify exchange holidays or an active trading session.',
            'Candles summarize observed snapshots; missing trade activity is not reconstructed.',
            'Specialists are deterministic functions over shared data, not autonomous AI or independent market sources.',
            'Research context is not an order instruction or profitability claim.']+missing_derivatives,
        'provenance':{'source':_provenance_text(record.get('source')),'master_verified_at_utc':_provenance_text(verified_at),
            'master_sha256':_provenance_text(record.get('sha256')),'clock_timezone':'Asia/Kolkata'},'specialists':specialists}
