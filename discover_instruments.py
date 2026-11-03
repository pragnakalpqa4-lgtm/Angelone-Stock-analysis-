"""Download the official daily Angel One master and retain NIFTY/SENSEX records.

No credentials are used. Output contains public instrument metadata only.
"""
import json, os, tempfile, urllib.request
from datetime import datetime, timezone

URL='https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json'
OUTPUT=os.path.join(os.path.dirname(__file__),'verified_instruments.json')
REQUIRED={'token','symbol','name','expiry','strike','lotsize','instrumenttype','exch_seg','tick_size'}

def normalize(row):
    if not REQUIRED.issubset(row): raise ValueError('instrument master schema changed')
    return {k:str(row[k]) for k in sorted(REQUIRED)}

def select(rows):
    selected=[]
    for raw in rows:
        name=str(raw.get('name','')).upper().strip()
        symbol=str(raw.get('symbol','')).upper().strip()
        seg=str(raw.get('exch_seg','')).upper().strip()
        typ=str(raw.get('instrumenttype','')).upper().strip()
        is_index=(name=='NIFTY' and ('NIFTY 50' in symbol or symbol=='NIFTY')) or name=='SENSEX'
        is_option=name in ('NIFTY','SENSEX') and typ=='OPTIDX' and seg in ('NFO','BFO','NSE_FO','BSE_FO')
        if is_index or is_option: selected.append(normalize(raw))
    return selected

def download(opener=urllib.request.urlopen):
    req=urllib.request.Request(URL,headers={'User-Agent':'AngelPaperResearch/0.1'})
    with opener(req,timeout=60) as response: rows=json.load(response)
    if not isinstance(rows,list) or len(rows)<1000: raise RuntimeError('instrument master is incomplete')
    selected=select(rows)
    if not selected or not any(x['instrumenttype'].upper()=='OPTIDX' for x in selected): raise RuntimeError('required index options not present')
    payload={'source':URL,'downloaded_at_utc':datetime.now(timezone.utc).isoformat(),'records':selected}
    fd,tmp=tempfile.mkstemp(dir=os.path.dirname(OUTPUT),prefix='instruments-',suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(payload,f,indent=2)
        os.replace(tmp,OUTPUT)
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    return payload

def summary(payload):
    records=payload['records']; expiries={}
    for name in ('NIFTY','SENSEX'):
        exp=sorted({datetime.strptime(r['expiry'],'%d%b%Y').date() for r in records if r['name'].upper()==name and r['instrumenttype'].upper()=='OPTIDX' and r['expiry']})
        expiries[name]=[x.strftime('%d%b%Y').upper() for x in exp[:3]]
    return {'saved':OUTPUT,'records':len(records),'nearest_expiries_as_listed':expiries}

if __name__=='__main__': print(json.dumps(summary(download()),indent=2))
