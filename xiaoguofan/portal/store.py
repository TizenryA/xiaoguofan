import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from argon2 import PasswordHasher
from cryptography.fernet import Fernet

from portal.accounts import account_email

DEFAULTS = {'site_name': '渡鸦の小锅饭', 'public_login': False, 'discord_client_id': '',
            'discord_client_secret': '', 'blocked_guilds': [], 'block_enabled': False, 'discord_allowlist': [],
            'verification_hours': 24, 'requests_per_minute': 20, 'max_accounts': 5,
            'max_keys': 10, 'max_workers': 6, 'global_concurrency': 3}


def digest(s):
    return hashlib.sha256(s.encode()).hexdigest()


class Store:
    def __init__(self, directory, admin_password):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        keypath = self.root / 'master.key'
        if not keypath.exists():
            fd = os.open(keypath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
        self.crypt = Fernet(keypath.read_bytes())
        self.db = sqlite3.connect(self.root / 'portal.db', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, discord_id TEXT UNIQUE, name TEXT, role TEXT, disabled INTEGER DEFAULT 0,
          verified REAL DEFAULT 0, policy INTEGER DEFAULT 0, created REAL);
        CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, user_id TEXT, label TEXT, payload TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS api_keys(id TEXT PRIMARY KEY, user_id TEXT, label TEXT, hash TEXT UNIQUE, prefix TEXT, created REAL, revoked INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, user_id TEXT, csrf TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY, payload TEXT, policy INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, user_id TEXT, key_id TEXT, model TEXT, status INTEGER, duration REAL,
          input_tokens INTEGER, output_tokens INTEGER, created REAL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, actor TEXT, action TEXT, created REAL);
        ''')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(accounts)')}
        for name, ddl in [('enabled','INTEGER NOT NULL DEFAULT 1'), ('status',"TEXT NOT NULL DEFAULT 'unverified'"), ('checked','REAL'), ('note',"TEXT NOT NULL DEFAULT ''")]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE accounts ADD COLUMN {name} {ddl}')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(accounts)')}
        if 'plan' not in columns:
            self.db.execute("ALTER TABLE accounts ADD COLUMN plan TEXT NOT NULL DEFAULT 'free'")
        request_columns = {r[1] for r in self.db.execute('PRAGMA table_info(requests)')}
        for name, ddl in [('account_id','TEXT'), ('generation','INTEGER')]:
            if name not in request_columns:
                self.db.execute(f'ALTER TABLE requests ADD COLUMN {name} {ddl}')
        self.db.execute('CREATE INDEX IF NOT EXISTS idx_requests_owner_time ON requests(user_id,created)')
        self.db.execute("INSERT OR IGNORE INTO users VALUES ('admin',NULL,'管理员','admin',0,0,0,?)", (time.time(),))
        if not self.db.execute("SELECT 1 FROM metadata WHERE key='admin_hash'").fetchone():
            self.db.execute('INSERT INTO metadata VALUES (?,?)', ('admin_hash', PasswordHasher().hash(admin_password)))
        if not self.db.execute('SELECT 1 FROM settings').fetchone():
            self.db.execute('INSERT INTO settings VALUES (1,?,1)', (self.encrypt(DEFAULTS),))
        self.db.commit()
        os.chmod(self.root / 'portal.db', 0o600)

    def encrypt(self, obj):
        return self.crypt.encrypt(json.dumps(obj, ensure_ascii=False).encode()).decode()

    def decrypt(self, value):
        return json.loads(self.crypt.decrypt(value.encode()))

    def one(self, sql, args=()):
        with self.lock:
            r = self.db.execute(sql, args).fetchone()
            return dict(r) if r else None

    def all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def run(self, sql, args=()):
        with self.lock:
            n = self.db.execute(sql, args).rowcount
            self.db.commit()
            return n

    def settings(self, public=False):
        row = self.one('SELECT * FROM settings')
        v = DEFAULTS | self.decrypt(row['payload'])
        v['policy'] = row['policy']
        if public:
            v['discord_secret_set'] = bool(v.pop('discord_client_secret'))
        return v

    def save_settings(self, values):
        with self.lock:
            old = self.settings()
            new = old | values
            policy = old['policy'] + int(any(old[k] != new[k] for k in ['blocked_guilds', 'block_enabled', 'discord_client_id', 'discord_client_secret', 'discord_allowlist']))
            new.pop('policy', None)
            self.run('UPDATE settings SET payload=?,policy=? WHERE id=1', (self.encrypt(new), policy))
            self.audit('admin', 'settings_updated')

    def discord_user(self, discord_id, name):
        with self.lock:
            u = self.one('SELECT * FROM users WHERE discord_id=?', (discord_id,))
            if not u:
                self.run('INSERT INTO users(id,discord_id,name,role,created) VALUES (?,?,?,?,?)',
                         (uuid.uuid4().hex, discord_id, name[:100], 'user', time.time()))
            else:
                self.run('UPDATE users SET name=? WHERE id=?', (name[:100], u['id']))
            return self.one('SELECT * FROM users WHERE discord_id=?', (discord_id,))

    def mark_verified(self, uid, checked_policy=None):
        with self.lock:
            current = self.settings()['policy']
            if checked_policy is not None and checked_policy != current:
                return False
            self.run('UPDATE users SET verified=?,policy=? WHERE id=?', (time.time(), current, uid))
            return True

    def user(self, uid):
        return self.one('SELECT * FROM users WHERE id=?', (uid,))

    def login(self, uid):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        self.run('DELETE FROM sessions WHERE expires<?', (time.time(),))
        self.run('INSERT INTO sessions VALUES (?,?,?,?)', (digest(token), uid, csrf, time.time()+86400))
        return token, csrf

    def session(self, token):
        row = self.one('SELECT s.csrf,u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>?', (digest(token), time.time()))
        return row if row and not row['disabled'] else None

    def accounts(self, uid):
        rows = self.all('SELECT id,label,created,enabled,status,checked,note,plan,payload FROM accounts WHERE user_id=? ORDER BY created DESC', (uid,))
        for row in rows:
            row['email'] = account_email(self.decrypt(row.pop('payload')))
        return rows

    def account(self, uid, aid):
        r = self.one('SELECT * FROM accounts WHERE user_id=? AND id=?', (uid, aid))
        if r:
            r['payload'] = self.decrypt(r['payload'])
        return r

    def add_account(self, uid, label, payload):
        with self.lock:
            if len(self.accounts(uid)) >= self.settings()['max_accounts']:
                raise ValueError('账号数量达到限制')
            aid = uuid.uuid4().hex
            self.run('INSERT INTO accounts(id,user_id,label,payload,created) VALUES (?,?,?,?,?)', (aid, uid, label, self.encrypt(payload), time.time()))
            self.audit(uid, 'account_imported')
            return aid

    def delete_account(self, uid, aid):
        return bool(self.run('DELETE FROM accounts WHERE user_id=? AND id=?', (uid, aid)))

    def update_account(self, uid, aid, values):
        allowed = {k:v for k,v in values.items() if k in ['label','enabled','status','checked','note','plan']}
        if not allowed:
            return False
        sql = ','.join(k+'=?' for k in allowed)
        return bool(self.run('UPDATE accounts SET '+sql+' WHERE user_id=? AND id=?', (*allowed.values(), uid, aid)))

    def records(self, uid, limit=20, offset=0):
        self.cleanup()
        return {'total':self.one('SELECT COUNT(*) n FROM requests WHERE user_id=?', (uid,))['n'],
                'items':self.all('SELECT r.model,r.status,r.duration,r.input_tokens,r.output_tokens,r.created,r.account_id,r.generation,a.label account_label FROM requests r LEFT JOIN accounts a ON a.id=r.account_id AND a.user_id=r.user_id WHERE r.user_id=? ORDER BY r.id DESC LIMIT ? OFFSET ?', (uid, limit, offset))}

    def create_key(self, uid, label):
        with self.lock:
            if len(self.keys(uid)) >= self.settings()['max_keys']:
                raise ValueError('密钥数量达到限制')
            raw, kid = 'sk-asp-' + secrets.token_urlsafe(32), uuid.uuid4().hex
            self.run('INSERT INTO api_keys VALUES (?,?,?,?,?,?,0)', (kid, uid, label, digest(raw), raw[:14], time.time()))
            self.audit(uid, 'key_created')
            return {'id': kid, 'key': raw}

    def keys(self, uid):
        return self.all('SELECT id,label,prefix,created FROM api_keys WHERE user_id=? AND revoked=0 ORDER BY created DESC', (uid,))

    def key_user(self, key):
        return self.one('SELECT u.*,k.id AS key_id FROM api_keys k JOIN users u ON u.id=k.user_id WHERE k.hash=? AND k.revoked=0 AND u.disabled=0', (digest(key),))

    def revoke_key(self, uid, kid):
        return bool(self.run('UPDATE api_keys SET revoked=1 WHERE user_id=? AND id=? AND revoked=0', (uid, kid)))

    def audit(self, uid, action):
        self.run('INSERT INTO audit(actor,action,created) VALUES (?,?,?)', (uid, action, time.time()))

    def record(self, uid, kid, model, status, duration, usage=None, account_id=None, generation=None):
        usage = usage or {}
        self.run('INSERT INTO requests(user_id,key_id,model,status,duration,input_tokens,output_tokens,created,account_id,generation) VALUES (?,?,?,?,?,?,?,?,?,?)',
                 (uid, kid, model[:120], status, duration, usage.get('input_tokens'), usage.get('output_tokens'), time.time(), account_id, generation))
        self.run('DELETE FROM requests WHERE created<?', (time.time()-30*86400,))

    def cleanup(self):
        cutoff = time.time()-30*86400
        self.run('DELETE FROM requests WHERE created<?', (cutoff,))
        self.run('DELETE FROM audit WHERE created<?', (cutoff,))
        self.run('DELETE FROM sessions WHERE expires<?', (time.time(),))

    def stats(self, uid):
        self.cleanup()
        return {'summary': self.one('SELECT COUNT(*) requests,COALESCE(SUM(status<400),0) success,COALESCE(SUM(input_tokens),0) input_tokens,COALESCE(SUM(output_tokens),0) output_tokens,COUNT(input_tokens) metered_requests FROM requests WHERE user_id=?', (uid,)),
                'recent': self.all('SELECT model,status,duration,input_tokens,output_tokens,created FROM requests WHERE user_id=? ORDER BY id DESC LIMIT 50', (uid,)),
                'daily': self.all("SELECT strftime('%m-%d',created,'unixepoch') day,COUNT(*) requests FROM requests WHERE user_id=? AND created>? GROUP BY day ORDER BY day", (uid, time.time()-7*86400))}
