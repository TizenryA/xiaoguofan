import re
import secrets
import threading
import time

import httpx


def check_guilds(config, guilds):
    if not config['block_enabled']:
        return True
    if not isinstance(guilds, list) or any(not isinstance(g, dict) or not str(g.get('id', '')).isdigit() for g in guilds):
        return False
    return not set(config['blocked_guilds']).intersection(str(g.get('id', '')) for g in guilds)


class OAuthStates:
    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def issue(self, browser, policy):
        with self.lock:
            self.items = {k: v for k, v in self.items.items() if v[2] > time.time()}
            if len(self.items) >= 2000:
                raise ValueError('登录请求过多，请稍后再试')
            token = secrets.token_urlsafe(32)
            self.items[token] = (browser, policy, time.time()+600)
            return token

    def consume(self, token, browser, policy):
        with self.lock:
            value = self.items.get(token)
            if not value or value[0] != browser or value[1] != policy or value[2] < time.time():
                return False
            self.items.pop(token)
            return True


async def discord_identity(code, config, redirect_uri):
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post('https://discord.com/api/v10/oauth2/token', data={
            'client_id': config['discord_client_id'], 'client_secret': config['discord_client_secret'],
            'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri})
        response.raise_for_status()
        token = response.json()['access_token']
        headers = {'Authorization': 'Bearer '+token}
        try:
            r = await client.get('https://discord.com/api/v10/users/@me', headers=headers)
            r.raise_for_status()
            user = r.json()
            if not isinstance(user, dict) or not re.fullmatch(r'[0-9]{15,22}', str(user.get('id', ''))):
                raise ValueError('Discord 返回无效')
            exempt = str(user['id']) in config.get('discord_allowlist', [])
            guilds = []
            if config['block_enabled'] and not exempt:
                after = None
                for _ in range(20):
                    params = {'limit': 200}
                    if after:
                        params['after'] = after
                    r = await client.get('https://discord.com/api/v10/users/@me/guilds', params=params, headers=headers)
                    r.raise_for_status()
                    chunk = r.json()
                    if not isinstance(chunk, list):
                        raise ValueError('Discord 验证失败')
                    guilds.extend(chunk)
                    if len(chunk) < 200:
                        break
                    after = chunk[-1]['id']
                else:
                    raise ValueError('Discord 验证未完成')
            if not exempt and not check_guilds(config, guilds):
                raise PermissionError('当前账号不符合本站访问规则')
            return user
        finally:
            # Do not retain OAuth access/refresh tokens or full guild membership lists.
            try:
                await client.post('https://discord.com/api/v10/oauth2/token/revoke', data={
                    'client_id': config['discord_client_id'], 'client_secret': config['discord_client_secret'], 'token': token})
            except httpx.HTTPError:
                pass
