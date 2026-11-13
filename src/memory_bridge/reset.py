"""Reset only the active library and this application's managed outputs."""
import re
import shutil
from pathlib import Path

from . import __version__
from .common import BridgeError, now


OUTPUT_FOLDERS = ('浏览器备份', '接续目录', '完整备份')
LEGACY_PACKAGE = re.compile(r'(?:迁移包|桌面接续目录)-\d{8}-\d{6}-[0-9a-f]{6}')
RESET_TABLES = (
    'extraction_candidates', 'memory_sources', 'memory_versions', 'memories',
    'messages_fts', 'observations', 'message_versions', 'messages',
    'conversation_versions', 'conversation_continuations', 'conversation_trash',
    'conversations', 'members', 'imports', 'conversation_resets', 'pending_archive_deletions',
)


def managed_directory(path, parent):
    if path.resolve() != parent / path.name or (path.exists() and not path.is_dir()):
        raise BridgeError(f'无法确认清理目录的实际位置：{path}')
    return path


def empty_directory(path):
    if path.is_dir():
        shutil.rmtree(path)
        path.mkdir()


def clear_upgrade_cache(store, output):
    output = Path(output).resolve()
    db = store.db
    with db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT value FROM metadata WHERE key='desktop_cache_version'").fetchone()
        if row and row[0] == __version__:
            return
        empty_directory(managed_directory(store.root/'tmp', store.root))
        browser = managed_directory(output/'浏览器备份', output)
        if browser.is_dir():
            for task in browser.iterdir():
                if not re.fullmatch(r'[0-9a-f]{32}', task.name) or not task.is_dir():
                    continue
                task = managed_directory(task, browser)
                for path in task.glob('*.zip.part'):
                    if path.is_file() and path.resolve() == task/path.name:
                        path.unlink()
        db.execute("INSERT OR REPLACE INTO metadata VALUES('desktop_cache_version',?)", (__version__,))


def reset_library(store, output, *, confirmed=False):
    if confirmed is not True:
        raise BridgeError('请先确认清空本地存档。')
    output = Path(output).resolve()
    targets = [managed_directory(store.root/name, store.root) for name in ('archives', 'tmp')]
    targets.extend(managed_directory(output/name, output) for name in OUTPUT_FOLDERS)
    legacy = [managed_directory(path, output) for path in output.iterdir()
              if LEGACY_PACKAGE.fullmatch(path.name) and (path/'manifest.json').is_file()] if output.is_dir() else []
    if any(target == store.root or target in store.root.parents for target in targets + legacy):
        raise BridgeError('资料库位于待清空的备份目录中，请先更改存储位置。')
    db = store.db
    db.execute('PRAGMA secure_delete=ON')
    stamp = now()
    failed = []
    with db:
        db.execute('BEGIN IMMEDIATE')
        count = db.execute('SELECT COUNT(*) FROM conversations').fetchone()[0]
        for table in RESET_TABLES:
            db.execute(f'DELETE FROM {table}')
        db.execute("INSERT OR REPLACE INTO metadata VALUES('capture_reset_at',?)", (stamp,))
        for path in targets + legacy:
            try:
                if path in legacy:
                    shutil.rmtree(path)
                else:
                    empty_directory(path)
            except OSError:
                failed.append(str(path))
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return {'conversations':count, 'failed':failed, 'reset_at':stamp}
