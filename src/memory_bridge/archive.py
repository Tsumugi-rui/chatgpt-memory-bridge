from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import struct
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .common import BridgeError, file_hash


@dataclass(frozen=True)
class Limits:
    max_archive: int | None = 8 * 1024**3
    max_member: int | None = 2 * 1024**3
    max_total: int | None = 8 * 1024**3
    max_members: int | None = 100_000
    max_ratio: int | None = 1000
    max_directory: int | None = 64 * 1024**2
    strict_structure: bool = True

    @classmethod
    def personal(cls):
        """Archive everything available; filenames are labels, never extraction paths."""
        return cls(None, None, None, None, None, None, False)


def exceeds(value, maximum):
    return maximum is not None and value > maximum


def safe_name(name: str) -> str:
    path = PurePosixPath(name)
    if (not name or "\\" in name or "\x00" in name or path.is_absolute()
            or any(x in (".", "..", "") for x in name.rstrip("/").split("/"))):
        raise BridgeError("unsafe_zip_path")
    for part in path.parts:
        if ":" in part or part.endswith((".", " ")) or any(ord(x) < 32 for x in part):
            raise BridgeError("unsafe_zip_path")
        if re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0], re.I):
            raise BridgeError("unsafe_windows_path")
    return str(path)


def inspect_zip(zf: zipfile.ZipFile, limits: Limits) -> list[zipfile.ZipInfo]:
    members = zf.infolist()
    if exceeds(len(members), limits.max_members):
        raise BridgeError("zip_member_count_limit")
    seen = set()
    total = 0
    for member in members:
        name = safe_name(member.orig_filename) if limits.strict_structure else member.orig_filename
        folded = name.casefold()
        if folded in seen and limits.strict_structure:
            raise BridgeError("duplicate_zip_path")
        seen.add(folded)
        mode = member.external_attr >> 16
        if limits.strict_structure and (stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
            raise BridgeError("zip_special_file")
        if member.flag_bits & 1 and limits.strict_structure:
            raise BridgeError("encrypted_zip_member_unsupported")
        if exceeds(member.file_size, limits.max_member):
            raise BridgeError("zip_member_size_limit")
        total += member.file_size
        if exceeds(total, limits.max_total):
            raise BridgeError("zip_total_size_limit")
        if limits.max_ratio is not None and member.file_size > max(member.compress_size, 1) * limits.max_ratio:
            raise BridgeError("zip_compression_ratio_limit")
    return members


def preflight_zip(path: Path, limits: Limits):
    """Bound central-directory allocation before ZipFile constructs ZipInfo objects.

    Supports ordinary and ZIP64 EOCD records; rejects multi-disk and trailing junk.
    Member names and sizes are still checked separately after this preflight.
    """
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - 65557))
        tail = stream.read(65557)
        index = len(tail)
        while True:
            index = tail.rfind(b"PK\x05\x06", 0, index)
            if index < 0:
                raise BridgeError("invalid_zip_end_record")
            if index + 22 <= len(tail):
                record = struct.unpack_from("<4s4H2IH", tail, index)
                if index + 22 + record[-1] == len(tail):
                    break
        _, disk, cd_disk, disk_count, count, directory_size, _, _ = record
        if disk or cd_disk or disk_count != count:
            raise BridgeError("multidisk_zip_unsupported")
        eocd_position = size - len(tail) + index
        if count == 65535 or directory_size == 0xFFFFFFFF:
            if eocd_position < 20:
                raise BridgeError("invalid_zip64_locator")
            stream.seek(eocd_position - 20)
            locator = stream.read(20)
            signature, locator_disk, offset, disks = struct.unpack("<4sIQI", locator)
            if signature != b"PK\x06\x07" or locator_disk or disks != 1 or offset > size - 56:
                raise BridgeError("invalid_zip64_locator")
            stream.seek(offset)
            raw = stream.read(56)
            if len(raw) != 56:
                raise BridgeError("invalid_zip64_record")
            fields = struct.unpack("<4sQ2H2I4Q", raw)
            if (fields[0] != b"PK\x06\x06" or fields[1] < 44 or fields[4] or fields[5]
                    or fields[6] != fields[7] or offset + 12 + fields[1] != eocd_position - 20):
                raise BridgeError("invalid_zip64_record")
            count, directory_size = fields[7], fields[8]
        if exceeds(count, limits.max_members):
            raise BridgeError("zip_member_count_limit")
        if exceeds(directory_size, limits.max_directory):
            raise BridgeError("zip_directory_size_limit")


def preserve_zip(source: Path, root: Path, limits: Limits) -> tuple[str, Path]:
    """Copy input bytes once; never modify the source or trust its filename."""
    if not source.is_file():
        raise BridgeError("input_not_a_file")
    if exceeds(source.stat().st_size, limits.max_archive):
        raise BridgeError("archive_size_limit")
    (root / "archives").mkdir(parents=True, exist_ok=True)
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="bridge-", dir=root / "tmp")
    temp = Path(temporary)
    result = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
            size = 0
            for chunk in iter(lambda: inp.read(1024 * 1024), b""):
                size += len(chunk)
                if exceeds(size, limits.max_archive):
                    raise BridgeError("archive_size_limit")
                result.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        checksum = result.hexdigest()
        target = root / "archives" / f"{checksum}.zip"
        if target.exists():
            if file_hash(target) != checksum:
                raise BridgeError("existing_archive_checksum_mismatch")
        else:
            # Exclusive creation also prevents overwriting an existing archive.
            try:
                with target.open("xb") as out, temp.open("rb") as inp:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
            except FileExistsError:
                if file_hash(target) != checksum:
                    raise BridgeError("existing_archive_checksum_mismatch")
        return checksum, target
    finally:
        temp.unlink(missing_ok=True)


def hash_member(zf, member, limits):
    result = hashlib.sha256()
    total = 0
    with zf.open(member) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            if exceeds(total, limits.max_member) or total > member.file_size:
                raise BridgeError("zip_actual_size_limit")
            result.update(chunk)
    if total != member.file_size:
        raise BridgeError("zip_size_mismatch")
    return result.hexdigest()

