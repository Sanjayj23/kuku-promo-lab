"""Strict, bounded input inventory. ZIP contents are never extracted."""
from __future__ import annotations

import hashlib
import stat
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from .schemas import digest


class InputError(ValueError):
    pass


def safe_name(name):
    n = unicodedata.normalize('NFC', name.replace('\\', '/'))
    parts = n.split('/')
    if n.startswith('/') or any(p in {'.', '..'} for p in parts) or ':' in parts[0] or '\x00' in n:
        raise InputError(f"Unsafe archive path: {name!r}")
    return str(PurePosixPath(n)).casefold()


def read_pack(path, config):
    path = Path(path)
    if path.is_symlink():
        raise InputError("Input root cannot be a symlink")
    if not path.exists():
        raise InputError(f"Input does not exist: {path}")
    files, inventory, seen = [], [], set()
    total = 0

    def accept(name, size, read, ratio=1, mode=0, encrypted=False, directory=False):
        nonlocal total
        normalized = safe_name(name)
        if normalized in seen:
            raise InputError(f"Duplicate normalized member: {name}")
        seen.add(normalized)
        if stat.S_ISLNK(mode) or encrypted:
            raise InputError(f"Symlink/encrypted member: {name}")
        if directory:
            inventory.append({'name': name, 'status': 'directory'})
            return
        if size > config['max_member_bytes'] or ratio > config['max_compression_ratio']:
            raise InputError(f"Member limit exceeded: {name}")
        total += size
        if total > config['max_total_bytes']:
            raise InputError("Expanded input exceeds total byte limit")
        if '__macosx' in normalized.split('/') or Path(name).name in {'.DS_Store'} or Path(name).name.startswith('._'):
            inventory.append({'name': name, 'status': 'ignored_metadata', 'size': size})
            return
        if not normalized.endswith('.txt'):
            raise InputError(f"Unsupported input file: {name}; expected UTF-8 .txt")
        if len(files) >= config['max_files']:
            raise InputError("Too many script files")
        raw = read()
        if len(raw) != size:
            raise InputError(f"Member size changed: {name}")
        try:
            text = raw.decode('utf-8-sig', errors='strict')
        except UnicodeError as exc:
            raise InputError(f"Invalid UTF-8: {name}") from exc
        if '\x00' in text:
            raise InputError(f"Binary/NUL content: {name}")
        sha = hashlib.sha256(raw).hexdigest()
        files.append({'name': name, 'sha256': sha, 'text': text, 'bytes': size})
        inventory.append({'name': name, 'sha256': sha, 'size': size, 'status': 'script'})

    if path.is_dir():
        for child in sorted(path.rglob('*')):
            if child.is_symlink():
                raise InputError(f"Folder symlink: {child.relative_to(path)}")
            if child.is_dir():
                continue
            accept(child.relative_to(path).as_posix(), child.stat().st_size, child.read_bytes)
        pack_hash = digest([(f['name'], f['sha256']) for f in files])
        hash_basis = 'sorted-member-name-and-sha256'
    else:
        try:
            with zipfile.ZipFile(path) as archive:
                if len(archive.infolist()) > config['max_files'] * 4:
                    raise InputError('Too many archive members')
                for info in archive.infolist():
                    accept(info.filename, info.file_size, lambda i=info: archive.read(i),
                           info.file_size / max(1, info.compress_size), info.external_attr >> 16,
                           bool(info.flag_bits & 1), info.is_dir())
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise InputError(f"Invalid/unsupported ZIP: {exc}") from exc
        pack_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        hash_basis = 'zip-bytes'
    if not files:
        raise InputError("No screenplay .txt files found")
    return sorted(files, key=lambda f: f['name']), {'pack_hash': pack_hash, 'hash_basis': hash_basis,
                                                  'inventory': inventory, 'expanded_bytes': total}
