#!/usr/bin/env python3
"""Bounded, schema-checked I/O shared by critically-review helpers.

All helpers accept untrusted review artifacts. In particular, output paths are
an attacker-controlled filesystem boundary: validation must continue through
publication, not stop at a path precheck.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NoReturn

import referencing
from jsonschema import Draft202012Validator

MAX_INPUT_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 1_048_576
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 10_000
MAX_STRING_LENGTH = 10_000
MAX_FINDINGS = 500
MAX_EVIDENCE_PER_FINDING = 50
MAX_INPUT_FILES = 64
MAX_DISPLAY_NAME = 128
_REPARSE_POINT = 0x0400
_TEST_OUTPUT_HOOK: Callable[[str, Path], None] | None = None


class ToolError(Exception):
    """A value-free, stable CLI failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SafeArgumentParser(argparse.ArgumentParser):
    """Avoid argparse echoing untrusted arguments in an error message."""

    def error(self, message: str) -> NoReturn:
        del message
        raise ToolError("invalid-arguments")


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int


@dataclass(frozen=True)
class FileState:
    """Identity and mutation state captured for a force-write preflight."""

    identity: FileIdentity
    size: int
    modified: int
    changed: int
    content_digest: bytes | None = None


@dataclass
class InputSnapshot:
    """An opened input held for the helper invocation."""

    path: Path
    identity: FileIdentity
    display_name: str
    source_id: str
    descriptor: int

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


@dataclass
class DirectoryLease:
    """A no-reparse directory chain held for one output write window."""

    path: Path
    identity: FileIdentity
    descriptors: list[int] = field(default_factory=list)
    handles: list[int] = field(default_factory=list)

    @property
    def directory_fd(self) -> int:
        if not self.descriptors:
            raise ToolError("output-platform-unsupported")
        return self.descriptors[-1]

    def assert_stable(self) -> None:
        try:
            info = os.lstat(self.path)
        except OSError as exc:
            raise ToolError("output-parent-changed") from exc
        if _is_reparse_info(info) or not stat.S_ISDIR(info.st_mode):
            raise ToolError("output-parent-changed")
        if not _same_identity(_identity_from_stat(info), self.identity):
            raise ToolError("output-parent-changed")

    def close(self) -> None:
        for descriptor in reversed(self.descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self.descriptors.clear()
        if self.handles:
            _close_windows_handles(self.handles)
        self.handles.clear()


def add_error_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json-errors",
        action="store_true",
        help="Emit value-free failures as JSON instead of stderr text.",
    )


def safe_main(main: Callable[[], int]) -> int:
    """Execute a helper without exposing values or a traceback on failures."""
    try:
        return main()
    except ToolError as exc:
        _emit_error(exc.code, "--json-errors" in sys.argv)
        return 2
    except (BrokenPipeError, KeyboardInterrupt):
        _emit_error("interrupted", "--json-errors" in sys.argv)
        return 2
    except Exception:
        _emit_error("internal-error", "--json-errors" in sys.argv)
        return 2


def _emit_error(code: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"ok": False, "error": {"code": code}}, sort_keys=True))
    else:
        print(f"ERROR: {code}", file=sys.stderr)


def safe_identifier(path: Path) -> str:
    """Return a bounded basename suitable for a machine or human result."""
    name = path.name or "file"
    cleaned = "".join(
        char if char.isascii() and (char.isalnum() or char in "._-") else "_"
        for char in name
    )
    return (cleaned or "file")[:MAX_DISPLAY_NAME]


def _identity_from_stat(info: os.stat_result) -> FileIdentity:
    return FileIdentity(info.st_dev, info.st_ino)


def _state_from_stat(info: os.stat_result) -> FileState:
    return FileState(
        _identity_from_stat(info),
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _same_identity(left: FileIdentity, right: FileIdentity) -> bool:
    return left.device == right.device and left.inode == right.inode


def _same_state(left: FileState, right: FileState) -> bool:
    return (
        _same_identity(left.identity, right.identity)
        and left.size == right.size
        and left.modified == right.modified
        and left.changed == right.changed
    )


def _is_reparse_info(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & _REPARSE_POINT)


def _absolute_path(path_text: str) -> Path:
    try:
        raw_path = os.fspath(path_text)
    except TypeError as exc:
        raise ToolError("invalid-path") from exc
    if "\x00" in raw_path:
        raise ToolError("invalid-path")
    return Path(os.path.abspath(raw_path))


def _normal_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _require_output_leaf(output: Path) -> str:
    name = output.name
    if name in {"", ".", ".."} or (os.name == "nt" and ":" in name):
        raise ToolError("invalid-output-path")
    return name


def _close_windows_handles(handles: list[int]) -> None:
    if os.name != "nt":
        return
    import ctypes

    close_handle = ctypes.windll.kernel32.CloseHandle
    for handle in reversed(handles):
        close_handle(handle)


def _windows_open_directory(path: Path) -> tuple[int, FileIdentity]:
    """Open a Windows directory without following a final reparse point.

    The handle excludes ``FILE_SHARE_DELETE``. Holding every ancestor this way
    prevents another process from replacing a parent with a junction while a
    path-based Windows operation is in progress.
    """
    import ctypes
    from ctypes import wintypes

    file_read_attributes = 0x0080
    file_share_read = 0x0001
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    file_attribute_directory = 0x0010
    invalid_handle_value = ctypes.c_void_p(-1).value

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime_dwLowDateTime", wintypes.DWORD),
            ("ftCreationTime_dwHighDateTime", wintypes.DWORD),
            ("ftLastAccessTime_dwLowDateTime", wintypes.DWORD),
            ("ftLastAccessTime_dwHighDateTime", wintypes.DWORD),
            ("ftLastWriteTime_dwLowDateTime", wintypes.DWORD),
            ("ftLastWriteTime_dwHighDateTime", wintypes.DWORD),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        os.fspath(path),
        file_read_attributes,
        # Directory-entry changes, including renames and junction insertion,
        # require write sharing on the parent directory. Keep only read share
        # while this output window is active.
        file_share_read,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    if handle == invalid_handle_value:
        error = ctypes.get_last_error()
        if error in {2, 3}:
            raise FileNotFoundError(error, "directory missing")
        raise OSError(error, "directory open failed")
    information = ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise OSError(error, "directory inspection failed")
    if not information.dwFileAttributes & file_attribute_directory:
        kernel32.CloseHandle(handle)
        raise ToolError("output-parent-invalid")
    if information.dwFileAttributes & _REPARSE_POINT:
        kernel32.CloseHandle(handle)
        raise ToolError("output-reparse")
    identity = FileIdentity(
        int(information.dwVolumeSerialNumber),
        (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow),
    )
    return int(handle), identity


def _open_directory_chain(path: Path, *, create: bool = False) -> DirectoryLease:
    """Acquire a no-reparse lease for every ancestor through ``path``."""
    absolute = _absolute_path(os.fspath(path))
    parts = absolute.parts
    if not parts:
        raise ToolError("output-parent-invalid")
    if os.name == "nt":
        current = Path(absolute.anchor)
        handles: list[int] = []
        try:
            root_handle, _ = _windows_open_directory(current)
            handles.append(root_handle)
            for part in parts[1:]:
                current /= part
                try:
                    handle, _ = _windows_open_directory(current)
                except FileNotFoundError:
                    if not create:
                        raise ToolError("output-parent-invalid")
                    try:
                        os.mkdir(current)
                    except FileExistsError:
                        pass
                    except OSError as exc:
                        raise ToolError("output-parent-invalid") from exc
                    handle, _ = _windows_open_directory(current)
                handles.append(handle)
            try:
                final_info = os.lstat(current)
            except OSError as exc:
                raise ToolError("output-parent-invalid") from exc
            if _is_reparse_info(final_info) or not stat.S_ISDIR(final_info.st_mode):
                raise ToolError("output-reparse")
            return DirectoryLease(
                current, _identity_from_stat(final_info), handles=handles
            )
        except Exception:
            _close_windows_handles(handles)
            raise

    required = {os.open, os.stat}
    if (
        not required.issubset(os.supports_dir_fd)
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise ToolError("output-platform-unsupported")
    descriptor_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root = Path(absolute.anchor)
    descriptors: list[int] = []
    current = root
    try:
        descriptors.append(os.open(os.fspath(root), descriptor_flags))
        for part in parts[1:]:
            try:
                child_descriptor = os.open(
                    part, descriptor_flags, dir_fd=descriptors[-1]
                )
            except FileNotFoundError:
                if not create:
                    raise ToolError("output-parent-invalid")
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptors[-1])
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ToolError("output-parent-invalid") from exc
                child_descriptor = os.open(
                    part, descriptor_flags, dir_fd=descriptors[-1]
                )
            except OSError as exc:
                raise ToolError("output-reparse") from exc
            info = os.fstat(child_descriptor)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child_descriptor)
                raise ToolError("output-parent-invalid")
            descriptors.append(child_descriptor)
            current /= part
        return DirectoryLease(
            current,
            _identity_from_stat(os.fstat(descriptors[-1])),
            descriptors=descriptors,
        )
    except Exception:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def secure_directory(path_text: str) -> DirectoryLease:
    """Create a review workspace without traversing a junction or symlink."""
    return _open_directory_chain(_absolute_path(path_text), create=True)


def _lstat_in_parent(lease: DirectoryLease, name: str) -> os.stat_result:
    try:
        if os.name == "nt":
            return os.lstat(lease.path / name)
        return os.stat(name, dir_fd=lease.directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ToolError("output-unreadable") from exc


def _open_in_parent(
    lease: DirectoryLease, name: str, flags: int, mode: int = 0o600
) -> int:
    flags |= getattr(os, "O_BINARY", 0)
    if os.name != "nt":
        flags |= os.O_NOFOLLOW
    try:
        if os.name == "nt":
            return os.open(os.fspath(lease.path / name), flags, mode)
        return os.open(name, flags, mode, dir_fd=lease.directory_fd)
    except FileExistsError:
        raise
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ToolError("output-unwritable") from exc


def _unlink_in_parent(lease: DirectoryLease, name: str) -> None:
    try:
        if os.name == "nt":
            try:
                lease.assert_stable()
            except ToolError:
                return
            os.unlink(lease.path / name)
        else:
            os.unlink(name, dir_fd=lease.directory_fd)
    except OSError:
        pass


def _link_in_parent(lease: DirectoryLease, source: str, destination: str) -> None:
    try:
        if os.name == "nt":
            os.link(
                lease.path / source, lease.path / destination, follow_symlinks=False
            )
        else:
            os.link(
                source,
                destination,
                src_dir_fd=lease.directory_fd,
                dst_dir_fd=lease.directory_fd,
                follow_symlinks=False,
            )
    except FileExistsError as exc:
        raise ToolError("output-exists") from exc
    except OSError as exc:
        raise ToolError("output-unwritable") from exc


def _windows_nt_create_relative(
    root_handle: int,
    name: str,
    desired_access: int,
    disposition: int,
    create_options: int,
    *,
    share_access: int = 0x0001,
) -> int:
    """Open a leaf relative to a verified directory handle without reparsing."""
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ushort),
            ("MaximumLength", ctypes.c_ushort),
            ("Buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_long), ("Information", ctypes.c_size_t)]

    object_case_insensitive = 0x0040
    object_dont_reparse = 0x1000
    file_attribute_normal = 0x0080
    file_synchronous_io_nonalert = 0x0020
    file_open_reparse_point = 0x00200000
    encoded = name.encode("utf-16-le")
    buffer = ctypes.create_unicode_buffer(name)
    unicode_name = UnicodeString(
        len(encoded), len(encoded) + 2, ctypes.cast(buffer, wintypes.LPWSTR)
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        root_handle,
        ctypes.pointer(unicode_name),
        object_case_insensitive | object_dont_reparse,
        None,
        None,
    )
    status_block = IoStatusBlock()
    file_handle = wintypes.HANDLE()
    nt_create_file = ctypes.WinDLL("ntdll", use_last_error=True).NtCreateFile
    nt_create_file.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    nt_create_file.restype = ctypes.c_long
    status = nt_create_file(
        ctypes.byref(file_handle),
        desired_access,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        file_attribute_normal,
        share_access,
        disposition,
        create_options | file_synchronous_io_nonalert | file_open_reparse_point,
        None,
        0,
    )
    if status < 0:
        # STATUS_OBJECT_NAME_COLLISION is the only expected retry condition.
        if status & 0xFFFFFFFF == 0xC0000035:
            raise FileExistsError("temporary name collision")
        raise ToolError("output-unwritable")
    return int(file_handle.value)


def _windows_file_times(handle: int) -> tuple[int, int]:
    """Return stable last-write and change timestamps for an open handle."""
    import ctypes
    from ctypes import wintypes

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
        ]

    file_basic_info = 0
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    get_information.restype = wintypes.BOOL
    information = FileBasicInfo()
    if not get_information(
        handle,
        file_basic_info,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        raise ToolError("output-unwritable")
    return int(information.LastWriteTime), int(information.ChangeTime)


def _file_state_from_descriptor(descriptor: int) -> FileState:
    try:
        info = os.fstat(descriptor)
    except OSError as exc:
        raise ToolError("output-changed") from exc
    state = _state_from_stat(info)
    if os.name != "nt":
        return state
    import msvcrt

    modified, changed = _windows_file_times(msvcrt.get_osfhandle(descriptor))
    return FileState(state.identity, state.size, modified, changed)


def _bounded_content_digest(descriptor: int, expected: FileState) -> bytes:
    """Hash one existing force target without retaining its bytes beyond this call."""
    if expected.size > MAX_OUTPUT_BYTES:
        raise ToolError("output-too-large")
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        remaining = MAX_OUTPUT_BYTES + 1
        digest = hashlib.sha256()
        total = 0
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            remaining -= len(chunk)
        current = _file_state_from_descriptor(descriptor)
    except OSError as exc:
        raise ToolError("output-unreadable") from exc
    if total > MAX_OUTPUT_BYTES:
        raise ToolError("output-too-large")
    if total != expected.size or not _same_state(current, expected):
        raise ToolError("output-changed")
    return digest.digest()


def _windows_preflight_state(
    lease: DirectoryLease,
    name: str,
    inputs: list[InputSnapshot],
    expected: FileIdentity,
) -> FileState:
    """Read a force target's version through the same safe parent handle."""
    import msvcrt

    generic_read = 0x80000000
    synchronize = 0x00100000
    file_open = 1
    file_non_directory_file = 0x0040
    # This is a snapshot only. It shares read/write/delete so it cannot block
    # the deterministic mutation hook between preflight and final open.
    handle = _windows_nt_create_relative(
        lease.handles[-1],
        name,
        generic_read | synchronize,
        file_open,
        file_non_directory_file,
        share_access=0x0007,
    )
    descriptor: int | None = None
    try:
        descriptor = msvcrt.open_osfhandle(
            handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)
        )
        handle = None
        state = _check_descriptor(descriptor, inputs, expected=expected)
        content_digest = _bounded_content_digest(descriptor, state)
        _check_descriptor(descriptor, inputs, expected=state)
        return FileState(
            state.identity,
            state.size,
            state.modified,
            state.changed,
            content_digest,
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
        elif handle is not None:
            _close_windows_handles([handle])


def _windows_lock_exclusive_file(descriptor: int) -> None:
    """Block all range writes until the final force descriptor closes."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    lockfile_exclusive_lock = 0x00000002
    lockfile_fail_immediately = 0x00000001
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    lock_file_ex = kernel32.LockFileEx
    lock_file_ex.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(Overlapped),
    ]
    lock_file_ex.restype = wintypes.BOOL
    overlapped = Overlapped()
    if not lock_file_ex(
        msvcrt.get_osfhandle(descriptor),
        lockfile_exclusive_lock | lockfile_fail_immediately,
        0,
        0xFFFFFFFF,
        0xFFFFFFFF,
        ctypes.byref(overlapped),
    ):
        raise ToolError("output-changed")


def _windows_create_temporary(lease: DirectoryLease, name: str) -> int:
    import msvcrt

    delete = 0x00010000
    generic_write = 0x40000000
    file_write_attributes = 0x0100
    file_read_attributes = 0x0080
    synchronize = 0x00100000
    file_create = 2
    file_non_directory_file = 0x0040
    handle = _windows_nt_create_relative(
        lease.handles[-1],
        name,
        generic_write
        | file_read_attributes
        | file_write_attributes
        | delete
        | synchronize,
        file_create,
        file_non_directory_file,
    )
    return msvcrt.open_osfhandle(handle, os.O_WRONLY | getattr(os, "O_BINARY", 0))


def _windows_publish_create_only(
    lease: DirectoryLease, destination: str, data: bytes, inputs: list[InputSnapshot]
) -> None:
    """Create the final Windows name relative to the held parent handle.

    This is intentionally create-only. The underlying Windows API has no
    portable Python compare-and-replace primitive, so an existing target never
    becomes an overwrite candidate here.
    """
    import msvcrt

    generic_write = 0x40000000
    file_read_attributes = 0x0080
    synchronize = 0x00100000
    file_create = 2
    file_non_directory_file = 0x0040
    try:
        handle = _windows_nt_create_relative(
            lease.handles[-1],
            destination,
            generic_write | file_read_attributes | synchronize,
            file_create,
            file_non_directory_file,
        )
    except FileExistsError as exc:
        raise ToolError("output-exists") from exc
    descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY | getattr(os, "O_BINARY", 0))
    try:
        _check_descriptor(descriptor, inputs)
        _write_all(descriptor, data)
        _check_descriptor(descriptor, inputs)
    finally:
        os.close(descriptor)


def _identity_from_descriptor(descriptor: int) -> FileIdentity:
    return _identity_from_stat(os.fstat(descriptor))


def _check_regular_single_link(
    info: os.stat_result, *, code: str = "output-not-regular-file"
) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise ToolError(code)
    if info.st_nlink != 1:
        raise ToolError("output-hardlink")


def _check_output_entry(
    lease: DirectoryLease,
    name: str,
    inputs: list[InputSnapshot],
    *,
    expected: FileIdentity | None = None,
) -> FileState:
    info = _lstat_in_parent(lease, name)
    if _is_reparse_info(info):
        raise ToolError("output-reparse")
    identity = _identity_from_stat(info)
    if expected is not None and not _same_identity(identity, expected):
        raise ToolError("output-changed")
    _compare_to_inputs(identity, inputs)
    _check_regular_single_link(info)
    if os.name == "nt":
        return _windows_preflight_state(lease, name, inputs, identity)
    return _state_from_stat(info)


def _compare_to_inputs(identity: FileIdentity, inputs: list[InputSnapshot]) -> None:
    if any(_same_identity(identity, item.identity) for item in inputs):
        raise ToolError("output-alias")


def _check_descriptor(
    descriptor: int,
    inputs: list[InputSnapshot],
    *,
    expected: FileIdentity | FileState | None = None,
) -> FileState:
    state = _file_state_from_descriptor(descriptor)
    if isinstance(expected, FileState) and not _same_state(state, expected):
        raise ToolError("output-changed")
    if isinstance(expected, FileIdentity) and not _same_identity(
        state.identity, expected
    ):
        raise ToolError("output-changed")
    try:
        info = os.fstat(descriptor)
    except OSError as exc:
        raise ToolError("output-changed") from exc
    if _is_reparse_info(info):
        raise ToolError("output-reparse")
    _compare_to_inputs(state.identity, inputs)
    _check_regular_single_link(info)
    if isinstance(expected, FileState) and expected.content_digest is not None:
        current_digest = _bounded_content_digest(descriptor, state)
        if current_digest != expected.content_digest:
            raise ToolError("output-changed")
        try:
            info = os.fstat(descriptor)
        except OSError as exc:
            raise ToolError("output-changed") from exc
        if _is_reparse_info(info):
            raise ToolError("output-reparse")
        _compare_to_inputs(state.identity, inputs)
        _check_regular_single_link(info)
    return state


def _check_absent(
    lease: DirectoryLease, name: str, inputs: list[InputSnapshot]
) -> None:
    try:
        info = _lstat_in_parent(lease, name)
    except FileNotFoundError:
        return
    if _is_reparse_info(info):
        raise ToolError("output-reparse")
    _compare_to_inputs(_identity_from_stat(info), inputs)
    _check_regular_single_link(info)
    raise ToolError("output-exists")


def _create_temporary(lease: DirectoryLease) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for _ in range(8):
        name = f".critically-review-{secrets.token_hex(16)}.tmp"
        try:
            if os.name == "nt":
                return name, _windows_create_temporary(lease, name)
            return name, _open_in_parent(lease, name, flags)
        except FileExistsError:
            continue
        except ToolError as exc:
            if exc.code != "output-unwritable":
                raise
    raise ToolError("output-unwritable")


def _write_all(descriptor: int, data: bytes) -> None:
    written = 0
    try:
        while written < len(data):
            count = os.write(descriptor, data[written:])
            if count <= 0:
                raise ToolError("output-unwritable")
            written += count
        os.fsync(descriptor)
    except ToolError:
        raise
    except OSError as exc:
        raise ToolError("output-unwritable") from exc


def _run_test_hook(stage: str, output: Path) -> None:
    """Allow deterministic in-process races without adding a production CLI."""
    if _TEST_OUTPUT_HOOK is not None:
        _TEST_OUTPUT_HOOK(stage, output)


def snapshot_input(path_text: str) -> InputSnapshot:
    """Open one bounded regular input without following a final reparse point."""
    path = _absolute_path(path_text)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise ToolError("input-unreadable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ToolError("input-not-regular-file")
        if info.st_size > MAX_INPUT_BYTES:
            raise ToolError("input-too-large")
        source_hash = hashlib.sha256(_normal_path(path).encode("utf-8")).hexdigest()[
            :16
        ]
        return InputSnapshot(
            path=path,
            identity=_identity_from_stat(info),
            display_name=safe_identifier(path),
            source_id=f"source-{source_hash}",
            descriptor=descriptor,
        )
    except Exception:
        os.close(descriptor)
        raise


def close_snapshots(snapshots: list[InputSnapshot]) -> None:
    for snapshot in snapshots:
        snapshot.close()


def _check_json_depth(raw: bytes) -> None:
    """Reject structurally deep JSON before json.loads allocates a tree."""
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ToolError("json-too-deep")
        elif byte in (0x5D, 0x7D):
            depth -= 1
            if depth < 0:
                raise ToolError("invalid-json")
    if in_string or depth != 0:
        raise ToolError("invalid-json")


def _check_text(value: str) -> None:
    if len(value) > MAX_STRING_LENGTH:
        raise ToolError("json-string-too-long")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ToolError("invalid-unicode-scalar")


def _check_value_limits(
    value: Any, depth: int = 0, counter: list[int] | None = None
) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ToolError("json-too-deep")
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > MAX_JSON_NODES:
        raise ToolError("json-too-many-nodes")
    if isinstance(value, str):
        _check_text(value)
    elif isinstance(value, list):
        for child in value:
            _check_value_limits(child, depth + 1, counter)
    elif isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ToolError("invalid-json")
            _check_text(key)
            _check_value_limits(child, depth + 1, counter)


def read_json(snapshot: InputSnapshot) -> Any:
    """Read one held, bounded, strict-UTF-8 JSON input."""
    try:
        before = os.fstat(snapshot.descriptor)
        if not _same_identity(_identity_from_stat(before), snapshot.identity):
            raise ToolError("input-changed")
        os.lseek(snapshot.descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = MAX_INPUT_BYTES + 1
        while remaining:
            chunk = os.read(snapshot.descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(snapshot.descriptor)
    except ToolError:
        raise
    except OSError as exc:
        raise ToolError("input-unreadable") from exc
    if not _same_identity(_identity_from_stat(after), snapshot.identity):
        raise ToolError("input-changed")
    if len(raw) > MAX_INPUT_BYTES:
        raise ToolError("input-too-large")
    _check_json_depth(raw)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ToolError("invalid-utf8") from exc
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ToolError("invalid-json") from exc
    _check_value_limits(value)
    return value


def _load_validator(schema_name: str) -> Draft202012Validator:
    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    report_schema = json.loads(
        (assets_dir / "review-report.schema.json").read_text(encoding="utf-8")
    )
    finding_schema = json.loads(
        (assets_dir / "finding.schema.json").read_text(encoding="utf-8")
    )
    finding_resource = referencing.Resource.from_contents(finding_schema)
    registry = referencing.Registry().with_resource(
        "finding.schema.json", finding_resource
    )
    schema = finding_schema if schema_name == "finding" else report_schema
    return Draft202012Validator(schema, registry=registry)


def _schema_error_paths(validator: Draft202012Validator, value: Any) -> list[str]:
    paths = {
        "invalid-schema"
        + (
            ":" + ".".join(str(part) for part in error.absolute_path)
            if error.absolute_path
            else ""
        )
        for error in validator.iter_errors(value)
    }
    return sorted(paths)


def _validate_unique_ids(findings: list[dict[str, Any]]) -> list[str]:
    identifiers = [finding.get("id") for finding in findings]
    if any(
        not isinstance(identifier, str) or not identifier.strip()
        for identifier in identifiers
    ):
        return ["invalid-finding-id"]
    if len(set(identifiers)) != len(identifiers):
        return ["duplicate-finding-id"]
    return []


def validate_payload(value: Any, *, allow_findings_list: bool) -> tuple[str, list[str]]:
    """Validate a report or, for merge inputs, a standalone findings list."""
    if isinstance(value, dict):
        errors = _schema_error_paths(_load_validator("report"), value)
        findings = value.get("findings")
        if isinstance(findings, list):
            errors.extend(_validate_unique_ids(findings))
        return "report", sorted(set(errors))
    if allow_findings_list and isinstance(value, list):
        validator = _load_validator("finding")
        flattened = [
            error for item in value for error in _schema_error_paths(validator, item)
        ]
        if len(value) > MAX_FINDINGS:
            flattened.append("findings-limit")
        if all(isinstance(item, dict) for item in value):
            flattened.extend(_validate_unique_ids(value))
        return "findings-list", sorted(set(flattened))
    return "unknown", ["unsupported-json-shape"]


def require_valid_payload(value: Any, *, allow_findings_list: bool) -> str:
    kind, errors = validate_payload(value, allow_findings_list=allow_findings_list)
    if errors:
        raise ToolError(errors[0])
    return kind


def json_payload(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise ToolError("invalid-unicode-scalar") from exc


def _lease_for_output(
    output: Path, parent_lease: DirectoryLease | None
) -> tuple[DirectoryLease, bool]:
    parent = output.parent
    if parent_lease is not None:
        if _normal_path(parent_lease.path) != _normal_path(parent):
            raise ToolError("output-parent-invalid")
        parent_lease.assert_stable()
        return parent_lease, False
    return _open_directory_chain(parent), True


def _publish_create_only(
    lease: DirectoryLease,
    temporary: str,
    output_name: str,
    data: bytes,
    inputs: list[InputSnapshot],
) -> None:
    _check_absent(lease, output_name, [])
    if os.name == "nt":
        _windows_publish_create_only(lease, output_name, data, inputs)
        return
    _link_in_parent(lease, temporary, output_name)


def _force_write_windows(
    lease: DirectoryLease,
    name: str,
    data: bytes,
    inputs: list[InputSnapshot],
    expected: FileState,
) -> None:
    """Safely replace an unchanged Windows target through one held handle."""
    if os.name != "nt":
        raise ToolError("output-force-unsupported")
    import msvcrt

    generic_read = 0x80000000
    generic_write = 0x40000000
    synchronize = 0x00100000
    file_open = 1
    file_non_directory_file = 0x0040
    _run_test_hook("before-force-open", lease.path / name)
    lease.assert_stable()
    handle = _windows_nt_create_relative(
        lease.handles[-1],
        name,
        generic_read | generic_write | synchronize,
        file_open,
        file_non_directory_file,
    )
    descriptor: int | None = None
    try:
        descriptor = msvcrt.open_osfhandle(
            handle, os.O_RDWR | getattr(os, "O_BINARY", 0)
        )
        handle = None
        # The no-write/no-delete share mode prevents new writers, deletion, and
        # parent entry changes. The exclusive range lock also blocks I/O from
        # handles that were already open before this final handle was acquired.
        _windows_lock_exclusive_file(descriptor)
        # Compare the final held handle to the safe preflight before any
        # destructive operation. The transient content digest closes the
        # same-size, timestamp-restored rewrite case that metadata cannot see.
        _check_descriptor(descriptor, inputs, expected=expected)
        lease.assert_stable()
        _run_test_hook("before-force-truncate", lease.path / name)
        # The no-write/no-delete sharing mode keeps this exact file identity
        # stable until close. Validate after the hook, immediately before the
        # only destructive operation. This recomputes the bounded digest from
        # this exact locked handle immediately before truncation.
        lease.assert_stable()
        _check_descriptor(descriptor, inputs, expected=expected)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        _write_all(descriptor, data)
        _check_descriptor(descriptor, inputs, expected=expected.identity)
    except ToolError:
        raise
    except OSError as exc:
        raise ToolError("output-unwritable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        elif handle is not None:
            _close_windows_handles([handle])


def write_output(
    output_text: str,
    output_text_path: str,
    inputs: list[InputSnapshot],
    *,
    force: bool,
    parent_lease: DirectoryLease | None = None,
) -> Path:
    """Create a new output without following aliases or swapped parents.

    New targets are create-only. POSIX publishes a private temporary through a
    no-overwrite directory-relative link. Windows creates its final name through
    the held directory handle after preparing the payload. Forced Windows writes
    preflight and compare identity, size, last-write, change state, and a
    transient bounded SHA-256 digest through an exact regular-file handle held
    without write or delete sharing through truncation, write, and flush.
    Other platforms fail closed for forced
    replacement because their portable APIs cannot prove the target remained
    unaliased and mutation-stable through publication.
    """
    try:
        data = output_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ToolError("invalid-unicode-scalar") from exc
    if len(data) > MAX_OUTPUT_BYTES:
        raise ToolError("output-too-large")
    output = _absolute_path(output_text_path)
    output_name = _require_output_leaf(output)
    lease, close_lease = _lease_for_output(output, parent_lease)
    temporary_name: str | None = None
    temporary_descriptor: int | None = None
    try:
        lease.assert_stable()
        try:
            existing = _check_output_entry(lease, output_name, inputs)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not force:
                raise ToolError("output-exists")
            _force_write_windows(lease, output_name, data, inputs, existing)
            lease.assert_stable()
            return output

        temporary_name, temporary_descriptor = _create_temporary(lease)
        _check_descriptor(temporary_descriptor, inputs)
        _write_all(temporary_descriptor, data)
        _check_descriptor(temporary_descriptor, inputs)
        os.close(temporary_descriptor)
        temporary_descriptor = None

        lease.assert_stable()
        _check_absent(lease, output_name, inputs)
        _run_test_hook("before-publish", output)
        # This is immediately before publication and catches deterministic
        # hardlink or parent-junction substitutions.
        lease.assert_stable()
        _check_absent(lease, output_name, inputs)
        _run_test_hook("after-final-validation", output)
        _publish_create_only(lease, temporary_name, output_name, data, inputs)
        lease.assert_stable()
        # Publication has two links only while the private temporary name
        # exists. Remove it before requiring the public output to be unique.
        _unlink_in_parent(lease, temporary_name)
        temporary_name = None
        _check_output_entry(lease, output_name, inputs)
        lease.assert_stable()
        return output
    finally:
        if temporary_descriptor is not None:
            try:
                os.close(temporary_descriptor)
            except OSError:
                pass
        if temporary_name is not None:
            _unlink_in_parent(lease, temporary_name)
        if close_lease:
            lease.close()
