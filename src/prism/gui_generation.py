"""GUI-only coordination for manual exports outside project outputs."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from .generation_core import (
    GenerationCoreError,
    finalize_generation,
    plan_generation,
    render_generation,
)
from .generation_lock import GenerationLock
from .lifecycle import load_model
from .project import PROJECT_FILENAME, ProjectError
from .project_lock import LockReleaseError
from .schedule import load_work_calendar
from .storage import ProjectTransaction


def _response(data: Dict[str, Any], revision: Optional[str], status: str,
              commit_state: str, error: Optional[Dict[str, Any]] = None,
              warnings: Sequence[Dict[str, Any]] = (),
              review_checklist: Optional[str] = None) -> Dict[str, Any]:
    return {
        "ok": error is None,
        "status": status,
        "data": data,
        "warnings": list(warnings),
        "revision": revision,
        "commit_state": commit_state,
        "records_saved": None,
        "review_checklist": review_checklist,
        "error": error,
    }


def _error(code: str, message: str, details: Optional[Dict[str, Any]] = None,
           retryable: bool = False, commit_state: str = "not_committed") -> Dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "retryable": retryable,
        "commit_state": commit_state,
        "details": details or {},
    }


def _installed_model(project: Any, roots: Iterable[Path]) -> Any:
    matches = []
    for root in roots:
        root = Path(root).resolve()
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/model.json")):
            try:
                model = load_model(path)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                raise GenerationCoreError(
                    "MODEL_INCOMPATIBLE", "已安装模型无效",
                    {"path": str(path), "reason": str(error)},
                )
            if (model.id, model.version) == (project.model_id, project.model_version):
                matches.append(model)
    if not matches:
        raise GenerationCoreError(
            "MODEL_NOT_FOUND", "项目引用的模型未安装",
            {"id": project.model_id, "version": project.model_version},
        )
    if len(matches) > 1:
        raise GenerationCoreError(
            "MODEL_INCOMPATIBLE", "检测到重复模型 id/version",
            {"id": project.model_id, "version": project.model_version,
             "paths": [str(model.source_dir) for model in matches]},
        )
    return matches[0]


def _export_locked(application_root: Path, project_root: Path, destination: Path,
                   model_roots: Iterable[Path]) -> Dict[str, Any]:
    with ProjectTransaction(project_root) as transaction:
        snapshot = deepcopy(transaction.project)
    revision = snapshot.loaded_revision
    data = {"preflight": False, "expected_outputs": [], "results": []}
    try:
        model = _installed_model(snapshot, model_roots)
        calendar = load_work_calendar(
            application_root / "assets/calendars/cn_workdays_2026_2035.json",
        )
        plans = plan_generation(
            snapshot, application_root / "assets", model, calendar,
        )
        data["expected_outputs"] = [
            {"template_id": plan.template_id,
             "output": str((destination / Path(plan.output).name).resolve())}
            for plan in plans
        ]
        results, wrote_output, audit_issues = render_generation(
            snapshot, plans, calendar, destination=destination,
        )
    except (OSError, ValueError, GenerationCoreError, ProjectError) as error:
        code = getattr(error, "code", "GENERATION_FAILED")
        return _response(
            data, revision, "failed", "not_committed",
            _error(code, "GUI 外部导出批次执行失败",
                   {"reason": str(error), **dict(getattr(error, "details", {}) or {})},
                   bool(getattr(error, "retryable", False))),
        )

    finalization = finalize_generation(
        project_root, plans, results, audit_issues, destination=destination,
    )
    wrote_output = wrote_output or finalization.wrote_output
    data["results"] = results
    failures = [item for item in results if item["render_status"] == "failed"]
    commit_state = "committed" if wrote_output else "not_committed"
    if failures or finalization.checklist_failed:
        status = "partial_failure" if len(failures) < len(results) else "failed"
        details = {"failed_template_ids": [item["template_id"] for item in failures]}
        if finalization.checklist_failed:
            status = "partial_failure"
            details.update(stage="review_checklist",
                           reason=finalization.checklist_message)
        return _response(
            data, revision, status, commit_state,
            _error("GENERATION_FAILED", "一个或多个模板导出失败", details,
                   commit_state=commit_state),
            finalization.warnings, finalization.review_checklist,
        )
    status = "success_with_warnings" if finalization.warnings else "success"
    return _response(
        data, revision, status, commit_state, warnings=finalization.warnings,
        review_checklist=finalization.review_checklist,
    )


def export_documents(application_root: Path, project_root: Path, destination: Path,
                     model_roots: Optional[Iterable[Path]] = None) -> Dict[str, Any]:
    """Export through a GUI-only entry point without persisting project outputs."""
    application_root = Path(application_root).resolve()
    project_root = Path(project_root)
    destination = Path(destination)
    data = {"preflight": False, "expected_outputs": [], "results": []}
    if not project_root.is_absolute() or not project_root.is_dir():
        return _response(
            data, None, "failed", "not_committed",
            _error("PROJECT_NOT_FOUND", "项目目录不存在", {"project": str(project_root)}),
        )
    project_root = project_root.resolve()
    if not (project_root / PROJECT_FILENAME).is_file():
        return _response(
            data, None, "failed", "not_committed",
            _error("PROJECT_NOT_FOUND", "目录中没有 基本数据.json",
                   {"project": str(project_root)}),
        )
    if not destination.is_absolute():
        return _response(
            data, None, "failed", "not_committed",
            _error("INVALID_ARGUMENT", "外部导出目录必须是绝对路径",
                   {"destination": str(destination)}),
        )
    destination = destination.resolve()
    try:
        destination.relative_to((project_root / "outputs").resolve())
    except ValueError:
        pass
    else:
        return _response(
            data, None, "failed", "not_committed",
            _error("INVALID_ARGUMENT", "外部导出目录不能位于项目 outputs 内",
                   {"destination": str(destination)}),
        )
    if model_roots is None:
        appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
        model_roots = (
            application_root / "assets/models",
            appdata / "PRISM/models",
        )
    response = None
    try:
        with GenerationLock(project_root):
            response = _export_locked(
                application_root, project_root, destination, model_roots,
            )
    except LockReleaseError as error:
        if response is None:
            return _response(
                data, None, "failed", "not_committed",
                _error("PROJECT_LOCKED", str(error),
                       {"lock_release_error": str(error)}),
            )
        result = deepcopy(response)
        details = {"lock_release_error": str(error)}
        if result["error"] is None:
            result["ok"] = False
            result["status"] = "failed"
            result["error"] = _error(
                "PROJECT_LOCKED", "生成锁释放失败：{}".format(error), details,
                commit_state=result["commit_state"],
            )
        else:
            result["error"]["details"].update(details)
        return result
    except ProjectError as error:
        return _response(
            data, None, "failed", getattr(error, "commit_state", "not_committed"),
            _error(getattr(error, "code", "PROJECT_LOAD_FAILED"), str(error),
                   dict(getattr(error, "details", {}) or {}),
                   bool(getattr(error, "retryable", False)),
                   getattr(error, "commit_state", "not_committed")),
        )
    except Exception as error:
        return _response(
            data, None, "failed", "not_committed",
            _error("PROJECT_LOAD_FAILED", "GUI 外部导出执行失败",
                   {"reason": str(error)}),
        )
    return response
