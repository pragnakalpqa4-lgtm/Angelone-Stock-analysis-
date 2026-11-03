"""Active research universe. These are index reference feeds, not executable contracts."""
INDEX_SCOPE=(
    {'name':'NIFTY','symbol':'Nifty 50','token':'99926000','exch_seg':'NSE','exchange_type':1,'instrumenttype':'AMXIDX'},
    {'name':'SENSEX','symbol':'SENSEX','token':'99919000','exch_seg':'BSE','exchange_type':3,'instrumenttype':'AMXIDX'},
)
INDEX_TOKENS=tuple(r['token'] for r in INDEX_SCOPE)


def active_record(record):
    return any(all(str(record.get(k,''))==str(expected[k]) for k in ('name','token','exch_seg','instrumenttype')) for expected in INDEX_SCOPE)
