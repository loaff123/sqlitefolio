"""Bounded read-only input capture and alias-aware output preflight."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from .contract import InputError, canonical, read_json, validate_manifest

SQL_CAP = 1048576
DB_CAP = 16777216
TOTAL_CAP = 33554432


def sha(data):
    return hashlib.sha256(data).hexdigest()


def no_symlinks(path):
    # Check lexical components before collapsing '..', which could erase a link.
    incoming = Path(path)
    if not incoming.is_absolute(): incoming = Path.cwd() / incoming
    current = Path(incoming.anchor)
    for component in incoming.parts[1:]:
        if component == '..':
            current = current.parent
        else:
            current = current / component
            try:
                if stat.S_ISLNK(current.lstat().st_mode):
                    raise InputError('symlink paths are unsupported')
            except FileNotFoundError:
                continue
    return current


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def bounded_read(path, limit):
    path = no_symlinks(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise InputError('input must be regular, unlinked, and within byte cap')
            parts = []; size = 0
            while True:
                chunk = os.read(fd, min(65536, limit + 1 - size))
                if not chunk: break
                parts.append(chunk); size += len(chunk)
                if size > limit: raise InputError('input exceeds byte cap')
            after = os.fstat(fd)
            named = path.stat(follow_symlinks=False)
            if signature(before) != signature(after) or signature(after) != signature(named):
                raise InputError('input changed during capture')
            return b''.join(parts), signature(after)
        finally:
            os.close(fd)
    except OSError as exc:
        raise InputError('cannot read a regular input file: ' + str(exc.strerror)) from exc


def validate_output(output, inputs):
    output = no_symlinks(output)
    if not output.parent.is_dir():
        raise InputError('output parent must already exist')
    if output.exists() or output.is_symlink():
        raise InputError('output destination already exists; refusing to clobber')
    for path in inputs:
        path = no_symlinks(path)
        if output == path or output in path.parents:
            raise InputError('output must not alias or contain any input')
    return output


@dataclass
class InputBundle:
    manifest: dict
    files: dict
    source_kind: str
    source_sha256: str
    database: bytes | None
    captured: dict
    database_path: Path | None

    def unchanged(self):
        try:
            for path, (digest, sig, cap) in self.captured.items():
                data, current = bounded_read(path, cap)
                if current != sig or sha(data) != digest: return False
            if self.database_path is not None and any(Path(str(self.database_path)+s).exists() for s in ('-wal','-shm','-journal')):
                return False
            return True
        except InputError:
            return False


def load_inputs(path, output=None):
    path = no_symlinks(path)
    original, sig = bounded_read(path, SQL_CAP)
    manifest = validate_manifest(read_json(original))
    captured = {path:(sha(original), sig, SQL_CAP)}
    files = {'inputs/original-manifest.json':original}
    normalized = dict(manifest)
    used_inodes = {sig[:2]}
    total = len(original)

    def capture(name, destination, cap=SQL_CAP, sql=True):
        nonlocal total
        source_path = no_symlinks(path.parent / name)
        data, identity = bounded_read(source_path, cap)
        # Reusing the same explicitly named file is permitted, aliases are not.
        if identity[:2] in used_inodes and source_path not in captured:
            raise InputError('aliased input files are unsupported')
        used_inodes.add(identity[:2])
        if source_path not in captured: total += len(data)
        if total > TOTAL_CAP: raise InputError('total input byte cap exceeded')
        captured[source_path] = (sha(data), identity, cap)
        if sql:
            try: data.decode('utf-8')
            except UnicodeError as exc: raise InputError('SQL must be strict UTF-8') from exc
            if b'\0' in data: raise InputError('SQL must not contain NUL')
        if destination is not None: files[destination] = data
        return data, source_path

    source = manifest['source']; database = None; dbpath = None
    if 'database' in source:
        database, dbpath = capture(source['database'], None, DB_CAP, False)
        if len(database) < 100 or database[:16] != b'SQLite format 3\0' or database[18:20] != b'\1\1':
            raise InputError('source must be a regular DELETE-journal SQLite database')
        if any(Path(str(dbpath)+s).exists() for s in ('-wal','-shm','-journal')):
            raise InputError('source sidecars present; quiescent DELETE source required')
        source_kind = 'database'; source_digest = sha(database)
        normalized['source'] = {'database':'sample.db'}
    else:
        schema, _ = capture(source['schema'],'inputs/schema.sql')
        seed, _ = capture(source['seed'],'inputs/seed.sql')
        source_kind = 'sql'
        source_digest = sha(canonical({'schema_sha256':sha(schema),'seed_sha256':sha(seed)}))
        normalized['source'] = {'schema':'inputs/schema.sql','seed':'inputs/seed.sql'}
    candidates = []
    for i, candidate in enumerate(manifest['candidates']):
        dest = f'inputs/candidate-{i+1}.sql'
        capture(candidate['sql'],dest)
        candidates.append({'name':candidate['name'],'sql':dest})
    normalized['candidates'] = candidates
    if output is not None: validate_output(output, captured)
    return InputBundle(normalized, files, source_kind, source_digest, database, captured, dbpath)
