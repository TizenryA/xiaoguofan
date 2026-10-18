"""Build a deterministic allowlisted extension ZIP; no profile/cookies included."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parents[1]
source = root / 'extension'
files = ['manifest.json', 'core.js', 'popup.js', 'background.js', 'popup.html', 'popup.css', '使用说明.txt']
version = json.loads((source / 'manifest.json').read_text())['version']
assert version == '0.3.4'
archive = root / 'portal/static/AIStudio-Lite-账号导入.zip'
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
    for name in files:
        info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        z.writestr(info, (source / name).read_bytes())
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
print('EXTENSION_ZIP', version, archive.stat().st_size, hashlib.sha256(archive.read_bytes()).hexdigest())
