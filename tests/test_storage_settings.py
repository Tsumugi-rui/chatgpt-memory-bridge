import json
from pathlib import Path

import pytest

from memory_bridge.common import BridgeError,file_hash
from memory_bridge.gui_service import run_operation
from memory_bridge.settings import load,save
from memory_bridge.store import Store
from memory_bridge.storage import copy_library


def test_copy_storage_preserves_original_and_remembers_new_paths(tmp_path,make_zip):
    from conftest import conversation
    source=tmp_path/'old';target=tmp_path/'new';settings=tmp_path/'config/settings.json'
    with Store(source) as store:
        store.import_zip(make_zip([conversation()]))
    original=file_hash(next((source/'archives').glob('*.zip')))
    (source/'extra').mkdir();(source/'extra/identity.md').write_text('用户自己的补充资料',encoding='utf-8')
    result=run_operation('configure_storage',source,{'data_root':target,'export_root':tmp_path/'exports','copy_existing':True,'settings_path':settings})
    assert result['copied'] and result['verification'] and result['source_preserved']
    assert load(settings)['data_root']==str(target.resolve())
    assert load(settings)['export_root']==str((tmp_path/'exports').resolve())
    assert (source/'bridge.sqlite3').is_file() and (target/'bridge.sqlite3').is_file()
    assert (target/'extra/identity.md').read_text(encoding='utf-8')=='用户自己的补充资料'
    assert file_hash(next((target/'archives').glob('*.zip')))==original
    for root in (source,target):
        with Store(root) as store:
            assert store.search('数学') and store.verify_archives()[0]['ok']


def test_failed_copy_does_not_overwrite_target_or_change_settings(tmp_path):
    source=tmp_path/'old';target=tmp_path/'existing';settings=tmp_path/'settings.json'
    with Store(source):pass
    target.mkdir();(target/'important.txt').write_text('keep')
    save(settings,source,tmp_path/'exports')
    with pytest.raises(BridgeError,match='must_be_empty'):
        run_operation('configure_storage',source,{'data_root':target,'export_root':tmp_path/'newexports','copy_existing':True,'settings_path':settings})
    assert (target/'important.txt').read_text()=='keep'
    assert load(settings)['data_root']==str(source.resolve())


@pytest.mark.parametrize('name',['same','nested','parent'])
def test_copy_rejects_overlapping_paths(tmp_path,name):
    source=tmp_path/'old'
    target={'same':source,'nested':source/'child','parent':tmp_path}[name]
    with pytest.raises(BridgeError,match='overlap'):copy_library(source,target)


def test_switch_to_existing_library_without_copying(tmp_path):
    source=tmp_path/'one';target=tmp_path/'two';settings=tmp_path/'config/settings.json'
    with Store(source):pass
    with Store(target):pass
    result=run_operation('configure_storage',source,{'data_root':target,'export_root':tmp_path/'out','copy_existing':False,'settings_path':settings})
    assert not result['copied'] and load(settings)['data_root']==str(target.resolve())


def test_invalid_export_location_keeps_previous_settings(tmp_path):
    settings=tmp_path/'settings.json';source=tmp_path/'data'
    save(settings,source,tmp_path/'exports')
    old=settings.read_bytes()
    with pytest.raises(BridgeError,match='outside_library'):
        run_operation('configure_storage',source,{'data_root':source,'export_root':source/'exports','settings_path':settings})
    assert settings.read_bytes()==old


def test_atomic_settings_write_keeps_previous_file_on_failure(tmp_path,monkeypatch):
    import memory_bridge.settings as settings_module
    settings=tmp_path/'settings.json';save(settings,tmp_path/'data',tmp_path/'exports');old=settings.read_bytes()
    def fail(*_):raise OSError('synthetic failure')
    monkeypatch.setattr(settings_module.os,'replace',fail)
    with pytest.raises(OSError):save(settings,tmp_path/'newdata',tmp_path/'newout')
    assert settings.read_bytes()==old


def test_desktop_next_launch_uses_saved_storage_choices(tmp_path,monkeypatch):
    import memory_bridge.desktop as desktop
    from memory_bridge.settings import user_settings_path
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path));monkeypatch.setattr(desktop.sys,'argv',['MemoryBridge.exe'])
    save(user_settings_path(),tmp_path/'chosen-library',tmp_path/'chosen-output')
    calls=[];monkeypatch.setattr(desktop,'main',lambda **kwargs:calls.append(kwargs))
    desktop.launch()
    assert calls[0]['data_root']==str((tmp_path/'chosen-library').resolve())
    assert calls[0]['export_root']==str((tmp_path/'chosen-output').resolve())


def test_single_root_copies_library_and_saves_layout(tmp_path,make_zip):
    from conftest import conversation
    source=tmp_path/'old';root=tmp_path/'all-data';config=tmp_path/'preferences.json'
    with Store(source) as store:store.import_zip(make_zip([conversation()]),'account')
    result=run_operation('configure_root',source,{'storage_root':root,'settings_path':config})
    assert result['copied'] and (source/'bridge.sqlite3').is_file()
    assert all((root/name).is_dir() for name in ('资料库','浏览器备份','接续目录','完整备份'))
    assert load(config)['storage_root']==str(root.resolve())
    assert load(config)['export_root']==str(root.resolve())
    with Store(root/'资料库') as store:assert store.verify_archives()[0]['ok']


def test_existing_legacy_root_can_be_organized_without_recursive_copy(tmp_path,make_zip):
    from conftest import conversation
    root=tmp_path/'all-data'
    with Store(root) as store:store.import_zip(make_zip([conversation()]),'account')
    (root/'identity.md').write_text('keep',encoding='utf-8')
    result=run_operation('configure_root',root,{'storage_root':root})
    assert result['copied'] and (root/'bridge.sqlite3').exists()
    assert (root/'资料库/identity.md').read_text()=='keep'
    assert not (root/'资料库/资料库').exists()
    with Store(root/'资料库') as store:assert store.verify_archives()[0]['ok']


def test_switch_unified_root_and_next_launch(tmp_path,monkeypatch):
    import memory_bridge.desktop as desktop
    from memory_bridge.settings import user_settings_path
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'appdata'));monkeypatch.setattr(desktop.sys,'argv',['MemoryBridge.exe'])
    root=tmp_path/'root'
    with Store(root/'资料库'):pass
    run_operation('configure_root',tmp_path/'old',{'storage_root':root,'copy_existing':False,'settings_path':user_settings_path()})
    calls=[];monkeypatch.setattr(desktop,'main',lambda **kwargs:calls.append(kwargs));desktop.launch()
    assert calls[0]['data_root']==str(root/'资料库') and calls[0]['export_root']==str(root)
