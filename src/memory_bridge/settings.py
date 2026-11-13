"""Persistent user paths; the settings file stays independent of the chosen library."""
from pathlib import Path
import json
import os
import tempfile

from .common import BridgeError


def user_settings_path():
    return Path(os.environ.get('LOCALAPPDATA') or Path.home())/'ChatGPTMemoryBridge/settings.json'


def load(path):
    try:
        result=json.loads(Path(path).read_text(encoding='utf-8'))
        return result if isinstance(result,dict) else {}
    except (OSError,ValueError):
        return {}


def resolve_paths(data,output):
    if not str(data).strip() or not str(output).strip():
        raise BridgeError('storage_directory_required')
    data,output=Path(data).expanduser().resolve(),Path(output).expanduser().resolve()
    if data==output or data in output.parents:
        raise BridgeError('export_must_be_outside_library')
    return data,output


def save(path,data,output,storage_root=None):
    data,output=resolve_paths(data,output)
    path=Path(path).resolve();path.parent.mkdir(parents=True,exist_ok=True)
    if path==data or data in path.parents:
        raise BridgeError('settings_must_be_outside_library')
    values={'data_root':str(data),'export_root':str(output)}
    if storage_root is not None:
        root=Path(storage_root).expanduser().resolve()
        if data!=root/'资料库' or output!=root:raise BridgeError('invalid_unified_storage_layout')
        values['storage_root']=str(root)
    fd,name=tempfile.mkstemp(prefix='.settings-',suffix='.json',dir=path.parent)
    temp=Path(name)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(values,stream,ensure_ascii=False,indent=2)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)
