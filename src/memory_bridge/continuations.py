"""Group source conversations without changing their messages or identities."""
import re

from .common import BridgeError


MARKER = re.compile(r'^[ \t]*【Memory Bridge 接续编号：([0-9a-f]{64})】[ \t\r]*$', re.MULTILINE)


def continuation_header(pk):
    return f'【Memory Bridge 接续编号：{pk}】\n' if pk else ''


def track_root(db, pk):
    row = db.execute('SELECT COALESCE(l.root_pk,c.pk) FROM active_conversations c '
                     'LEFT JOIN conversation_continuations l ON l.conversation_pk=c.pk WHERE c.pk=?', (pk,)).fetchone()
    return row[0] if row else None


def track_members(db, pk):
    root = track_root(db, pk)
    if root is None:
        raise BridgeError('这条对话已不在当前资料库中，请刷新列表。')
    return list(db.execute('''SELECT c.pk,c.external_id,c.source,c.title FROM conversations c
        LEFT JOIN conversation_continuations l ON c.pk=l.conversation_pk
        WHERE c.pk=? OR l.root_pk=? ORDER BY COALESCE(l.position,0),c.pk''', (root, root)))


def attach(db, conversation, target):
    """Append a whole existing conversation group to the target in its existing order."""
    target = track_root(db, target)
    if target is None:
        raise BridgeError('找不到要接续的对话，请刷新列表。')
    members = track_members(db, conversation)
    if members[0]['pk'] == target:
        return False
    position = db.execute('SELECT COALESCE(MAX(position),0) FROM conversation_continuations WHERE root_pk=?',
                          (target,)).fetchone()[0]
    db.executemany('''INSERT INTO conversation_continuations(conversation_pk,root_pk,position) VALUES(?,?,?)
        ON CONFLICT(conversation_pk) DO UPDATE SET root_pk=excluded.root_pk,position=excluded.position''',
        [(member['pk'], target, position + i) for i, member in enumerate(members, 1)])
    return True


def detach(db, conversation):
    # An explicit removal also prevents reattaching from the same marker on reimport.
    if track_root(db, conversation) is None:
        raise BridgeError('这条对话已不在当前列表中，请刷新后重试。')
    db.execute('UPDATE conversation_continuations SET root_pk=NULL,position=0 WHERE conversation_pk=?',
               (conversation,))


def attach_from_messages(db, conversation, messages):
    if track_root(db, conversation) is None:
        return False
    if db.execute('SELECT 1 FROM conversation_continuations WHERE conversation_pk=? OR root_pk=?',
                  (conversation, conversation)).fetchone():
        return False
    targets = set()
    for msg in messages:
        if msg['role'] != 'user':
            continue
        # Uploaded filenames can precede the prompt in the captured message.
        for match in MARKER.finditer(msg['text']):
            target = track_root(db, match[1])
            if target and target != conversation:
                targets.add(target)
    if len(targets) == 1:
        return attach(db, conversation, targets.pop())
    return False
