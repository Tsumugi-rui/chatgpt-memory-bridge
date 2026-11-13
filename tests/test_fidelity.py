import json
import sqlite3
import zipfile

import pytest

from conftest import conversation
from memory_bridge.archive import Limits
from memory_bridge.common import BridgeError, file_hash
from memory_bridge.export import export_package, verify_package
from memory_bridge.fidelity import restore_library
from memory_bridge.memories import Memories, RuleExtractor
from memory_bridge.portable import JsonChunks, openwebui_projection
from memory_bridge.adapters import ChatGPTAdapter
from memory_bridge.store import Store


def jsonlines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_personal_defaults_keep_sensitive_candidates_and_all_history(tmp_path, make_zip):
    raw = conversation("我偏好：向我的邮箱me@example.com发送资料\n我的学习进度：诊断知识入门")
    with Store(tmp_path / "library") as store:
        store.import_zip(make_zip([raw]))
        memories = Memories(store)
        assert memories.extract()["candidates"] == 2
        first = next(item for item in memories.list() if item["kind"] == "preferences")
        memories.update(first["id"], content="人工修正的候选")
        memories.delete(first["id"], confirmed=True)
        package = tmp_path / "package"
        export_package(store, package)
        items = json.loads((package / "memory.json").read_text(encoding="utf-8"))["memories"]
        assert {x["status"] for x in items} == {"pending", "deleted"}
        assert "pending" in (package / "learning.md").read_text(encoding="utf-8")
        assert len(jsonlines(package / "memory_history.jsonl")) == 4
        assert verify_package(package)["ok"]


def test_all_files_include_unusual_names_duplicates_and_unknown_metadata(tmp_path):
    archive = tmp_path / "all.zip"
    contents = [("conversations.json", json.dumps([conversation()])), ("../odd.json", '{"unknown": true}'),
                ("CON.txt", "reserved-name"), ("same.bin", "first"), ("same.bin", "second"),
                ("a\\b.dat", "backslash-name"), ("user.json", '{"name":"合成用户","email":"demo@example.com"}')]
    with pytest.warns(UserWarning, match="Duplicate name"), zipfile.ZipFile(archive, "w") as zf:
        for name, body in contents:
            info = zipfile.ZipInfo(name)
            info.filename = name  # zipfile otherwise rewrites backslashes on Windows when writing.
            info.orig_filename = name
            zf.writestr(info, body)
        zf.writestr("empty/", "")
    with Store(tmp_path / "library") as store:
        assert store.import_zip(archive)["status"] == "complete"
        package = tmp_path / "package"
        export_package(store, package)
        entries = jsonlines(package / "archive_members.jsonl")
        assert len(entries) == 8
        assert entries[-1]["status"] == "directory"
        for entry, (name, body) in zip(entries, contents):
            assert entry["original_path"] == name
            assert (package / entry["content_path"]).read_bytes() == body.encode("utf-8")
        assert len({x["content_path"] for x in entries[:-1]}) == 7
        assert not (tmp_path / "odd.json").exists()
        assert json.loads((package / "account_data.json").read_text(encoding="utf-8"))["files"]
        assert verify_package(package)["ok"]


def test_roundtrip_entire_library_and_incremental_continuation(tmp_path, make_zip):
    first = make_zip([conversation("我的回复语言偏好：中文")])
    second = make_zip([conversation("我的回复语言偏好：日文")])
    with Store(tmp_path / "library") as store:
        for source in (first, second):
            store.import_zip(source)
            Memories(store).extract()
        memory = Memories(store).list()[0]
        Memories(store).update(memory["id"], status="outdated")
        package = tmp_path / "package"
        export_package(store, package)
        counts = {t: store.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("imports", "conversations", "messages", "memories", "memory_versions", "observations", "extraction_candidates")}
        tables = [x[0] for x in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        original_rows = {t: sorted([tuple(x) for x in store.db.execute('SELECT * FROM "' + t.replace('"', '""') + '"')], key=repr) for t in tables}
    restored = tmp_path / "restored"
    result = restore_library(package, restored)
    assert result["restored"] and not result["chatgpt_account_state_restored"]
    with Store(restored) as store:
        for table, rows in original_rows.items():
            assert sorted([tuple(x) for x in store.db.execute('SELECT * FROM "' + table.replace('"', '""') + '"')], key=repr) == rows
        for table, count in counts.items():
            assert store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == count
        assert store.verify_versions()["ok"] and all(x["ok"] for x in store.verify_archives())
        assert store.import_zip(second)["already_imported"]
        assert Memories(store).extract()["candidates"] == 0
        assert store.import_zip(make_zip([conversation("我偏好：简明")]))["stats"]["changed_messages"] == 1
    with pytest.raises(FileExistsError):
        restore_library(package, restored)


def test_reference_matching_retains_ambiguous_and_missing(tmp_path, make_zip):
    raw = conversation()
    raw["mapping"]["n1"]["message"]["content"]["parts"].extend([
        {"file_id": "file-demo"}, {"asset_pointer": "sandbox:/mnt/data/exact.pdf"},
        {"asset_pointer": "sandbox:/mnt/data/duplicate.png"}])
    archive = make_zip([raw], files={"file-demo-image.png": b"one", "folder/exact.pdf": b"two",
        "a/duplicate.png": b"different1", "b/duplicate.png": b"different2"})
    with Store(tmp_path / "lib") as store:
        store.import_zip(archive)
        package = tmp_path / "package"
        export_package(store, package)
        refs = jsonlines(package / "attachment_references.jsonl")
        by_ref = {x["reference"]: x for x in refs}
        assert by_ref["file-demo"]["status"] == "matched"
        assert by_ref["sandbox:/mnt/data/exact.pdf"]["status"] == "matched"
        assert by_ref["sandbox:/mnt/data/duplicate.png"]["status"] == "ambiguous"
        assert by_ref["file-service://missing"]["status"] == "missing_local"


def test_openwebui_preserves_branches_and_reports_unsupported_roles(tmp_path, make_zip):
    raw = conversation()
    raw["mapping"]["tool"] = {"parent": "n1", "children": [], "message": {
        "id": "tool-message", "author": {"role": "tool"}, "content": {"parts": ["tool output"]}}}
    with Store(tmp_path / "library") as store:
        store.import_zip(make_zip([raw]))
        package = tmp_path / "package"
        export_package(store, package)
        platform = json.loads(next((package / "platforms/openwebui").glob("*.json")).read_text(encoding="utf-8"))[0]
        history = platform["chat"]["history"]
        assert history["currentId"] == "branch-b"
        assert set(history["messages"]["n1"]["childrenIds"]) == {"branch-a", "branch-b"}
        assert history["messages"]["branch-a"]["parentId"] == "n1"
        report = json.loads((package / "portability_report.json").read_text(encoding="utf-8"))
        assert any(x["node_id"] == "tool" for x in report["openwebui_omissions"][0]["nodes"])
        text = next((package / "readable").glob("*.md")).read_text(encoding="utf-8")
        assert "tool output" in text and "branch-a" in text and "branch-b" in text
        upload = json.loads(next((package / "platforms/chatgpt").glob("*.json")).read_text(encoding="utf-8"))
        assert upload == [raw]


def test_json_chunks_do_not_truncate_oversize_conversations(tmp_path):
    chunks = JsonChunks(tmp_path / "chunks", 100)
    try:
        chunks.write({"id": "one", "text": "a" * 30}, "one")
        chunks.write({"id": "two", "text": "b" * 30}, "two")
        chunks.write({"id": "large", "text": "中" * 100}, "large")
    finally:
        chunks.close()
    parts = sorted((tmp_path / "chunks").glob("*.json"))
    assert len(parts) == 3 and chunks.oversized == ["large"]
    assert json.loads(parts[-1].read_text(encoding="utf-8"))[0]["text"] == "中" * 100


def test_restore_rejects_tampering_or_missing_originals(tmp_path, make_zip):
    with Store(tmp_path / "lib") as store:
        store.import_zip(make_zip([conversation()]))
        package = tmp_path / "package"
        export_package(store, package, include_originals=False)
        with pytest.raises(BridgeError, match="requires_library"):
            restore_library(package, tmp_path / "restore1")
        (package / "library/bridge.sqlite3").write_bytes(b"damaged")
        with pytest.raises(BridgeError, match="integrity"):
            restore_library(package, tmp_path / "restore2")
        assert not (tmp_path / "restore2").exists()


def test_personal_mode_has_no_fixed_size_or_sensitive_exclusions(tmp_path, make_zip):
    assert Limits.personal().max_member is None
    assert RuleExtractor().candidates("我的学习进度：诊断知识入门")
    assert not RuleExtractor().candidates("我的学习进度：诊断知识入门", allow_sensitive=False)
    raw = {"id": "same-id", "messages": [
        {"id": "m", "role": "user", "content": "first"},
        {"id": "m", "role": "user", "content": "second"}]}
    view = ChatGPTAdapter().normalize(raw)
    assert len(view["messages"]) == 2
    assert len({x["node_id"] for x in view["messages"]}) == 2


def test_history_is_extracted_even_when_both_imports_precede_extraction(tmp_path, make_zip):
    first = conversation("我的回复语言偏好：中文")
    second = conversation("我的回复语言偏好：日文", mid="changed-message-id")
    with Store(tmp_path / "lib") as store:
        store.import_zip(make_zip([first]))
        store.import_zip(make_zip([second]))
        memories = Memories(store)
        assert memories.extract()["candidates"] == 2
        previous = next(x for x in memories.list() if "中文" in x["content"])
        assert previous["sources"][0]["message_id"] == "m1"
        assert previous["status"] == "conflict"
        assert memories.extract()["candidates"] == 0


def test_numbered_conversation_files_are_all_indexed(tmp_path, make_zip):
    archive = make_zip(files={"conversations-0000.json": json.dumps([conversation(cid="one")]),
                              "conversations-0001.json": json.dumps([conversation(cid="two")]),
                              "account.json": '{"custom_instructions":"keep all data"}'})
    with Store(tmp_path / "library") as store:
        result = store.import_zip(archive)
        assert result["stats"]["new_conversations"] == 2
        assert result["stats"]["indexed_json_files"] == 2


def test_cli_restore_and_exclude_sensitive_are_optional(tmp_path, make_zip, capsys):
    from memory_bridge.cli import main
    base = ["--data", str(tmp_path / "lib")]
    archive = make_zip([conversation("我的学习进度：诊断知识入门")])
    assert main(base + ["import", str(archive)]) == 0
    assert main(base + ["memory", "extract", "--exclude-sensitive"]) == 0
    assert main(base + ["memory", "extract"]) == 0
    package = tmp_path / "package"
    assert main(base + ["export", "--out", str(package)]) == 0
    assert main(["restore", str(package), "--out", str(tmp_path / "restored")]) == 0
    with Store(tmp_path / "restored") as store:
        assert len(Memories(store).list()) == 1
