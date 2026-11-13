from pathlib import Path
import json
import pytest
from memory_bridge.plugin_update import update_plugin,RUNTIME
from memory_bridge.common import BridgeError


def test_plugin_update_preserves_old_runtime_and_keeps_same_installed_path(tmp_path):
    source=Path(__file__).resolve().parents[1]/'extension';target=tmp_path/'installed';target.mkdir()
    old={'name':'ChatGPT Memory Bridge · 对话备份','version':'1.1.0'}
    (target/'manifest.json').write_text(json.dumps(old),encoding='utf-8')
    (target/'collector.js').write_text('old collector',encoding='utf-8')
    (target/'user-extra.txt').write_text('keep',encoding='utf-8')
    result=update_plugin(source,target,tmp_path/'exports')
    assert json.loads((Path(result['backup'])/'manifest.json').read_text())==old
    assert (Path(result['backup'])/'collector.js').read_text()=='old collector'
    assert (target/'user-extra.txt').read_text()=='keep'
    assert all((target/name).read_bytes()==(source/name).read_bytes() for name in RUNTIME)


def test_unrelated_extension_refused(tmp_path):
    target=tmp_path/'other';target.mkdir()
    (target/'manifest.json').write_text('{"name":"Other"}',encoding='utf-8')
    with pytest.raises(BridgeError):update_plugin(tmp_path,target,tmp_path/'backup')
