"""Exact byte reassembly of a single logical browser export, independent of file order."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import shutil
import zipfile

from .archive import preserve_zip, Limits
from .common import BridgeError, now, dumps, file_hash


@contextmanager
def prepare_imports(paths, library, store=None, source='default'):
    groups, ordinary = {}, []
    for path in map(Path, paths):
        if not zipfile.is_zipfile(path):
            ordinary.append(path)
            continue
        with zipfile.ZipFile(path) as z:
            if 'bridge-volume.json' not in z.namelist():
                ordinary.append(path)
                continue
            if sorted(z.namelist()) != ['bridge-volume.json', 'payload.bin'] or z.getinfo('bridge-volume.json').file_size > 1000000:
                raise BridgeError('invalid_volume_inventory')
            info=json.loads(z.read('bridge-volume.json'))
            if (info.get('format')!='memory-bridge-volume-v1' or type(info.get('index')) is not int
                or type(info.get('count')) is not int or not 1 <= info['index'] <= info['count'] <= 10000
                or not isinstance(info.get('hashes'),list) or len(info['hashes'])!=info['count']
                or type(info.get('total_bytes')) is not int or info['total_bytes']<1):
                raise BridgeError('invalid_volume_metadata')
            if any(not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h) for h in info['hashes']):
                raise BridgeError('invalid_volume_hashes')
            identity=json.dumps([info['hashes'],info['total_bytes']],separators=(',',':'))
            set_id=hashlib.sha256(identity.encode()).hexdigest()
            if info.get('set_id')!=set_id:
                raise BridgeError('volume_set_identity_mismatch')
            group=groups.setdefault(set_id,{})
            if info['index'] in group:
                if group[info['index']][0].read_bytes()!=path.read_bytes():
                    raise BridgeError('duplicate_volume_conflict')
            else:
                group[info['index']]=(path,info)
    # Validate every set before creating any import records.
    for group in groups.values():
        if len(group)!=next(iter(group.values()))[1]['count']:
            raise BridgeError('missing_volumes_select_all_zip_parts')
    tmp=Path(library)/'tmp';tmp.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='volumes-',dir=tmp) as work:
        for set_id,group in groups.items():
            target=Path(work)/(set_id+'.zip');total=0
            with target.open('wb') as output:
                for index in sorted(group):
                    path,info=group[index];sha=hashlib.sha256()
                    with zipfile.ZipFile(path) as z,z.open('payload.bin') as payload:
                        while block:=payload.read(1024*1024):
                            total+=len(block);sha.update(block);output.write(block)
                    if sha.hexdigest()!=info['hashes'][index-1]:
                        raise BridgeError('volume_checksum_mismatch')
            if total!=info['total_bytes'] or not zipfile.is_zipfile(target):
                raise BridgeError('volume_reassembly_invalid')
            # Preserve both original transfer volumes and the reconstructed authoritative ZIP.
            logical_hash = file_hash(target) if store is not None else None
            for path,_ in group.values():
                if store is None:
                    preserve_zip(path,Path(library),Limits.personal())
                else:
                    with store.db:
                        store.db.execute('BEGIN IMMEDIATE')
                        checksum,_=preserve_zip(path,Path(library),Limits.personal())
                        store.db.execute("INSERT OR IGNORE INTO imports(sha256,source,platform,imported_at,status,stats) VALUES(?,?,'browser_volume',?,'complete',?)",
                            (checksum,source,now(),dumps({'input_kind':'browser_volume','set_id':set_id,
                                                        'reassembled_sha256':logical_hash,'parsed_records':0})))
            ordinary.append(target)
        yield ordinary
