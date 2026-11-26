"""Isolated Microsoft Word field-update worker."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path

from .project_lock import process_start_identity


FROZEN_WORKER_ARG = "--prism-word-worker"


def _write_status(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(str(temporary), str(path))


def _word_pid(hwnd: int) -> int:
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    if pid.value <= 0:
        raise RuntimeError("cannot establish private Word process identity")
    return int(pid.value)


def run(document: Path, status_path: Path) -> int:
    """Update one private DOCX copy and publish machine-readable worker state."""
    document = Path(document).resolve()
    status_path = Path(status_path).resolve()
    if document.parent != status_path.parent or document.is_symlink() or not document.is_file():
        raise ValueError("worker paths must identify one private regular file directory")
    state = {
        "state": "starting", "worker_pid": os.getpid(),
        "worker_start": process_start_identity(os.getpid()),
        "word_pid": None, "word_start": None, "message": "",
    }
    _write_status(status_path, state)

    def record_word(hwnd: int) -> None:
        pid = _word_pid(hwnd)
        started = process_start_identity(pid)
        if type(started) is not int:
            raise RuntimeError("cannot establish private Word process identity")
        state.update(state="running", word_pid=pid, word_start=started)
        _write_status(status_path, state)

    try:
        from .word_finalize import update_word_fields, PaginationUnstable, WordUnavailable

        update_word_fields(document, ownership_callback=record_word)
        state.update(state="success", message="Word fields updated")
        _write_status(status_path, state)
        return 0
    except PaginationUnstable as error:
        state.update(state="unstable", message=str(error))
    except (ImportError, ModuleNotFoundError, WordUnavailable) as error:
        state.update(state="unavailable", message=str(error))
    except Exception as error:
        state.update(state="failed", message=str(error))
    _write_status(status_path, state)
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PRISM private Word finalization worker")
    parser.add_argument("document")
    parser.add_argument("status")
    args = parser.parse_args(argv)
    return run(Path(args.document), Path(args.status))


if __name__ == "__main__":
    raise SystemExit(main())
