from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from portal.main import create_app


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('id'):
            self.links[attrs['id']] = attrs


@pytest.mark.parametrize('prefix', ['', '/lite'])
def test_google_login_entry_is_official_and_does_not_claim_credential_callback(tmp_path, prefix):
    app = create_app(tmp_path, 'test-password-long-enough', 'https://portal.test' + prefix)
    with TestClient(app, base_url='https://portal.test') as c:
        page = c.get(prefix or '/').text
        links = Links()
        links.feed(page)
        link = links.links['google-login-entry']
        url = urlsplit(link['href'])
        assert url.scheme == 'https' and url.hostname == 'accounts.google.com'
        assert url.path == '/AccountChooser'
        assert parse_qs(url.query) == {'continue': ['https://aistudio.google.com/']}
        assert link['target'] == '_blank'
        assert {'noopener', 'noreferrer'} <= set(link['rel'].split())
        assert link['referrerpolicy'] == 'no-referrer'
        download = links.links['google-import-extension']
        assert download['href'] == prefix + '/static/AIStudio-Lite-账号导入.zip'
        assert 'download' in download
        assert '登录不会自动上传凭据' in page
        assert '返回本页' in page
        assert 'HttpOnly' in page
        assert 'Google 密码仅在 Google 官网输入' in page
