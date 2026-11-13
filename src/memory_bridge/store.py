from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

from .adapters import ChatGPTAdapter, iter_conversations
from .archive import Limits, hash_member, inspect_zip, preserve_zip, preflight_zip
from .common import BridgeError, digest, dumps, file_hash, now
from .browser_package import is_browser_package, read_browser_package, merge_browser_history
from .continuations import attach_from_messages, track_root


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO metadata VALUES ('schema_version','1');
CREATE TABLE IF NOT EXISTS imports(
 id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, source TEXT NOT NULL, platform TEXT NOT NULL,
 imported_at TEXT NOT NULL, status TEXT NOT NULL, stats TEXT NOT NULL DEFAULT '{}',
 warnings TEXT NOT NULL DEFAULT '[]', UNIQUE(sha256, source, platform));
CREATE TABLE IF NOT EXISTS members(
 import_id INTEGER NOT NULL REFERENCES imports(id), path TEXT NOT NULL, size INTEGER,
 sha256 TEXT, kind TEXT, status TEXT NOT NULL, PRIMARY KEY(import_id,path));
CREATE TABLE IF NOT EXISTS conversations(
 pk TEXT PRIMARY KEY, platform TEXT NOT NULL, source TEXT NOT NULL, external_id TEXT NOT NULL,
 title TEXT, created REAL, updated REAL, hash TEXT NOT NULL,
 first_import INTEGER NOT NULL REFERENCES imports(id), last_import INTEGER NOT NULL REFERENCES imports(id),
 UNIQUE(platform,source,external_id));
CREATE TABLE IF NOT EXISTS conversation_continuations(
 conversation_pk TEXT PRIMARY KEY REFERENCES conversations(pk),
 root_pk TEXT REFERENCES conversations(pk), position INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS continuation_root ON conversation_continuations(root_pk,position);
CREATE TABLE IF NOT EXISTS conversation_trash(
 root_pk TEXT NOT NULL PRIMARY KEY REFERENCES conversations(pk), deleted_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversation_resets(pk TEXT NOT NULL PRIMARY KEY, deleted_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending_archive_deletions(sha256 TEXT NOT NULL PRIMARY KEY);
CREATE VIEW IF NOT EXISTS active_conversations AS
 SELECT c.* FROM conversations c
 LEFT JOIN conversation_continuations l ON l.conversation_pk=c.pk
 LEFT JOIN conversation_trash t ON t.root_pk=COALESCE(l.root_pk,c.pk)
 WHERE t.root_pk IS NULL;
CREATE TABLE IF NOT EXISTS conversation_versions(
 conversation_pk TEXT NOT NULL REFERENCES conversations(pk), hash TEXT NOT NULL,
 raw_json TEXT NOT NULL, import_id INTEGER NOT NULL REFERENCES imports(id), member TEXT NOT NULL,
 PRIMARY KEY(conversation_pk,hash));
CREATE TABLE IF NOT EXISTS messages(
 pk TEXT PRIMARY KEY, conversation_pk TEXT NOT NULL REFERENCES conversations(pk),
 node_id TEXT NOT NULL, message_id TEXT, role TEXT NOT NULL, text TEXT NOT NULL, created REAL,
 parent TEXT, children TEXT NOT NULL, refs TEXT NOT NULL, hash TEXT NOT NULL,
 first_import INTEGER NOT NULL REFERENCES imports(id), last_import INTEGER NOT NULL REFERENCES imports(id),
 UNIQUE(conversation_pk,node_id));
CREATE TABLE IF NOT EXISTS message_versions(
 message_pk TEXT NOT NULL REFERENCES messages(pk), hash TEXT NOT NULL, raw_json TEXT NOT NULL,
 import_id INTEGER NOT NULL REFERENCES imports(id), member TEXT NOT NULL,
 PRIMARY KEY(message_pk,hash));
CREATE TABLE IF NOT EXISTS observations(
 import_id INTEGER NOT NULL REFERENCES imports(id), conversation_pk TEXT NOT NULL REFERENCES conversations(pk),
 message_pk TEXT NOT NULL DEFAULT '', hash TEXT NOT NULL, member TEXT NOT NULL,
 PRIMARY KEY(import_id,conversation_pk,message_pk,hash,member));
CREATE TABLE IF NOT EXISTS memories(
 id TEXT PRIMARY KEY, content TEXT NOT NULL, kind TEXT NOT NULL, epistemic TEXT NOT NULL,
 status TEXT NOT NULL, confirmed INTEGER NOT NULL, confidence REAL NOT NULL,
 source_time REAL, extracted_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 conflict_key TEXT, conflicts TEXT NOT NULL DEFAULT '[]', merged_into TEXT);
CREATE TABLE IF NOT EXISTS memory_sources(
 memory_id TEXT NOT NULL REFERENCES memories(id), message_pk TEXT NOT NULL,
 message_hash TEXT NOT NULL, PRIMARY KEY(memory_id,message_pk,message_hash),
 FOREIGN KEY(message_pk,message_hash) REFERENCES message_versions(message_pk,hash));
CREATE TABLE IF NOT EXISTS memory_versions(
 memory_id TEXT NOT NULL REFERENCES memories(id), version INTEGER NOT NULL,
 snapshot TEXT NOT NULL, reason TEXT NOT NULL, changed_at TEXT NOT NULL,
 PRIMARY KEY(memory_id,version));
CREATE TABLE IF NOT EXISTS extraction_candidates(
 fingerprint TEXT PRIMARY KEY, memory_id TEXT NOT NULL REFERENCES memories(id));
CREATE INDEX IF NOT EXISTS messages_time ON messages(created);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_pk);
CREATE INDEX IF NOT EXISTS observations_import ON observations(import_id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(pk UNINDEXED, text, tokenize='trigram');
"""


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "bridge.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        try:
            self.db.executescript(SCHEMA)
        except sqlite3.OperationalError as exc:
            self.close()
            raise BridgeError("sqlite_fts5_trigram_required") from exc
        if self.db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] != '1':
            self.close()
            raise BridgeError("unsupported_database_schema")
        if self.db.execute('SELECT 1 FROM pending_archive_deletions LIMIT 1').fetchone():
            from .deletion import finish_archive_deletions
            finish_archive_deletions(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.db.close()

    def imports(self):
        return [self._import_result(x) for x in self.db.execute("SELECT * FROM imports ORDER BY id")]

    @staticmethod
    def _import_result(row):
        result = dict(row)
        for key in ("stats", "warnings"):
            result[key] = json.loads(result[key])
        return result

    def import_zip(self, path, source="default", limits=None, retry=False, progress=None, capture_task=None):
        limits = limits or Limits.personal()
        if progress:
            progress("正在逐字节保存原始 ZIP…")
        with self.db:
            # Reserve the archive and its import row together against concurrent deletion.
            self.db.execute('BEGIN IMMEDIATE')
            checksum, archived = preserve_zip(Path(path), self.root, limits)
            old = self.db.execute("SELECT * FROM imports WHERE sha256=? AND source=? AND platform='chatgpt'",
                                  (checksum, source)).fetchone()
            # Older desktop versions archived some plugin ZIPs without indexing them.
            if old and not retry and json.loads(old['stats']).get('input_kind') != 'browser_extension':
                try:
                    with zipfile.ZipFile(archived) as probe:
                        retry = is_browser_package(probe)
                except (zipfile.BadZipFile, OSError, NotImplementedError):
                    pass
            if old and not retry:
                return {**self._import_result(old), "already_imported": True}
            if old:
                iid = old["id"]
                self.db.execute("UPDATE imports SET status='processing' WHERE id=?", (iid,))
            else:
                iid = self.db.execute("INSERT INTO imports(sha256,source,platform,imported_at,status) VALUES(?,?,'chatgpt',?,'processing')",
                                      (checksum, source, now())).lastrowid
        stats = dict(new_conversations=0, changed_conversations=0, unchanged_conversations=0,
                     new_messages=0, changed_messages=0, unchanged_messages=0,
                     parsed_records=0, unrecognized_records=0, indexed_json_files=0,
                     unparsed_json_files=0, archived_members=0, unresolved_references=0,
                     historical_conversation_observations=0, historical_message_observations=0)
        warnings = []
        partial = False
        adapter = ChatGPTAdapter()
        try:
            if limits.strict_structure or limits.max_members is not None or limits.max_directory is not None:
                preflight_zip(archived, limits)
            with zipfile.ZipFile(archived) as zf:
                members = inspect_zip(zf, limits)
                browser_package = is_browser_package(zf)
                if browser_package:
                    stats['input_kind'] = 'browser_extension'
                member_hashes = {}
                for member_number, member in enumerate(members, 1):
                    if progress:
                        progress(f"读取文件 {member_number}/{len(members)}；已索引 {stats['parsed_records']} 个对话")
                    if member.is_dir():
                        continue
                    suffix = Path(member.filename).suffix.lower()
                    kind = "json" if suffix == ".json" else "html" if suffix == ".html" else "attachment_or_other"
                    try:
                        member_hash = hash_member(zf, member, limits)
                    except (BridgeError, zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError):
                        partial = True
                        warnings.append({"code": "unreadable_member", "member": member.filename})
                        with self.db:
                            self.db.execute("INSERT OR REPLACE INTO members VALUES(?,?,?,?,?,?)",
                                            (iid, member.filename, member.file_size, None, kind, "unreadable",))
                        continue
                    with self.db:
                        self.db.execute("INSERT OR REPLACE INTO members VALUES(?,?,?,?,?,?)",
                                        (iid, member.filename, member.file_size, member_hash, kind, "archived"))
                    stats["archived_members"] += 1
                    member_hashes[member.filename] = member_hash
                    if browser_package:
                        continue
                    if suffix != ".json":
                        continue
                    parsed = 0
                    try:
                        for raw, fallback in iter_conversations(lambda: zf.open(member)):
                            normalized = adapter.normalize(raw, fallback)
                            if normalized is None:
                                stats["unrecognized_records"] += 1
                                continue
                            with self.db:
                                self._index(normalized, source, iid, member.filename, stats)
                            parsed += 1
                            stats["parsed_records"] += 1
                            if progress and stats["parsed_records"] % 25 == 0:
                                progress(f"已索引 {stats['parsed_records']} 个对话，正在继续读取…")
                            warnings.extend({"code": issue, "member": member.filename,
                                             "conversation_id": normalized["id"]} for issue in set(normalized["issues"]))
                            for msg in normalized["messages"]:
                                stats["unresolved_references"] += len(msg["references"])
                    except (ValueError, TypeError, OverflowError, RecursionError, zipfile.BadZipFile, OSError, ijson_error()) as exc:
                        partial = True
                        warnings.append({"code": "json_parse_incomplete", "member": member.filename,
                                         "error_type": type(exc).__name__, "recovered_records": parsed})
                    if parsed:
                        stats["indexed_json_files"] += 1
                    else:
                        stats["unparsed_json_files"] += 1
                        warnings.append({"code": "json_not_indexed", "member": member.filename})
                if browser_package:
                    if progress:
                        progress("正在校验插件包并索引已采集的对话…")
                    normalized, coverage, browser_warnings, browser_partial = read_browser_package(zf, member_hashes)
                    if not coverage['from_start']:
                        normalized = merge_browser_history(self, normalized, source)
                    merged_info=normalized['raw'].get('_memory_bridge_browser',{}).get('cross_package_merge',{})
                    if merged_info.get('disconnected_roots',0)>1:
                        browser_partial=True
                        browser_warnings.append({'code':'browser_cross_package_unconnected_segments','roots':merged_info['disconnected_roots']})
                    with self.db:
                        self._index(normalized, source, iid, "对话全文.json", stats)
                        if (capture_task and coverage.get('delivery_id') == capture_task
                                and coverage['from_start'] and coverage['page_checks_passed']):
                            pk = digest(['chatgpt', source, normalized['id']])
                            self.db.execute('DELETE FROM conversation_resets WHERE pk=?',(pk,))
                            result = self.db.execute('''DELETE FROM conversation_trash WHERE root_pk=
                                COALESCE((SELECT root_pk FROM conversation_continuations WHERE conversation_pk=?),?)''',(pk,pk))
                            if result.rowcount:
                                stats['restored_after_recapture'] = True
                    stats.update(input_kind="browser_extension", browser_coverage=coverage,
                                 parsed_records=1, indexed_json_files=1)
                    stats['unresolved_references'] += sum(len(m['references']) for m in normalized['messages'])
                    warnings.extend(browser_warnings)
                    partial = partial or browser_partial
        except (zipfile.BadZipFile, BridgeError, OSError, NotImplementedError) as exc:
            status = "rejected"
            warnings.append({"code": str(exc) if isinstance(exc, BridgeError) else "invalid_archive",
                             "error_type": type(exc).__name__})
        else:
            status = "partial" if partial or not stats["parsed_records"] else "complete"
        previous = self.db.execute("SELECT max(id) FROM imports WHERE id<? AND source=? AND platform='chatgpt' AND status IN ('complete','partial')",
                                   (iid, source)).fetchone()[0]
        stats["not_observed_conversations"] = self._missing(previous, iid, "message_pk='' AND") if previous else 0
        stats["not_observed_messages"] = self._missing(previous, iid, "message_pk!='' AND") if previous else 0
        with self.db:
            self.db.execute("UPDATE imports SET status=?,stats=?,warnings=? WHERE id=?",
                            (status, dumps(stats), dumps(warnings), iid))
        return self._import_result(self.db.execute("SELECT * FROM imports WHERE id=?", (iid,)).fetchone())

    def _missing(self, previous, current, condition):
        return self.db.execute(f"""SELECT count(*) FROM (
            SELECT DISTINCT conversation_pk,message_pk FROM observations WHERE {condition} import_id=?
            EXCEPT SELECT DISTINCT conversation_pk,message_pk FROM observations WHERE {condition} import_id=?)""",
                               (previous, current)).fetchone()[0]

    def _index(self, conv, source, iid, member, stats):
        cpk = digest(["chatgpt", source, conv["id"]])
        chash = digest(conv["raw"])
        old = self.db.execute("SELECT hash,last_import FROM conversations WHERE pk=?", (cpk,)).fetchone()
        key = ("historical_conversation_observations" if old and old["last_import"] > iid else
               "new_conversations" if old is None else "unchanged_conversations" if old[0] == chash else "changed_conversations")
        stats[key] += 1
        self.db.execute("""INSERT INTO conversations VALUES(?,'chatgpt',?,?,?,?,?,?,?,?)
            ON CONFLICT(pk) DO UPDATE SET title=excluded.title,created=excluded.created,updated=excluded.updated,
            hash=excluded.hash,last_import=excluded.last_import WHERE excluded.last_import>=conversations.last_import""",
                        (cpk, source, conv["id"], conv["title"], conv["created"], conv["updated"], chash, iid, iid))
        self.db.execute("INSERT OR IGNORE INTO conversation_versions VALUES(?,?,?,?,?)", (cpk, chash, dumps(conv["raw"]), iid, member))
        self.db.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)", (iid, cpk, "", chash, member))
        for msg in conv["messages"]:
            mpk = digest([cpk, msg["node_id"]])
            mhash = digest(msg["raw"])
            old = self.db.execute("SELECT hash,last_import FROM messages WHERE pk=?", (mpk,)).fetchone()
            key = ("historical_message_observations" if old and old["last_import"] > iid else
                   "new_messages" if old is None else "unchanged_messages" if old[0] == mhash else "changed_messages")
            stats[key] += 1
            self.db.execute("""INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(pk) DO UPDATE SET message_id=excluded.message_id,role=excluded.role,text=excluded.text,
                created=excluded.created,parent=excluded.parent,children=excluded.children,refs=excluded.refs,
                hash=excluded.hash,last_import=excluded.last_import WHERE excluded.last_import>=messages.last_import""",
                            (mpk, cpk, msg["node_id"], msg["message_id"], msg["role"], msg["text"], msg["created"],
                             msg["parent"], dumps(msg["children"]), dumps(msg["references"]), mhash, iid, iid))
            self.db.execute("INSERT OR IGNORE INTO message_versions VALUES(?,?,?,?,?)", (mpk, mhash, dumps(msg["raw"]), iid, member))
            self.db.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)", (iid, cpk, mpk, mhash, member))
            if old is None or (old["last_import"] <= iid and old[0] != mhash):
                self.db.execute("DELETE FROM messages_fts WHERE pk=?", (mpk,))
                self.db.execute("INSERT INTO messages_fts(pk,text) VALUES(?,?)", (mpk, msg["text"]))
        if attach_from_messages(self.db, cpk, conv['messages']):
            stats['linked_continuations'] = stats.get('linked_continuations', 0) + 1

    def trash_conversation(self, pk, *, confirmed=False):
        if not confirmed:
            raise BridgeError('请先确认将对话移入回收站。')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if track_root(self.db, pk) != pk:
                raise BridgeError('对话状态已变化，请刷新列表后重试。')
            self.db.execute('INSERT INTO conversation_trash(root_pk,deleted_at) VALUES(?,?)', (pk, now()))

    def restore_conversation(self, pk):
        with self.db:
            result = self.db.execute('DELETE FROM conversation_trash WHERE root_pk=?', (pk,))
            if not result.rowcount:
                raise BridgeError('回收站里找不到这条对话，请刷新列表。')

    def search(self, query=None, *, conversation_id=None, source=None, topic=None, after=None, before=None, limit=50):
        conditions, params = [], []
        join = ""
        if query:
            if len(query) >= 3:
                join = " JOIN messages_fts f ON f.pk=m.pk"
                conditions.append("messages_fts MATCH ?")
                params.append('"' + query.replace('"', '""') + '"')
            else:
                conditions.append("instr(lower(m.text),lower(?))>0")
                params.append(query)
        for clause, value in (("c.external_id=?", conversation_id), ("c.source=?", source),
                              ("instr(lower(c.title),lower(?))>0", topic), ("m.created>=?", after), ("m.created<=?", before)):
            if value is not None:
                conditions.append(clause)
                params.append(value)
        sql = """SELECT m.*,c.external_id AS conversation_id,c.title,c.source FROM messages m
                 JOIN active_conversations c ON c.pk=m.conversation_pk""" + join
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY m.created,m.pk LIMIT ?"
        return [dict(x) for x in self.db.execute(sql, [*params, max(1, min(limit, 10000))])]

    def repair_archive(self, path, *, confirmed=False):
        """Replace a missing/corrupt archive only with exact matching original bytes."""
        if not confirmed:
            raise BridgeError("repair_requires_confirmation")
        source = Path(path)
        checksum = file_hash(source)
        if not self.db.execute("SELECT 1 FROM imports WHERE sha256=?", (checksum,)).fetchone():
            raise BridgeError("repair_input_not_a_known_archive")
        target = self.root / "archives" / f"{checksum}.zip"
        if source.resolve() == target.resolve():
            raise BridgeError("repair_requires_independent_original")
        # Validate the proposed bytes in an application-owned staging archive first.
        from tempfile import TemporaryDirectory
        import os
        temp_root = (self.root / "tmp").resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="bridge-repair-", dir=temp_root) as staging:
            if Path(staging).resolve().parent != temp_root:
                raise BridgeError("repair_staging_path_invalid")
            verified_hash, staged = preserve_zip(source, Path(staging), Limits.personal())
            if verified_hash != checksum:
                raise BridgeError("repair_input_changed")
            os.replace(staged, target)
        return {"repaired": True, "sha256": checksum}

    def conversation_records(self, ids=None, source=None):
        conditions, params = [], []
        if ids:
            conditions.append("external_id IN (" + ",".join("?" for _ in ids) + ")")
            params.extend(ids)
        if source is not None:
            conditions.append("source=?")
            params.append(source)
        sql = "SELECT * FROM conversations" + (" WHERE " + " AND ".join(conditions) if conditions else "") + " ORDER BY pk"
        for row in self.db.execute(sql, params):
            item = dict(row)
            item["versions"] = [dict(v) for v in self.db.execute(
                "SELECT hash,raw_json,import_id,member FROM conversation_versions WHERE conversation_pk=? ORDER BY import_id", (row["pk"],))]
            for version in item["versions"]:
                version["raw"] = json.loads(version.pop("raw_json"))
            item["messages"] = []
            for message in self.db.execute("SELECT * FROM messages WHERE conversation_pk=? ORDER BY created,pk", (row["pk"],)):
                msg = dict(message)
                for key in ("children", "refs"):
                    msg[key] = json.loads(msg[key])
                item["messages"].append(msg)
            yield item

    def verify_archives(self):
        return [{"sha256": row[0], "ok": (self.root / "archives" / f"{row[0]}.zip").is_file()
                 and file_hash(self.root / "archives" / f"{row[0]}.zip") == row[0]}
                for row in self.db.execute("SELECT DISTINCT sha256 FROM imports")]

    def verify_versions(self):
        errors, checked = [], 0
        for table, pk in (("conversation_versions", "conversation_pk"), ("message_versions", "message_pk")):
            for row in self.db.execute(f"SELECT {pk},hash,raw_json FROM {table}"):
                checked += 1
                try:
                    valid = digest(json.loads(row["raw_json"])) == row["hash"]
                except (ValueError, TypeError):
                    valid = False
                if not valid:
                    errors.append({"table": table, "pk": row[pk], "code": "raw_version_hash_mismatch"})
        return {"ok": not errors, "checked_versions": checked, "errors": errors}


def ijson_error():
    import ijson
    return ijson.JSONError
