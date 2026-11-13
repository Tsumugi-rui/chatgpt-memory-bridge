import copy
import hashlib
import json
import zipfile

import pytest

from memory_bridge.store import Store
from memory_bridge.export import export_package, verify_package
from memory_bridge.gui_service import run_operation
from memory_bridge.fidelity import restore_library
from memory_bridge.archive import preserve_zip, Limits
from memory_bridge.common import now


def make_capture(path, *, packed=True, messages=None, incomplete=False, unavailable=False, damage=None,
                 conversation_id='synthetic-c1', title='日语学习・数学', delivery_id=None, from_start=False):
    image = b'\x89PNG\r\n\x1a\nsynthetic-image'
    sha = hashlib.sha256(image).hexdigest()
    asset = {'kind': 'image', 'url': 'https://files.oaiusercontent.com/synthetic.png',
             'name': '合成教材图片', 'status': 'saved', 'path': f'assets/{sha}.png',
             'bytes': len(image), 'sha256': sha}
    messages = copy.deepcopy(messages or [
        {'id': 'u1', 'role': 'user', 'text': '我偏好：用中文解释数学', 'html': '<p>我偏好：用中文解释数学</p>',
         'assets': [asset], 'future': {'preserve': True}},
        {'id': 'a1', 'role': 'assistant', 'text': 'こんにちは。日本語の練習', 'html': '<p>こんにちは。</p>',
         'versions': [{'text': '旧回复', 'html': '<p>旧回复</p>'}]},
        {'id': 'u2', 'role': 'user', 'text': '继续', 'html': '<p>继续</p>'},
        {'id': 'u3', 'role': 'user', 'text': '继续', 'html': '<p>继续</p>'},
        {'id': 'a2', 'role': 'assistant', 'text': '接着学习', 'html': '<p>接着学习</p>'},
    ])
    segments = [messages[:2], messages[2:]] if incomplete else [messages]
    view = {'title': title, 'url': 'https://chatgpt.com/c/'+conversation_id, 'segments': segments, 'future': 123}
    job = {'version': 2 if packed else 1, 'url': view['url'], 'title': view['title'],
           'snapshots': [{'index': 0, 'messages': list(range(len(messages))) if packed else messages,
                          'top': 0, 'times': [{'text': '10月17日 21:28'}]}]}
    raw = {'format': 'memory-bridge-reference-v2', 'job': job, 'dictionary': messages} if packed else job
    inventory = [asset]
    if unavailable:
        inventory.append({'kind': 'button', 'name': '下载 missing.zip', 'url': '', 'status': 'unavailable', 'error': 'expired'})
    report = {'page_boundary_and_overlap_checks_passed': not incomplete, 'messages': len(messages),
              'segments': len(segments), 'gaps': len(segments)-1, 'bounds': {'top': True, 'bottom': not incomplete},
              'attachments': inventory, 'unknown_report_field': 'keep'}
    if delivery_id:
        report.update(delivery_id=delivery_id, from_start=from_start, incremental={'enabled':False})
    records = {'对话全文.json': view, '原始片段.json': raw, '完整性报告.json': report, '附件清单.json': inventory}
    if damage == 'reference':
        raw['job']['snapshots'][0]['messages'].append(999)
    if damage == 'message':
        messages[0]['text'] = ['not a string']
    if damage == 'asset':
        asset['sha256'] = 'f' * 64
    if damage == 'count':
        report['messages'] += 1
    if damage == 'unlisted':
        inventory.clear()
    data = {name: json.dumps(value, ensure_ascii=False).encode('utf-8') for name, value in records.items()}
    data[asset['path']] = image
    data['打开阅读.html'] = b'<html>synthetic only</html>'
    manifest = {'version': 1, 'files': [{'path': name, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()} for name, content in data.items()]}
    if damage == 'checksum':
        data['对话全文.json'] += b' '
    if damage == 'traversal':
        manifest['files'][0]['path'] = '../escape.json'
    data['manifest.json'] = json.dumps(manifest, ensure_ascii=False).encode('utf-8')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in data.items():
            archive.writestr(name, content)
    return data


@pytest.mark.parametrize('packed', [False, True])
def test_browser_import_preserves_messages_raw_html_repetitions_assets_and_original_bytes(tmp_path, packed):
    path = tmp_path / 'browser.zip'
    original = make_capture(path, packed=packed)
    with Store(tmp_path/'lib') as store:
        result = store.import_zip(path, 'same-account')
        assert result['status'] == 'complete', result
        assert result['stats']['new_messages'] == 5
        assert result['stats']['browser_coverage']['scope'] == 'displayed_branch_only'
        assert len(store.search('继续')) == 2
        assert store.search('日本語')
        assert store.import_zip(path, 'same-account')['already_imported']
        current = list(store.conversation_records())[0]
        raw = current['versions'][0]['raw']
        assert raw['_memory_bridge_browser']['node_order'] == ['u1','a1','u2','u3','a2']
        assert raw['mapping']['u3']['parent'] == 'u2'
        assert raw['mapping']['u1']['message']['browser_message']['future']['preserve']
        assert raw['mapping']['a1']['message']['browser_message']['versions'][0]['text'] == '旧回复'
        assert raw['_memory_bridge_browser']['report']['unknown_report_field'] == 'keep'
        backup = store.root/'archives'/f"{result['sha256']}.zip"
        assert backup.read_bytes() == path.read_bytes()
        destination = tmp_path/'migration'
        export_package(store, destination)
        assert verify_package(destination)['ok']
        refs = [json.loads(line) for line in (destination/'attachment_references.jsonl').read_text(encoding='utf-8').splitlines()]
        assert len(refs) == 1 and refs[0]['status'] == 'matched'
        target = refs[0]['candidates'][0]['content_path']
        assert (destination/target).read_bytes() == next(data for name,data in original.items() if name.startswith('assets/'))
        readable = next((destination/'readable').glob('*.md')).read_text(encoding='utf-8')
        assert '![归档图片](../files/' in readable
        assert readable.index('### "u1"') < readable.index('### "a1"') < readable.index('### "u2"') < readable.index('### "u3"') < readable.index('### "a2"')
        assert '浏览器插件采集覆盖' in (destination/'migration_report.md').read_text(encoding='utf-8')
        assert 'displayed_branch_only' in (destination/'browser_coverage.json').read_text(encoding='utf-8')


@pytest.mark.parametrize('incomplete,unavailable,damage', [(True,False,None),(False,True,None),(False,False,'count'),(False,False,'unlisted')])
def test_incomplete_browser_capture_remains_partial_after_import_and_export(tmp_path, incomplete, unavailable, damage):
    path=tmp_path/'partial.zip';make_capture(path,incomplete=incomplete,unavailable=unavailable,damage=damage)
    result=run_operation('migrate',tmp_path/'lib',{'zip':path,'source':'account','destination':tmp_path/'migration'})
    assert result['import']['status']=='partial'
    assert result['verification']['ok']
    assert 'partial' in (tmp_path/'migration/migration_report.md').read_text(encoding='utf-8')
    if incomplete:
        with Store(tmp_path/'lib') as store:
            raw=list(store.conversation_records())[0]['versions'][0]['raw']
            assert raw['mapping']['u2']['parent'] is None
            assert raw['current_node'] is None


@pytest.mark.parametrize('damage', ['checksum','reference','message','asset','traversal'])
def test_damaged_browser_zip_is_preserved_but_never_indexed_as_success(tmp_path, damage):
    path=tmp_path/'bad.zip';make_capture(path,damage=damage)
    with Store(tmp_path/'lib') as store:
        result=store.import_zip(path)
        assert result['status']=='rejected', result
        assert not list(store.conversation_records())
        assert store.verify_archives()[0]['ok']


def test_browser_incremental_update_keeps_disappeared_messages_and_old_versions(tmp_path):
    one=tmp_path/'one.zip';make_capture(one)
    two=tmp_path/'two.zip';make_capture(two,messages=[{'id':'u1','role':'user','text':'我偏好：简短中文回答','html':'<p>新版</p>'},
                                                    {'id':'a3','role':'assistant','text':'新内容','html':'<p>新内容</p>'}])
    with Store(tmp_path/'lib') as store:
        store.import_zip(one,'account')
        update=store.import_zip(two,'account')
        assert update['stats']['new_conversations']==0
        assert update['stats']['changed_messages']==1
        assert update['stats']['new_messages']==1
        assert update['stats']['not_observed_messages']==4
        assert store.db.execute('SELECT count(*) FROM messages').fetchone()[0]==6
        assert store.db.execute('SELECT count(*) FROM message_versions').fetchone()[0]==7


def test_browser_full_workflow_migrate_restore_and_search_without_official_zip(tmp_path):
    archive=tmp_path/'capture.zip';make_capture(archive)
    result=run_operation('migrate',tmp_path/'lib',{'zip':archive,'source':'personal','destination':tmp_path/'migration'})
    assert result['verification']['ok'] and result['extraction']['candidates']>=1
    assert run_operation('restore',tmp_path/'lib',{'package':tmp_path/'migration','destination':tmp_path/'restored',
        'settings_path':tmp_path/'settings.json','export_root':tmp_path/'future-exports'})['restored']
    assert json.loads((tmp_path/'settings.json').read_text(encoding='utf-8'))['data_root']==str((tmp_path/'restored').resolve())
    with Store(tmp_path/'restored') as store:
        assert len(store.search('继续'))==2
        assert store.verify_archives()[0]['ok']
        assert store.verify_versions()['ok']


def test_plugin_package_previously_archived_by_old_software_is_automatically_reindexed(tmp_path):
    path=tmp_path/'legacy.zip';make_capture(path)
    with Store(tmp_path/'lib') as store:
        checksum,_=preserve_zip(path,store.root,Limits.personal())
        with store.db:
            iid=store.db.execute("INSERT INTO imports(sha256,source,platform,imported_at,status,stats) VALUES(?,?,'chatgpt',?,'partial',?)",(checksum,'account',now(),json.dumps({'parsed_records':0}))).lastrowid
        result=store.import_zip(path,'account')
        assert result['id']==iid and result['status']=='complete'
        assert result['stats']['new_messages']==5
        assert store.db.execute('SELECT count(*) FROM imports').fetchone()[0]==1
        assert store.import_zip(path,'account')['already_imported']


def test_actual_plugin_incremental_exports_import_as_one_complete_message_union(tmp_path):
    import os,subprocess
    from pathlib import Path
    env={**os.environ,'MB_INCREMENTAL_DIR':str(tmp_path)}
    result=subprocess.run(['node','--test','--test-name-pattern=completed capture resumes','extension/tests/collector.test.cjs'],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,timeout=30)
    assert result.returncode==0,result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
    with Store(tmp_path/'library') as store:
        one=store.import_zip(tmp_path/'old.zip','account');assert one['stats']['new_messages']==50
        two=store.import_zip(tmp_path/'delta.zip','account');assert two['status']!='rejected'
        assert len(list(store.conversation_records()))==1
        assert store.db.execute('SELECT count(*) FROM messages').fetchone()[0]==70
        assert store.search('ONLY_OLD_HEAD') and store.search('新增 69')
        assert store.import_zip(tmp_path/'delta.zip','account')['already_imported']
        assert all(x['ok'] for x in store.verify_archives()) and store.verify_versions()['ok']


def test_unexported_plugin_checkpoint_resumes_and_imports_old_and_new_messages_together(tmp_path):
    import os,subprocess
    from pathlib import Path
    env={**os.environ,'MB_RESUME_DIR':str(tmp_path)}
    result=subprocess.run(['node','--test','--test-name-pattern=paused checkpoint without any ZIP','extension/tests/collector.test.cjs'],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,timeout=30)
    assert result.returncode==0,result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
    with Store(tmp_path/'library') as store:
        archive=tmp_path/'resumed-full.zip'
        imported=store.import_zip(archive,'account')
        assert imported['status']=='complete'
        assert imported['stats']['new_messages']==90
        assert len(list(store.conversation_records()))==1
        assert store.search('UNEXPORTED_OLD_HEAD') and store.search('新增 89')
        assert store.import_zip(archive,'account')['already_imported']
        assert all(item['ok'] for item in store.verify_archives())
        assert store.verify_versions()['ok']


def test_resume_requires_the_exact_saved_archive_and_fresh_capture_restores_trash(tmp_path):
    from memory_bridge.browser_bridge import resume_requires_restart
    from memory_bridge.catalog import library_rows

    library=tmp_path/'lib'
    original=tmp_path/'old.zip'
    make_capture(original,delivery_id='a'*32)
    receipt={'task':'a'*32,'snapshots':1}
    url='https://chatgpt.com/c/synthetic-c1'
    with Store(library) as store:
        first=store.import_zip(original,'account')
        pk=library_rows(store)[0]['pk']
        assert not resume_requires_restart(library,'account',url,receipt)
        assert resume_requires_restart(library,'different',url,receipt)
        assert resume_requires_restart(library,'account',url,{**receipt,'snapshots':2})
        archived=library/'archives'/(first['sha256']+'.zip')
        archived.unlink()
        assert resume_requires_restart(library,'account',url,receipt)
        archived.write_bytes(original.read_bytes())
        store.trash_conversation(pk,confirmed=True)
        assert resume_requires_restart(library,'account',url,None)
        assert not resume_requires_restart(library,'account',url,None,'2099-01-01T00:00:00Z')
        partial=tmp_path/'partial.zip'
        make_capture(partial,incomplete=True,delivery_id='b'*32,from_start=True)
        store.import_zip(partial,'account',capture_task='b'*32)
        assert not library_rows(store)
        complete=tmp_path/'fresh.zip'
        make_capture(complete,delivery_id='c'*32,from_start=True,unavailable=True)
        result=store.import_zip(complete,'account',capture_task='c'*32)
        assert result['stats']['restored_after_recapture']
        assert [row['pk'] for row in library_rows(store)]==[pk]
        assert all(archive['ok'] for archive in store.verify_archives())
