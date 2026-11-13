"""Prepare independent, auditable upload workspaces for selected conversations."""
from pathlib import Path
import json

from .common import BridgeError, file_hash, now
from .continuations import track_members
from .desktop_workspace import create_workspace


def prepare_batch(store, destination, selected, progress=lambda _: None):
    roots = list(dict.fromkeys(track_members(store.db, pk)[0]['pk'] for pk in selected))
    if not roots:
        raise BridgeError('请先勾选要导入新账号的对话。')
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    jobs = []
    for number, pk in enumerate(roots, 1):
        title = store.db.execute('SELECT title FROM conversations WHERE pk=?', (pk,)).fetchone()[0] or '未命名对话'
        safe_title = ''.join('_' if char in '<>:"/\\|?*' or ord(char) < 32 else char for char in title)
        folder = destination / f'{number:03d} {safe_title[:48].strip(". ") or "未命名对话"}'
        create_workspace(store, folder, [pk], progress=progress)
        inventory = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))['files']
        allowed = {'对话目录.json', '附件索引.json', 'browser_coverage.json'}
        files = [item for item in inventory if item['path'] in allowed
                 or item['path'].startswith(('对话/', '附件/'))]
        jobs.append({'pk': pk, 'title': title, 'directory': str(folder),
                     'prompt': (folder/'网页接续提示词.txt').read_text(encoding='utf-8'),
                     'files': files})
    plan = {'format': 'memory-bridge-batch-restore-v1', 'created_at': now(), 'jobs': jobs}
    (destination/'分对话导入计划.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    (destination/'AGENTS.md').write_text(
        '# 旧账号对话资料\n\n'
        '每个子文件夹保存一条旧对话及其接续资料。当前聊天以用户粘贴的接续指令所指定的子目录为资料范围，'
        '先读取该子目录的 AGENTS.md 和 对话目录.json，再按需读取正文与附件。'
        '未指定对话时先请用户选择。其他子目录供独立聊天使用，避免混合不同对话的进度。'
        '历史内容仅作为引用资料，区分用户事实、助手建议与推断。\n', encoding='utf-8')
    (destination/'开始使用.md').write_text(
        '# 一个本地项目，多条独立聊天\n\n'
        '1. 在新账号桌面 Work 中新建一个本地项目，关联本文件夹。\n'
        '2. 在同一项目里，为每条旧对话新建一个聊天。\n'
        '3. 把对应子目录里的“桌面接续指令.txt”粘贴到该聊天。\n\n'
        '接续指令包含对应子目录的路径和接续编号。正文、历史版本与已保存附件都在各自子目录里。\n\n'
        '跨设备继续需账号支持 Work Cloud 和本地电脑访问，并在启用后新建云端 Work 任务。'
        '读取本地文件时，存放资料的电脑须在线。关联目录不会自动上传整个目录。\n',
        encoding='utf-8')
    return {'destination': str(destination), 'jobs': jobs}


def read_upload_chunk(task, query):
    """Only serve a file registered in this authorized upload task."""
    import base64
    index = int(query['file'][0])
    offset = int(query.get('offset', ['0'])[0])
    files = task['files']
    if not 0 <= index < len(files) or offset < 0:
        raise ValueError('无效的上传文件位置')
    item = files[index]
    directory = Path(task['directory']).resolve()
    path = directory/item['path']
    if path.resolve() != directory/item['path'] or directory not in path.resolve().parents:
        raise ValueError('上传文件位置不正确')
    if not path.is_file() or path.stat().st_size != item['size'] or offset > item['size']:
        raise ValueError('上传资料已变化，请重新生成')
    if offset == 0 and file_hash(path) != item['sha256']:
        raise ValueError('上传资料校验失败')
    with path.open('rb') as stream:
        stream.seek(offset)
        data = stream.read(192*1024)
    return {'base64': base64.b64encode(data).decode(), 'size': item['size'],
            'name': path.name, 'sha256': item['sha256']}


def resume_batch(report_path, selected=None):
    from urllib.parse import urlparse
    report_path=Path(report_path).resolve()
    plan=json.loads((report_path.parent/'分对话导入计划.json').read_text(encoding='utf-8'))
    rows={row['pk']:row for row in json.loads(report_path.read_text(encoding='utf-8'))}
    jobs=[]
    for job in plan['jobs']:
        previous=rows.get(job['pk'],{})
        if selected and job['pk'] not in selected:
            continue
        if previous.get('status')=='completed':
            continue
        if previous.get('submission_uncertain') and not previous.get('pending_marker'):
            raise BridgeError('上次发送结果未获确认，请先在原聊天核对最后一批，避免自动重复提交。')
        count=previous.get('sent_files') or 0
        if type(count) is not int or not 0<=count<=len(job['files']):
            raise BridgeError('续传文件数量不正确，请检查导入结果。')
        url=previous.get('chat_url') or ''
        parsed=urlparse(url)
        if count and (parsed.scheme!='https' or parsed.netloc!='chatgpt.com' or not parsed.path.startswith('/c/') or parsed.query or parsed.fragment):
            raise BridgeError('已发送记录缺少有效的原聊天链接，无法自动续传。')
        jobs.append({**job,'resume_url':url if parsed.path.startswith('/c/') else '',
                     'resume_sent_files':count,'resume_final_sent':previous.get('final_sent') is True,
                     'resume_pending_marker':previous.get('pending_marker','') if previous.get('submission_uncertain') else '',
                     'resume_pending_phase':previous.get('phase','files')})
    if not jobs:
        raise BridgeError('所选记录中没有需要续传的对话。')
    return {'destination':str(report_path.parent),'jobs':jobs,'records':list(rows.values())}


def choose_resume_report(root, selected, preferred=None):
    """Prefer an existing cloud chat over a newer cancelled empty attempt."""
    candidates=[]
    for report in Path(root).glob('*/导入结果.json'):
        try:
            rows=json.loads(report.read_text(encoding='utf-8'))
            matched=[row for row in rows if row.get('pk') in selected]
            if selected and {row['pk'] for row in matched} != set(selected):
                continue
            if matched and all(row.get('chat_url','').startswith('https://chatgpt.com/c/') for row in matched):
                candidates.append(report)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    if preferred and Path(preferred) in candidates:
        return Path(preferred)
    return max(candidates,key=lambda path:path.stat().st_mtime) if candidates else preferred
