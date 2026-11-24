"""Additive schema for the shared engine (v1.0).

Rules: only CREATE TABLE/INDEX IF NOT EXISTS. Existing tables (users, accounts,
api_keys, requests, account_links, model_catalogues, ...) are never altered,
rewritten or deleted by this module, so rolling back to 0.9.22 keeps working
against the same portal.db (old code simply ignores the new tables).
"""
import time

SCHEMA_VERSION = '1.0-shared-engine'

STATEMENTS = (
    # Which portal account is loaded into the shared engine, for which owner.
    # engine_id is the engine's account id (the Google email, lower case).
    '''CREATE TABLE IF NOT EXISTS engine_accounts(
        engine_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, user_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'pending',
        message TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT '', models TEXT NOT NULL DEFAULT '[]',
        active INTEGER NOT NULL DEFAULT 0, synced REAL, observed REAL)''',
    'CREATE INDEX IF NOT EXISTS idx_engine_accounts_user ON engine_accounts(user_id)',
    'CREATE INDEX IF NOT EXISTS idx_engine_accounts_account ON engine_accounts(account_id)',
    # Responses API ids created through the shared engine, so previous_response_id
    # can only continue a conversation the same user started.
    '''CREATE TABLE IF NOT EXISTS response_owners(
        response_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created REAL NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS idx_response_owners_created ON response_owners(created)',
    # Admin-only alerts; never shown to ordinary users.
    '''CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
        level TEXT NOT NULL DEFAULT 'warning', message TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 1,
        created REAL NOT NULL, updated REAL NOT NULL, resolved REAL)''',
    'CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts(resolved, updated)',
    'CREATE TABLE IF NOT EXISTS model_catalogues(owner TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT, updated REAL)',
)


def apply(db):
    """Apply the additive schema to an open sqlite3 connection (idempotent)."""
    for statement in STATEMENTS:
        db.execute(statement)
    db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT)')
    db.execute("INSERT INTO metadata(key,value) VALUES ('schema_shared_engine',?) ON CONFLICT(key) DO NOTHING",
               (SCHEMA_VERSION+'@'+str(int(time.time())),))
    db.commit()


PROTECTED_TABLES = ('users', 'accounts', 'api_keys', 'requests', 'account_links', 'sessions', 'settings', 'preferences')


def protected_counts(db):
    """Row counts of tables this migration must never change."""
    out = {}
    for table in PROTECTED_TABLES:
        try:
            out[table] = db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        except Exception:
            out[table] = None
    return out
