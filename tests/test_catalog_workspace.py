from pathlib import Path
import hashlib
import json
import shutil
import zipfile
import pytest
from memory_bridge.catalog import scan_backups,library_rows
from memory_bridge.desktop_workspace import create_workspace,verify_workspace
from memory_bridge.store import Store
from memory_bridge.gui_service import run_operation
from memory_bridge.common import BridgeError
from test_browser_package import make_capture
from test_volumes import split


def test_nested_folder_scan_groups_legacy_packages_and_ignores_program_zips(tmp_path):
    root=tmp_path/'backups';(root/'one/deep').mkdir(parents=True);(root/'two').mkdir()
    make_capture(root/'one/deep/a.zip');make_capture(root/'two/b.zip')
    with zipfile.ZipFile(root/'program.zip','w') as z:z.writestr('MemoryBridge.exe',b'fake')
    result=scan_backups([root,root/'one'])
    assert len(result['backups'])==1
    row=result['backups'][0]
    assert row['title']=='日语学习・数学' and len(row['paths'])==2 and row['complete']


def test_volume_scan_one_row_missing_volumes_visible(tmp_path):
    original=tmp_path/'original.zip';make_capture(original)
    parts=split(original,tmp_path)
    root=tmp_path/'outer';root.mkdir()
    for index,path in enumerate(parts):
        folder=root/str(index)/'nested';folder.mkdir(parents=True)
        shutil.copyfile(path,folder/path.name)
    rows=scan_backups([root])['backups']
    assert len(rows)==1 and rows[0]['complete'] and len(rows[0]['paths'])==len(parts)
    partial=scan_backups([root/'0'])['backups']
    assert len(partial)==1 and not partial[0]['complete'] and '缺卷' in partial[0]['status']


def test_selected_workspace_only_chosen_account_and_preserves_images_and_originals(tmp_path):
    zip_path=tmp_path/'source.zip';make_capture(zip_path)
    with Store(tmp_path/'library') as store:
        store.import_zip(zip_path,'one');store.import_zip(zip_path,'two')
        rows=library_rows(store);selected=next(r for r in rows if r['source']=='one')
        result=create_workspace(store,tmp_path/'workspace',[selected['pk']])
        assert result['conversations']==1 and result['verification']['ok'] and not result['uploaded']
        directory=Path(result['destination']);index=json.loads((directory/'对话目录.json').read_text(encoding='utf-8'))
        assert len(index)==1 and index[0]['source']=='one'
        raw=[json.loads(line) for line in (directory/'对话原始记录.jsonl').read_text(encoding='utf-8').splitlines()]
        assert len(raw)==1 and raw[0]['pk']==selected['pk']
        files=list((directory/'附件').iterdir())
        assert len(files)==1 and files[0].read_bytes()==b'\x89PNG\r\n\x1a\nsynthetic-image'
        assert not (directory/'originals').exists() and not (directory/'library').exists()
        assert verify_workspace(directory)['ok']
        assert run_operation('verify_package',store.root,{'package':directory})['ok']
        assert all(x['ok'] for x in store.verify_archives())
        assert 'AGENTS.md' in result['prompt'] and str(directory) in result['prompt']
        (directory/'AGENTS.md').write_text('tampered',encoding='utf-8')
        assert not verify_workspace(directory)['ok']


def test_workspace_splits_very_long_message_without_losing_text(tmp_path):
    path=tmp_path/'long.zip';body='日语数学'*20000
    make_capture(path,messages=[{'id':'one','role':'user','text':body,'html':'<p>long</p>'}])
    with Store(tmp_path/'lib') as store:
        store.import_zip(path,'account');pk=library_rows(store)[0]['pk']
        create_workspace(store,tmp_path/'result',[pk],chunk_bytes=4096)
    root=tmp_path/'result';parts=json.loads((root/'对话目录.json').read_text(encoding='utf-8'))[0]['parts']
    assert len(parts)>1 and all((root/p).stat().st_size<=4096 for p in parts)
    assert body in ''.join((root/p).read_text(encoding='utf-8') for p in parts)


def test_workspace_requires_explicit_selection(tmp_path):
    with Store(tmp_path/'lib') as store:
        with pytest.raises(BridgeError):create_workspace(store,tmp_path/'output',[])
    assert not (tmp_path/'output').exists()


def test_cross_account_prompt_classifies_renamed_dialogue_and_exports_both_accounts(tmp_path):
    a, b, unrelated = (tmp_path/f'{name}.zip' for name in ('a','b','unrelated'))
    library = tmp_path/'lib'
    make_capture(a, conversation_id='japanese-a', title='日语学习')
    with Store(library) as store:
        store.import_zip(a, 'A')
        original = library_rows(store)[0]['pk']
        create_workspace(store,tmp_path/'first',[original])
        prompt = (tmp_path/'first'/'网页接续提示词.txt').read_text(encoding='utf-8')
        make_capture(b, conversation_id='japanese-b', title='助词练习', messages=[
            {'id':'u1','role':'user','text':'0001.md\n'+prompt,'html':'<p>新账号</p>'},
            {'id':'a1','role':'assistant','text':'继续练习助词。','html':'<p>继续练习助词。</p>'},
        ])
        assert store.import_zip(b,'B')['stats']['linked_continuations']==1
        make_capture(unrelated, conversation_id='unrelated', title='日语学习', messages=[
            {'id':'u1','role':'user','text':'另一套日语教材','html':'<p>另一套日语教材</p>'},
            {'id':'a1','role':'assistant','text':prompt,'html':'<p>引用了编号</p>'},
        ])
        store.import_zip(unrelated,'C')
        rows = library_rows(store)
        assert len(rows)==2
        group = next(row for row in rows if row['pk']==original)
        assert group['title']=='日语学习' and group['messages']==7
        assert [(part['source'],part['external_id']) for part in group['parts']]==[('A','japanese-a'),('B','japanese-b')]
        new = group['parts'][1]['pk']
        result = create_workspace(store,tmp_path/'combined',[original,new])
        assert result['conversations']==1 and result['verification']['ok']
    combined = tmp_path/'combined'
    index = json.loads((combined/'对话目录.json').read_text(encoding='utf-8'))
    assert [(item['source'],item['接续顺序']) for item in index]==[('A',1),('B',2)]
    assert [Path(part).name for item in index for part in item['parts']]==[
        '001 日语学习 - 分片0001.md', '002 助词练习 - 分片0001.md',
    ]
    assert len(run_operation('conversation_files',library,{'pk':original}))==2
    assert len(run_operation('conversation_track',library,{'pk':original}))==2
    run_operation('detach_continuation',library,{'pk':new})
    with Store(library) as store:
        store.import_zip(b,'B',retry=True)
        assert len(library_rows(store))==3


def test_manual_link_is_preserved_in_full_library_backup(tmp_path):
    from memory_bridge.export import export_package
    from memory_bridge.fidelity import restore_library

    library = tmp_path/'lib'
    archive = tmp_path/'japanese.zip'
    make_capture(archive)
    with Store(library) as store:
        for source in ('A','B','C'):
            store.import_zip(archive,source)
        keys = {row['source']:row['pk'] for row in library_rows(store)}
    run_operation('attach_continuation',library,{'pk':keys['B'],'target':keys['A']})
    run_operation('attach_continuation',library,{'pk':keys['C'],'target':keys['B']})
    with Store(library) as store:
        export_package(store,tmp_path/'backup')
    restore_library(tmp_path/'backup',tmp_path/'restored')
    with Store(tmp_path/'restored') as store:
        group, = library_rows(store)
        assert [part['source'] for part in group['parts']]==['A','B','C']
        assert len(list(store.conversation_records()))==3


def test_trashed_conversation_group_stays_hidden_until_restored(tmp_path):
    from memory_bridge.export import export_package
    from memory_bridge.fidelity import restore_library

    library = tmp_path/'library'
    archive = tmp_path/'old-capture.zip'
    make_capture(archive, packed=False)
    with Store(library) as store:
        for source in ('A', 'B', 'C'):
            store.import_zip(archive,source)
        keys = {row['source']:row['pk'] for row in library_rows(store)}
    run_operation('attach_continuation',library,{'pk':keys['B'],'target':keys['A']})
    with Store(library) as store:
        with pytest.raises(BridgeError,match='确认'):
            store.trash_conversation(keys['A'])
        assert len(library_rows(store))==2
        store.trash_conversation(keys['A'],confirmed=True)
        store.import_zip(archive,'B',retry=True)
        assert [row['source'] for row in library_rows(store)]==['C']
        group, = library_rows(store,trashed=True)
        assert [part['source'] for part in group['parts']]==['A','B']
        assert {row['source'] for row in store.search('日本語')}=={'C'}
        with pytest.raises(BridgeError,match='刷新列表'):
            create_workspace(store,tmp_path/'invalid',[keys['B']])
        assert all(row['ok'] for row in store.verify_archives())
        export_package(store,tmp_path/'backup')
    restore_library(tmp_path/'backup',tmp_path/'restored')
    with Store(tmp_path/'restored') as store:
        assert [row['pk'] for row in library_rows(store,trashed=True)]==[keys['A']]
        store.restore_conversation(keys['A'])
        group = next(row for row in library_rows(store) if row['pk']==keys['A'])
        assert [part['source'] for part in group['parts']]==['A','B']
        assert {row['source'] for row in store.search('日本語')}=={'A','B','C'}
        assert not library_rows(store,trashed=True)
