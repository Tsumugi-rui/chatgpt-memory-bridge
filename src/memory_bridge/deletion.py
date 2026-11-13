"""Permanently delete a conversation group and its exclusive library archives."""
import hashlib
import json
import re
import zipfile

from .common import BridgeError, now


def finish_archive_deletions(store):
    """Retry archive removal after a crash or a temporary file lock."""
    db = store.db
    with db:
        db.execute('BEGIN IMMEDIATE')
        pending = list(db.execute('SELECT sha256 FROM pending_archive_deletions'))
        for (checksum,) in pending:
            if not re.fullmatch(r'[0-9a-f]{64}',checksum):
                continue
            if not db.execute('SELECT 1 FROM imports WHERE sha256=?',(checksum,)).fetchone():
                archive = store.root/'archives'/(checksum+'.zip')
                if archive.parent.resolve().parent != store.root:
                    continue
                try:
                    archive.unlink(missing_ok=True)
                except OSError:
                    continue
            db.execute('DELETE FROM pending_archive_deletions WHERE sha256=?',(checksum,))
        return db.execute('SELECT COUNT(*) FROM pending_archive_deletions').fetchone()[0]


def _legacy_volume_checksum(store, rows):
    parts = {}
    count = None
    try:
        for row in rows:
            path = store.root/'archives'/(row['sha256']+'.zip')
            with zipfile.ZipFile(path) as archive:
                if archive.getinfo('bridge-volume.json').file_size > 1000000:
                    return None
                info = json.loads(archive.read('bridge-volume.json'))
                if type(info['count']) is not int or type(info['index']) is not int or not 1<=info['index']<=info['count']<=10000:
                    return None
                if count is not None and count != info['count']:
                    return None
                count = info['count'];parts[info['index']] = path
        if not count or set(parts) != set(range(1,count+1)):
            return None
        result = hashlib.sha256()
        for index in range(1,count+1):
            with zipfile.ZipFile(parts[index]) as archive, archive.open('payload.bin') as payload:
                while block := payload.read(1024*1024):
                    result.update(block)
        return result.hexdigest()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile):
        return None


def _include_volume_imports(store):
    db = store.db
    logical = {row['sha256'] for row in db.execute('SELECT sha256 FROM imports WHERE id IN (SELECT id FROM purge_imports)')}
    sources = {row['source'] for row in db.execute('SELECT source FROM imports WHERE id IN (SELECT id FROM purge_imports)')}
    groups = {}
    for row in db.execute("SELECT id,sha256,source,stats FROM imports WHERE platform='browser_volume'"):
        stats = json.loads(row['stats'])
        groups.setdefault(stats.get('set_id'),[]).append((row,stats.get('reassembled_sha256')))
    for group in groups.values():
        rows = [row for row,_ in group]
        if not any(row['source'] in sources for row in rows):
            continue
        known = {checksum for _,checksum in group if checksum}
        checksum = next(iter(known)) if len(known)==1 else _legacy_volume_checksum(store,rows)
        if checksum is None:
            raise BridgeError('资料库有旧版分卷缺失或损坏，无法核对它属于哪条对话。永久删除已停止。')
        if checksum in logical:
            db.executemany('INSERT INTO purge_imports VALUES(?)',[(row['id'],) for row in rows])


def delete_conversations(store, roots, *, confirmed=False, expected_members=None):
    if not confirmed:
        raise BridgeError('请先确认永久删除。')
    roots = set(roots)
    db = store.db
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TEMP TABLE purge_roots(pk TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO purge_roots VALUES(?)',[(pk,) for pk in roots])
        members = {row[0] for row in db.execute('''SELECT c.pk FROM conversations c
            LEFT JOIN conversation_continuations l ON l.conversation_pk=c.pk
            WHERE COALESCE(l.root_pk,c.pk) IN (SELECT pk FROM purge_roots)''')}
        if not roots or not roots <= members or (expected_members is not None and members != set(expected_members)):
            raise BridgeError('对话分组已变化，请刷新列表后重试。')
        db.execute('CREATE TEMP TABLE purge_conversations(pk TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO purge_conversations VALUES(?)',[(member,) for member in members])
        db.execute('CREATE TEMP TABLE purge_imports(id INTEGER PRIMARY KEY)')
        db.execute('''INSERT INTO purge_imports SELECT DISTINCT import_id FROM observations
            WHERE conversation_pk IN (SELECT pk FROM purge_conversations)''')
        _include_volume_imports(store)
        shared = db.execute('''SELECT 1 FROM observations o JOIN imports i ON i.id=o.import_id
            WHERE o.conversation_pk NOT IN (SELECT pk FROM purge_conversations)
            AND i.sha256 IN (SELECT sha256 FROM imports WHERE id IN (SELECT id FROM purge_imports)) LIMIT 1''').fetchone()
        if shared:
            raise BridgeError('选中的对话与其他对话共用原始 ZIP。请同时勾选使用该 ZIP 的对话，再选择“永久删除勾选项…”。')
        # The same archive may have been imported under another source without indexing a conversation.
        db.execute('''INSERT OR IGNORE INTO purge_imports SELECT id FROM imports WHERE sha256 IN
            (SELECT sha256 FROM imports WHERE id IN (SELECT id FROM purge_imports))''')
        if db.execute("SELECT 1 FROM imports WHERE status='processing' AND id IN (SELECT id FROM purge_imports) LIMIT 1").fetchone():
            raise BridgeError('这条对话的备份正在导入，请等导入完成后再删除。')
        db.execute('''INSERT OR IGNORE INTO pending_archive_deletions
            SELECT sha256 FROM imports WHERE id IN (SELECT id FROM purge_imports)''')
        db.execute('CREATE TEMP TABLE purge_messages AS SELECT pk FROM messages WHERE conversation_pk IN (SELECT pk FROM purge_conversations)')
        db.execute('''CREATE TEMP TABLE purge_memories AS SELECT DISTINCT memory_id FROM memory_sources
            WHERE message_pk IN (SELECT pk FROM purge_messages) AND memory_id NOT IN
            (SELECT memory_id FROM memory_sources WHERE message_pk NOT IN (SELECT pk FROM purge_messages))''')
        db.execute('DELETE FROM extraction_candidates WHERE memory_id IN (SELECT memory_id FROM purge_memories)')
        db.execute('''DELETE FROM memory_versions WHERE memory_id IN (SELECT memory_id FROM purge_memories)
            OR EXISTS (SELECT 1 FROM json_each(memory_versions.snapshot,'$.sources') source
                WHERE json_extract(source.value,'$.message_pk') IN (SELECT pk FROM purge_messages))''')
        db.execute('DELETE FROM memory_sources WHERE message_pk IN (SELECT pk FROM purge_messages)')
        db.execute('DELETE FROM memories WHERE id IN (SELECT memory_id FROM purge_memories)')
        db.execute('DELETE FROM messages_fts WHERE pk IN (SELECT pk FROM purge_messages)')
        db.execute('DELETE FROM observations WHERE conversation_pk IN (SELECT pk FROM purge_conversations)')
        db.execute('DELETE FROM message_versions WHERE message_pk IN (SELECT pk FROM purge_messages)')
        db.execute('DELETE FROM messages WHERE pk IN (SELECT pk FROM purge_messages)')
        db.execute('DELETE FROM conversation_versions WHERE conversation_pk IN (SELECT pk FROM purge_conversations)')
        db.execute('DELETE FROM conversation_trash WHERE root_pk IN (SELECT pk FROM purge_conversations)')
        db.execute('DELETE FROM conversation_continuations WHERE conversation_pk IN (SELECT pk FROM purge_conversations)')
        db.execute('DELETE FROM conversations WHERE pk IN (SELECT pk FROM purge_conversations)')
        db.execute('DELETE FROM members WHERE import_id IN (SELECT id FROM purge_imports)')
        db.execute('DELETE FROM imports WHERE id IN (SELECT id FROM purge_imports)')
        db.executemany('INSERT OR REPLACE INTO conversation_resets VALUES(?,?)',[(member,now()) for member in members])
        for table in ('purge_roots','purge_conversations','purge_imports','purge_messages','purge_memories'):
            db.execute(f'DROP TABLE {table}')
    return {'deleted':len(members),'pending_archives':finish_archive_deletions(store)}
