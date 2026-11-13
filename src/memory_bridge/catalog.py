"""Discover nested backups as logical conversations without loading their message bodies."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import os
import zipfile
import ijson


def library_rows(store, *, trashed=False):
    rows = [dict(row) for row in store.db.execute('''SELECT c.pk,c.external_id,c.source,c.title,
        (SELECT count(*) FROM messages m WHERE m.conversation_pk=c.pk) AS messages,
        COALESCE(t.deleted_at,i.imported_at) AS date, COALESCE(l.root_pk,c.pk) AS root_pk, COALESCE(l.position,0) AS position
        FROM conversations c JOIN imports i ON i.id=c.last_import
        LEFT JOIN conversation_continuations l ON l.conversation_pk=c.pk
        LEFT JOIN conversation_trash t ON t.root_pk=COALESCE(l.root_pk,c.pk)
        WHERE (t.root_pk IS NOT NULL)=?
        ORDER BY COALESCE(l.position,0),c.pk''', (trashed,))]
    groups = {}
    for row in rows:
        groups.setdefault(row['root_pk'], []).append(row)
    combined = []
    for parts in groups.values():
        root = parts[0]
        combined.append({**root, 'parts': parts,
                         'messages': sum(part['messages'] for part in parts),
                         'date': max(part['date'] for part in parts)})
    return sorted(combined, key=lambda row: (row['date'], row['title'] or '', row['pk']), reverse=True)


def scan_backups(folders,progress=lambda _:None):
    groups={};errors=[];visited=set()
    for folder in folders:
        folder=Path(folder).resolve()
        if not folder.is_dir():continue
        for base,dirs,files in os.walk(folder,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(base)/d).is_symlink() and d not in ('_internal','node_modules','.venv')]
            for name in files:
                path=Path(base)/name
                if not name.lower().endswith('.zip') or path.is_symlink():continue
                path=path.resolve()
                if path in visited:continue
                visited.add(path)
                try:
                    with zipfile.ZipFile(path) as archive:
                        names=set(archive.namelist());index=None;count=None
                        if 'bridge-volume.json' in names:
                            if archive.getinfo('bridge-volume.json').file_size>1000000:raise ValueError('分卷元数据过大')
                            info=json.loads(archive.read('bridge-volume.json'))
                            identity=info.get('set_id');count=info.get('count');index=info.get('index')
                            if not isinstance(identity,str) or len(identity)!=64 or type(count) is not int or type(index) is not int or not 1<=index<=count<=10000:raise ValueError('分卷信息无效')
                            key='volume:'+identity;title=Path(str(info.get('original_name') or name)).stem
                            url='';kind='volume'
                        elif {'对话全文.json','完整性报告.json','manifest.json'}.issubset(names):
                            meta={}
                            with archive.open('对话全文.json') as stream:
                                for prefix,event,value in ijson.parse(stream):
                                    if prefix in ('title','url') and event=='string':meta[prefix]=value
                                    if len(meta)==2 or prefix.startswith('segments'):break
                            url=meta.get('url','');title=meta.get('title') or path.stem
                            key='browser:'+url if url else 'path:'+str(path);kind='browser'
                        elif any(n.rsplit('/',1)[-1] in ('conversations.json','chat.html') for n in names):
                            title=path.stem+'（官方多对话备份）';url='';key='official:'+str(path);kind='official'
                        else:continue  # Software installers and source ZIPs are not conversation backups.
                    row=groups.setdefault(key,{'id':hashlib.sha256(key.encode()).hexdigest(),'title':title,'url':url,
                        'kind':kind,'paths':[],'indices':[],'expected':count,'bytes':0,'date':''})
                    if row['expected']!=count:raise ValueError('分卷总数冲突')
                    stat=path.stat();row['paths'].append(str(path));row['bytes']+=stat.st_size
                    row['date']=max(row['date'],datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec='seconds'))
                    if index is not None:row['indices'].append(index)
                    progress(f'已发现 {len(groups)} 组对话备份（含子文件夹）…')
                except (OSError,ValueError,zipfile.BadZipFile,ijson.JSONError,RuntimeError) as exc:
                    errors.append({'path':str(path),'error':str(exc)})
    for row in groups.values():
        row['complete']=row['expected'] is None or set(row['indices'])==set(range(1,row['expected']+1))
        row['status']=(f"{len(set(row['indices']))}/{row['expected']} 卷" if row['expected'] else f"{len(row['paths'])} 个包")+(' · 可入库' if row['complete'] else ' · 缺卷')
    return {'backups':sorted(groups.values(),key=lambda r:r['date'],reverse=True),'scan_errors':errors}
