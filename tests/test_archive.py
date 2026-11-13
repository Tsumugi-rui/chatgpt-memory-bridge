import io
import stat
import zipfile

import pytest

from memory_bridge.archive import Limits, inspect_zip, preserve_zip, safe_name
from memory_bridge.common import BridgeError, file_hash


@pytest.mark.parametrize("name", ["../evil", "/abs", "C:/foo", "a\\b", "a/../b", "CON.txt", "a/NUL", "foo.", "a//b", "./a"])
def test_unsafe_names(name):
    with pytest.raises(BridgeError):
        safe_name(name)


def test_preserve_identical_bytes(tmp_path):
    source = tmp_path / "original.zip"
    source.write_bytes(b"even corrupt input remains recoverable")
    checksum, backup = preserve_zip(source, tmp_path / "library", Limits())
    assert checksum == file_hash(source) == file_hash(backup)
    assert preserve_zip(source, tmp_path / "library", Limits())[1] == backup
    assert not list((tmp_path / "library/tmp").iterdir())


def test_zip_limits_and_symlink():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", "a" * 10000)
    with zipfile.ZipFile(buf) as zf:
        with pytest.raises(BridgeError, match="ratio"):
            inspect_zip(zf, Limits(max_ratio=2))
        with pytest.raises(BridgeError, match="member_size"):
            inspect_zip(zf, Limits(max_member=2))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "target")
    with zipfile.ZipFile(buf) as zf, pytest.raises(BridgeError, match="special"):
        inspect_zip(zf, Limits())


def test_case_duplicate():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("A", "1")
        zf.writestr("a", "2")
    with zipfile.ZipFile(buf) as zf, pytest.raises(BridgeError, match="duplicate"):
        inspect_zip(zf, Limits())
