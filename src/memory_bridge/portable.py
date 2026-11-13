"""Readable complete trees and documented platform projections (never the authority)."""
from __future__ import annotations

import json
from pathlib import Path

from .adapters import ChatGPTAdapter
from .common import BridgeError, dumps


class JsonChunks:
    def __init__(self, root, maximum):
        self.root, self.maximum = root, maximum
        root.mkdir(parents=True)
        self.stream = None
        self.count = self.size = self.parts = 0
        self.oversized = []

    def write(self, value, conversation_id):
        data = dumps(value).encode("utf-8")
        if self.stream and self.count and self.size + len(data) + 5 > self.maximum:
            self.stream.write(b"\n]\n")
            self.stream.close()
            self.stream = None
        if self.stream is None:
            self.parts += 1
            self.stream = (self.root / f"conversations-{self.parts:04d}.json").open("xb")
            self.stream.write(b"[\n")
            self.count, self.size = 0, 2
        if self.count:
            self.stream.write(b",\n")
            self.size += 2
        self.stream.write(data)
        self.count += 1
        self.size += len(data)
        if len(data) + 5 > self.maximum:
            self.oversized.append(conversation_id)

    def close(self):
        if self.stream:
            self.stream.write(b"\n]\n")
            self.stream.close()
            self.stream = None


def openwebui_projection(conv):
    nodes = {m["node_id"]: m for m in conv["messages"]}
    included = {key for key, msg in nodes.items() if msg["role"] in ("user", "assistant")}

    def parent_for(key):
        seen = {key}
        parent = nodes[key]["parent"] if key in nodes else None
        while parent is not None:
            if parent in seen or parent not in nodes:
                return None
            if parent in included:
                return parent
            seen.add(parent)
            parent = nodes[parent]["parent"]
        return None

    projected, models, omitted = {}, set(), []
    for key, msg in nodes.items():
        if key not in included:
            omitted.append({"node_id": key, "role": msg["role"], "reason": "target_supports_user_and_assistant_roles"})
            continue
        raw = msg["raw"].get("message", msg["raw"])
        metadata = raw.get("metadata") if isinstance(raw, dict) else None
        model = metadata.get("model_slug") if isinstance(metadata, dict) else None
        item = {"id": key, "parentId": parent_for(key), "childrenIds": [], "role": msg["role"], "content": msg["text"]}
        if msg["created"] is not None:
            item["timestamp"] = int(msg["created"])
        if msg["role"] == "assistant":
            item["done"] = True
            if isinstance(model, str):
                item["model"] = model
                models.add(model)
        projected[key] = item
    for key, item in projected.items():
        if item["parentId"] in projected:
            projected[item["parentId"]]["childrenIds"].append(key)
    current = str(conv["current_node"]) if conv["current_node"] is not None else None
    if current not in included:
        current = parent_for(current) if current in nodes else None
    if current is None and projected:
        current = next(reversed(projected))
        omitted.append({"reason": "current_node_missing_in_target_projection", "chosen_target_current_id": current})
    chat = {"title": conv["title"], "history": {"currentId": current, "messages": projected}}
    if models:
        chat["models"] = sorted(models)
    result = {"chat": chat, "meta": {"tags": ["memory_bridge"]}}
    for source, target in (("created", "created_at"), ("updated", "updated_at")):
        if conv[source] is not None:
            result[target] = int(conv[source])
    return result, omitted


def export_portable(store, destination, chunk_bytes):
    if chunk_bytes < 1:
        raise BridgeError("chunk_size_must_be_positive")
    chatgpt = JsonChunks(destination / "platforms" / "chatgpt", chunk_bytes)
    webui = JsonChunks(destination / "platforms" / "openwebui", chunk_bytes)
    readable = destination / "readable"
    readable.mkdir()
    report = {"conversations": 0, "openwebui_omissions": [], "attachments_need_manual_upload": True,
              "openwebui_live_import_tested": False,
              "preserved_in_universal_only": ["system/tool roles and placeholder nodes", "structured multimodal payloads",
                                              "original metadata", "earlier conversation versions"],
              "chatgpt_purpose": "reference_upload_not_native_history_restore"}
    index = []
    local_refs = {}
    refs_path = destination / 'attachment_references.jsonl'
    if refs_path.is_file():
        with refs_path.open(encoding='utf-8') as stream:
            for line in stream:
                entry = json.loads(line)
                if entry['status'] == 'matched' and entry['candidates']:
                    local_refs[(entry['source'], entry['conversation_id'], entry['node_id'], entry['reference'])] = entry['candidates'][0]['content_path']
    try:
        for record in store.conversation_records():
            current = next(x for x in record["versions"] if x["hash"] == record["hash"])
            raw = current["raw"]
            normalized = ChatGPTAdapter().normalize(raw, record["external_id"])
            chatgpt.write(raw, record["external_id"])
            projection, omitted = openwebui_projection(normalized)
            if projection["chat"]["history"]["messages"]:
                webui.write(projection, record["external_id"])
            else:
                omitted.append({"reason": "no_target_supported_messages"})
            if omitted:
                report["openwebui_omissions"].append({"conversation_id": record["external_id"], "source": record["source"], "nodes": omitted})
            path = readable / f"{record['pk']}.md"
            with path.open("x", encoding="utf-8", newline="\n") as out:
                out.write("# " + record["title"].replace("\n", " ") + "\n\n")
                out.write("对话 ID：" + json.dumps(record["external_id"], ensure_ascii=False) + "\n\n")
                out.write("以下完整列出各版本的全部节点，包括其他分支、工具消息和空节点；不是一条连续时间线。\n\n")
                for version in record["versions"]:
                    out.write(f"## 对话版本 {version['hash']}\n\n")
                    view = ChatGPTAdapter().normalize(version["raw"], record["external_id"])
                    out.write("来源：" + dumps({k: version[k] for k in ("import_id", "member")}) + "\n\n")
                    out.write("当前分支末节点：" + dumps(view["current_node"]) + "\n\n")
                    for msg in view["messages"]:
                        out.write("### " + json.dumps(msg["node_id"], ensure_ascii=False) + " / " + msg["role"] + "\n\n")
                        out.write("分支关系：" + dumps({"parent": msg["parent"], "children": msg["children"]}) + "\n\n")
                        out.write(msg["text"] + "\n\n")
                        if msg["references"]:
                            out.write("附件引用：" + dumps(msg["references"]) + "\n\n")
                            for reference in msg['references']:
                                local = local_refs.get((record['source'], record['external_id'], msg['node_id'], reference))
                                if local:
                                    label = '归档图片' if Path(local).suffix.lower() in {'.png','.jpg','.jpeg','.gif','.webp'} else '归档附件'
                                    out.write(('!' if label == '归档图片' else '') + '[' + label + '](../' + local + ')\n\n')
                        if not msg["text"]:
                            out.write("（非文本或空节点；完整内容见 conversations.jsonl 与原始文件。）\n\n")
            index.append({"conversation_id": record["external_id"], "source": record["source"], "title": record["title"],
                          "path": path.relative_to(destination).as_posix(), "versions": len(record["versions"])})
            report["conversations"] += 1
    finally:
        chatgpt.close()
        webui.close()
    report.update(chatgpt_parts=chatgpt.parts, openwebui_parts=webui.parts,
                  oversized_chatgpt_conversations=chatgpt.oversized, oversized_openwebui_conversations=webui.oversized)
    (readable / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "portability_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
