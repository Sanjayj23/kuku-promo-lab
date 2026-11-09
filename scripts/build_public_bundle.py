"""Build the public source ZIP from an allowlist, with binary-safe scanning.

  python scripts/build_public_bundle.py --out dist/PUBLIC_SOURCE.zip [--private-pack PACK.zip]

* Only allowlisted files and trees are included; private/, dist/, build outputs, caches,
  virtual environments, logs and ZIPs are never globbed in.
* Text files are scanned for secrets and private machine paths. Binary assets (fonts,
  images) are hashed and included without being decoded as UTF-8.
* With --private-pack, every text file is also checked against the confidential screenplay
  (lines of 24+ characters and character names); any hit stops the build.
* Works in a plain folder. A Git checkout is optional: if .git exists, tracked files under
  private/ or output/ are reported as findings; otherwise the inventory records that no Git
  check was possible.
* The archive is deterministic (sorted entries, fixed timestamps) and is written with a
  sibling .inventory.json listing every file's SHA-256 and size.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ['README.md', 'pyproject.toml', 'requirements.lock', 'requirements-docs.txt', '.gitignore', '.env.example']
TREES = ['src/kuku_promo_lab', 'config', 'schemas', 'tests', 'scripts', 'docs/assets']
DOCS = ['docs/TECHNICAL_REFERENCE.md', 'docs/tools_used.md', 'docs/demo_plan.md', 'docs/demo_v2.md',
        'docs/SUBMISSION_CHECKLIST.md', 'docs/case_study_evidence_v3.json']
TEXT = {'.py', '.md', '.json', '.jsonl', '.txt', '.toml', '.lock', '.cfg', '.ini', '.csv', '.html', '.css', '.js',
        '.svg', '.sh', '.example', '.gitignore', ''}
BINARY = {'.woff2', '.woff', '.ttf', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf'}
SKIP_PARTS = {'__pycache__', '.venv', 'private', 'dist', 'build', 'output', 'tmp', '.git'}
SKIP_SUFFIX = {'.pyc', '.zip', '.log', '.mp4', '.webm'}
SECRET = re.compile(r'\bsk-[A-Za-z0-9_-]{20,}\b|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|\bhf_[A-Za-z0-9]{30,}\b|'
                    r'\bghp_[A-Za-z0-9]{30,}\b|"key"\s*:\s*"[0-9a-f]{32}"')
PRIVATE_PATH = re.compile(r'/Users/[^\s"\']+/(?:Downloads|Documents)/')
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def collect(root):
    paths = [root/n for n in FILES+DOCS if (root/n).is_file()]
    for tree in TREES:
        base = root/tree
        if base.is_dir():
            paths += [p for p in base.rglob('*') if p.is_file()]
    keep = []
    for p in sorted(set(paths)):
        rel = p.relative_to(root)
        if SKIP_PARTS & set(rel.parts[:-1]) or p.suffix in SKIP_SUFFIX or '.egg-info' in str(rel):
            continue
        keep.append(p)
    return keep


def is_text(p):
    if p.suffix in BINARY:
        return False
    if p.suffix in TEXT or p.name in FILES:
        return True
    return b'\0' not in p.read_bytes()[:4096]


def confidential_corpus(pack):
    """Lines and speaker names from the private screenplay pack (never written anywhere)."""
    norm = lambda s: re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', s.lower())).strip()
    lines, names = set(), set()
    with zipfile.ZipFile(pack) as z:
        for n in z.namelist():
            if not n.endswith('.txt'):
                continue
            for raw in z.read(n).decode('utf-8', 'replace').splitlines():
                m = re.match(r'\*\*([A-Z][A-Za-z .\'-]+?)\s*(\(|:)', raw.strip())
                if m:
                    names.add(m.group(1).strip())
                t = norm(raw)
                if len(t) >= 24 and not re.match(r'^(start|scene|episode|continuity|actions)', t):
                    lines.add(t)
    generic = {'CAMERA', 'ACTION', 'SOUND FX', 'NARRATOR', 'JUDGE', 'GUARD', 'DOCTOR', 'CAPTAIN', 'PILOT', 'ENGINEER', 'MAID', 'LORD'}
    names = {n for n in names if len(n) >= 4 and n.upper() not in generic}
    return lines, names, norm


def scan(paths, root, pack=None):
    findings, text_files = [], 0
    corpus = confidential_corpus(pack) if pack else None
    for p in paths:
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            findings.append(f'Symlink: {rel}'); continue
        if not is_text(p):
            continue
        text_files += 1
        content = p.read_bytes().decode('utf-8', 'replace')
        if SECRET.search(content):
            findings.append(f'Possible secret: {rel}')
        if PRIVATE_PATH.search(content):
            findings.append(f'Private machine path: {rel}')
        if corpus and rel != 'scripts/build_public_bundle.py':
            lines, names, norm = corpus
            t = norm(content)
            hits = sum(1 for l in lines if l in t)
            nh = sorted(n for n in names if re.search(r'\b'+re.escape(n)+r'\b', content))
            if hits or nh:
                findings.append(f'Confidential screenplay material in {rel}: {hits} line(s), names {nh}')
    git = 'not a git checkout; tracked-file check skipped'
    if (root/'.git').exists():
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=root, capture_output=True).stdout.decode().split('\0')
        bad = [n for n in tracked if n.startswith(('private/', 'output/', '.env')) and n != '.env.example']
        findings += [f'Tracked private path: {n}' for n in bad]
        git = f'git checkout; {len([n for n in tracked if n])} tracked files checked'
    return findings, text_files, git


def write_zip(paths, root, target, prefix):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in paths:
            info = zipfile.ZipInfo(f'{prefix}/{p.relative_to(root).as_posix()}', FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes())


def build(out, root=ROOT, pack=None, prefix='kuku-promo-lab'):
    target = Path(out).resolve()
    if target.exists():
        raise SystemExit('Bundle already exists; choose a new path')
    paths = collect(root)
    if any(p.resolve() == target for p in paths):
        raise SystemExit('Output path is inside the allowlist; choose a path under dist/ or outside the source')
    findings, text_files, git = scan(paths, root, pack)
    readme = (root/'README.md').read_text()
    included = {p.relative_to(root).as_posix() for p in paths}
    missing_assets = [ref for ref in re.findall(r'(?:src="|\]\()((?:docs|src)/[^")\s]+)', readme) if ref not in included]
    findings += [f'README references a file not in the bundle: {m}' for m in missing_assets]
    if findings:
        raise SystemExit('\n'.join(findings))
    target.parent.mkdir(parents=True, exist_ok=True)
    write_zip(paths, root, target, prefix)
    inventory = {
        'bundle': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'file_count': len(paths),
        'text_files_scanned': text_files, 'binary_files': len(paths)-text_files, 'git_check': git,
        'confidential_pack_scan': 'performed' if pack else 'not performed (no --private-pack given)',
        'scan_findings': findings,
        'files': [{'path': p.relative_to(root).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                   'bytes': p.stat().st_size} for p in paths],
    }
    target.with_suffix('.inventory.json').write_text(json.dumps(inventory, indent=2)+'\n')
    return inventory


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='dist/PUBLIC_SOURCE.zip')
    ap.add_argument('--private-pack', help='Optional confidential screenplay ZIP to scan against (never included)')
    ap.add_argument('--root', default=str(ROOT))
    a = ap.parse_args(argv)
    inv = build(a.out, Path(a.root), a.private_pack)
    print(json.dumps({k: inv[k] for k in ('bundle', 'sha256', 'file_count', 'text_files_scanned', 'binary_files',
                                           'git_check', 'confidential_pack_scan', 'scan_findings')}, indent=2))


if __name__ == '__main__':
    sys.exit(main())
