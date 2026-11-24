"""Pure helpers for the shared-engine proxy: routing rules, errors, client IP."""
import ipaddress
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))

# Endpoints whose objects live engine-wide (files, caches, stored responses,
# long-running operations, realtime sockets). They cannot be scoped per user
# safely in the shared engine, so the portal refuses them with a clear message.
BLOCKED = (
    re.compile(r'^/v1(beta)?/files(/|$)'),
    re.compile(r'^/v1beta/cachedContents(/|$)'),
    re.compile(r'^/v1/videos(/|$)'),
    re.compile(r'^/v1beta/operations(/|$)'),
    re.compile(r'^/v1(beta)?/interactions(/|$)'),
    re.compile(r'^/v1/responses/[^/]+(/.*)?$'),
    re.compile(r'^/v1/live$'),
    re.compile(r'^/v1/robotics(/|$)'),
    re.compile(r'^/v1beta/decisions$'),
    re.compile(r'^/v1beta/models/[^/]+:predictLongRunning$'),
)
BLOCKED_MESSAGE = '本站暂不支持这个接口（文件上传、缓存、视频和后台任务等），请改用普通对话接口'
GENERATION_PATHS = ('/v1/chat/completions', '/v1/completions', '/v1/messages', '/v1/responses', '/v1/images/generations')
RESPONSE_ID = re.compile(rb'"id"\s*:\s*"(resp_[A-Za-z0-9_-]{6,120})"')


def blocked(path):
    return any(rule.search(path) for rule in BLOCKED)


def body_blocked(parsed):
    """Body features that would reach engine-wide state."""
    if not isinstance(parsed, dict):
        return ''
    if parsed.get('cachedContent') or parsed.get('cached_content'):
        return '本站暂不支持上下文缓存（cachedContent），请直接发送完整内容'
    raw = json.dumps(parsed.get('input'), ensure_ascii=True) if 'input' in parsed else ''
    if '"item_reference"' in raw:
        return '本站暂不支持引用历史条目（item_reference），请直接发送完整内容或使用 previous_response_id'
    return ''


def is_generation(method, path):
    return method == 'POST' and (path in GENERATION_PATHS or ':generateContent' in path or ':streamGenerateContent' in path)


def protocol(path, headers):
    if headers.get('anthropic-version') or path.startswith('/v1/messages'):
        return 'anthropic'
    if path.startswith('/v1beta/'):
        return 'gemini'
    return 'openai'


def error_body(kind, status, message):
    if kind == 'anthropic':
        etype = {400: 'invalid_request_error', 401: 'authentication_error', 403: 'permission_error', 404: 'not_found_error',
                 429: 'rate_limit_error'}.get(status, 'api_error')
        return {'type': 'error', 'error': {'type': etype, 'message': message}}
    if kind == 'gemini':
        gstatus = {400: 'INVALID_ARGUMENT', 401: 'UNAUTHENTICATED', 403: 'PERMISSION_DENIED', 404: 'NOT_FOUND',
                   429: 'RESOURCE_EXHAUSTED', 504: 'DEADLINE_EXCEEDED'}.get(status, 'UNAVAILABLE')
        return {'error': {'code': status, 'message': message, 'status': gstatus}}
    code = {429: 'rate_limit_exceeded', 401: 'invalid_api_key', 404: 'not_found'}.get(status, 'upstream_error')
    return {'error': {'message': message, 'type': code, 'code': status}}


def retry_seconds(headers, body=b''):
    value = headers.get('retry-after', '')
    if value.isdigit():
        return int(value)
    match = re.search(rb'"retryDelay"\s*:\s*"(\d+)s"', body or b'')
    return int(match.group(1)) if match else None


def recovery_message(seconds, now=None):
    """Human message for 'all of your own accounts are out of quota'."""
    base = '你名下的可用账号额度都暂时用完了（已自动依次尝试你自己的全部账号，不会借用别人的账号）'
    if not seconds or seconds <= 0:
        return base+'，请稍后再试'
    at = datetime.fromtimestamp((now or time.time())+seconds, CST)
    if seconds < 90:
        span = f'约 {int(seconds)} 秒后'
    elif seconds < 5400:
        span = f'约 {math.ceil(seconds/60)} 分钟后'
    else:
        span = f'约 {seconds/3600:.1f} 小时后'
    return f'{base}，预计北京时间 {at:%m-%d %H:%M} 恢复（{span}）'


def upstream_message(status, raw):
    """Turn an engine error body into a short message without internal terms."""
    text = ''
    try:
        value = json.loads(raw)
        err = value.get('error') if isinstance(value, dict) else None
        if isinstance(err, dict):
            text = str(err.get('message') or '')
        elif isinstance(value, dict):
            text = str(value.get('message') or value.get('detail') or '')
    except (ValueError, AttributeError):
        text = raw.decode(errors='replace')[:300] if isinstance(raw, bytes) else str(raw)[:300]
    if status in (401, 403):
        return 'Google 登录状态失效或被拒绝，请到「我的账号」重新导入'
    if status == 404 and 'model' in text.lower():
        return '没有这个模型，或者你的账号暂时用不了它'
    if status >= 500 and not text:
        return '生成服务暂时出错，请稍后重试'
    for term in ('账户池', '账号池', 'Worker', 'worker', 'WAA', 'BotGuard', 'SAPISID'):
        if term in text:
            return '生成服务暂时出错，请稍后重试' if status >= 500 else '请求没有成功，请检查参数后重试'
    return text[:300] or ('请求没有成功' if status < 500 else '生成服务暂时出错，请稍后重试')


class ResponseIdScanner:
    """Find Responses API ids in the first bytes of an output stream (bounded)."""

    def __init__(self, limit=262144):
        self.buffer = bytearray()
        self.limit = limit
        self.ids = []

    def feed(self, chunk):
        if len(self.buffer) >= self.limit:
            return
        self.buffer.extend(chunk[:self.limit-len(self.buffer)])
        for match in RESPONSE_ID.finditer(self.buffer):
            value = match.group(1).decode()
            if value not in self.ids:
                self.ids.append(value)


def parse_networks(value):
    out = []
    for item in (value or '').replace(';', ',').split(','):
        item = item.strip()
        if item:
            try:
                out.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                pass
    return out


# Cloudflare edge ranges (https://www.cloudflare.com/ips/), used only to decide
# whether CF-Connecting-IP may be trusted.
CLOUDFLARE = parse_networks(
    '173.245.48.0/20,103.21.244.0/22,103.22.200.0/22,103.31.4.0/22,141.101.64.0/18,108.162.192.0/18,'
    '190.93.240.0/20,188.114.96.0/20,197.234.240.0/22,198.41.128.0/17,162.158.0.0/15,104.16.0.0/13,'
    '104.24.0.0/14,172.64.0.0/13,131.0.72.0/22,2400:cb00::/32,2606:4700::/32,2803:f800::/32,2405:b500::/32,'
    '2405:8100::/32,2a06:98c0::/29,2c0f:f248::/32')
LOOPBACK = parse_networks('127.0.0.0/8,::1/128')


def _ip(value):
    try:
        return ipaddress.ip_address(value.strip())
    except (ValueError, AttributeError):
        return None


def client_ip(peer, headers, trusted):
    """Real client IP behind Caddy / DMIT / Cloudflare.

    Forwarding headers are only believed when the direct peer is a trusted
    proxy (loopback or PORTAL_TRUSTED_PROXIES). X-Forwarded-For is walked from
    the right, skipping trusted hops; CF-Connecting-IP is only used when the
    hop that added it is a Cloudflare edge address.
    """
    proxies = LOOPBACK+trusted
    address = _ip(peer)
    if address is None:
        return peer or 'unknown'
    if not any(address in net for net in proxies):
        return str(address)
    chain = [_ip(x) for x in headers.get('x-forwarded-for', '').split(',') if x.strip()]
    chain = [x for x in chain if x is not None]
    candidate = address
    for hop in reversed(chain):
        candidate = hop
        if any(hop in net for net in proxies):
            continue
        break
    if any(candidate in net for net in CLOUDFLARE):
        cf = _ip(headers.get('cf-connecting-ip', ''))
        if cf is not None:
            return str(cf)
    return str(candidate)
