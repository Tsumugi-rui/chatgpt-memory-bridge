import json

import pytest

from conftest import conversation
from memory_bridge.common import BridgeError, file_hash
from memory_bridge.export import export_conversations, export_package, verify_package
from memory_bridge.memories import Memories
from memory_bridge.store import Store


def test_package_complete_provenance_and_tamper(tmp_path, make_zip):
    original = make_zip([conversation()], files={"chat.html": "hello", "assets/image.png": b"synthetic"})
    with Store(tmp_path / "lib") as store:
        store.import_zip(original)
        mem = Memories(store)
        mem.extract()
        pending = tmp_path / "pending"
        export_package(store, pending, include_pending=False)
        assert not json.loads((pending / "memory.json").read_text(encoding="utf-8"))["memories"]
        item = mem.list()[0]
        mem.update(item["id"], status="active", confirmed=True)
        package = tmp_path / "package"
        manifest = export_package(store, package)
        assert verify_package(package)["ok"]
        backup = next((package / "originals").glob("*.zip"))
        assert file_hash(backup) == file_hash(original)
        exported = json.loads((package / "memory.json").read_text(encoding="utf-8"))["memories"][0]
        assert exported["sources"][0]["archive_sha256"] == file_hash(original)
        assert exported["sources"][0]["message_hash"]
        assert json.loads((package / "conversations.jsonl").read_text(encoding="utf-8"))["versions"][0]["raw"] == conversation()
        assert "未执行" in (package / "migration_report.md").read_text(encoding="utf-8")
        assert len(manifest["files"]) >= 13
        (package / "profile.md").write_text("tampered", encoding="utf-8")
        check = verify_package(package)
        assert not check["ok"]
        assert check["errors"][0]["path"] == "profile.md"


def test_export_refuses_overwrite_and_manifest_escape(tmp_path, make_zip):
    with Store(tmp_path / "lib") as store:
        store.import_zip(make_zip([conversation()]))
        package = tmp_path / "package"
        export_package(store, package, include_pending=True, include_originals=False)
        assert verify_package(package)["ok"]
        with pytest.raises(FileExistsError):
            export_package(store, package)
        manifest_path = package / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["path"] = "../secret"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(BridgeError, match="unsafe"):
            verify_package(package)


def test_selected_conversation_and_corrupt_archive(tmp_path, make_zip):
    original = make_zip([conversation(), conversation(cid="c2")])
    with Store(tmp_path / "lib") as store:
        store.import_zip(original)
        dest = tmp_path / "one.jsonl"
        assert export_conversations(store, dest, ["c1"])["exported_conversations"] == 1
        with pytest.raises(BridgeError, match="not_found"):
            export_conversations(store, tmp_path / "none.jsonl", ["missing"])
        backup = next((store.root / "archives").glob("*.zip"))
        backup.write_bytes(b"tampered")
        with pytest.raises(BridgeError, match="integrity"):
            export_package(store, tmp_path / "badpackage")
        assert not (tmp_path / "badpackage/manifest.json").exists()
