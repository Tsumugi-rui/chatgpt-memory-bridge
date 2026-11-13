import json
import zipfile

import pytest


def conversation(text="我偏好：用中文解释数学", cid="c1", role="user", mid="m1"):
    return {"id": cid, "title": "数学・日本語", "create_time": 1700000000, "update_time": 1700000100,
            "current_node": "branch-b", "unknown_field": {"future": True}, "mapping": {
                "root": {"id": "root", "message": None, "parent": None, "children": ["n1"]},
                "n1": {"id": "n1", "parent": "root", "children": ["branch-a", "branch-b"], "message": {
                    "id": mid, "author": {"role": role}, "create_time": 1700000001,
                    "content": {"content_type": "text", "parts": [text]}, "future": [1, 2]}},
                "branch-a": {"parent": "n1", "children": [], "message": {"id": "m2", "author": {"role": "assistant"},
                    "content": {"parts": ["建议你每天学习三小时。"]}}},
                "branch-b": {"parent": "n1", "children": [], "message": {"id": "m3", "author": {"role": "assistant"},
                    "content": {"parts": [{"content_type": "image_asset_pointer", "asset_pointer": "file-service://missing"}, "こんにちは"]}}},
            }}


@pytest.fixture
def make_zip(tmp_path):
    count = 0

    def make(data=None, *, files=None):
        nonlocal count
        count += 1
        path = tmp_path / f"export{count}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            if data is not None:
                zf.writestr("nested/conversations.json", json.dumps(data, ensure_ascii=False))
            for name, value in (files or {}).items():
                zf.writestr(name, value)
        return path

    return make

