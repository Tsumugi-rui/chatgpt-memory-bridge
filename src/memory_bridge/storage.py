from pathlib import Path
from contextlib import closing
import shutil
import sqlite3

from .common import BridgeError,file_hash
from .store import Store


def copy_library(source,destination,progress=lambda _:None,allow_legacy_child=False):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    child_allowed=allow_legacy_child and destination.parent==source and destination.name=='资料库'
    if source==destination or (source in destination.parents and not child_allowed) or destination in source.parents:
        raise BridgeError('library_copy_paths_overlap')
    if not (source/'bridge.sqlite3').is_file():
        raise BridgeError('source_library_missing')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise BridgeError('destination_library_must_be_empty')
    destination.mkdir(parents=True,exist_ok=True)
    progress('正在复制资料库，原目录会保留…')
    with Store(source) as store:
        store.db.execute('BEGIN')
        try:
            with closing(sqlite3.connect(destination/'bridge.sqlite3')) as target:
                store.db.backup(target)
                target.execute('PRAGMA journal_mode=DELETE')
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise BridgeError('copied_database_integrity_failed')
            files=0
            for path in source.rglob('*'):
                relative=path.relative_to(source)
                if child_allowed and (destination==path.resolve() or destination in path.resolve().parents
                    or relative.parts[0] in ('浏览器备份','接续目录','完整备份')):
                    continue
                if relative.parts[0]=='tmp' or str(relative) in ('bridge.sqlite3','bridge.sqlite3-wal','bridge.sqlite3-shm'):
                    continue
                if path.is_symlink():
                    raise BridgeError('library_copy_symlink_unsupported')
                if not path.is_file():
                    continue
                target=destination/relative
                if destination not in target.resolve().parents:
                    raise BridgeError('library_copy_path_escape')
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,target)
                if path.stat().st_size!=target.stat().st_size or file_hash(path)!=file_hash(target):
                    raise BridgeError('library_copy_checksum_mismatch')
                files+=1
                progress(f'已复制并校验 {files} 个文件…')
        finally:
            store.db.rollback()
    with Store(destination) as copied:
        archives=copied.verify_archives()
        if (not all(x['ok'] for x in archives) or not copied.verify_versions()['ok']
                or copied.db.execute('PRAGMA foreign_key_check').fetchall()):
            raise BridgeError('copied_library_verification_failed')
    return {'copied':True,'files':files,'source_preserved':True,'verification':True}
