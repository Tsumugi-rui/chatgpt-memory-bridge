import io
import json
import os
import struct
import time
import zipfile

import pytest

from conftest import conversation
from memory_bridge.archive import Limits, preflight_zip
from memory_bridge.cli import main
from memory_bridge.common import BridgeError, file_hash
from memory_bridge.export import export_package, verify_package
from memory_bridge.memories import Memories
from memory_bridge.store import Store


def test_preflight_limits_before_allocation(tmp_path, make_zip):
    source = make_zip([conversation()], files={"one": b"1"})
    with pytest.raises(BridgeError, match="member_count"):
        preflight_zip(source, Limits(max_members=1))
    with pytest.raises(BridgeError, match="directory_size"):
        preflight_zip(source, Limits(max_directory=1))
    with Store(tmp_path / "lib") as store:
        result = store.import_zip(source, limits=Limits(max_members=1))
        assert result["status"] == "rejected"
        assert not store.search()
        assert store.verify_archives()[0]["ok"]


def test_zip64_preflight_and_import(tmp_path, make_zip):
    source = make_zip([conversation()])
    blob = source.read_bytes()
    old = struct.unpack("<4s4H2IH", blob[-22:])
    zip64_offset = len(blob) - 22
    end64 = struct.pack("<4sQ2H2I4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, old[4], old[4], old[5], old[6])
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, zip64_offset, 1)
    end = struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 65535, 65535, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    source.write_bytes(blob[:-22] + end64 + locator + end)
    preflight_zip(source, Limits())
    with Store(tmp_path / "lib") as store:
        assert store.import_zip(source)["status"] == "complete"


def test_crc_failure_and_corrupt_container_recovery(tmp_path, make_zip):
    # Flip stored attachment bytes without changing the ZIP central CRC.
    source = tmp_path / "crc.zip"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("conversations.json", json.dumps([conversation()]))
        zf.writestr("bad.bin", b"unique-attachment-payload")
    source.write_bytes(source.read_bytes().replace(b"unique-attachment-payload", b"Xnique-attachment-payload"))
    with Store(tmp_path / "lib") as store:
        result = store.import_zip(source)
        assert result["status"] == "partial"
        assert any(x["code"] == "unreadable_member" for x in result["warnings"])
        assert result["stats"]["parsed_records"] == 1
        bad = tmp_path / "broken.zip"
        bad.write_bytes(b"not a zip")
        assert store.import_zip(bad)["status"] == "rejected"
        assert all(x["ok"] for x in store.verify_archives())


def test_repair_checksum_and_version_tampering(tmp_path, make_zip):
    source = make_zip([conversation()])
    with Store(tmp_path / "lib") as store:
        store.import_zip(source)
        archive = next((store.root / "archives").glob("*.zip"))
        archive.write_bytes(b"damage")
        assert not store.verify_archives()[0]["ok"]
        with pytest.raises(BridgeError, match="confirmation"):
            store.repair_archive(source)
        with pytest.raises(BridgeError, match="not_a_known"):
            store.repair_archive(make_zip([conversation(cid="different")]), confirmed=True)
        store.repair_archive(source, confirmed=True)
        assert file_hash(archive) == file_hash(source)
        assert store.verify_archives()[0]["ok"]
        assert store.verify_versions()["ok"]
        with store.db:
            store.db.execute("UPDATE message_versions SET raw_json='{}'")
        assert not store.verify_versions()["ok"]
        with pytest.raises(BridgeError, match="integrity"):
            export_package(store, tmp_path / "package")


def test_missing_backup_and_unlisted_manifest(tmp_path, make_zip):
    with Store(tmp_path / "lib") as store:
        store.import_zip(make_zip([conversation()]))
        package = tmp_path / "package"
        export_package(store, package)
        backup = next((package / "originals").glob("*.zip"))
        backup.unlink()
        assert not verify_package(package)["ok"]
        nested = package / "unexpected"
        nested.mkdir()
        (nested / "manifest.json").write_text("{}")
        assert any(x["code"] == "unlisted_file" for x in verify_package(package)["errors"])


def test_temp_cleanup_scoped_and_confirmed(tmp_path, capsys):
    root = tmp_path / "lib"
    temp = root / "tmp"
    temp.mkdir(parents=True)
    old = temp / "bridge-old"
    fresh = temp / "bridge-fresh"
    unrelated = temp / "user-file"
    for path in (old, fresh, unrelated):
        path.write_text("synthetic")
    os.utime(old, (time.time() - 90000,) * 2)
    os.utime(unrelated, (time.time() - 90000,) * 2)
    assert main(["--data", str(root), "clean-temp"]) == 2
    assert old.exists()
    assert main(["--data", str(root), "clean-temp", "--yes"]) == 0
    assert not old.exists() and fresh.exists() and unrelated.exists()


def test_source_version_survives_edits_and_conflict_versions(tmp_path, make_zip):
    with Store(tmp_path / "lib") as store:
        store.import_zip(make_zip([conversation("我的回复语言偏好：中文")]))
        memories = Memories(store)
        memories.extract()
        first = memories.list()[0]
        memories.update(first["id"], status="active", confirmed=True)
        store.import_zip(make_zip([conversation("我的回复语言偏好：日文")]))
        memories.extract()
        previous = memories.get(first["id"])
        assert previous["status"] == "conflict" and not previous["confirmed"]
        assert previous["sources"][0]["message_hash"] == first["sources"][0]["message_hash"]
        assert memories.history(first["id"])[-1]["reason"] == "conflict_flagged"
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_retry_earlier_interrupted_import_does_not_revert_newer_messages(tmp_path, make_zip):
    first = make_zip([conversation("我偏好：中文回答")])
    with Store(tmp_path / "lib") as store:
        imported = store.import_zip(first)
        with store.db:
            store.db.execute("UPDATE imports SET status='processing' WHERE id=?", (imported["id"],))
        store.import_zip(make_zip([conversation("我偏好：日文回答")]))
        retried = store.import_zip(first, retry=True)
        assert retried["stats"]["historical_message_observations"] == 4
        assert store.search("日文回答") and not store.search("中文回答")
        assert store.verify_versions()["ok"]
