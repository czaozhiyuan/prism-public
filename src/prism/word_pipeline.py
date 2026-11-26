"""Bounded parent-side control for the isolated Word finalization worker."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import List, Optional

from .project_lock import process_start_identity
from .word_worker import FROZEN_WORKER_ARG


WORD_TIMEOUT_SECONDS = 120.0
WORD_CLEANUP_SECONDS = 10.0


@dataclass
class WordOutcome:
    status: str
    message: str
    cleanup_warnings: List[str]


def _read_status(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _terminate_owned_process(pid: object, expected_start: object, deadline: float) -> Optional[str]:
    """Terminate only a PID whose creation identity matches the worker record."""
    if type(pid) is not int or type(expected_start) is not int:
        return "private Word identity was not recorded; no external process was terminated"
    current = process_start_identity(pid)
    if current == "gone":
        return None
    if current != expected_start:
        return "private Word identity could not be verified; no external process was terminated"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x0001 | 0x00100000, False, pid)
    if not handle:
        return "private Word process could not be opened for bounded cleanup"
    try:
        if not kernel.TerminateProcess(handle, 1):
            return "private Word process termination failed"
        remaining = max(0.0, deadline - time.monotonic())
        if kernel.WaitForSingleObject(handle, int(remaining * 1000)) != 0:
            return "private Word process did not exit within the cleanup deadline"
    finally:
        kernel.CloseHandle(handle)
    return None


def _worker_command(document: Path, status: Path) -> list:
    if getattr(sys, "frozen", False):
        return [sys.executable, FROZEN_WORKER_ARG, str(document), str(status)]
    return [sys.executable, "-m", "prism.word_worker", str(document), str(status)]


def run_word_worker(source: Path, project_root: Path,
                    timeout: float = WORD_TIMEOUT_SECONDS,
                    cleanup_timeout: float = WORD_CLEANUP_SECONDS) -> WordOutcome:
    """Finalize a private copy, publishing it only after a successful worker exit."""
    source = Path(source).resolve()
    root = Path(project_root).resolve()
    if timeout <= 0 or cleanup_timeout <= 0 or cleanup_timeout > WORD_CLEANUP_SECONDS:
        raise ValueError("invalid Word worker timeout")
    try:
        source.relative_to(root)
    except ValueError:
        raise ValueError("Word source must stay inside the project root")
    if source.is_symlink() or not source.is_file():
        raise ValueError("Word source must be a regular rendered file")

    workspace = Path(tempfile.mkdtemp(prefix=".prism-word-", dir=str(root))).resolve()
    private = workspace / "document.docx"
    status_path = workspace / "status.json"
    warnings: List[str] = []
    process = None
    outcome = None
    cleanup_deadline = None
    try:
        shutil.copy2(str(source), str(private))
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            _worker_command(private, status_path), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            startupinfo=startupinfo, creationflags=creationflags,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            state = _read_status(status_path)
            cleanup_deadline = time.monotonic() + cleanup_timeout
            try:
                process.terminate()
                process.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
            except (OSError, subprocess.TimeoutExpired) as error:
                warnings.append("Word worker cleanup failed: {}".format(error))
            warning = _terminate_owned_process(
                state.get("word_pid"), state.get("word_start"), cleanup_deadline,
            )
            if warning:
                warnings.append(warning)
            outcome = WordOutcome("timeout", "Word finalization exceeded {} seconds".format(timeout), warnings)
        else:
            state = _read_status(status_path)
            worker_state = state.get("state")
            message = str(state.get("message", ""))
            if process.returncode == 0 and worker_state == "success" and private.is_file():
                os.replace(str(private), str(source))
                outcome = WordOutcome("success", message or "Word fields updated", warnings)
            elif worker_state == "unstable":
                outcome = WordOutcome("unstable", message or "Word pagination is unstable", warnings)
            elif worker_state == "unavailable":
                outcome = WordOutcome("unavailable", message or "Microsoft Word is unavailable", warnings)
            else:
                outcome = WordOutcome("failed", message or "Word finalization worker failed", warnings)
    except Exception as error:
        outcome = WordOutcome("failed", str(error), warnings)
    finally:
        if process is not None and process.poll() is None:
            if cleanup_deadline is None:
                cleanup_deadline = time.monotonic() + cleanup_timeout
            try:
                process.terminate()
                process.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
            except (OSError, subprocess.TimeoutExpired) as error:
                warnings.append("Word worker remained active: {}".format(error))
        try:
            shutil.rmtree(str(workspace))
        except OSError as error:
            warnings.append("Word worker temporary material cleanup failed: {}".format(error))
    if outcome is None:
        return WordOutcome("failed", "Word finalization worker produced no result", warnings)
    outcome.cleanup_warnings = warnings
    return outcome
