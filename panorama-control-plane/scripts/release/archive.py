"""Archive an uninstalled offline bundle using only Python's standard library."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--bundle', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
root = args.bundle.resolve(strict=True)
manifest = json.loads((root / 'release.json').read_text(encoding='utf-8'))
allowed = {'app', 'release.json', 'offline.mjs', 'install.sh', 'install.ps1', 'README.md'}
if {p.name for p in root.iterdir()} != allowed:
    raise SystemExit('Archive only a fresh, uninstalled bundle: unexpected root files.')
files = sorted((p for p in root.rglob('*') if p.is_file()), key=lambda p: p.relative_to(root).as_posix())
if any(p.is_symlink() for p in root.rglob('*')):
    raise SystemExit('Archive links are forbidden.')
actual = [{'path': p.relative_to(root / 'app').as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
          for p in files if p.is_relative_to(root / 'app')]
if actual != manifest['files']:
    raise SystemExit('Bundle content differs from release.json.')
version = manifest['version']
prefix = f'panorama-{version}'
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
archives = [output / f'{prefix}-linux.tar.gz', output / f'{prefix}-windows.zip']
if any(p.exists() for p in archives) or (output / 'SHA256SUMS.txt').exists():
    raise SystemExit('Release assets already exist; choose a new output directory.')
with archives[0].open('xb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode='w') as tar:
    for path in files:
        info = tar.gettarinfo(str(path), arcname=f'{prefix}/{path.relative_to(root).as_posix()}')
        info.uid = info.gid = 0
        info.uname = info.gname = ''
        info.mtime = 0
        info.mode = 0o755 if path.suffix == '.sh' else 0o644
        with path.open('rb') as stream:
            tar.addfile(info, stream)
with zipfile.ZipFile(archives[1], 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in files:
        info = zipfile.ZipInfo(f'{prefix}/{path.relative_to(root).as_posix()}', (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o100755 if path.suffix == '.sh' else 0o100644) << 16
        archive.writestr(info, path.read_bytes())
records = [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in archives]
(output / 'SHA256SUMS.txt').write_text(''.join(f"{r['sha256']}  {r['name']}\n" for r in records), encoding='utf-8', newline='\n')
print(json.dumps({'version': version, 'assets': records}, indent=2))
