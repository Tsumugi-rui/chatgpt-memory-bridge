"""Import browser capture packages without pretending they are official exports."""
from __future__ import annotations

import json
import copy
import re
from urllib.parse import urlparse

from .adapters import ChatGPTAdapter
from .archive import safe_name
from .common import BridgeError, digest

VIEW = '对话全文.json'
RAW = '原始片段.json'
REPORT = '完整性报告.json'
ASSETS = '附件清单.json'


def merge_browser_history(store, normalized, source):
    """Keep one conversation ID and stitch proven shared message IDs across old ZIPs.

    Disconnected roots remain disconnected: text similarity alone never invents continuity.
    Original input versions and raw ZIPs remain the evidence for every derived union.
    """
    cpk=digest(['chatgpt',source,normalized['id']])
    previous=store.db.execute('SELECT v.raw_json FROM conversations c JOIN conversation_versions v ON v.conversation_pk=c.pk AND v.hash=c.hash WHERE c.pk=?',(cpk,)).fetchone()
    if not previous:return normalized
    old=json.loads(previous[0]);fresh=normalized['raw']
    if not isinstance(old.get('_memory_bridge_browser'),dict):return normalized
    oldmap=old.get('mapping',{});newmap=fresh['mapping']
    # Identical input has already been handled by import idempotency.
    raw=copy.deepcopy(fresh);merged=copy.deepcopy(oldmap)
    for key,node in newmap.items():
        replacement=copy.deepcopy(node)
        if key in merged:
            if replacement.get('parent') is None and merged[key].get('parent'):
                replacement['parent']=merged[key]['parent']
            replacement['children']=list(dict.fromkeys([*merged[key].get('children',[]),*replacement.get('children',[])]))
        merged[key]=replacement
    # Add explicit edges without joining independent roots lacking overlap evidence.
    for key,node in merged.items():
        parent=node.get('parent')
        if parent in merged and key not in merged[parent].get('children',[]):merged[parent].setdefault('children',[]).append(key)
    ordered=[];seen=set()
    for start in merged:
        key=start;trail=[];visiting=set()
        while key in merged and key not in seen:
            if key in visiting:raise BridgeError('browser_cross_package_parent_cycle')
            visiting.add(key);trail.append(key);key=merged[key].get('parent')
        for key in reversed(trail):seen.add(key);ordered.append(key)
    raw['mapping']=merged;raw['_memory_bridge_browser']['node_order']=ordered
    raw['_memory_bridge_browser']['cross_package_merge']={'previous_version_hash':digest(old),'shared_nodes':len(set(oldmap)&set(newmap)),
        'disconnected_roots':sum(n.get('parent') is None for n in merged.values()),'scope':'union_of_captured_nodes_not_account_completeness'}
    result=ChatGPTAdapter().normalize(raw)
    # A union view must not assert that old-only nodes were observed in this new ZIP.
    result['messages']=[m for m in result['messages'] if m['node_id'] in newmap]
    return result


def is_browser_package(archive):
    return {VIEW, REPORT, 'manifest.json'}.issubset(archive.namelist())


def load_json(archive, name):
    try:
        with archive.open(name) as stream:
            return json.load(stream)
    except (ValueError, KeyError, UnicodeError, TypeError) as exc:
        raise BridgeError('browser_invalid_json:' + name) from exc


def verify_manifest(archive, hashes):
    names = [m.filename for m in archive.infolist() if not m.is_dir()]
    if len(names) != len(set(names)):
        raise BridgeError('browser_duplicate_zip_members')
    manifest = load_json(archive, 'manifest.json')
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list):
        raise BridgeError('browser_invalid_manifest')
    listed = set()
    sizes = {m.filename: m.file_size for m in archive.infolist()}
    for item in manifest['files']:
        if not isinstance(item, dict):
            raise BridgeError('browser_invalid_manifest_entry')
        name = safe_name(item.get('path', ''))
        count, checksum = item.get('bytes'), item.get('sha256')
        if name in listed or name == 'manifest.json':
            raise BridgeError('browser_duplicate_manifest_entry')
        listed.add(name)
        if (type(count) is not int or count < 0 or not isinstance(checksum, str)
                or not re.fullmatch(r'[0-9a-f]{64}', checksum)):
            raise BridgeError('browser_invalid_manifest_entry')
        if name not in hashes or sizes.get(name) != count or hashes[name] != checksum:
            raise BridgeError('browser_manifest_mismatch:' + name)
    if listed != set(names) - {'manifest.json'}:
        raise BridgeError('browser_manifest_inventory_mismatch')
    if not {VIEW, RAW, REPORT, ASSETS}.issubset(listed):
        raise BridgeError('browser_required_file_missing')
    return manifest


def raw_capture_metadata(raw):
    if not isinstance(raw, dict):
        raise BridgeError('browser_invalid_raw_capture')
    if raw.get('format') == 'memory-bridge-reference-v2':
        job, dictionary = raw.get('job'), raw.get('dictionary')
        if not isinstance(job, dict) or not isinstance(dictionary, list):
            raise BridgeError('browser_invalid_reference_archive')
        if not all(isinstance(m, dict) for m in dictionary):
            raise BridgeError('browser_invalid_message_dictionary')
        snapshots = job.get('snapshots')
        if not isinstance(snapshots, list):
            raise BridgeError('browser_invalid_snapshots')
        for snapshot in snapshots:
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get('messages'), list):
                raise BridgeError('browser_invalid_snapshots')
            if any(type(i) is not int or i < 0 or i >= len(dictionary) for i in snapshot['messages']):
                raise BridgeError('browser_damaged_message_reference')
        fmt = 'reference-v2'
    else:
        job = raw
        snapshots = job.get('snapshots')
        if not isinstance(snapshots, list) or any(not isinstance(s, dict) or not isinstance(s.get('messages'), list) for s in snapshots):
            raise BridgeError('browser_invalid_snapshots')
        fmt = 'snapshot-v1'
    return {'raw_format': fmt, 'snapshot_count': len(snapshots),
            'job_metadata': {k: v for k, v in job.items() if k != 'snapshots'},
            'dictionary_entries': len(raw.get('dictionary', [])), 'raw_member': RAW}


def read_browser_package(archive, hashes):
    try:
        return _read_browser_package(archive, hashes)
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        raise BridgeError('browser_invalid_capture_structure') from exc


def _read_browser_package(archive, hashes):
    verify_manifest(archive, hashes)
    view, report, assets, original = (load_json(archive, name) for name in (VIEW, REPORT, ASSETS, RAW))
    if not isinstance(view, dict) or not isinstance(view.get('segments'), list) or not isinstance(report, dict) or not isinstance(assets, list):
        raise BridgeError('browser_invalid_capture_structure')
    metadata = raw_capture_metadata(original)
    segments = view['segments']
    if any(not isinstance(s, list) or any(not isinstance(m, dict) for m in s) for s in segments):
        raise BridgeError('browser_invalid_segments')
    if any(not isinstance(m.get('role'), str) or not isinstance(m.get('text'), str) for s in segments for m in s):
        raise BridgeError('browser_invalid_message')
    if any(not isinstance(a, dict) for a in assets):
        raise BridgeError('browser_invalid_asset_inventory')
    url = view.get('url') or metadata['job_metadata'].get('url') or ''
    if not isinstance(url, str):
        raise BridgeError('browser_invalid_source_url')
    parsed = urlparse(url)
    match = re.fullmatch(r'/(c|share)/([A-Za-z0-9-]+)/?', parsed.path) if parsed.hostname == 'chatgpt.com' else None
    # Share IDs identify snapshots, not necessarily the original account conversation ID.
    external_id = match[2] if match and match[1] == 'c' else 'browser-share:' + digest(url or view.get('title'))
    warnings = [{'code': 'browser_displayed_branch_only', 'member': VIEW}]
    if not match or match[1] == 'share':
        warnings.append({'code': 'browser_source_id_not_original_conversation', 'member': VIEW})
    saved_by_url, unavailable = {}, []
    for asset in assets:
        if asset.get('status') == 'saved':
            name = safe_name(asset.get('path', ''))
            if (not name.startswith('assets/') or name not in hashes or asset.get('sha256') != hashes[name]
                    or type(asset.get('bytes')) is not int or asset['bytes'] != archive.getinfo(name).file_size):
                raise BridgeError('browser_asset_checksum_mismatch')
            saved_by_url[asset.get('url') or 'button:' + str(asset.get('name'))] = name
        else:
            unavailable.append(asset)
    declared = {a.get('url') or 'button:' + str(a.get('name')) for a in assets}
    undeclared = set()
    for segment in segments:
        for message in segment:
            for asset in message.get('assets', []) if isinstance(message.get('assets'), list) else []:
                if isinstance(asset, dict):
                    ref = asset.get('url') or 'button:' + str(asset.get('name'))
                    if ref not in declared:
                        undeclared.add(str(ref))
    if undeclared:
        warnings.append({'code': 'browser_assets_absent_from_inventory', 'count': len(undeclared)})
    bounds = report.get('bounds') if isinstance(report.get('bounds'), dict) else {}
    connected = len(segments) == 1 and report.get('gaps') == 0
    boundary_ok = bounds.get('top') is True and bounds.get('bottom') is True
    source_checks = report.get('page_boundary_and_overlap_checks_passed') is True
    if not boundary_ok or not source_checks:
        warnings.append({'code': 'browser_capture_not_finished', 'member': REPORT})
    if not connected:
        warnings.append({'code': 'browser_unconnected_segments', 'segments': len(segments), 'member': REPORT})
    missing_count = len(unavailable) + len(undeclared)
    if missing_count:
        warnings.append({'code': 'browser_attachments_unavailable', 'count': missing_count, 'member': ASSETS})
    count = sum(len(s) for s in segments)
    count_ok = type(report.get('messages')) is int and report['messages'] == count
    if not count_ok:
        warnings.append({'code': 'browser_message_count_mismatch', 'reported': report.get('messages'), 'actual': count})
    metadata.update({'source_url': url, 'report': report, 'assets': assets,
                     'view_metadata': {k: v for k, v in view.items() if k != 'segments'},
                     'scope': 'displayed_branch_only', 'account_state_restored': False})
    raw = {'id': external_id, 'title': str(view.get('title') or '浏览器采集对话'), 'mapping': {},
           'current_node': None, '_memory_bridge_browser': metadata}
    used = set()
    for si, segment in enumerate(segments):
        previous = None
        for index, message in enumerate(segment):
            mid = message.get('id')
            if not isinstance(mid, str) or not mid:
                # Keep repeated short replies as separate occurrences; context helps later imports.
                anchors = [m.get('id') for m in segment[max(0,index-1):index+2]]
                mid = 'browser-derived:' + digest([si, index, anchors, message['role'], message['text']])
                warnings.append({'code': 'browser_derived_message_id', 'segment': si, 'position': index})
            node_id = mid
            if node_id in used:
                node_id = mid + ':browser-occurrence:' + str(si) + ':' + str(index)
            used.add(node_id)
            references = []
            for asset in message.get('assets', []) if isinstance(message.get('assets'), list) else []:
                if not isinstance(asset, dict):
                    continue
                source_ref = asset.get('url') or 'button:' + str(asset.get('name'))
                references.append({'asset_pointer': saved_by_url.get(source_ref, source_ref), 'browser_asset': asset})
            raw['mapping'][node_id] = {
                'parent': previous, 'children': [], 'message': {
                    'id': mid, 'author': {'role': message['role']},
                    'content': {'parts': [message['text'], *references]},
                    'create_time': message.get('timestamp'), 'browser_message': message,
                    'browser_segment': si, 'browser_position': index,
                }}
            if previous is not None:
                raw['mapping'][previous]['children'].append(node_id)
            previous = node_id
        if connected and bounds.get('bottom') is True:
            raw['current_node'] = previous
    summary = {'input_kind': 'browser_extension', 'scope': 'displayed_branch_only',
               'messages': count, 'segments': len(segments), 'bounds': bounds,
               'page_checks_passed': source_checks and boundary_ok and connected and count_ok,
               'saved_attachments': len(assets) - len(unavailable), 'unavailable_attachments': missing_count,
               'snapshot_count': metadata['snapshot_count'], 'raw_format': metadata['raw_format'],
               'account_state_restored': False}
    delivery = report.get('delivery_id')
    if isinstance(delivery, str) and re.fullmatch(r'[0-9a-f]{32}', delivery):
        summary['delivery_id'] = delivery
    incremental = report.get('incremental')
    summary['standalone'] = isinstance(incremental, dict) and incremental.get('enabled') is False
    summary['from_start'] = summary['standalone'] and report.get('from_start') is True
    metadata['node_order'] = list(raw['mapping'])
    partial = not summary['page_checks_passed'] or bool(missing_count) or not count
    return ChatGPTAdapter().normalize(raw), summary, warnings, partial
