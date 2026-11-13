"""Whole-library portability: every archive member, immutable evidence, and SQLite state."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote

from .adapters import references
from .common import BridgeError, dumps, file_hash


def materialize_archives(store, destination, progress=None):
    """Use generated member numbers so duplicate/unusual names never discard bytes."""
    entries, stats = [], dict(files=0, unreadable=0, directories=0, archives_unreadable=0)
    archives = {}
    for item in store.imports():
        archives.setdefault(item["sha256"], []).append(item)
    for checksum, imports in archives.items():
        labels = {"archive_sha256": checksum, "import_ids": [x["id"] for x in imports],
                  "sources": sorted({x["source"] for x in imports})}
        try:
            with zipfile.ZipFile(store.root / "archives" / f"{checksum}.zip") as archive:
                for index, info in enumerate(archive.infolist()):
                    if progress and index % 25 == 0:
                        progress(f"整理原始文件：本归档第 {index + 1} 项，累计导出 {stats['files']} 个文件")
                    entry = {**labels, "entry_index": index, "original_path": info.orig_filename,
                             "size": info.file_size, "compressed_size": info.compress_size,
                             "crc32": f"{info.CRC:08x}", "compression": info.compress_type,
                             "external_attributes": info.external_attr, "flags": info.flag_bits,
                             "header_offset": info.header_offset, "date_time": list(info.date_time),
                             "extra_base64": base64.b64encode(info.extra).decode("ascii"),
                             "comment_base64": base64.b64encode(info.comment).decode("ascii"),
                             "content_path": None, "sha256": None}
                    entries.append(entry)
                    if info.is_dir():
                        entry["status"] = "directory"
                        stats["directories"] += 1
                        continue
                    suffix = Path(info.filename).suffix
                    if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", suffix):
                        suffix = ".bin"
                    relative = f"files/{checksum}/{index:06d}{suffix}"
                    path = destination / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    count, digest = 0, hashlib.sha256()
                    try:
                        with archive.open(info) as inp, path.open("xb") as out:
                            for chunk in iter(lambda: inp.read(1024 * 1024), b""):
                                out.write(chunk)
                                count += len(chunk)
                                digest.update(chunk)
                        if count != info.file_size:
                            raise BridgeError("archive_member_size_mismatch")
                        entry.update(status="materialized", content_path=relative, sha256=digest.hexdigest())
                        stats["files"] += 1
                    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError, BridgeError) as exc:
                        entry.update(status="unreadable", recovered_bytes=count,
                                     partial_content_path=relative if path.exists() else None,
                                     error_type=type(exc).__name__)
                        stats["unreadable"] += 1
        except (OSError, zipfile.BadZipFile, NotImplementedError) as exc:
            entries.append({**labels, "entry_index": None, "status": "archive_unreadable", "error_type": type(exc).__name__})
            stats["archives_unreadable"] += 1
    with (destination / "archive_members.jsonl").open("w", encoding="utf-8", newline="\n") as out:
        for entry in entries:
            out.write(dumps(entry) + "\n")
    accounts = []
    for entry in entries:
        name = entry.get("original_path", "")
        if name.lower().endswith((".json", ".html", ".htm")):
            indexed = store.db.execute("SELECT 1 FROM conversation_versions WHERE member=? AND import_id IN (" +
                ",".join("?" for _ in entry["import_ids"]) + ") LIMIT 1", [name, *entry["import_ids"]]).fetchone()
            accounts.append({"original_path": name, "archive_sha256": entry["archive_sha256"],
                             "entry_index": entry["entry_index"], "content_path": entry["content_path"],
                             "status": entry["status"], "contains_indexed_conversations": bool(indexed)})
    (destination / "account_data.json").write_text(json.dumps({"files": accounts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return entries, stats


def _reference_matches(reference, entries):
    value = unquote(reference).split("?", 1)[0].split("#", 1)[0]
    value = re.sub(r"^(?:file-service|sediment|https?)://", "", value)
    value = re.sub(r"^sandbox:/", "", value).replace("\\", "/").lstrip("/")
    available = [x for x in entries if x.get("status") == "materialized"]
    matches = [x for x in available if x["original_path"].replace("\\", "/").lstrip("/") == value]
    method = "exact_path"
    if not matches:
        basename = value.rsplit("/", 1)[-1]
        matches = [x for x in available if x["original_path"].replace("\\", "/").rsplit("/", 1)[-1] == basename]
        method = "basename"
    if not matches and re.fullmatch(r"file-[A-Za-z0-9-]+", value):
        matches = [x for x in available if x["original_path"].replace("\\", "/").rsplit("/", 1)[-1].startswith((value + "-", value + "_", value + "."))]
        method = "file_id_prefix"
    if not matches:
        return {"status": "missing_local", "candidates": []}
    status = "matched" if len({x["sha256"] for x in matches}) == 1 else "ambiguous"
    return {"status": status, "method": method, "candidates": [
        {key: x[key] for key in ("archive_sha256", "entry_index", "original_path", "content_path", "sha256")} for x in matches]}


def export_references(store, destination, entries):
    stats = dict(matched=0, ambiguous=0, missing_local=0)
    by_source = {}
    for entry in entries:
        for source in entry.get("sources", []):
            by_source.setdefault(source, []).append(entry)
    sql = """SELECT v.*,m.message_id,m.node_id,c.external_id AS conversation_id,c.source
        FROM message_versions v JOIN messages m ON m.pk=v.message_pk
        JOIN conversations c ON c.pk=m.conversation_pk ORDER BY c.pk,m.pk,v.hash"""
    with (destination / "attachment_references.jsonl").open("w", encoding="utf-8", newline="\n") as out:
        for row in store.db.execute(sql):
            for ref in references(json.loads(row["raw_json"])):
                match = _reference_matches(ref, by_source.get(row["source"], []))
                stats[match["status"]] += 1
                record = {"reference": ref, "conversation_id": row["conversation_id"], "source": row["source"],
                          "message_id": row["message_id"], "node_id": row["node_id"], "message_hash": row["hash"], **match}
                out.write(dumps(record) + "\n")
    return stats


def snapshot_library(store, destination):
    path = destination / "library" / "bridge.sqlite3"
    path.parent.mkdir()
    with closing(sqlite3.connect(path)) as target:
        store.db.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BridgeError("snapshot_integrity_failed")


def restore_library(package, destination):
    from .export import verify_package
    package, destination = Path(package).resolve(), Path(destination).resolve()
    if destination == package or package in destination.parents:
        raise BridgeError("restore_must_be_outside_package")
    result = verify_package(package)
    if not result["ok"]:
        raise BridgeError("package_integrity_failed")
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("includes_library") or not manifest.get("includes_originals"):
        raise BridgeError("full_restore_requires_library_snapshot_and_originals")
    database = package / "library" / "bridge.sqlite3"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise BridgeError("snapshot_integrity_failed")
        checksums = {x[0] for x in db.execute("SELECT DISTINCT sha256 FROM imports")}
        if checksums != set(manifest["archive_sha256"]):
            raise BridgeError("snapshot_archive_inventory_mismatch")
        counts = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("imports", "conversations", "messages", "memories", "memory_versions", "observations")}
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(database, destination / "bridge.sqlite3")
    (destination / "archives").mkdir()
    (destination / "tmp").mkdir()
    for checksum in checksums:
        target = destination / "archives" / f"{checksum}.zip"
        shutil.copyfile(package / "originals" / f"{checksum}.zip", target)
        if file_hash(target) != checksum:
            raise BridgeError("restored_archive_checksum_mismatch")
    return {"restored": True, "scope": "memory_bridge_library", "counts": counts,
            "chatgpt_account_state_restored": False}
