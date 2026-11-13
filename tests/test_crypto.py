import pytest

pytest.importorskip("cryptography")

from memory_bridge.common import BridgeError
from memory_bridge.crypto import decrypt_file, encrypt_file


def test_encrypted_backup_stream_and_authentication(tmp_path):
    data = b"synthetic private data" * 150000
    plain = tmp_path / "input.zip"
    encrypted = tmp_path / "backup.mbenc"
    plain.write_bytes(data)
    password = "synthetic-password-123"
    encrypt_file(plain, encrypted, password)
    assert b"synthetic private data" not in encrypted.read_bytes()
    out = tmp_path / "restored.zip"
    decrypt_file(encrypted, out, password, tmp_path / "tmp")
    assert out.read_bytes() == data
    with pytest.raises(BridgeError, match="already_exists"):
        decrypt_file(encrypted, out, password, tmp_path / "tmp")
    with pytest.raises(BridgeError, match="wrong_password"):
        decrypt_file(encrypted, tmp_path / "wrong.zip", "wrong-long-password", tmp_path / "tmp")
    assert not (tmp_path / "wrong.zip").exists()
    damaged = bytearray(encrypted.read_bytes())
    damaged[50] ^= 1
    encrypted.write_bytes(damaged)
    with pytest.raises(BridgeError, match="modified"):
        decrypt_file(encrypted, tmp_path / "tampered.zip", password, tmp_path / "tmp")
    assert not list((tmp_path / "tmp").iterdir())


def test_short_password_and_truncation(tmp_path):
    source = tmp_path / "in"
    source.write_bytes(b"synthetic")
    with pytest.raises(BridgeError, match="12"):
        encrypt_file(source, tmp_path / "no", "short")
    backup = tmp_path / "backup"
    encrypt_file(source, backup, "strong-synthetic-password")
    backup.write_bytes(backup.read_bytes()[:-1])
    with pytest.raises(BridgeError, match="truncated"):
        decrypt_file(backup, tmp_path / "out", "strong-synthetic-password", tmp_path / "tmp")
