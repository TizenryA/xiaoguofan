from html.parser import HTMLParser
from pathlib import Path


class Page(HTMLParser):
    def __init__(self): super().__init__(); self.tabs=[]; self.ids={}
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if 'data-tab' in a:self.tabs.append(a['data-tab'])
        if 'id' in a:self.ids[a['id']]=a


def test_md3_navigation_avatar_and_guide():
    html=(Path(__file__).parents[1]/'portal/static/lite.html').read_text()
    p=Page();p.feed(html)
    assert p.tabs[:3]==['quota','records','upload']
    assert 'avatar-button' in p.ids and 'profile-menu' in p.ids
    assert p.ids['anti-truncation']['role']=='switch'
    assert 'extension-guide' in p.ids
    assert all(n in html for n in ['SAPISID','__Secure-1PAPISID','__Secure-3PAPISID','猫娘图标.webp'])
