from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
import sys
import time
from pathlib import Path

from . import __version__
from .archive import Limits
from .common import BridgeError, timestamp
from .crypto import decrypt_file, encrypt_file
from .export import export_conversations, export_package, verify_package
from .memories import EPISTEMICS, KINDS, STATES, Memories
from .store import Store


def date_arg(value):
    result = timestamp(value)
    if result is None:
        raise argparse.ArgumentTypeError("使用 Unix 秒或含时区的 ISO 日期，例如 2026-10-27T00:00:00+08:00")
    return result


def parser():
    p = argparse.ArgumentParser(prog="memory-bridge", description="本地归档优先的 ChatGPT Memory Bridge")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--data", default="data", help="本地资料库（默认 data）")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="创建资料库")
    gui = commands.add_parser("gui", help="打开本地图形界面")
    gui.add_argument("--export-root", default="outputs/迁移结果")
    imp = commands.add_parser("import", help="导入插件迁移 ZIP 或官方 ZIP，保留原始字节")
    imp.add_argument("zip", type=Path)
    imp.add_argument("--source", default="default", help="同账号保持一致；不同账号使用不同值")
    imp.add_argument("--retry", action="store_true", help="重新解析已有归档（含已完成的导入），用于升级后补建索引")
    imp.add_argument("--strict", action="store_true", help="启用旧版体积上限和 ZIP 结构限制；默认个人全量模式")
    commands.add_parser("imports", help="列出导入状态和增量报告")
    repair = commands.add_parser("repair-archive", help="用相同哈希的独立原件修复丢失/损坏的本地 ZIP")
    repair.add_argument("zip", type=Path)
    repair.add_argument("--yes", action="store_true", help="确认替换已损坏的备份")
    search = commands.add_parser("search", help="全文、时间、标题主题和对话 ID 检索")
    search.add_argument("query", nargs="?")
    search.add_argument("--id", dest="conversation_id")
    search.add_argument("--source")
    search.add_argument("--topic", help="对话标题关键词；MVP 不做语义主题分类")
    search.add_argument("--after", type=date_arg)
    search.add_argument("--before", type=date_arg)
    search.add_argument("--limit", type=int, default=50)
    conv = commands.add_parser("conversations", help="导出指定完整对话及其所有原始版本为 JSONL")
    conv.add_argument("--id", action="append", required=True)
    conv.add_argument("--source")
    conv.add_argument("--out", type=Path, required=True)
    exp = commands.add_parser("export", help="生成新账号上下文迁移目录")
    exp.add_argument("--out", type=Path, required=True)
    scope = exp.add_mutually_exclusive_group()
    scope.add_argument("--include-pending", dest="include_pending", action="store_true", help="全量记忆（默认，保留全部状态和历史）")
    scope.add_argument("--confirmed-only", dest="include_pending", action="store_false", help="仅缩减上下文记忆视图，原始数据和数据库仍全量携带")
    exp.set_defaults(include_pending=True)
    exp.add_argument("--no-originals", action="store_true", help="不将原始 ZIP 复制到迁移目录")
    exp.add_argument("--chunk-mb", type=int, default=20, help="平台 JSON 文件分片大小（默认20 MiB，超大单对话不截断）")
    restore = commands.add_parser("restore", help="从完整迁移包恢复本工具的资料库与原始 ZIP")
    restore.add_argument("package", type=Path)
    restore.add_argument("--out", type=Path, required=True)
    verify = commands.add_parser("verify", help="验证迁移包清单，或当前资料库与原始归档")
    verify.add_argument("package", type=Path, nargs="?")
    memory = commands.add_parser("memory", help="可追溯的离线记忆与人工审核")
    sub = memory.add_subparsers(dest="action", required=True)
    extract = sub.add_parser("extract")
    sensitivity = extract.add_mutually_exclusive_group()
    sensitivity.add_argument("--allow-sensitive", dest="allow_sensitive", action="store_true", help="保留包含敏感信息的候选（默认）")
    sensitivity.add_argument("--exclude-sensitive", dest="allow_sensitive", action="store_false", help="可选：过滤规则识别到的敏感候选")
    extract.set_defaults(allow_sensitive=True)
    listing = sub.add_parser("list")
    listing.add_argument("--status", choices=sorted(STATES))
    listing.add_argument("--confirmed-only", action="store_true")
    show = sub.add_parser("show")
    show.add_argument("id")
    add = sub.add_parser("add")
    add.add_argument("--content", required=True)
    add.add_argument("--kind", choices=sorted(KINDS), required=True)
    add.add_argument("--message", required=True, help="search 返回的消息 pk")
    add.add_argument("--message-hash", help="可选具体历史消息版本")
    add.add_argument("--epistemic", choices=sorted(EPISTEMICS), default="fact")
    add.add_argument("--conflict-key")
    edit = sub.add_parser("edit")
    edit.add_argument("id")
    edit.add_argument("--content", required=True)
    review = sub.add_parser("review")
    review.add_argument("id")
    review.add_argument("--status", choices=["pending", "active", "outdated", "conflict"], default="active")
    review.add_argument("--confirm", action="store_true", help="明确确认该记忆")
    outdated = sub.add_parser("outdated")
    outdated.add_argument("id")
    conflict = sub.add_parser("conflict")
    conflict.add_argument("first")
    conflict.add_argument("second")
    merge = sub.add_parser("merge")
    merge.add_argument("target")
    merge.add_argument("other")
    merge.add_argument("--yes", action="store_true", help="确认合并同内容记忆")
    delete = sub.add_parser("delete")
    delete.add_argument("id")
    delete.add_argument("--yes", action="store_true", help="确认软删除；不会销毁原始证据")
    history = sub.add_parser("history")
    history.add_argument("id")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("id")
    rollback.add_argument("--version", type=int, required=True)
    clean = commands.add_parser("clean-temp", help="清理应用创建且超过24小时的临时文件")
    clean.add_argument("--yes", action="store_true", help="确认删除")
    for name in ("encrypt", "decrypt"):
        crypto = commands.add_parser(name, help="交互式密码加密/解密本地备份文件（需要 crypto 依赖）")
        crypto.add_argument("input", type=Path)
        crypto.add_argument("--out", type=Path, required=True)
    return p


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def run(args):
    if args.command == "gui":
        from .gui import main as gui_main
        gui_main(data_root=args.data, export_root=args.export_root)
        return 0
    if args.command == "restore":
        from .fidelity import restore_library
        print_json(restore_library(args.package, args.out))
        return 0
    if args.command == "verify" and args.package is not None:
        result = verify_package(args.package)
        print_json(result)
        return 0 if result["ok"] else 2
    with Store(args.data) as store:
        memories = Memories(store)
        if args.command == "init":
            result = {"initialized": True, "schema_version": 1, "offline": True}
        elif args.command == "import":
            result = store.import_zip(args.zip, args.source, limits=Limits() if args.strict else None, retry=args.retry)
            print_json(result)
            return 0 if result["status"] == "complete" else 2
        elif args.command == "imports":
            result = store.imports()
        elif args.command == "repair-archive":
            result = store.repair_archive(args.zip, confirmed=args.yes)
        elif args.command == "search":
            result = store.search(args.query, conversation_id=args.conversation_id, source=args.source,
                                  topic=args.topic, after=args.after, before=args.before, limit=args.limit)
        elif args.command == "conversations":
            result = export_conversations(store, args.out, args.id, args.source)
        elif args.command == "export":
            manifest = export_package(store, args.out, include_pending=args.include_pending,
                                      include_originals=not args.no_originals, chunk_mb=args.chunk_mb)
            result = {"exported": True, "files": len(manifest["files"]), "originals": manifest["includes_originals"]}
        elif args.command == "verify":
            integrity = [x[0] for x in store.db.execute("PRAGMA integrity_check")]
            foreign_keys = [list(x) for x in store.db.execute("PRAGMA foreign_key_check")]
            archives = store.verify_archives()
            versions = store.verify_versions()
            result = {"ok": integrity == ["ok"] and not foreign_keys and all(x["ok"] for x in archives) and versions["ok"],
                      "database": integrity, "foreign_key_errors": foreign_keys, "archives": archives, "versions": versions}
            print_json(result)
            return 0 if result["ok"] else 2
        elif args.command == "clean-temp":
            if not args.yes:
                raise BridgeError("delete_requires_confirmation_use_--yes")
            count = 0
            temp_root = store.root / "tmp"
            if temp_root.is_dir() and not temp_root.is_symlink():
                for path in temp_root.glob("bridge-*"):
                    if path.is_file() and not path.is_symlink() and path.resolve().parent == temp_root.resolve() and path.stat().st_mtime < time.time() - 86400:
                        path.unlink()
                        count += 1
            result = {"deleted_temporary_files": count}
        elif args.command in ("encrypt", "decrypt"):
            password = getpass.getpass("备份密码（至少12字符，不会保存）: ")
            if args.command == "encrypt":
                if password != getpass.getpass("再次输入密码: "):
                    raise BridgeError("passwords_do_not_match")
                result = encrypt_file(args.input, args.out, password)
            else:
                result = decrypt_file(args.input, args.out, password, store.root / "tmp")
        elif args.command == "memory":
            if args.action == "extract":
                result = memories.extract(allow_sensitive=args.allow_sensitive)
            elif args.action == "list":
                result = memories.list(args.status, args.confirmed_only)
            elif args.action == "show":
                result = memories.get(args.id)
            elif args.action == "add":
                result = memories.add(args.content, args.kind, args.message, epistemic=args.epistemic,
                                      conflict_key=args.conflict_key, message_hash=args.message_hash)
            elif args.action == "edit":
                result = memories.update(args.id, content=args.content)
            elif args.action == "review":
                result = memories.update(args.id, status=args.status, confirmed=args.confirm)
            elif args.action == "outdated":
                result = memories.update(args.id, status="outdated", confirmed=False)
            elif args.action == "conflict":
                memories.mark_conflict(args.first, args.second)
                result = {"conflict_flagged": True}
            elif args.action == "merge":
                result = memories.merge(args.target, args.other, confirmed=args.yes)
            elif args.action == "delete":
                memories.delete(args.id, confirmed=args.yes)
                result = {"soft_deleted": args.id, "original_evidence_retained": True}
            elif args.action == "history":
                result = memories.history(args.id)
            elif args.action == "rollback":
                result = memories.rollback(args.id, args.version)
        print_json(result)
        return 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args)
    except BridgeError as exc:
        print_json({"error": str(exc)})
        return 2
    except (OSError, sqlite3.Error, ValueError, EOFError) as exc:
        # No tracebacks, message text, filenames or exception payloads in default errors.
        print_json({"error": "operation_failed", "error_type": type(exc).__name__})
        return 2
    except KeyboardInterrupt:
        print_json({"error": "interrupted_partial_output_may_remain"})
        return 130


if __name__ == "__main__":
    sys.exit(main())
