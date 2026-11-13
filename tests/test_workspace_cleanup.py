import json
import pytest
from memory_bridge.common import BridgeError
from memory_bridge.workspace_cleanup import list_workspaces, clean_workspaces


def test_cleanup_is_selective_and_preserves_library_and_original_zip(tmp_path):
    root=tmp_path/'接续目录';root.mkdir()
    library=tmp_path/'资料库';library.mkdir();(library/'original.zip').write_bytes(b'original')
    for name,status in [('done','completed'),('pending','error')]:
        folder=root/name;folder.mkdir()
        (folder/'分对话导入计划.json').write_text('{}')
        (folder/'导入结果.json').write_text(json.dumps([{'status':status}]))
        (folder/'attachment.png').write_bytes(b'picture')
    unknown=root/'personal';unknown.mkdir();(unknown/'keep.txt').write_text('keep')
    rows=list_workspaces(root)
    assert {row['status'] for row in rows}=={'已完成上传','未完成／可续传'}
    with pytest.raises(BridgeError,match='确认'):clean_workspaces(root,['done'])
    with pytest.raises(BridgeError):clean_workspaces(root,['done','../资料库'],True)
    assert (root/'done').exists()
    result=clean_workspaces(root,['done'],True)
    assert result['removed']==1 and result['bytes']>0
    assert not (root/'done').exists()
    assert (root/'pending/attachment.png').read_bytes()==b'picture'
    assert (unknown/'keep.txt').is_file()
    assert (library/'original.zip').read_bytes()==b'original'


def test_cleanup_rejects_wrong_root_and_linked_workspace(tmp_path):
    root=tmp_path/'资料库';root.mkdir()
    with pytest.raises(BridgeError,match='位置'):clean_workspaces(root,['x'],True)
    proper=tmp_path/'接续目录';proper.mkdir()
    target=tmp_path/'original';target.mkdir();(target/'manifest.json').write_text('{}')
    link=proper/'linked'
    try:link.symlink_to(target,target_is_directory=True)
    except OSError:pytest.skip('Creating symbolic links unavailable')
    assert list_workspaces(proper)==[]
    with pytest.raises(BridgeError):clean_workspaces(proper,['linked'],True)
    assert target.exists()
