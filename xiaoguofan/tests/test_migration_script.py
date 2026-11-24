import hashlib
import json
import subprocess
import sys
from pathlib import Path

from engine_fixture import cookie_payload

from portal.store import Store

SCRIPT = Path(__file__).resolve().parents[1]/'scripts'/'migrate_shared_engine.py'


def legacy(tmp_path):
    s = Store(tmp_path, 'fixture-password-long')
    a = s.discord_user('100000000000000001', 'a')
    b = s.discord_user('100000000000000002', 'b')
    s.add_account(a['id'], 'x', cookie_payload('shared@gmail.com'))
    s.add_account(b['id'], 'y', cookie_payload('shared@gmail.com'))
    s.add_account(b['id'], 'z', cookie_payload('solo@gmail.com'))
    s.create_key(a['id'], 'k')
    # Simulate a 0.9.22 database: no shared-engine tables yet.
    for t in ['engine_accounts', 'response_owners', 'alerts']:
        s.db.execute(f'DROP TABLE {t}')
    s.db.execute("DELETE FROM metadata WHERE key='schema_shared_engine'")
    s.db.commit()
    s.db.close()


def run(*args):
    out = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_check_mode_is_read_only_and_reports_masked_duplicates(tmp_path):
    legacy(tmp_path)
    digest = hashlib.sha256((tmp_path/'portal.db').read_bytes()).hexdigest()
    report = run('--data', str(tmp_path), '--check')
    assert hashlib.sha256((tmp_path/'portal.db').read_bytes()).hexdigest() == digest
    assert report['before'] == report['after_on_copy'] and report['before']['accounts'] == 3
    assert report['google_emails'] == 2
    assert [c['email'] for c in report['duplicate_email_conflicts']] == ['sh***@gmail.com']
    assert 'shared@gmail.com' not in json.dumps(report)


def test_apply_backs_up_and_preserves_protected_rows(tmp_path):
    legacy(tmp_path)
    result = run('--data', str(tmp_path))
    assert Path(result['backup']).exists()
    assert result['before'] == result['after'] and result['integrity'] == 'ok'
    import sqlite3
    db = sqlite3.connect(tmp_path/'portal.db')
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'engine_accounts', 'response_owners', 'alerts', 'model_catalogues'} <= tables
    again = run('--data', str(tmp_path))
    assert again['before'] == again['after']
