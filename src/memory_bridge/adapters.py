"""Tolerant export adapter. Unknown fields always remain in the raw versions."""
from __future__ import annotations

import re
from typing import Protocol, BinaryIO

import ijson

from .common import digest, timestamp


class ConversationAdapter(Protocol):
    platform: str

    def normalize(self, raw: dict, fallback_id: str | None = None) -> dict | None: ...


def locate_shape(open_stream) -> tuple[str, str]:
    """Probe events without loading a full file; reopen for actual streaming."""
    with open_stream() as stream:
        for index, (prefix, event, value) in enumerate(ijson.parse(stream, use_float=True)):
            if index == 0 and event == "start_array":
                return "array", "item"
            if prefix in ("mapping", "messages") and event in ("start_map", "start_array"):
                return "single", ""
            if prefix in ("conversations", "items", "data.conversations", "data.items", "data"):
                if event == "start_array":
                    return "array", prefix + ".item"
                if event == "start_map" and prefix != "data":
                    return "keyed", prefix
            # Format discovery is bounded; unrecognized objects fall back to keyed objects.
            if index > 2048:
                break
    return "keyed", ""


def iter_conversations(open_stream):
    shape, prefix = locate_shape(open_stream)
    with open_stream() as stream:
        if shape == "keyed":
            for key, item in ijson.kvitems(stream, prefix, use_float=True):
                if isinstance(item, dict):
                    yield item, key
        else:
            for item in ijson.items(stream, prefix, use_float=True):
                if isinstance(item, dict):
                    yield item, None


def text_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(filter(None, (text_content(x) for x in content)))
    if isinstance(content, dict):
        # Preserve multiple textual channels when a multimodal object includes them.
        return "\n".join(filter(None, (text_content(content[key]) for key in
                         ("parts", "text", "content", "transcript") if key in content)))
    return ""


def references(value):
    found = set()

    def walk(item, key=""):
        if isinstance(item, dict):
            for k, v in item.items():
                walk(v, k)
        elif isinstance(item, list):
            for v in item:
                walk(v, key)
        elif isinstance(item, str):
            if key in ("asset_pointer", "file_id", "attachment_id", "download_url"):
                found.add(item)
            found.update(re.findall(r"(?:file-service://|sediment://|sandbox:/)[^\s\"<>]+", item))

    walk(value)
    return sorted(found)


class ChatGPTAdapter:
    platform = "chatgpt"

    def normalize(self, raw: dict, fallback_id: str | None = None) -> dict | None:
        mapping = raw.get("mapping")
        messages = raw.get("messages")
        if not isinstance(mapping, dict) and not isinstance(messages, list):
            return None
        issues = []
        cid = raw.get("id") or raw.get("conversation_id") or fallback_id
        if not cid:
            cid = "derived:" + digest(raw)
            issues.append("derived_conversation_id_unstable_on_edit")
        result = {
            "id": str(cid), "title": str(raw.get("title") or "Untitled"),
            "created": timestamp(raw.get("create_time", raw.get("created_at"))),
            "updated": timestamp(raw.get("update_time", raw.get("updated_at"))),
            "raw": raw, "messages": [], "issues": issues,
            "current_node": raw.get("current_node"),
        }
        entries = mapping.items() if isinstance(mapping, dict) else enumerate(messages)
        browser = raw.get('_memory_bridge_browser')
        if isinstance(mapping, dict) and isinstance(browser, dict) and isinstance(browser.get('node_order'), list):
            ordered = list(dict.fromkeys(k for k in browser['node_order'] if isinstance(k, str) and k in mapping))
            ordered_set = set(ordered)
            entries = [(k, mapping[k]) for k in ordered] + [(k, v) for k, v in mapping.items() if k not in ordered_set]
        seen = set()
        for key, node in entries:
            if not isinstance(node, dict):
                issues.append("non_object_message_node")
                continue
            msg = node.get("message") if isinstance(mapping, dict) else node
            if not isinstance(msg, dict):
                # Placeholder nodes preserve branch edges even when there is no message.
                msg = {}
            mid = msg.get("id")
            nid = str(key) if isinstance(mapping, dict) else str(mid or "derived:" + digest(node))
            if nid in seen:
                issues.append("duplicate_message_id_disambiguated")
                while nid in seen:
                    nid = f"{nid}:occurrence:{key}"
            seen.add(nid)
            if not mid and msg:
                issues.append("missing_message_id")
            author = msg.get("author")
            role = author.get("role") if isinstance(author, dict) else msg.get("role")
            parent = node.get("parent", msg.get("parent_id"))
            children = node.get("children", [])
            if not isinstance(children, list):
                children = []
                issues.append("invalid_children")
            result["messages"].append({
                "node_id": nid, "message_id": str(mid) if mid is not None else None,
                "role": str(role or "unknown"), "text": text_content(msg.get("content")),
                "created": timestamp(msg.get("create_time", msg.get("created_at"))),
                "parent": str(parent) if parent is not None else None,
                "children": [str(x) for x in children], "raw": node,
                "references": references(node),
            })
        node_ids = {x["node_id"] for x in result["messages"]}
        for message in result["messages"]:
            if message["parent"] is not None and message["parent"] not in node_ids:
                issues.append("missing_parent_node")
            if any(child not in node_ids for child in message["children"]):
                issues.append("missing_child_node")
        return result

