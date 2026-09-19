"""Sandbox-writable working database, checkpointed by trusted lifecycle hooks."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import tempfile


def working_directory(archive):
    identity = f'{getattr(os, "getuid", lambda: "windows")()}:{archive}'
    name = 'codex-tasklist-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
    base = Path(tempfile.gettempdir()) if os.name == 'nt' else Path('/tmp')
    directory = base / name
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or (os.name != 'nt' and
            (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700))):
        raise ValueError(f'Refusing unsafe tasklist runtime directory: {directory}')
    target = directory / 'tasks.sqlite3'
    source = next((path for path in (archive / 'runtime.sqlite3', archive / 'tasks.sqlite3') if path.exists()), None)
    if source is not None and not target.exists():
        restored = directory / f'restore-{os.getpid()}.sqlite3'
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as saved, \
                closing(sqlite3.connect(restored)) as working:
            saved.backup(working)
        try:
            os.link(restored, target)
        except FileExistsError:
            pass
        finally:
            restored.unlink()
    return directory


def checkpoint(db, archive):
    # ponytail: full snapshot per hook; incremental storage if queues become large.
    archive.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(archive / 'runtime.sqlite3', timeout=2)) as saved:
        db.backup(saved)
