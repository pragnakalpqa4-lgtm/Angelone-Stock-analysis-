"""Windows DPAPI credential storage for the current Windows user only."""
import base64,ctypes,getpass,hashlib,hmac,json,os,struct,time
from ctypes import wintypes

STORE=os.path.join(os.environ.get('LOCALAPPDATA',os.path.expanduser('~')),'AngelPaper','credentials.dpapi')
CRYPTPROTECT_UI_FORBIDDEN=0x1

class DATA_BLOB(ctypes.Structure):
    _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]

def blob(data):
    buf=ctypes.create_string_buffer(data); return DATA_BLOB(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte))),buf

def protect(data):
    if os.name!='nt': raise RuntimeError('DPAPI requires Windows')
    source,keep=blob(data); out=DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source),'AngelPaper SmartAPI credentials',None,None,None,CRYPTPROTECT_UI_FORBIDDEN,ctypes.byref(out)):
        raise ctypes.WinError()
    try: return ctypes.string_at(out.pbData,out.cbData)
    finally: ctypes.windll.kernel32.LocalFree(out.pbData)

def unprotect(data):
    if os.name!='nt': raise RuntimeError('DPAPI requires Windows')
    source,keep=blob(data); out=DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source),None,None,None,None,CRYPTPROTECT_UI_FORBIDDEN,ctypes.byref(out)):
        raise ctypes.WinError()
    try: return ctypes.string_at(out.pbData,out.cbData)
    finally: ctypes.windll.kernel32.LocalFree(out.pbData)

def normalize_seed(seed):
    seed=''.join(seed.upper().split()).replace('-','')
    if not seed: return ''
    try: base64.b32decode(seed+'='*((8-len(seed)%8)%8),casefold=True)
    except Exception as exc: raise ValueError('TOTP setup secret is not valid base32') from exc
    return seed

def save(values,path=STORE):
    allowed={'api_key','client_id','pin','totp_secret'}
    if set(values)!=allowed or not all(values[k] for k in ('api_key','client_id','pin')): raise ValueError('required credentials missing')
    clean={k:str(values[k]).strip() for k in allowed}; clean['totp_secret']=normalize_seed(clean['totp_secret'])
    os.makedirs(os.path.dirname(path),exist_ok=True)
    temp=path+'.tmp'
    with open(temp,'wb') as f: f.write(protect(json.dumps(clean,separators=(',',':')).encode()))
    os.replace(temp,path)
    return path

def load(path=STORE):
    with open(path,'rb') as f: raw=f.read()
    result=json.loads(unprotect(raw).decode())
    if not all(result.get(k) for k in ('api_key','client_id','pin')): raise RuntimeError('credential store is incomplete')
    return result

def totp(seed,at=None,step=30,digits=6):
    seed=normalize_seed(seed); at=time.time() if at is None else at
    key=base64.b32decode(seed+'='*((8-len(seed)%8)%8),casefold=True)
    digest=hmac.new(key,struct.pack('>Q',int(at)//step),hashlib.sha1).digest(); offset=digest[-1]&15
    code=(struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%(10**digits)
    return str(code).zfill(digits)

def setup():
    print('One-time local setup. Values are encrypted with Windows DPAPI and are never printed.')
    print('TOTP setup secret is optional. Leave blank to enter only the 6-digit TOTP each day.')
    values={'api_key':getpass.getpass('SmartAPI API key: '),'client_id':getpass.getpass('Client ID: '),
            'pin':getpass.getpass('Angel One PIN: '),'totp_secret':getpass.getpass('TOTP setup secret (optional, NOT the 6-digit code): ')}
    path=save(values)
    print('Encrypted credential store created:',path)
    print('Automatic TOTP enabled:',bool(values['totp_secret']))

if __name__=='__main__': setup()
