"""Explicit native Cookie-header import. Attributes are unknown, never inferred as facts."""
import base64
import re
import time

import httpx
from fastapi import HTTPException

IDENTITY_URL='https://accounts.google.com/ListAccounts?gpsia=1&source=ChromiumBrowser&json=standard&laf=b64bin'
# Authentication only: no analytics, user-supplied headers, or arbitrary target URLs.
GOOGLE_NAMES={'SID','HSID','SSID','APISID','SAPISID','SIDCC','__Secure-1PSID','__Secure-3PSID',
              '__Secure-1PAPISID','__Secure-3PAPISID','__Secure-1PSIDTS','__Secure-3PSIDTS',
              '__Secure-1PSIDCC','__Secure-3PSIDCC','NID','AEC','__Secure-ENID','__Secure-BUCKET'}
ACCOUNTS_NAMES=GOOGLE_NAMES|{'GAPS','LSID','__Host-GAPS','__Host-1PLSID','__Host-3PLSID','__Host-LSID','ACCOUNT_CHOOSER'}


def parse_header(raw):
    if not isinstance(raw,str) or not raw or len(raw)>65536 or re.search(r'[\x00-\x1f\x7f]',raw):
        raise HTTPException(422,'Cookie 为空、过长或包含换行；请仅粘贴浏览器复制的 Cookie 值')
    raw=raw.strip()
    if raw.lower().startswith('cookie:'):raw=raw[7:].strip()
    result={}
    for part in raw.split(';'):
        if not part.strip():continue
        name,sep,value=part.strip().partition('=')
        if not sep or not re.fullmatch(r"[-!#$%&'*+.^_`|~0-9A-Za-z]{1,256}",name) or (len(value)>20000 or not value.isascii() or re.search(r'[\x00-\x20\x7f;,"\\]',value)) or name.lower() in {'domain','path','expires','max-age','secure','httponly','samesite','partitioned'}:
            raise HTTPException(422,'不是有效 Cookie 请求头；不要粘贴 Set-Cookie 属性、JSON 或其他请求头')
        if name in result:raise HTTPException(422,'Cookie 名称重复，请从同一页面重新复制，不要合并不同账号')
        result[name]=value
    if not result or len(result)>300:raise HTTPException(422,'Cookie 项数无效')
    return result


def decode_accounts(raw):
    def fields(data):
        pos=0;out=[]
        def varint():
            nonlocal pos
            value=0
            for shift in range(0,56,7):
                if pos>=len(data):raise ValueError()
                x=data[pos];pos+=1;value|=(x&127)<<shift
                if not x&128:return value
            raise ValueError()
        while pos<len(data):
            tag=varint();num,typ=tag>>3,tag&7
            if not num:raise ValueError()
            if typ==0:value=varint()
            elif typ==2:
                n=varint()
                if n>len(data)-pos:raise ValueError()
                value=data[pos:pos+n];pos+=n
            elif typ in (1,5):
                pos+=8 if typ==1 else 4
                if pos>len(data):raise ValueError()
                continue
            else:raise ValueError()
            out.append((num,typ,value))
        return out
    try:
        import json
        text=raw.decode().strip()
        if text.startswith('"'):text=json.loads(text)
        data=base64.b64decode(text+'='*((-len(text))%4),validate=True)
        rows=[]
        for index,(num,typ,body) in enumerate(fields(data)):
            if num!=1 or typ!=2 or index>=100:raise ValueError()
            f=fields(body)
            def get(n,t,default):
                values=[v for k,w,v in f if k==n and w==t]
                if len(values)>1:raise ValueError()
                return values[0] if values else default
            email=get(3,2,b'').decode().lower();gaia=get(10,2,b'').decode()
            valid=get(9,0,1) and not get(14,0,0) and get(15,0,1)
            if valid and re.fullmatch(r'[^\s@]{1,200}@[^\s@]{1,100}',email) and re.fullmatch(r'\d{1,32}',gaia):
                rows.append({'email':email,'gaia_id':gaia,'authuser':index})
        if len({x['gaia_id'] for x in rows})!=len(rows):raise ValueError()
        return rows
    except (ValueError,TypeError,UnicodeError):
        raise HTTPException(422,'Google 账号响应无法识别，未保存 Cookie；请改用 JSON 导出或稍后重试') from None


async def verify_native(raw,accounts_raw='',*,transport=None):
    main={k:v for k,v in parse_header(raw).items() if k in GOOGLE_NAMES}
    if not {'SAPISID','__Secure-1PAPISID','__Secure-3PAPISID'}<=main.keys() or not any(main.get(n) for n in ['SID','__Secure-1PSID','__Secure-3PSID']) or any(not v for v in main.values()):
        raise HTTPException(422,'缺少 Google 会话关键项，请使用浏览器原生复制 Cookie；document.cookie 不能代替完整会话')
    extra={k:v for k,v in parse_header(accounts_raw).items() if k in ACCOUNTS_NAMES} if accounts_raw else {}
    for k in main.keys()&extra.keys():
        if main[k]!=extra[k]:raise HTTPException(422,'两处 Cookie 的登录会话冲突，请在同一浏览器重新复制')
    selected=main|extra
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=False,trust_env=False,transport=transport) as client:
            async with client.stream('POST',IDENTITY_URL,headers={'Cookie':'; '.join(k+'='+v for k,v in selected.items()),'Origin':'https://www.google.com','Referer':'https://aistudio.google.com/','Content-Type':'application/x-www-form-urlencoded','User-Agent':'Mozilla/5.0'},content=b' ') as response:
                if response.status_code!=200:raise HTTPException(422,'Google 未接受此登录会话，未保存；请重新复制或改用 JSON 导出')
                body=bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body)>100000:raise HTTPException(422,'Google 账号响应过大，未保存 Cookie')
    except httpx.HTTPError:
        raise HTTPException(502,'服务器无法连接 Google 身份接口，未保存 Cookie；请稍后重试') from None
    rows=decode_accounts(bytes(body))
    if not rows:raise HTTPException(422,'Google 未返回已登录账号。可补充同一浏览器 Accounts 页的原生 Cookie，或改用 JSON 导出；未保存')
    # Explicit target-scoped reconstruction, not claims about original Domain/HttpOnly/expiry.
    cookies=[{'name':k,'value':v,'domain':'.google.com','path':'/'} for k,v in main.items()]
    cookies += [{'name':k,'value':v,'domain':'accounts.google.com','path':'/'} for k,v in extra.items() if k not in main]
    state={'cookies':cookies,'origins':[], 'xiaoguofan_cookie_source':{'kind':'native_header','attributes_known':False,'scope_policy':'google-auth-allowlist-v1','expiry_policy':'unknown-session-until-upstream-rejects','verified_at':time.time()}}
    return {'storage_state':state,'locale':'en-US','timezone':'America/Los_Angeles'},rows
