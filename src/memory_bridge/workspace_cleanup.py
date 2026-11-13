"""Explicit cleanup of generated handoff workspaces, never the source library."""
import json
import shutil
from pathlib import Path

from .common import BridgeError


def _linked(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def list_workspaces(root):
    root = Path(root).absolute()
    if not root.is_dir() or _linked(root):
        return []
    rows = []
    for folder in sorted(root.iterdir(), reverse=True):
        if not folder.is_dir() or _linked(folder):
            continue
        if not (folder/'manifest.json').is_file() and not (folder/'分对话导入计划.json').is_file():
            continue
        try:
            paths = list(folder.rglob('*'))
            if any(_linked(path) for path in paths):
                continue
            size = sum(path.stat().st_size for path in paths if path.is_file())
            report = folder/'导入结果.json'
            status = '本地接续资料'
            if report.exists():
                records = json.loads(report.read_text(encoding='utf-8'))
                status = '已完成上传' if records and all(row.get('status') == 'completed' for row in records) else '未完成／可续传'
            rows.append({'name': folder.name, 'path': str(folder), 'bytes': size, 'status': status})
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return rows


def clean_workspaces(root, names, confirmed=False):
    if not confirmed:
        raise BridgeError('清理接续目录需要确认。')
    root = Path(root).absolute()
    if root.name != '接续目录' or _linked(root) or root.resolve() != root:
        raise BridgeError('接续目录位置不正确。')
    available = {row['name']: row for row in list_workspaces(root)}
    chosen = list(dict.fromkeys(names))
    if not chosen or any(name not in available for name in chosen):
        raise BridgeError('所选接续资料已变化，请刷新后重试。')
    targets = [root/name for name in chosen]
    # Validate all absolute targets before the first recursive removal.
    for path in targets:
        if path.resolve().parent != root or _linked(path):
            raise BridgeError('接续资料位置不正确。')
    released = sum(available[name]['bytes'] for name in chosen)
    for path in targets:
        shutil.rmtree(path)
    return {'removed': len(targets), 'bytes': released}
