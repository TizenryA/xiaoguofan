#!/usr/bin/env python3
"""小锅饭 1.0 共享引擎：数据库迁移 / 预检脚本。

  python3 scripts/migrate_shared_engine.py --data /opt/xiaoguofan/data --check
      只读预检：在临时副本上演练迁移，原库一个字节都不改。
  python3 scripts/migrate_shared_engine.py --data /opt/xiaoguofan/data
      先用 SQLite 在线备份 portal.db，再追加新表，最后核对受保护表的行数。

迁移只做 CREATE TABLE/INDEX IF NOT EXISTS（engine_accounts、response_owners、
alerts、model_catalogues），不修改、不删除 users/accounts/api_keys/requests 等
任何已有数据。回滚到 0.9.22 时旧代码会忽略这些新表。
"""
import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portal import migrations  # noqa: E402
from portal.accounts import account_email  # noqa: E402


def mask(email):
    name, _, domain = email.partition('@')
    return (name[:2] + '***' if len(name) > 2 else name[:1] + '***') + '@' + domain


def backup(source, target):
    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    target.chmod(0o600)


def integrity(db):
    return db.execute('PRAGMA integrity_check').fetchone()[0]


def preflight(data):
    """Run the migration and the ownership plan on a throwaway copy."""
    from portal.store import Store
    with tempfile.TemporaryDirectory(prefix='xgf-migrate-') as tmp:
        tmp = Path(tmp)
        backup(data/'portal.db', tmp/'portal.db')
        shutil.copy2(data/'master.key', tmp/'master.key')
        raw = sqlite3.connect(tmp/'portal.db')
        before = migrations.protected_counts(raw)
        raw.close()
        store = Store(tmp, 'unused-'+str(time.time()))  # admin hash already exists
        after = migrations.protected_counts(store.db)
        rows = store.all('SELECT a.id,a.user_id,a.enabled,a.payload,a.created,u.disabled user_disabled '
                         'FROM accounts a JOIN users u ON u.id=a.user_id ORDER BY a.created,a.id')
        owners, conflicts, missing, undecryptable = {}, [], 0, 0
        for row in rows:
            try:
                email = account_email(store.decrypt(row['payload']))
            except Exception:
                undecryptable += 1
                continue
            if not email:
                missing += 1
                continue
            owner = owners.setdefault(email, row)
            if owner['user_id'] != row['user_id']:
                conflicts.append({'email': mask(email), 'owner_user': owner['user_id'], 'other_user': row['user_id'], 'other_account': row['id']})
        ok = integrity(store.db)
        store.db.close()
    return {'before': before, 'after_on_copy': after, 'integrity_on_copy': ok,
            'accounts_total': len(rows), 'google_emails': len(owners), 'accounts_without_email': missing,
            'accounts_undecryptable': undecryptable, 'duplicate_email_conflicts': conflicts}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--data', default='/opt/xiaoguofan/data', type=Path)
    parser.add_argument('--check', action='store_true', help='只读预检，不改动原库')
    parser.add_argument('--backup-dir', type=Path, help='备份目录，默认与 portal.db 同目录')
    args = parser.parse_args()
    db_path = args.data/'portal.db'
    if not db_path.exists() or not (args.data/'master.key').exists():
        sys.exit(f'找不到 {db_path} 或 master.key')
    report = preflight(args.data)
    if report['before'] != report['after_on_copy']:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit('演练失败：受保护表行数发生变化，已中止（原库未改动）')
    if args.check:
        report['mode'] = 'check（原库未改动）'
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    target_dir = args.backup_dir or args.data
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir/f'portal.db.before-1.0-{time.strftime("%Y%m%d-%H%M%S")}'
    backup(db_path, target)
    check = sqlite3.connect(target)
    if integrity(check) != 'ok' or migrations.protected_counts(check) != report['before']:
        sys.exit(f'备份校验失败：{target}')
    check.close()
    db = sqlite3.connect(db_path)
    before = migrations.protected_counts(db)
    migrations.apply(db)
    after = migrations.protected_counts(db)
    result = {'mode': 'apply', 'backup': str(target), 'before': before, 'after': after,
              'integrity': integrity(db), 'duplicate_email_conflicts': report['duplicate_email_conflicts'],
              'accounts_without_email': report['accounts_without_email']}
    db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if before != after or result['integrity'] != 'ok':
        sys.exit(f'核对失败！请用备份恢复：cp {target} {db_path}')


if __name__ == '__main__':
    main()
