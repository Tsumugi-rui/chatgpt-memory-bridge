import hashlib
import json
import zipfile

import pytest
from memory_bridge.common import BridgeError
from memory_bridge.gui_service import run_operation
from memory_bridge.store import Store
from memory_bridge.export import verify_package
from test_browser_package import make_capture


def split(path,tmp_path,size=800):
    data=path.read_bytes();chunks=[data[i:i+size] for i in range(0,len(data),size)]
    hashes=[hashlib.sha256(c).hexdigest() for c in chunks]
    set_id=hashlib.sha256(json.dumps([hashes,len(data)],separators=(',',':')).encode()).hexdigest()
    paths=[]
    for index,chunk in enumerate(chunks,1):
        info={'format':'memory-bridge-volume-v1','set_id':set_id,'index':index,'count':len(chunks),'total_bytes':len(data),'hashes':hashes}
        part=tmp_path/f'part-{index}.zip'
        with zipfile.ZipFile(part,'w') as z:
            z.writestr('bridge-volume.json',json.dumps(info));z.writestr('payload.bin',chunk)
        paths.append(part)
    return paths


def test_many_volumes_one_conversation_exact_archive_and_restorable_package(tmp_path):
    original=tmp_path/'one.zip';make_capture(original)
    parts=split(original,tmp_path)
    library=tmp_path/'lib';destination=tmp_path/'package'
    result=run_operation('migrate',library,{'zips':list(reversed(parts)),'source':'account','destination':destination})
    assert result['verification']['ok'] and len(result['imports'])==1
    with Store(library) as store:
        assert store.db.execute('SELECT count(*) FROM conversations').fetchone()[0]==1
        assert store.db.execute('SELECT count(*) FROM messages').fetchone()[0]==5
        assert len(store.imports())==len(parts)+1
        for path in [original,*parts]:
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            assert (library/'archives'/(sha+'.zip')).read_bytes()==path.read_bytes()
            assert (destination/'originals'/(sha+'.zip')).read_bytes()==path.read_bytes()
    again=run_operation('import',library,{'zips':parts,'source':'account'})
    assert again['import']['already_imported']
    assert verify_package(destination)['ok']
    restored=tmp_path/'restored';run_operation('restore',library,{'package':destination,'destination':restored})
    assert run_operation('verify_library',restored,{})['ok']


def test_missing_volume_does_not_make_partial_conversation(tmp_path):
    original=tmp_path/'one.zip';make_capture(original);parts=split(original,tmp_path)
    with pytest.raises(BridgeError,match='missing_volumes'):
        run_operation('import',tmp_path/'lib',{'zips':parts[:-1],'source':'account'})
    with Store(tmp_path/'lib') as store:assert not store.imports()


def test_corrupt_payload_refuses_import(tmp_path):
    original=tmp_path/'one.zip';make_capture(original);parts=split(original,tmp_path)
    with zipfile.ZipFile(parts[0]) as z:info=z.read('bridge-volume.json')
    with zipfile.ZipFile(parts[0],'w') as z:
        z.writestr('bridge-volume.json',info);z.writestr('payload.bin',b'corrupt')
    with pytest.raises(BridgeError,match='volume_checksum'):
        run_operation('import',tmp_path/'lib',{'zips':parts,'source':'account'})


@pytest.mark.parametrize('reverse',[False,True])
def test_legacy_multiple_zip_same_conversation_stitches_overlap_without_losing_replies(tmp_path,reverse):
    first=tmp_path/'a.zip';second=tmp_path/'b.zip'
    messages=[{'id':f'm{i}','role':'user' if i%2==0 else 'assistant','text':'继续' if i%2==0 else f'课文 {i}','html':f'<p>{i}</p>'} for i in range(7)]
    make_capture(first,messages=messages[:4])
    make_capture(second,messages=messages[2:])
    paths=[second,first] if reverse else [first,second]
    result=run_operation('import',tmp_path/'lib',{'zips':paths,'source':'same-account'})
    assert len(result['imports'])==2
    with Store(tmp_path/'lib') as store:
        assert len(list(store.conversation_records()))==1
        assert store.db.execute('SELECT count(*) FROM messages').fetchone()[0]==7
        assert len(store.search('继续'))==4
        row=store.db.execute("SELECT parent FROM messages WHERE node_id='m2'").fetchone()
        assert row[0]=='m1'
        assert store.verify_versions()['ok']


def test_permanent_deletion_clears_old_volume_archives_and_forces_recapture(tmp_path):
    from memory_bridge.browser_bridge import resume_requires_restart
    from memory_bridge.catalog import library_rows
    from memory_bridge.memories import Memories

    original=tmp_path/'one.zip';make_capture(original);parts=split(original,tmp_path)
    library=tmp_path/'lib'
    run_operation('import',library,{'zips':parts,'source':'account'})
    with Store(library) as store:
        pk=library_rows(store)[0]['pk']
        Memories(store).extract()
        assert Memories(store).list()
        with store.db:
            store.db.execute("UPDATE imports SET stats=json_remove(stats,'$.reassembled_sha256') WHERE platform='browser_volume'")
        store.trash_conversation(pk,confirmed=True)
    result=run_operation('delete_conversation',library,{'pk':pk,'confirmed':True,'members':[pk]})
    assert result=={'deleted':1,'pending_archives':0}
    with Store(library) as store:
        assert not list(store.conversation_records())
        assert not store.imports() and not list((library/'archives').glob('*.zip'))
        assert not store.search() and not Memories(store).list()
        assert not store.db.execute('SELECT 1 FROM messages_fts').fetchone()
        assert not store.db.execute('SELECT 1 FROM memory_versions').fetchone()
        assert not store.db.execute('PRAGMA foreign_key_check').fetchall()
    assert all(path.is_file() for path in parts)
    assert resume_requires_restart(library,'account','https://chatgpt.com/c/synthetic-c1',None)


def test_permanent_deletion_refuses_shared_archive_without_changing_either_conversation(tmp_path):
    from memory_bridge.catalog import library_rows

    archive=tmp_path/'shared.zip';make_capture(archive)
    library=tmp_path/'lib'
    with Store(library) as store:
        store.import_zip(archive,'A');store.import_zip(archive,'B')
        pk=library_rows(store)[0]['pk']
    with pytest.raises(BridgeError,match='共用原始 ZIP'):
        run_operation('delete_conversation',library,{'pk':pk,'confirmed':True})
    with Store(library) as store:
        assert len(library_rows(store))==2
        assert all(item['ok'] for item in store.verify_archives())
        both = [row['pk'] for row in library_rows(store)]
    result=run_operation('delete_conversation',library,{'pks':both,'confirmed':True})
    assert result=={'deleted':2,'pending_archives':0}
    assert not list((library/'archives').glob('*.zip'))
