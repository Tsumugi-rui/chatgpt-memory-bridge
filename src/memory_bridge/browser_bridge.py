"""Loopback-only command queue for an explicitly paired browser extension."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from threading import Thread, RLock
from urllib.parse import urlparse, parse_qs
import hashlib
import json
import re
import secrets
import time
import uuid
import os
import sqlite3

from .common import digest, file_hash


CAPTURE_COMMANDS = ('capture', 'capture_from_start')


def resume_requires_restart(library, source, url, receipt, draft_started=None):
    """Check the archive that backed the browser's last acknowledged checkpoint."""
    if not library:
        return bool(receipt)
    root = Path(library)
    pk = digest(['chatgpt', source, url.rsplit('/', 1)[-1]])
    try:
        with sqlite3.connect((root/'bridge.sqlite3').resolve().as_uri()+'?mode=ro', uri=True) as db:
            reset_all = db.execute("SELECT value FROM metadata WHERE key='capture_reset_at'").fetchone()
            if reset_all and (draft_started or not receipt):
                newer = db.execute('SELECT julianday(?) > julianday(?)',(draft_started,reset_all[0])).fetchone()[0]
                if not newer:
                    return True
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_resets'").fetchone():
                reset = db.execute('''SELECT 1 FROM conversation_resets WHERE pk=?
                    AND (julianday(?) IS NULL OR julianday(?)<=julianday(deleted_at))''',
                    (pk,draft_started,draft_started)).fetchone()
                if reset:
                    return True
            trashed = db.execute('''SELECT julianday(t.deleted_at) AS deleted, julianday(?) AS draft FROM conversations c
                LEFT JOIN conversation_continuations l ON l.conversation_pk=c.pk
                JOIN conversation_trash t ON t.root_pk=COALESCE(l.root_pk,c.pk) WHERE c.pk=?''',(draft_started,pk)).fetchone()
            if trashed and (trashed[1] is None or trashed[0] is None or trashed[1] <= trashed[0]):
                return True
            if not receipt:
                return False
            delivery = receipt.get('task')
            if not isinstance(delivery,str) or not re.fullmatch(r'[0-9a-f]{32}',delivery):
                return True
            match = db.execute('''SELECT i.sha256 FROM imports i JOIN observations o ON o.import_id=i.id
                WHERE o.conversation_pk=? AND i.status IN ('complete','partial')
                AND json_extract(i.stats,'$.browser_coverage.delivery_id')=?
                AND json_extract(i.stats,'$.browser_coverage.snapshot_count')=?
                AND json_extract(i.stats,'$.browser_coverage.standalone')=1 LIMIT 1''',
                (pk,delivery,receipt.get('snapshots'))).fetchone()
        if not match or not re.fullmatch(r'[0-9a-f]{64}',match[0]):
            return True
        archive = root/'archives'/(match[0]+'.zip')
        return not archive.is_file() or file_hash(archive) != match[0]
    except (OSError, sqlite3.Error):
        return bool(receipt)


def installed_origins():
    """Trust only IDs of this extension registered in local browser profiles."""
    found=set();home=Path(os.environ.get('LOCALAPPDATA') or Path.home())
    for browser in ('Microsoft/Edge/User Data','Google/Chrome/User Data'):
        root=home/browser
        if not root.is_dir():continue
        for profile in root.iterdir():
            if not profile.is_dir() or not (profile.name=='Default' or profile.name.startswith('Profile ')):continue
            for name in ('Preferences','Secure Preferences'):
                try:
                    values=json.loads((profile/name).read_text(encoding='utf-8')).get('extensions',{}).get('settings',{})
                    for identity,item in values.items():
                        path=Path(item.get('path',''))
                        if not path.is_absolute():path=profile/'Extensions'/path
                        try:manifest=item.get('manifest') or json.loads((path/'manifest.json').read_text(encoding='utf-8'))
                        except (OSError,ValueError):continue
                        if re.fullmatch('[a-p]{32}',identity) and str(manifest.get('name','')).startswith('ChatGPT Memory Bridge'):
                            found.add('chrome-extension://'+identity)
                except (OSError,ValueError,TypeError,AttributeError):continue
    return found


class BrowserBridge:
    def __init__(self, output, port=18765, trusted_origins=None):
        self.output=Path(output).resolve();self.events=Queue();self.lock=RLock()
        self.cache_reset_at=None
        self.code=str(secrets.randbelow(900000)+100000);self.token=secrets.token_urlsafe(32)
        self.origin=None;self.connected=0;self.tasks={};self.uploads={}
        self.discover_origins=trusted_origins is None
        self.trusted_origins=installed_origins() if self.discover_origins else set(trusted_origins)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_OPTIONS(self):self.handle_request(True)
            def do_GET(self):self.handle_request()
            def do_POST(self):self.handle_request()
            def handle_request(self,options=False):
                origin=self.headers.get('Origin') or self.headers.get('X-Bridge-Origin','')
                if not re.fullmatch(r'chrome-extension://[a-p]{32}',origin):
                    self.send_error(403);return
                if self.headers.get('Host')!=f'127.0.0.1:{owner.server.server_port}':
                    self.send_error(403);return
                if options:
                    self.send_response(204);self.send_header('Access-Control-Allow-Origin',origin)
                    self.send_header('Access-Control-Allow-Headers','Authorization,Content-Type,X-Bridge-Origin')
                    self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS');self.end_headers();return
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<=length<=1024*1024:raise ValueError('request_too_large')
                    body=self.rfile.read(length);route=urlparse(self.path)
                    with owner.lock:
                        if route.path in ('/pair','/connect') and self.command=='POST':
                            if route.path=='/pair' and json.loads(body).get('code')!=owner.code:raise ValueError('pairing_code_mismatch')
                            if route.path=='/connect' and origin not in owner.trusted_origins:
                                if owner.discover_origins:
                                    registered=installed_origins()
                                    if origin in registered:
                                        owner.trusted_origins.update(registered)
                                if origin not in owner.trusted_origins:
                                    raise ValueError('此插件尚未识别，请使用备用配对码连接一次')
                            if owner.origin and owner.origin!=origin:raise ValueError('already_paired')
                            owner.origin=origin;owner.trusted_origins.add(origin);owner.connected=time.time();result={'token':owner.token}
                            owner.events.put(('connected',{}))
                        else:
                            if origin!=owner.origin or self.headers.get('Authorization')!='Bearer '+owner.token:
                                self.send_error(403);return
                            owner.connected=time.time()
                            if route.path=='/commands' and self.command=='GET':
                                # Leasing prevents a restarted worker from duplicating a long capture.
                                active=any(t['status'] in ('dispatched','running') for t in owner.tasks.values())
                                command=None if active else next((t for t in owner.tasks.values() if t['status']=='queued'),None)
                                if command:command['status']='dispatched'
                                result={'command':command,'cache_reset_at':owner.cache_reset_at}
                            elif route.path=='/task' and self.command=='GET':
                                task=owner.tasks[parse_qs(route.query)['id'][0]]
                                result={'status':task['status']}
                            elif route.path=='/restore-file' and self.command=='GET':
                                from .batch_restore import read_upload_chunk
                                query=parse_qs(route.query);task=owner.tasks[query['id'][0]]
                                if task['kind']!='restore' or task['status']!='running':
                                    raise ValueError('导入任务已停止')
                                result=read_upload_chunk(task,query)
                            elif route.path=='/result' and self.command=='POST':
                                data=json.loads(body);task=owner.tasks[data['id']]
                                if data.get('status') not in ('running','completed','error'):raise ValueError('invalid_task_status')
                                if task['status'] in ('dispatched','running'):
                                    if task['kind']=='restore' and data['status']=='error':
                                        for queued in owner.tasks.values():
                                            if queued['kind']=='restore' and queued['status']=='queued':
                                                queued['status']='cancelled'
                                    if task['kind'] in CAPTURE_COMMANDS:
                                        if data['status']=='completed' and not data.get('files'):
                                            data.update(status='error',error='插件未送达 ZIP，已停止本轮采集。')
                                        if data['status']=='error' or (data['status']=='completed' and data.get('capture_status')!='captured'):
                                            held=[]
                                            for queued in owner.tasks.values():
                                                if queued['kind'] in CAPTURE_COMMANDS and queued['status']=='queued':
                                                    queued['status']='cancelled';held.append(queued['id'])
                                            data['held_tasks']=held
                                    task['status']=data['status'];task['result']=data
                                    owner.events.put(('result',data))
                                result={'ok':True}
                            elif route.path=='/resume' and self.command=='POST':
                                data=json.loads(body);task=owner.tasks[data['id']]
                                receipt=data.get('receipt')
                                if task['kind'] not in CAPTURE_COMMANDS or task['status']!='running' or (receipt is not None and not isinstance(receipt,dict)):
                                    raise ValueError('invalid_resume_request')
                                # Hash large archives after releasing the queue lock.
                                draft_started=data.get('draft_started')
                                if not isinstance(draft_started,str):draft_started=None
                                resume_check=(task.get('library'),task.get('source','old-account'),task['url'],receipt,draft_started)
                                result=None
                            elif route.path=='/upload' and self.command=='POST':
                                query=parse_qs(route.query);task=owner.tasks[query['task'][0]]
                                if task['kind'] not in CAPTURE_COMMANDS or task['status'] not in ('dispatched','running'):raise ValueError('inactive_capture')
                                part=int(query['part'][0]);offset=int(query['offset'][0]);name=query['name'][0]
                                if (not 1<=part<=10000 or not name.endswith('.zip') or len(name.encode('utf-16-le'))>440
                                    or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]',name) or name.startswith('.') or name.rstrip('. ')!=name):
                                    raise ValueError('invalid_upload_name')
                                key=(task['id'],part);entry=owner.uploads.get(key)
                                if entry is None:
                                    task['folder'].mkdir(parents=True,exist_ok=True)
                                    path=task['folder']/name
                                    if path.exists() or path.with_suffix('.zip.part').exists():raise ValueError('upload_file_exists')
                                    entry={'path':path,'size':0,'sha':hashlib.sha256()};owner.uploads[key]=entry
                                if offset!=entry['size']:raise ValueError('upload_offset_mismatch')
                                with entry['path'].with_suffix('.zip.part').open('ab') as stream:stream.write(body)
                                entry['size']+=len(body);entry['sha'].update(body)
                                if query.get('final')==['1']:
                                    if entry['sha'].hexdigest()!=query['sha256'][0]:raise ValueError('upload_checksum_mismatch')
                                    entry['path'].with_suffix('.zip.part').rename(entry['path'])
                                    owner.events.put(('file',{'id':task['id'],'path':str(entry['path'])}))
                                result={'ok':True,'bytes':entry['size']}
                            else:raise ValueError('unknown_request')
                    if result is None:
                        result={'restart':resume_requires_restart(*resume_check)}
                    self.send_response(200);self.send_header('Access-Control-Allow-Origin',origin)
                    self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.end_headers()
                    self.wfile.write(json.dumps(result,default=str).encode())
                except (ValueError,KeyError,OSError,TypeError) as exc:
                    self.send_response(400);self.send_header('Access-Control-Allow-Origin',origin);self.end_headers()
                    self.wfile.write(json.dumps({'error':str(exc)}).encode())
        self.server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
        self.server.daemon_threads=True
        Thread(target=self.server.serve_forever,daemon=True).start()

    def submit(self,kind,**payload):
        if kind not in ('list','restore',*CAPTURE_COMMANDS):raise ValueError('unknown_browser_command')
        if kind in CAPTURE_COMMANDS and not re.fullmatch(r'https://chatgpt\.com/c/[A-Za-z0-9-]+',payload.get('url','')):
            raise ValueError('invalid_conversation_url')
        with self.lock:
            task={'id':uuid.uuid4().hex,'kind':kind,'status':'queued',**payload}
            if kind in CAPTURE_COMMANDS:task['folder']=self.output/'浏览器备份'/task['id']
            self.tasks[task['id']]=task
            return task['id']

    def close(self):
        self.server.shutdown();self.server.server_close()

    def cancel(self,kind=None):
        with self.lock:
            for task in self.tasks.values():
                if task['status'] in ('queued','dispatched','running') and (kind is None or task['kind']==kind):
                    task['status']='cancelled'
                    if task['kind']=='restore':
                        previous=task.get('result',{})
                        self.events.put(('result',{'id':task['id'],'status':'cancelled',
                            'sent_files':previous.get('sent_files',0),'chat_url':previous.get('chat_url',''),
                            'final_sent':previous.get('final_sent',False),'phase':previous.get('phase','files'),
                            'message':'上传已中断，后续队列已停止。已发送内容请在对应聊天核对。'}))
