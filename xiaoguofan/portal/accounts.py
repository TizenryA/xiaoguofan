"""Transport identity is distinct from the owner's display remark."""
import copy
import math
import re
import time

from fastapi import HTTPException


def normalize_email(value):
    if not isinstance(value,str) or len(value)>254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+",value):
        raise ValueError('请填写该登录状态所属的 Google 邮箱，不是备注')
    return value.lower()


def account_email(payload):
    # Old official exports used label as email. Never guess an address from a nickname.
    value=payload.get('email') or payload.get('label','')
    try:
        return normalize_email(value)
    except ValueError:
        return ''


def normalize_cookie(cookie):
    if not isinstance(cookie, dict):
        raise HTTPException(422, 'Cookie 格式错误')
    result = dict(cookie)
    expires = result.get('expires', -1 if result.get('session') is True else result.get('expirationDate', -1))
    if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
        raise HTTPException(422, 'Cookie 有效期格式错误')
    result['expires'] = expires
    value = result.get('sameSite', '')
    mapping = {'no_restriction':'None', 'lax':'Lax', 'strict':'Strict', 'unspecified':'', 'None':'None', 'Lax':'Lax', 'Strict':'Strict', '':''}
    if not isinstance(value, str) or value not in mapping:
        raise HTTPException(422, 'Cookie SameSite 格式无效，请重新导出认证文件')
    if 'sameSite' in result:
        result['sameSite'] = mapping[value]
    return result


def validate_selected_identity(data):
    state=data.get('storage_state',{})
    identity=state.get('xiaoguofan_identity') if isinstance(state,dict) else None
    if identity is None:return
    if not isinstance(identity,dict) or set(identity)!={'email','gaia_id','authuser'}:
        raise HTTPException(422,'账号选择信息格式无效，请重新导出')
    if identity['email']!=account_email(data) or not isinstance(identity['gaia_id'],str) or not re.fullmatch(r'[0-9]{1,32}',identity['gaia_id']) or type(identity['authuser']) is not int or not 0<=identity['authuser']<=99:
        raise HTTPException(422,'登录身份与邮箱不匹配，请重新导出；修改邮箱不能切换账号')


def worker_payload(account):
    data=copy.deepcopy(account['payload'])
    validate_selected_identity(data)
    email=account_email(data)
    if not email:
        raise HTTPException(422,'请在账号管理中补填该账号的 Google 邮箱后重试；无需重新上传凭据')
    data.pop('email',None)
    data['label']=email
    if isinstance(data.get('storage_state'), dict) and isinstance(data['storage_state'].get('cookies'), list):
        cookies = [normalize_cookie(c) for c in data['storage_state']['cookies']]
        required = {'SAPISID', '__Secure-1PAPISID', '__Secure-3PAPISID'}
        for name in required:
            candidates = [c for c in cookies if c.get('name') == name]
            if candidates and not any(c['expires'] == -1 or c['expires'] > time.time() for c in candidates):
                raise HTTPException(422, 'Google 登录凭据已过期，请重新登录并导出；不会延长原凭据有效期')
        data['storage_state']['cookies'] = cookies
    return data
