"""Update runtime files in place to preserve the extension's path and checkpoints."""
from pathlib import Path
import json
import shutil
from .common import BridgeError,file_hash
from .gui_service import unique_destination

RUNTIME=('manifest.json','popup.html','popup.js','background.js','core.js','zip.js','volumes.js','store.js','collector.js','desktop.js','browser_control.js','restore.js','README.md')

def update_plugin(source,destination,backup_parent):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    try:manifest=json.loads((destination/'manifest.json').read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:raise BridgeError('请选择原插件文件夹，其中应有 manifest.json') from exc
    if not str(manifest.get('name','')).startswith('ChatGPT Memory Bridge'):
        raise BridgeError('所选文件夹不是 Memory Bridge 插件')
    if source==destination:raise BridgeError('这里已经是新版插件目录；请直接在扩展管理页重新加载')
    if any(not (source/name).is_file() for name in RUNTIME):raise BridgeError('随附插件文件不完整')
    backup=unique_destination(Path(backup_parent)/'插件更新备份','更新前插件')
    if destination==backup or destination in backup.parents:raise BridgeError('插件备份位置不能放在原插件目录内')
    backup.mkdir(parents=True)
    for name in RUNTIME:
        if (destination/name).is_file():
            shutil.copy2(destination/name,backup/name)
            if file_hash(destination/name)!=file_hash(backup/name):raise BridgeError('插件备份校验失败')
    for name in RUNTIME:
        shutil.copy2(source/name,destination/name)
        if file_hash(source/name)!=file_hash(destination/name):raise BridgeError('插件更新校验失败')
    return {'backup':str(backup),'files':len(RUNTIME)}
