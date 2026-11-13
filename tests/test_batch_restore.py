from pathlib import Path
import base64
import json

import pytest
from conftest import conversation
from memory_bridge.batch_restore import prepare_batch, read_upload_chunk
from memory_bridge.store import Store
from memory_bridge.gui_service import run_operation


def test_batch_keeps_conversations_and_prompts_separate(tmp_path, make_zip):
    archive=make_zip([conversation(text='日语学习记录',cid='japanese'),
                      conversation(text='数学学习记录',cid='math')])
    library=tmp_path/'library'
    with Store(library) as store:
        store.import_zip(archive,'old')
        pks=[row[0] for row in store.db.execute('SELECT pk FROM conversations ORDER BY external_id')]
    result=run_operation('prepare_batch_restore',library,{'destination':tmp_path/'batch','selected':pks})
    assert len(result['jobs'])==2
    for job in result['jobs']:
        assert job['pk'] in job['prompt']
        assert all(other not in job['prompt'] for other in pks if other!=job['pk'])
        directory=Path(job['directory'])
        raw=json.loads((directory/'对话原始记录.jsonl').read_text(encoding='utf-8').strip())
        assert raw['pk']==job['pk']
        assert (directory/'网页接续提示词.txt').is_file()
        for number,item in enumerate(job['files']):
            chunk=read_upload_chunk(job,{'file':[str(number)],'offset':['0']})
            assert base64.b64decode(chunk['base64'])==(directory/item['path']).read_bytes()
        item=job['files'][0]
        (directory/item['path']).write_text('tampered')
        with pytest.raises(ValueError,match='变化|校验'):
            read_upload_chunk(job,{'file':['0']})
    assert Path(archive).is_file()


def test_upload_chunks_and_path_boundary(tmp_path):
    root=tmp_path/'upload';root.mkdir()
    outside=tmp_path/'other.txt';outside.write_text('private')
    with pytest.raises(ValueError,match='位置'):
        read_upload_chunk({'directory':str(root),'files':[{'path':'../other.txt','size':7}]},{'file':['0']})


def test_resume_retains_completed_records_and_original_chat(tmp_path):
    from memory_bridge.batch_restore import resume_batch
    jobs=[{'pk':'a','files':[{},{}]},{'pk':'b','files':[{}]}]
    (tmp_path/'分对话导入计划.json').write_text(json.dumps({'jobs':jobs}),encoding='utf-8')
    report=tmp_path/'导入结果.json'
    rows=[{'pk':'a','status':'error','sent_files':2,'chat_url':'https://chatgpt.com/c/original','final_sent':False},
          {'pk':'b','status':'completed','sent_files':1}]
    report.write_text(json.dumps(rows),encoding='utf-8')
    result=resume_batch(report)
    assert len(result['jobs'])==1 and len(result['records'])==2
    assert result['jobs'][0]['resume_url']=='https://chatgpt.com/c/original'
    assert result['jobs'][0]['resume_sent_files']==2


def test_resume_prefers_original_chat_over_newer_empty_attempt(tmp_path):
    from memory_bridge.batch_restore import choose_resume_report
    old=tmp_path/'old';old.mkdir()
    new=tmp_path/'new';new.mkdir()
    original=old/'导入结果.json';empty=new/'导入结果.json'
    original.write_text(json.dumps([{'pk':'a','chat_url':'https://chatgpt.com/c/original','sent_files':30}]),encoding='utf-8')
    empty.write_text(json.dumps([{'pk':'a','chat_url':'https://chatgpt.com/','sent_files':0}]),encoding='utf-8')
    assert choose_resume_report(tmp_path,['a'],empty)==original
    assert choose_resume_report(tmp_path,['b'],empty)==empty
