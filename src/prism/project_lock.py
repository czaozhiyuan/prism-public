"""Short Windows project locks with handle-bound ownership and deletion."""

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import socket
import threading
import time
from uuid import uuid4

from .project import ProjectError


LOCK_FILENAME = '.prism.lock'


class ProjectLocked(ProjectError):
    def __init__(self, path, reason):
        super().__init__('项目锁不可用：{}；{}'.format(path, reason), 'PROJECT_LOCKED')
        self.retryable = True
        self.commit_state = 'not_committed'
        self.details = {'path': str(path), 'reason': reason}


class LockReleaseError(ProjectError):
    """Lock cleanup failed; the business caller supplies its commit outcome."""

    def __init__(self, error):
        super().__init__('项目锁释放失败：{}'.format(error), 'PROJECT_LOCKED')
        self.retryable = False
        self.lock_release_error = str(error)


class _FileInfo(ctypes.Structure):
    _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME),
                ('accessed', wintypes.FILETIME), ('written', wintypes.FILETIME),
                ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]


@lru_cache(maxsize=1)
def _api():
    if os.name != 'nt':
        raise ProjectError('项目锁当前只支持 Windows 本机文件系统', 'UNSUPPORTED_PLATFORM')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateFileW': ([wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                         wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                         wintypes.HANDLE], wintypes.HANDLE),
        'GetFileInformationByHandle': ([wintypes.HANDLE, ctypes.POINTER(_FileInfo)], wintypes.BOOL),
        'SetFileInformationByHandle': ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                       wintypes.DWORD], wintypes.BOOL),
        'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
        'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        'WaitForSingleObject': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        'GetProcessTimes': ([wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4,
                           wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes = arguments
        function.restype = result
    return kernel


def _process_start(pid):
    """Return a creation timestamp, 'gone', or 'unknown'; never guess stale."""
    kernel = _api()
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        return 'gone' if ctypes.get_last_error() == 87 else 'unknown'
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        if status == 0:
            return 'gone'
        if status != 258:
            return 'unknown'
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            return 'unknown'
        return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def process_start_identity(pid):
    """Return the stable Windows process creation identity used by lock ownership."""
    return _process_start(pid)


class ProjectLock:
    """No reentrancy: the outer operation owns this handle until it returns."""

    def __init__(self, root, timeout=0.0, process_type='prism'):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('timeout must be a finite nonnegative number')
        self.root = Path(root).resolve()
        self.path = self.root / LOCK_FILENAME
        self.timeout = timeout
        self.process_type = process_type
        self.token = uuid4().hex
        self._file = None
        self._thread = None

    def _open(self):
        import msvcrt
        kernel = _api()
        # DELETE access and share=0 protect both contents and pathname identity.
        access = 0x80000000 | 0x40000000 | 0x00010000
        flags = 0x80 | 0x00200000
        handle = kernel.CreateFileW(str(self.path), access, 0, None, 1, flags, None)
        created = handle != ctypes.c_void_p(-1).value
        if not created:
            error = ctypes.get_last_error()
            if error not in (80, 183):
                raise ProjectLocked(self.path, 'exclusive_create: {}'.format(error))
            handle = kernel.CreateFileW(str(self.path), access, 0, None, 3, flags, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ProjectLocked(self.path, 'exclusive_open: {}'.format(ctypes.get_last_error()))
        try:
            info = _FileInfo()
            if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.attributes & (0x400 | 0x10) or info.links != 1:
                raise ProjectLocked(self.path, 'lock is a reparse point, directory, or hard link')
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        try:
            self._file = os.fdopen(descriptor, 'r+b')
        except BaseException:
            os.close(descriptor)
            raise
        return created

    def _record(self):
        def unique_pairs(entries):
            result = {}
            for key, value in entries:
                if key in result:
                    raise ValueError('duplicate lock field')
                result[key] = value
            return result
        self._file.seek(0)
        value = self._file.read(8193)
        if len(value) > 8192:
            raise ValueError('oversized lock record')
        return json.loads(value.decode('utf-8'), object_pairs_hook=unique_pairs)

    def _stale(self, record):
        if not isinstance(record, dict):
            return False
        if (type(record.get('pid')) is not int or not 0 < record['pid'] <= 0xffffffff
                or type(record.get('process_start')) is not int or record['process_start'] <= 0
                or not isinstance(record.get('owner_token'), str)
                or not re.fullmatch(r'[0-9a-f]{32}', record['owner_token'])
                or not isinstance(record.get('created_at'), str) or not record['created_at']
                or not isinstance(record.get('process_type'), str) or not record['process_type']
                or record.get('host') != socket.gethostname()):
            return False
        try:
            datetime.fromisoformat(record['created_at'])
        except ValueError:
            return False
        current = _process_start(record['pid'])
        return current == 'gone' or (type(current) is int and current != record['process_start'])

    def _delete_owned_handle(self):
        import msvcrt
        # FILE_DISPOSITION_INFO uses a one-byte BOOLEAN, not a Win32 BOOL.
        disposition = ctypes.c_ubyte(1)
        handle = msvcrt.get_osfhandle(self._file.fileno())
        if not _api().SetFileInformationByHandle(handle, 4, ctypes.byref(disposition),
                                                 ctypes.sizeof(disposition)):
            raise ctypes.WinError(ctypes.get_last_error())

    def acquire(self):
        if self._file is not None:
            raise ProjectLocked(self.path, 'nested acquisition is not allowed')
        stamp = _process_start(os.getpid())
        if type(stamp) is not int:
            raise ProjectLocked(self.path, 'cannot establish process identity')
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                created = self._open()
                break
            except ProjectLocked:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.05, remaining))
        owned = created
        try:
            if not created:
                try:
                    stale = self._stale(self._record())
                except (ValueError, UnicodeError):
                    stale = False
                if not stale:
                    raise ProjectLocked(self.path, 'ownership unknown or owner still alive')
                # Recovery updates the exclusively held file, never unlinks a path.
                owned = True
            record = dict(pid=os.getpid(), process_start=stamp, owner_token=self.token,
                          created_at=datetime.now(timezone.utc).isoformat(),
                          process_type=self.process_type, host=socket.gethostname())
            self._file.seek(0)
            self._file.write(json.dumps(record, ensure_ascii=False).encode('utf-8'))
            self._file.truncate()
            self._file.flush()
            os.fsync(self._file.fileno())
            self._thread = threading.get_ident()
            return self
        except BaseException:
            try:
                if owned:
                    self._delete_owned_handle()
            finally:
                self._file.close()
                self._file = None
            raise

    def require_owned(self, root):
        if (self._file is None or self._thread != threading.get_ident()
                or Path(root).resolve() != self.root):
            raise ProjectLocked(self.path, 'operation does not own this project lock')

    def release(self):
        self.require_owned(self.root)
        try:
            try:
                record = self._record()
                matches = isinstance(record, dict) and record.get('owner_token') == self.token
            except (ValueError, UnicodeError):
                matches = False
            if not matches:
                raise ProjectLocked(self.path, 'owner token changed; lock retained')
            self._delete_owned_handle()
        finally:
            self._file.close()
            self._file = None
            self._thread = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, error_type, error, traceback):
        try:
            self.release()
        except (OSError, ProjectError) as release_error:
            if error is None:
                raise LockReleaseError(release_error) from release_error
            # Keep the original transaction outcome when cleanup also fails.
            error.lock_release_error = str(release_error)
        return False
