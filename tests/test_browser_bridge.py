import hashlib
import json
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

import pytest
from memory_bridge.browser_bridge import BrowserBridge


@pytest.fixture
def bridge(tmp_path):
    bridge=BrowserBridge(tmp_path,port=0)
    yield bridge
    bridge.close()


def request(bridge,path,body=None,token=None,origin='chrome-extension://'+'a'*32):
    headers={'X-Bridge-Origin':origin,'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    data=body if isinstance(body,bytes) else json.dumps(body).encode() if body is not None else None
    with urlopen(Request(f'http://127.0.0.1:{bridge.server.server_port}'+path,data=data,headers=headers),timeout=5) as response:
        return json.load(response)


def test_pair_queue_lease_and_list(bridge):
    with pytest.raises(HTTPError):request(bridge,'/pair',{'code':'wrong'})
    token=request(bridge,'/pair',{'code':bridge.code})['token']
    job=bridge.submit('list')
    command=request(bridge,'/commands',token=token)['command']
    assert command['id']==job
    assert request(bridge,'/commands',token=token)['command'] is None
    request(bridge,'/result',{'id':job,'status':'completed','items':[{'title':'日本語','url':'https://chatgpt.com/c/test'}]},token)
    assert bridge.tasks[job]['result']['items'][0]['title']=='日本語'


def test_restore_failure_holds_remaining_conversations(bridge):
    token=request(bridge,'/pair',{'code':bridge.code})['token']
    first=bridge.submit('restore',title='first',files=[],directory='unused')
    second=bridge.submit('restore',title='second',files=[],directory='unused')
    assert request(bridge,'/commands',token=token)['command']['id']==first
    request(bridge,'/result',{'id':first,'status':'error','error':'upload quota'},token)
    assert bridge.tasks[second]['status']=='cancelled'
    assert request(bridge,'/commands',token=token)['command'] is None


@pytest.mark.parametrize('result',[
    {'status':'completed','capture_status':'partial','files':['partial.zip']},
    {'status':'error','error':'正文未加载'},
    {'status':'completed','capture_status':'captured','files':[]},
])
def test_incomplete_capture_stops_queue(bridge,result):
    token=request(bridge,'/pair',{'code':bridge.code})['token']
    japanese=bridge.submit('capture',url='https://chatgpt.com/c/japanese')
    next_chat=bridge.submit('capture',url='https://chatgpt.com/c/next')
    assert request(bridge,'/commands',token=token)['command']['id']==japanese
    assert request(bridge,'/commands',token=token)['command'] is None
    request(bridge,'/result',{'id':japanese,**result},token)
    assert bridge.tasks[japanese]['result']['held_tasks']==[next_chat]
    assert request(bridge,'/commands',token=token)['command'] is None
    retry=bridge.submit('capture_from_start',url='https://chatgpt.com/c/japanese')
    assert request(bridge,'/commands',token=token)['command']['id']==retry


def test_upload_chunks_preserve_bytes_and_reject_replay(bridge):
    token=request(bridge,'/pair',{'code':bridge.code})['token']
    job=bridge.submit('capture',url='https://chatgpt.com/c/test')
    request(bridge,'/commands',token=token)
    data=b'PK\x03\x04synthetic'+bytes(range(256));sha=hashlib.sha256(data).hexdigest()
    path='/upload?task='+job+'&part=1&name=test.zip&offset=0'
    request(bridge,path,data[:10],token)
    with pytest.raises(HTTPError):request(bridge,path,data[:10],token)
    request(bridge,'/upload?task='+job+'&part=1&name=test.zip&offset=10&final=1&sha256='+sha,data[10:],token)
    target=bridge.tasks[job]['folder']/'test.zip'
    assert target.read_bytes()==data
    assert not target.with_suffix('.zip.part').exists()
    request(bridge,'/result',{'id':job,'status':'completed'},token)
    with pytest.raises(HTTPError):request(bridge,path,b'bad',token)


def test_other_origins_and_unpaired_requests_rejected(bridge):
    with pytest.raises(HTTPError):request(bridge,'/pair',{'code':bridge.code},origin='https://chatgpt.com')
    with pytest.raises(HTTPError):request(bridge,'/commands',token='wrong')
    with pytest.raises(ValueError):bridge.submit('capture',url='https://example.com/c/test')


def test_descriptive_unicode_zip_names_transfer_and_paths_are_rejected(bridge):
    token=request(bridge,'/pair',{'code':bridge.code})['token']
    job=bridge.submit('capture',url='https://chatgpt.com/c/test')
    request(bridge,'/commands',token=token)
    data=b'synthetic ZIP';name='ChatGPT__日语学习_日本語__2026-10-29_22-30-00-123_UTC+0800__test.zip'
    for invalid in ('../escape.zip','C:\\escape.zip','bad:name.zip'):
        with pytest.raises(HTTPError):request(bridge,'/upload?'+urlencode({'task':job,'part':1,'name':invalid,'offset':0}),data,token)
    path='/upload?'+urlencode({'task':job,'part':1,'name':name,'offset':0,'final':1,'sha256':hashlib.sha256(data).hexdigest()})
    request(bridge,path,data,token)
    assert (bridge.tasks[job]['folder']/name).read_bytes()==data


def test_recognized_extension_connects_without_code_and_other_ids_cannot_claim(tmp_path):
    origin='chrome-extension://'+'a'*32
    bridge=BrowserBridge(tmp_path,port=0,trusted_origins={origin})
    try:
        with pytest.raises(HTTPError):request(bridge,'/connect',{},origin='chrome-extension://'+'b'*32)
        token=request(bridge,'/connect',{})['token']
        assert token and bridge.origin==origin
        assert request(bridge,'/commands',token=token)['command'] is None
    finally:bridge.close()


def test_extension_registered_after_desktop_started_can_connect(tmp_path, monkeypatch):
    origin='chrome-extension://'+'a'*32
    registered=set()
    monkeypatch.setattr('memory_bridge.browser_bridge.installed_origins', lambda: set(registered))
    bridge=BrowserBridge(tmp_path,port=0)
    try:
        with pytest.raises(HTTPError):
            request(bridge,'/connect',{},origin=origin)
        registered.add(origin)
        assert request(bridge,'/connect',{},origin=origin)['token']
        assert bridge.origin==origin
    finally:bridge.close()


def test_interrupt_restore_keeps_capture_and_completed_tasks(bridge):
    finished=bridge.submit('restore',title='done')
    bridge.tasks[finished]['status']='completed'
    active=bridge.submit('restore',title='active')
    bridge.tasks[active].update(status='running',result={'sent_files':5,'chat_url':'https://chatgpt.com/c/synthetic'})
    queued=bridge.submit('restore',title='queued')
    capture=bridge.submit('capture',url='https://chatgpt.com/c/source')
    bridge.cancel(kind='restore')
    assert bridge.tasks[finished]['status']=='completed'
    assert bridge.tasks[active]['status']==bridge.tasks[queued]['status']=='cancelled'
    assert bridge.tasks[capture]['status']=='queued'
    event=bridge.events.get_nowait()[1]
    assert event['status']=='cancelled' and event['sent_files']==5
