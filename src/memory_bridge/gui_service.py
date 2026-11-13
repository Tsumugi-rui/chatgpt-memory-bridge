"""GUI operations have no Tk objects; every operation owns its SQLite connection."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4
import json

from .common import BridgeError
from .export import export_package, verify_package
from .fidelity import restore_library
from .memories import Memories
from .store import Store


def unique_destination(parent, prefix="迁移包"):
    return Path(parent).expanduser().resolve() / f"{prefix}-{datetime.now().astimezone():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"


def run_operation(operation, data, payload, progress=lambda message: None):
    if operation == 'list_workspaces':
        from .workspace_cleanup import list_workspaces
        return list_workspaces(payload['root'])
    if operation == 'clean_workspaces':
        from .workspace_cleanup import clean_workspaces
        return clean_workspaces(payload['root'], payload['names'], payload.get('confirmed', False))
    if operation == 'resume_batch_restore':
        from .batch_restore import resume_batch
        return resume_batch(payload['report'], payload.get('selected'))
    if operation == 'configure_root':
        from .settings import save
        from .storage import copy_library
        if not str(payload.get('storage_root','')).strip():raise BridgeError('请选择数据保存位置。')
        root=Path(payload['storage_root']).expanduser().resolve();target=root/'资料库'
        source=Path(data).resolve();result={'copied':False,'source_preserved':True}
        if source!=target and payload.get('copy_existing',True):
            result=copy_library(source,target,progress,allow_legacy_child=(source==root))
        else:
            if source==root and (root/'bridge.sqlite3').is_file():
                raise BridgeError('这里是旧版资料库，请勾选复制现有资料库，软件会保留原件并整理到“资料库”子目录。')
            with Store(target) as check:
                if check.db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise BridgeError('invalid_target_library')
        for name in ('浏览器备份','接续目录','完整备份'):(root/name).mkdir(parents=True,exist_ok=True)
        if payload.get('settings_path'):save(payload['settings_path'],target,root,storage_root=root)
        return {**result,'data_root':str(target),'export_root':str(root),'storage_root':str(root)}
    if operation == 'configure_storage':
        from .settings import resolve_paths,save
        from .storage import copy_library
        target,output=resolve_paths(payload['data_root'],payload['export_root'])
        result={'copied':False,'source_preserved':True}
        if payload.get('copy_existing') and Path(data).resolve()!=target:
            result=copy_library(data,target,progress)
        else:
            with Store(target) as check:
                if check.db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise BridgeError('invalid_target_library')
        output.mkdir(parents=True,exist_ok=True)
        if payload.get('settings_path'):
            save(payload['settings_path'],target,output)
        return {**result,'data_root':str(target),'export_root':str(output)}
    if operation == "restore":
        result=restore_library(payload["package"], payload["destination"])
        root=payload.get('storage_root')
        if root:
            for name in ('浏览器备份','接续目录','完整备份'):(Path(root)/name).mkdir(parents=True,exist_ok=True)
        if payload.get('settings_path'):
            from .settings import save
            save(payload['settings_path'],payload['destination'],payload['export_root'],storage_root=root)
        return result
    if operation == "verify_package":
        manifest=Path(payload['package'])/'manifest.json'
        if manifest.is_file() and manifest.stat().st_size<=16*1024*1024:
            try:kind=json.loads(manifest.read_text(encoding='utf-8')).get('format')
            except (ValueError,AttributeError):kind=None
            if kind=='memory-bridge-workspace-v1':
                from .desktop_workspace import verify_workspace
                return verify_workspace(payload['package'])
        return verify_package(payload["package"])
    with Store(data) as store:
        if operation == 'prepare_batch_restore':
            from .batch_restore import prepare_batch
            return prepare_batch(store, payload['destination'], payload['selected'], progress)
        if operation == 'reset_library':
            from .reset import reset_library
            return reset_library(store, payload['output'], confirmed=payload.get('confirmed', False))
        if operation == 'delete_conversation':
            from .deletion import delete_conversations
            roots=payload['pks'] if 'pks' in payload else [payload['pk']]
            return delete_conversations(store,roots,confirmed=payload.get('confirmed',False),
                                        expected_members=payload.get('members'))
        if operation == 'trash_conversation':
            store.trash_conversation(payload['pk'],confirmed=payload.get('confirmed',False))
            return None
        if operation == 'restore_conversation':
            store.restore_conversation(payload['pk'])
            return None
        if operation in ('attach_continuation', 'detach_continuation'):
            from .continuations import attach, detach
            with store.db:
                store.db.execute('BEGIN IMMEDIATE')
                if operation == 'attach_continuation':
                    attach(store.db, payload['pk'], payload['target'])
                else:
                    detach(store.db, payload['pk'])
            return None
        if operation == 'conversation_track':
            from .continuations import track_members
            return [record for member in track_members(store.db, payload['pk'])
                    for record in store.conversation_records([member['external_id']], member['source'])]
        if operation == 'conversation_files':
            from .continuations import track_members
            root = track_members(store.db, payload['pk'])[0]['pk']
            files=[]
            for row in store.db.execute('''SELECT DISTINCT i.sha256,i.imported_at FROM observations o
                    JOIN imports i ON i.id=o.import_id
                    LEFT JOIN conversation_continuations l ON l.conversation_pk=o.conversation_pk
                    WHERE COALESCE(l.root_pk,o.conversation_pk)=? ORDER BY i.imported_at''',(root,)):
                checksum=row['sha256']
                if len(checksum)!=64 or any(c not in '0123456789abcdef' for c in checksum):
                    raise BridgeError('原始归档索引不正确')
                path=store.root/'archives'/(checksum+'.zip')
                files.append({'path':str(path.resolve()),'date':row['imported_at'],'exists':path.is_file()})
            return files
        if operation == 'catalog':
            from .catalog import scan_backups,library_rows
            return {**scan_backups(payload.get('folders',[]),progress),'conversations':library_rows(store),
                    'trashed_conversations':library_rows(store,trashed=True)}
        if operation == 'desktop_workspace':
            from .desktop_workspace import create_workspace
            return create_workspace(store,payload['destination'],payload.get('selected',[]),progress=progress)
        if operation == 'prepare_handoff':
            if not store.db.execute('SELECT 1 FROM conversations LIMIT 1').fetchone():
                raise BridgeError('请先读取旧账号备份，再准备上传资料。')
            progress('正在整理本地资料库中的对话与记忆，准备新账号上传资料…')
            extracted=Memories(store).extract()
            manifest=export_package(store,payload['destination'],progress=progress)
            verified=verify_package(payload['destination'])
            if not verified['ok']:raise BridgeError('package_integrity_failed')
            return {'destination':str(payload['destination']),'files':len(manifest['files']),
                    'verification':verified,'extraction':extracted,'uploaded':False}
        if operation == "overview":
            from .catalog import library_rows
            reset = store.db.execute("SELECT value FROM metadata WHERE key='capture_reset_at'").fetchone()
            return {"counts": {table: store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                               for table in ("imports", "conversations", "messages", "memories")}, "imports": store.imports(),
                    'library_conversations':library_rows(store),'trashed_conversations':library_rows(store,trashed=True),
                    'reset_at':reset[0] if reset else None}
        if operation in ("import", "migrate"):
            from .volumes import prepare_imports
            results=[]
            with prepare_imports(payload.get('zips') or [payload['zip']], store.root, store, payload['source']) as inputs:
                for index,path in enumerate(inputs,1):
                    progress(f'正在导入第 {index}/{len(inputs)} 份逻辑备份（分卷会先合并）…')
                    results.append(store.import_zip(path, payload["source"], retry=payload.get("retry", False),
                                                    progress=progress,capture_task=payload.get('capture_task')))
            result=results[-1]
            batch={'import':result,'imports':results,'input_files':len(payload.get('zips') or [payload['zip']])}
            if operation == "import" or any(r['status']=='rejected' for r in results):
                return batch
            progress("正在从所有历史消息版本提取记忆…")
            extracted = Memories(store).extract()
            # Partial archives retain their recoverable data and a visibly incomplete import report.
            manifest = export_package(store, payload["destination"], progress=progress)
            progress("正在校验完整迁移包…")
            verified = verify_package(payload["destination"])
            if not verified["ok"]:
                raise BridgeError("package_integrity_failed")
            return {**batch, "extraction": extracted, "destination": str(payload["destination"]),
                    "files": len(manifest["files"]), "verification": verified}
        if operation == "extract":
            progress("正在提取所有历史版本中的用户陈述…")
            return Memories(store).extract()
        if operation == "search":
            return store.search(payload.get("query") or None, conversation_id=payload.get("conversation_id") or None,
                                topic=payload.get("topic") or None, source=payload.get("source") or None, limit=500)
        if operation == "conversation":
            return list(store.conversation_records([payload["id"]], source=payload["source"]))
        if operation == "memories":
            return Memories(store).list(payload.get("status") or None)
        if operation == "memory_update":
            fields = {key: payload[key] for key in ("content", "status", "confirmed") if key in payload}
            return Memories(store).update(payload["id"], **fields)
        if operation == "memory_delete":
            Memories(store).delete(payload["id"], confirmed=payload.get("confirmed", False))
            return {"soft_deleted": payload["id"]}
        if operation == "memory_history":
            return Memories(store).history(payload["id"])
        if operation == "memory_rollback":
            return Memories(store).rollback(payload["id"], payload["version"])
        if operation == "export":
            manifest = export_package(store, payload["destination"], progress=progress)
            progress("正在校验导出文件…")
            verified = verify_package(payload["destination"])
            if not verified["ok"]:
                raise BridgeError("package_integrity_failed")
            return {"destination": str(payload["destination"]), "files": len(manifest["files"]), "verification": verified}
        if operation == "verify_library":
            archives, versions = store.verify_archives(), store.verify_versions()
            database = store.db.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = [list(row) for row in store.db.execute("PRAGMA foreign_key_check")]
            return {"ok": database == "ok" and not foreign_keys and versions["ok"] and all(x["ok"] for x in archives),
                    "database": database, "foreign_keys": foreign_keys, "archives": archives, "versions": versions}
    raise BridgeError("unknown_gui_operation")
