from pathlib import Path

import pytest

from memory_bridge.browser_bridge import resume_requires_restart
from memory_bridge.common import BridgeError
from memory_bridge.gui_service import run_operation
from memory_bridge.reset import OUTPUT_FOLDERS, clear_upgrade_cache
from memory_bridge.store import Store
from conftest import conversation


def test_reset_erases_library_and_managed_copies_after_confirmation(tmp_path, make_zip):
    output = tmp_path / 'managed'
    library = output / '资料库'
    archive = make_zip([conversation(cid='test')])
    payload = {'output': str(output)}
    run_operation('import', library, {'zip': archive, 'source': 'account'})
    for name in OUTPUT_FOLDERS:
        folder = output / name
        folder.mkdir()
        (folder / 'old.zip').write_bytes(b'synthetic')
    unrelated = output / 'notes.txt'
    unrelated.write_text('keep')
    with pytest.raises(BridgeError, match='请先确认'):
        run_operation('reset_library', library, payload)
    assert (output / OUTPUT_FOLDERS[0] / 'old.zip').is_file()
    result = run_operation('reset_library', library, {**payload, 'confirmed': True})
    assert result['conversations'] == 1 and not result['failed']
    with Store(library) as store:
        assert not store.imports()
        assert not list(store.conversation_records())
        assert not list((store.root / 'archives').iterdir())
    assert all(not list((output / name).iterdir()) for name in OUTPUT_FOLDERS)
    assert unrelated.is_file() and Path(archive).is_file()
    assert resume_requires_restart(library, 'account', 'https://chatgpt.com/c/test', None)
    assert not resume_requires_restart(library, 'account', 'https://chatgpt.com/c/test', None, '2099-01-01T00:00:00Z')


def test_desktop_upgrade_clears_temporary_files_once(tmp_path):
    output = tmp_path / 'output'
    browser_task = output / '浏览器备份' / ('a' * 32)
    browser_task.mkdir(parents=True)
    incomplete = browser_task / 'transfer.zip.part'
    complete = browser_task / 'transfer.zip'
    incomplete.write_bytes(b'incomplete')
    complete.write_bytes(b'complete')
    with Store(tmp_path / 'library') as store:
        temporary = store.root / 'tmp'
        temporary.mkdir()
        (temporary / 'bridge-stale').touch()
        clear_upgrade_cache(store, output)
        assert not incomplete.exists() and complete.is_file()
        assert not list(temporary.iterdir())
        (temporary / 'bridge-current').touch()
        clear_upgrade_cache(store, output)
        assert (temporary / 'bridge-current').is_file()
