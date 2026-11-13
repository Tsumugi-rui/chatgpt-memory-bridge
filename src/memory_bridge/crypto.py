"""Optional local encrypted backup. No password appears in arguments or logs."""
from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path

from .common import BridgeError

MAGIC = b"CMBENC01"
CHUNK = 1024 * 1024


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        from cryptography.exceptions import InvalidTag
        return Cipher, algorithms, modes, Scrypt, InvalidTag
    except ImportError as exc:
        raise BridgeError("install_crypto_extra_first") from exc


def _key(password, salt, Scrypt):
    if len(password) < 12:
        raise BridgeError("backup_password_requires_12_characters")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))


def encrypt_file(source, destination, password):
    Cipher, algorithms, modes, Scrypt, _ = _crypto()
    source, destination = Path(source), Path(destination)
    salt, nonce = os.urandom(16), os.urandom(12)
    header = MAGIC + salt + nonce + struct.pack(">Q", source.stat().st_size)
    encryptor = Cipher(algorithms.AES(_key(password, salt, Scrypt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    with source.open("rb") as inp, destination.open("xb") as out:
        out.write(header)
        total = 0
        for chunk in iter(lambda: inp.read(CHUNK), b""):
            total += len(chunk)
            out.write(encryptor.update(chunk))
        if total != struct.unpack(">Q", header[36:44])[0]:
            raise BridgeError("source_size_changed_encrypted_output_incomplete")
        out.write(encryptor.finalize())
        out.write(encryptor.tag)
    return {"encrypted": True, "format": "CMBENC01", "algorithm": "AES-256-GCM+scrypt"}


def decrypt_file(source, destination, password, temporary_dir):
    Cipher, algorithms, modes, Scrypt, InvalidTag = _crypto()
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise BridgeError("destination_already_exists")
    temp_root = Path(temporary_dir).resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="bridge-decrypt-", dir=temp_root)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
            header = inp.read(44)
            if len(header) != 44 or header[:8] != MAGIC:
                raise BridgeError("invalid_encrypted_backup")
            expected = struct.unpack(">Q", header[36:44])[0]
            if source.stat().st_size != 44 + expected + 16:
                raise BridgeError("truncated_encrypted_backup")
            inp.seek(-16, 2)
            tag = inp.read(16)
            inp.seek(44)
            decryptor = Cipher(algorithms.AES(_key(password, header[8:24], Scrypt)), modes.GCM(header[24:36], tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            remaining = expected
            while remaining:
                chunk = inp.read(min(CHUNK, remaining))
                if not chunk:
                    raise BridgeError("truncated_encrypted_backup")
                out.write(decryptor.update(chunk))
                remaining -= len(chunk)
            try:
                out.write(decryptor.finalize())
            except InvalidTag as exc:
                raise BridgeError("wrong_password_or_modified_backup") from exc
        # Do not expose unverified plaintext at the requested destination.
        with temporary.open("rb") as inp, destination.open("xb") as out:
            for chunk in iter(lambda: inp.read(CHUNK), b""):
                out.write(chunk)
        return {"decrypted": True}
    finally:
        temporary.unlink(missing_ok=True)
