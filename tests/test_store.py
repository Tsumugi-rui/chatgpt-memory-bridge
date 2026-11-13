import json

import pytest

from memory_bridge.store import Store
from memory_bridge.archive import Limits
from conftest import conversation


@pytest.mark.parametrize("wrap", [lambda c: [c], lambda c: {"conversations": [c]},
    lambda c: {"items": [c]}, lambda c: {"data": {"conversations": [c]}},
    lambda c: c, lambda c: {"c1": c}, lambda c: {"conversations": {"c1": c}}])
def test_json_shapes(tmp_path, make_zip, wrap):
    raw = conversation()
    with Store(tmp_path / "library") as store:
        result = store.import_zip(make_zip(wrap(raw)))
        assert result["status"] == "complete", result
        assert result["stats"]["new_messages"] == 4
        records = list(store.conversation_records())
        assert records[0]["versions"][0]["raw"] == raw
        assert next(x for x in records[0]["messages"] if x["node_id"] == "n1")["children"] == ["branch-a", "branch-b"]
        assert store.search("中文")[0]["message_id"] == "m1"
        assert store.search("こんにちは")[0]["message_id"] == "m3"
        assert store.search(topic="数学", after=1700000000, before=1700000002)


def test_array_messages_and_missing_fields(tmp_path, make_zip):
    raw = {"messages": [{"role": "user", "content": "日本語", "created_at": "2023-11-14T00:00:00Z"},
                        {"id": "other", "role": "assistant", "content": {"text": "数学"}}, None]}
    with Store(tmp_path / "lib") as store:
        result = store.import_zip(make_zip([raw]))
        assert result["status"] == "complete"
        assert result["warnings"]
        assert store.search("日本語")


def test_idempotent_incremental_and_missing_history(tmp_path, make_zip):
    first = make_zip([conversation(), conversation(cid="c2")])
    with Store(tmp_path / "lib") as store:
        assert store.import_zip(first)["stats"]["new_conversations"] == 2
        assert store.import_zip(first)["already_imported"]
        edited = conversation("我偏好：简明回答")
        del edited["mapping"]["branch-a"]
        result = store.import_zip(make_zip([edited]))
        assert result["stats"]["changed_messages"] == 1
        assert result["stats"]["not_observed_conversations"] == 1
        assert result["stats"]["not_observed_messages"] == 5
        assert len(list(store.conversation_records())) == 2
        assert store.db.execute("SELECT count(*) FROM message_versions").fetchone()[0] == 9
        assert len(list(store.conversation_records())[0]["messages"]) == 4
        assert store.verify_archives()[0]["ok"]
        # Same IDs from a different account must not collide.
        assert store.import_zip(first, source="second-account")["stats"]["new_conversations"] == 2


@pytest.mark.parametrize("padding", [0, 70000])
def test_corrupt_json_partial_recovery(tmp_path, make_zip, padding):
    # Enough data for the first conversation to be yielded before the parser finds truncation.
    raw = json.dumps(conversation(), ensure_ascii=False)
    damaged = "[" + raw + "," + " " * padding + '{"id": "broken"'
    with Store(tmp_path / "lib") as store:
        result = store.import_zip(make_zip(files={"conversations.json": damaged}))
        assert result["status"] == "partial"
        assert result["stats"]["parsed_records"] == 1
        assert list(store.conversation_records())
        assert store.verify_archives()[0]["ok"]


def test_unknown_html_and_rejected_zip(tmp_path, make_zip):
    with Store(tmp_path / "lib") as store:
        result = store.import_zip(make_zip(files={"chat.html": "<p>hello</p>", "new.json": '{"unknown": true}'}))
        assert result["status"] == "partial"
        assert result["stats"]["archived_members"] == 2
        rejected = store.import_zip(make_zip(files={"../evil": "bad"}), limits=Limits())
        assert rejected["status"] == "rejected"
        assert store.verify_archives()[-1]["ok"]
