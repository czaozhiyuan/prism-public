"""Transactional GUI writes with explicit revision baselines."""

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project import Project
from .storage import ProjectTransaction, _import_data_locked, _remove_module_locked, apply_group_import


@dataclass
class GuiWriteResult:
    project: Project
    value: Any = None


def _rebase_editable_state(snapshot: Project, latest: Project) -> Project:
    """Bind GUI business state to the confirmed disk snapshot metadata."""
    candidate = deepcopy(snapshot)
    candidate.root = latest.root
    candidate.outputs = deepcopy(latest.outputs)
    candidate._storage_root = latest._storage_root
    candidate._loaded_revision = latest.loaded_revision
    candidate._module_files = deepcopy(latest._module_files)
    return candidate


def save(snapshot: Project, expected_revision: str) -> GuiWriteResult:
    with ProjectTransaction(snapshot.root, expected_revision=expected_revision) as transaction:
        candidate = _rebase_editable_state(snapshot, transaction.project)
        return GuiWriteResult(transaction.commit(candidate))


def import_data(snapshot: Project, source: Path, overwrite_basic: bool,
                replace_existing: bool, expected_revision: str, application_root=None) -> GuiWriteResult:
    with ProjectTransaction(snapshot.root, expected_revision=expected_revision) as transaction:
        candidate = _rebase_editable_state(snapshot, transaction.project)
        value = _import_data_locked(candidate, source, overwrite_basic, replace_existing, transaction, application_root)
        return GuiWriteResult(candidate, value)


def import_group(snapshot: Project, payload: dict, expected_revision: str, dry_run=False) -> GuiWriteResult:
    """Preview and commit a group patch against one explicit disk snapshot."""
    with ProjectTransaction(snapshot.root, expected_revision=expected_revision) as transaction:
        candidate = deepcopy(transaction.project)
        value = apply_group_import(candidate, payload)
        return GuiWriteResult(candidate if dry_run else transaction.commit(candidate), value)


def remove_module(snapshot: Project, module_id: str, delete_file: bool,
                  expected_revision: str) -> GuiWriteResult:
    with ProjectTransaction(snapshot.root, expected_revision=expected_revision) as transaction:
        candidate = _rebase_editable_state(snapshot, transaction.project)
        value = _remove_module_locked(candidate, module_id, delete_file, transaction)
        return GuiWriteResult(candidate, value)
