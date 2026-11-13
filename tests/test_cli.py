import json

from conftest import conversation
from memory_bridge.cli import main


def invoke(capsys, args, expected=0):
    assert main(args) == expected
    return json.loads(capsys.readouterr().out)


def test_end_to_end_cli(tmp_path, make_zip, capsys):
    base = ["--data", str(tmp_path / "library")]
    invoke(capsys, base + ["init"])
    archive = make_zip([conversation()])
    invoke(capsys, base + ["import", str(archive), "--source", "old-account"])
    matches = invoke(capsys, base + ["search", "数学", "--after", "2023-11-01T00:00:00Z"])
    assert matches[0]["message_id"] == "m1"
    invoke(capsys, base + ["memory", "extract"])
    memories = invoke(capsys, base + ["memory", "list", "--status", "pending"])
    mid = memories[0]["id"]
    invoke(capsys, base + ["memory", "review", mid, "--confirm"])
    package = tmp_path / "package"
    invoke(capsys, base + ["export", "--out", str(package)])
    assert invoke(capsys, ["verify", str(package)])["ok"]
    assert invoke(capsys, base + ["verify"])["ok"]
    invoke(capsys, base + ["memory", "delete", mid], expected=2)
    invoke(capsys, base + ["memory", "delete", mid, "--yes"])
    assert invoke(capsys, base + ["memory", "rollback", mid, "--version", "2"])["confirmed"]
    invoke(capsys, base + ["clean-temp"], expected=2)


def test_private_content_not_in_errors(tmp_path, capsys):
    result = invoke(capsys, ["--data", str(tmp_path / "lib"), "import", "private-missing-file.zip"], expected=2)
    assert result == {"error": "input_not_a_file"}
