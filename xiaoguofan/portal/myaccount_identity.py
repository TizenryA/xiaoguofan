"""Strict identity projection of an authenticated fixed Google Account page; never scan page text for emails."""
import json
import re
from html.parser import HTMLParser

from fastapi import HTTPException

MYACCOUNT_URL='https://myaccount.google.com/u/0/'
IDENTITY_USER_AGENT='Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36'


def parse_myaccount(text):
    class Page(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.bases=[];self.scripts=[];self.current=None
        def handle_starttag(self,tag,attrs):
            if tag=='base':self.bases.append(dict(attrs).get('href'))
            if tag=='script':self.current=[]
        def handle_data(self,data):
            if self.current is not None:self.current.append(data)
        def handle_endtag(self,tag):
            if tag=='script' and self.current is not None:
                self.scripts.append(''.join(self.current));self.current=None
    try:
        page=Page();page.feed(text)
        if page.bases!=[MYACCOUNT_URL]:raise ValueError()
        candidates=[s for s in page.scripts if re.match(r'^\s*(?:window\.)?WIZ_global_data\s*=',s)]
        if len(candidates)!=1:raise ValueError()
        match=re.fullmatch(r'\s*(?:window\.)?WIZ_global_data\s*=\s*(\{.*\})\s*;?\s*',candidates[0],re.S)
        if not match:raise ValueError()
        def unique(pairs):
            out={}
            for k,v in pairs:
                if k in out:raise ValueError()
                out[k]=v
            return out
        d=json.loads(match[1],object_pairs_hook=unique)
        email=d.get('oPEP7c');gaia=d.get('qDCSke')
        if not isinstance(email,str) or not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]{1,200}@[A-Za-z0-9.-]{1,100}',email):raise ValueError()
        if not isinstance(gaia,str) or not re.fullmatch(r'\d{10,32}',gaia) or d.get('W3Yyqf')!=gaia or d.get('S06Grb')!=gaia:raise ValueError()
        return {'email':email.lower(),'gaia_id':gaia,'authuser':0}
    except (ValueError,TypeError,KeyError):
        raise HTTPException(422,'Google 默认账号页未能严格核对登录身份，未保存；不接受页面散落邮箱或手填身份') from None


async def verify_myaccount(cookies,transport=None):
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=False,trust_env=False,transport=transport) as client:
            async with client.stream('GET',MYACCOUNT_URL,headers={'Cookie':'; '.join(k+'='+v for k,v in cookies.items()),'User-Agent':IDENTITY_USER_AGENT}) as response:
                if response.status_code!=200 or 'text/html' not in response.headers.get('content-type','').lower():
                    raise HTTPException(422,'Google 默认账号页未接受会话，未保存；请确认补充 Cookie 来自已登录的账号管理页')
                body=bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body)>2000000:raise HTTPException(422,'Google 默认账号页响应过大，未保存')
        return parse_myaccount(body.decode('utf-8',errors='strict'))
    except (httpx.HTTPError,UnicodeError):
        raise HTTPException(502,'无法读取 Google 默认账号身份，未保存；请稍后重试') from None
