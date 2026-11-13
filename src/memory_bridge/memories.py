from __future__ import annotations

import json
import re
import uuid
from typing import Protocol

from .adapters import text_content
from .common import BridgeError, digest, dumps, now, timestamp


KINDS = {"profile", "preferences", "learning", "projects", "reflections", "goals", "tasks", "interests"}
STATES = {"pending", "active", "outdated", "conflict", "deleted", "merged"}
EPISTEMICS = {"fact", "preference", "inference"}


def evidence_role(evidence):
    if not isinstance(evidence, dict):
        return None
    author = evidence.get("author")
    return author.get("role") if isinstance(author, dict) else evidence.get("role")
# Optional conservative filtering. Personal mode retains candidate content by default.
SENSITIVE = re.compile(
    r"@|(?:\d[\s-]?){7,}|"
    r"密码|口令|令牌|密钥|身份证|住址|地址|银行卡|病历|诊断|抑郁|疾病|宗教|政治|性取向|"
    r"password|secret|token|api.?key|address|diagnos|religio|politic|sexual|"
    r"パスワード|住所|診断", re.I)
INJECTION = re.compile(r"忽略.{0,16}(指令|规则)|系统提示|system\s*prompt|ignore.{0,20}(instructions|rules)|"
                       r"<\s*/?\s*(system|assistant)|覆盖.{0,10}规则", re.I)


class MemoryExtractor(Protocol):
    """Future adapters must return candidates, never execute conversation instructions.

    No network extractor ships in MVP. An external implementation must obtain explicit
    per-run user consent before any private text can leave this process.
    """

    def candidates(self, text: str, *, allow_sensitive: bool = True) -> list[dict]: ...


class RuleExtractor:
    version = "rules-v2-personal"

    def candidates(self, text, *, allow_sensitive=True):
        output = []
        for line in text.splitlines():
            line = line.strip()
            if not line or re.search(r"[?？]|假设|如果|例如|建议|也许|可能|引用", line):
                continue
            if not allow_sensitive and (SENSITIVE.search(line) or INJECTION.search(line)):
                continue
            match = re.fullmatch(r"我的(回复语言|回答长度|称呼)偏好[：:]\s*(.+)", line)
            if match:
                output.append(dict(content=line, kind="preferences", epistemic="preference",
                                   conflict_key="preference:" + match[1]))
                continue
            match = re.fullmatch(r"我的项目[「『\"]?([^：:「」『』\"]{1,40})[」』\"]?[：:]\s*(.+)", line)
            if match:
                output.append(dict(content=line, kind="projects", epistemic="fact", conflict_key="project:" + match[1].strip()))
                continue
            patterns = [
                (r"我(?:偏好|更喜欢)[：:]\s*(.+)", "preferences", "preference"),
                (r"我(?:正在学习|的学习进度)[：:]\s*(.+)", "learning", "fact"),
                (r"我的长期目标[：:]\s*(.+)", "goals", "fact"),
                (r"我的待办[：:]\s*(.+)", "tasks", "fact"),
                (r"我的观点[：:]\s*(.+)", "reflections", "fact"),
                (r"我长期感兴趣的主题[：:]\s*(.+)", "interests", "fact"),
                (r"I prefer[: ]\s*(.+)", "preferences", "preference"),
                (r"I am learning[: ]\s*(.+)", "learning", "fact"),
                (r"My long-term goal[: ]\s*(.+)", "goals", "fact"),
                (r"私の好み[：:]\s*(.+)", "preferences", "preference"),
            ]
            for pattern, kind, epistemic in patterns:
                if re.fullmatch(pattern, line, re.I):
                    output.append(dict(content=line, kind=kind, epistemic=epistemic, conflict_key=None))
                    break
        return output


class Memories:
    def __init__(self, store):
        self.store = store
        self.db = store.db

    def get(self, memory_id):
        row = self.db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise BridgeError("memory_not_found")
        item = dict(row)
        item["confirmed"] = bool(item["confirmed"])
        item["conflicts"] = json.loads(item["conflicts"])
        item["sources"] = [dict(x) for x in self.db.execute("""SELECT s.message_pk,s.message_hash,
            m.message_id,m.node_id,c.external_id AS conversation_id,c.source,c.platform,
            v.import_id,v.member,v.raw_json AS evidence_json,i.sha256 AS archive_sha256 FROM memory_sources s
            JOIN messages m ON m.pk=s.message_pk JOIN conversations c ON c.pk=m.conversation_pk
            JOIN message_versions v ON v.message_pk=s.message_pk AND v.hash=s.message_hash
            JOIN imports i ON i.id=v.import_id WHERE s.memory_id=? ORDER BY s.message_pk,s.message_hash""", (memory_id,))]
        for source in item["sources"]:
            raw = json.loads(source.pop("evidence_json"))
            evidence = raw.get("message", raw)
            if isinstance(evidence, dict):
                source["message_id"] = evidence.get("id")
                source["source_time"] = timestamp(evidence.get("create_time", evidence.get("created_at")))
                source["role"] = evidence_role(evidence)
        return item

    def list(self, status=None, confirmed_only=False):
        sql, params = "SELECT id FROM memories", []
        if status is not None:
            sql += " WHERE status=?"
            params.append(status)
        elif confirmed_only:
            sql += " WHERE confirmed=1 AND status='active'"
        return [self.get(x[0]) for x in self.db.execute(sql + " ORDER BY extracted_at,id", params)]

    def _save_version(self, memory_id, reason):
        version = self.db.execute("SELECT coalesce(max(version),0)+1 FROM memory_versions WHERE memory_id=?", (memory_id,)).fetchone()[0]
        self.db.execute("INSERT INTO memory_versions VALUES(?,?,?,?,?)", (memory_id, version, dumps(self.get(memory_id)), reason, now()))

    def add(self, content, kind, message_pk, *, epistemic="fact", confidence=0.7, conflict_key=None, message_hash=None):
        if kind not in KINDS or epistemic not in EPISTEMICS or not 0 <= confidence <= 1 or not content.strip():
            raise BridgeError("invalid_memory_fields")
        msg = self.db.execute("SELECT * FROM messages WHERE pk=?", (message_pk,)).fetchone()
        if msg is None:
            raise BridgeError("source_message_not_found")
        mhash = message_hash or msg["hash"]
        version = self.db.execute("SELECT raw_json FROM message_versions WHERE message_pk=? AND hash=?", (message_pk, mhash)).fetchone()
        if version is None:
            raise BridgeError("source_version_not_found")
        # Manual inference is permitted, but assistant statements are not auto-promoted to user facts.
        raw = json.loads(version[0])
        evidence = raw.get("message", raw)
        role = evidence_role(evidence)
        if epistemic != "inference" and role != "user":
            raise BridgeError("user_fact_requires_user_message")
        mid = str(uuid.uuid4())
        with self.db:
            source_time = timestamp(evidence.get("create_time", evidence.get("created_at"))) if isinstance(evidence, dict) else None
            self.db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (mid, content.strip(), kind, epistemic, "pending", 0, confidence, source_time, now(), now(), conflict_key, "[]", None))
            self.db.execute("INSERT INTO memory_sources VALUES(?,?,?)", (mid, message_pk, mhash))
            self._save_version(mid, "created_pending_review")
            if conflict_key:
                conflicts = [r[0] for r in self.db.execute("""SELECT id FROM memories WHERE conflict_key=? AND id!=?
                    AND content!=? AND status NOT IN ('deleted','merged','outdated')""", (conflict_key, mid, content.strip()))]
                for other in conflicts:
                    self._conflict(mid, other)
        return self.get(mid)

    def extract(self, *, allow_sensitive=True, extractor=None):
        extractor = extractor or RuleExtractor()
        stats = dict(candidates=0, duplicates=0, skipped_messages=0)
        for msg in self.db.execute("""SELECT v.message_pk AS pk,v.hash,v.raw_json FROM message_versions v
            JOIN messages m ON m.pk=v.message_pk JOIN active_conversations c ON c.pk=m.conversation_pk
            ORDER BY v.import_id,v.message_pk,v.hash"""):
            raw = json.loads(msg["raw_json"])
            evidence = raw.get("message", raw)
            if evidence_role(evidence) != "user":
                continue
            candidates = extractor.candidates(text_content(evidence.get("content")), allow_sensitive=allow_sensitive)
            if not candidates:
                stats["skipped_messages"] += 1
            for candidate in candidates:
                fingerprint = digest([getattr(extractor, "version", type(extractor).__name__),
                                      msg["pk"], msg["hash"], candidate])
                if self.db.execute("SELECT 1 FROM extraction_candidates WHERE fingerprint=?", (fingerprint,)).fetchone():
                    stats["duplicates"] += 1
                    continue
                # Identical candidate from the same immutable evidence is idempotent, including deleted items.
                existing = self.db.execute("""SELECT m.id FROM memories m JOIN memory_sources s ON s.memory_id=m.id
                    WHERE m.content=? AND m.kind=? AND s.message_pk=? AND s.message_hash=?""",
                                           (candidate["content"], candidate["kind"], msg["pk"], msg["hash"])).fetchone()
                if existing:
                    with self.db:
                        self.db.execute("INSERT INTO extraction_candidates VALUES(?,?)", (fingerprint, existing[0]))
                    stats["duplicates"] += 1
                    continue
                created = self.add(message_pk=msg["pk"], message_hash=msg["hash"], **candidate)
                with self.db:
                    self.db.execute("INSERT INTO extraction_candidates VALUES(?,?)", (fingerprint, created["id"]))
                stats["candidates"] += 1
        return stats

    def update(self, memory_id, *, content=None, status=None, confirmed=None, epistemic=None, reason="manual_review"):
        current = self.get(memory_id)
        if status is not None and status not in STATES - {"merged"}:
            raise BridgeError("invalid_memory_status")
        if status == "deleted":
            raise BridgeError("use_confirmed_delete_operation")
        if epistemic is not None and epistemic not in EPISTEMICS:
            raise BridgeError("invalid_epistemic")
        if epistemic in ("fact", "preference"):
            for source in current["sources"]:
                raw = json.loads(self.db.execute("SELECT raw_json FROM message_versions WHERE message_pk=? AND hash=?",
                                                (source["message_pk"], source["message_hash"])).fetchone()[0])
                evidence = raw.get("message", raw)
                if evidence_role(evidence) != "user":
                    raise BridgeError("user_fact_requires_user_message")
        if current["status"] in ("deleted", "merged"):
            raise BridgeError("restore_memory_before_edit")
        changes = {k: v for k, v in dict(content=content, status=status, confirmed=confirmed, epistemic=epistemic).items() if v is not None}
        if content is not None and not content.strip():
            raise BridgeError("empty_memory")
        # Editing evidence-derived text invalidates previous human approval unless re-confirmed explicitly.
        if content is not None and content != current["content"] and confirmed is None:
            changes["confirmed"] = False
            changes["status"] = "pending"
        changes["updated_at"] = now()
        with self.db:
            self.db.execute("UPDATE memories SET " + ",".join(k + "=?" for k in changes) + " WHERE id=?", [*changes.values(), memory_id])
            self._save_version(memory_id, reason)
        return self.get(memory_id)

    def delete(self, memory_id, *, confirmed=False):
        if not confirmed:
            raise BridgeError("delete_requires_confirmation")
        self.get(memory_id)
        with self.db:
            self.db.execute("UPDATE memories SET status='deleted',confirmed=0,updated_at=? WHERE id=?", (now(), memory_id))
            self._save_version(memory_id, "user_confirmed_soft_delete")

    def _conflict(self, first, second):
        for mid, other in ((first, second), (second, first)):
            memory = self.get(mid)
            conflicts = sorted(set(memory["conflicts"] + [other]))
            self.db.execute("UPDATE memories SET status='conflict',confirmed=0,conflicts=?,updated_at=? WHERE id=?", (dumps(conflicts), now(), mid))
            self._save_version(mid, "conflict_flagged")

    def mark_conflict(self, first, second):
        if first == second:
            raise BridgeError("self_conflict")
        self.get(first)
        self.get(second)
        with self.db:
            self._conflict(first, second)

    def history(self, memory_id):
        self.get(memory_id)
        return [{**dict(x), "snapshot": json.loads(x["snapshot"])} for x in self.db.execute(
            "SELECT * FROM memory_versions WHERE memory_id=? ORDER BY version", (memory_id,))]

    def rollback(self, memory_id, version):
        item = self.db.execute("SELECT snapshot FROM memory_versions WHERE memory_id=? AND version=?", (memory_id, version)).fetchone()
        if item is None:
            raise BridgeError("memory_version_not_found")
        snapshot = json.loads(item[0])
        fields = ["content", "kind", "epistemic", "status", "confirmed", "confidence", "source_time", "extracted_at", "conflict_key", "merged_into"]
        with self.db:
            self.db.execute("UPDATE memories SET " + ",".join(k + "=?" for k in fields) + ",conflicts=?,updated_at=? WHERE id=?",
                            [*(snapshot[k] for k in fields), dumps(snapshot["conflicts"]), now(), memory_id])
            self.db.execute("DELETE FROM memory_sources WHERE memory_id=?", (memory_id,))
            for src in snapshot["sources"]:
                self.db.execute("INSERT INTO memory_sources VALUES(?,?,?)", (memory_id, src["message_pk"], src["message_hash"]))
            self._save_version(memory_id, f"rollback_to_version_{version}")
        return self.get(memory_id)

    def merge(self, target, other, *, confirmed=False):
        if not confirmed:
            raise BridgeError("merge_requires_confirmation")
        a, b = self.get(target), self.get(other)
        if target == other or a["status"] in ("deleted", "merged") or b["status"] in ("deleted", "merged"):
            raise BridgeError("invalid_merge")
        # Deliberately exact: significant differences need human edits, never semantic auto-overwrite.
        if any(a[k] != b[k] for k in ("content", "kind", "epistemic")):
            raise BridgeError("merge_requires_identical_content_type_and_epistemic")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO memory_sources SELECT ?,message_pk,message_hash FROM memory_sources WHERE memory_id=?", (target, other))
            self.db.execute("UPDATE memories SET status='merged',confirmed=0,merged_into=?,updated_at=? WHERE id=?", (target, now(), other))
            self._save_version(target, "merged_duplicate_evidence")
            self._save_version(other, "merged_into_" + target)
        return self.get(target)
