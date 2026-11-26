"""Shared preflight, rendering, and record helpers for formal generation batches."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .generation import (
    generate_batch,
    inspect_template,
    planned_output_name,
    review_rows_for_document,
    take_finalization_issues,
)
from .items import inspect_items
from .document_versions import context_for_template
from .handbook import HANDBOOK_ID, NOT_APPLICABLE, handbook_not_applicable
from .project import Project, ProjectError
from .project_lock import LockReleaseError
from .review_checklist import write_review_checklist
from .schedule import WorkCalendar
from .storage import ProjectTransaction
from .template_ids import (
    selected_template_ids,
    discover_template_paths,
    encode_template_id,
    relative_parts,
    resolve_template_id,
)
from . import word_pipeline


class GenerationCoreError(RuntimeError):
    """Stable core failure mapped by GUI and Agent adapters."""

    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class PlannedTemplate:
    template_id: str
    path: Path
    output: str
    context: Optional[Dict[str, Any]] = field(default=None, compare=False, repr=False)


@dataclass
class BatchFinalization:
    review_checklist: Optional[str]
    warnings: List[Dict[str, Any]]
    checklist_failed: bool
    checklist_message: str
    wrote_output: bool


def _is_reparse(path: Path) -> bool:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise GenerationCoreError(
            "PATH_OUTSIDE_PROJECT", "无法验证输出路径安全性",
            {"component": str(path), "reason": str(error)},
        )
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)


def safe_project_output(root: Path, relative: str) -> Path:
    """Resolve one canonical outputs path without crossing a link or reparse point."""
    try:
        parts = relative_parts(relative)
    except ProjectError as error:
        raise GenerationCoreError("PATH_OUTSIDE_PROJECT", "输出路径无效",
                                  {"path": relative, "reason": str(error)})
    if len(parts) != 2 or parts[0] != "outputs":
        raise GenerationCoreError("PATH_OUTSIDE_PROJECT", "输出必须位于项目 outputs 目录",
                                  {"path": relative})
    root = Path(root).resolve()
    current = root
    for part in parts:
        current = current / part
        if _is_reparse(current):
            raise GenerationCoreError("PATH_OUTSIDE_PROJECT", "输出路径包含链接或重解析点",
                                      {"path": relative, "component": str(current)})
    target = (root / Path(*parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise GenerationCoreError("PATH_OUTSIDE_PROJECT", "输出路径越出项目目录",
                                  {"path": relative})
    return target


def safe_external_output(destination: Path, name: str) -> Path:
    """Resolve one direct child of a user-selected export directory."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if Path(name).name != name or not name:
        raise GenerationCoreError("PATH_OUTSIDE_PROJECT", "外部导出文件名无效",
                                  {"path": name})
    target = destination / name
    if _is_reparse(target):
        raise GenerationCoreError("PATH_OUTSIDE_PROJECT", "外部导出目标包含链接或重解析点",
                                  {"path": str(target)})
    if target.resolve().parent != destination:
        raise GenerationCoreError("PATH_OUTSIDE_PROJECT", "外部导出目标越出所选目录",
                                  {"path": str(target)})
    return target


def generation_target(root: Path, relative: str,
                      destination: Optional[Path] = None) -> Path:
    """Map one planned output to its formal or external destination."""
    if destination is None:
        return safe_project_output(root, relative)
    return safe_external_output(destination, Path(relative).name)


def preflight_generation(project: Project, templates: Sequence[Tuple[str, Path]],
                         catalog=None) -> List[PlannedTemplate]:
    """Validate one snapshot and calculate its deterministic output set."""
    if "project_start" not in project.schedule:
        raise GenerationCoreError("PROJECT_UNSCHEDULED", "项目尚未设置开始日期",
                                  {"stage": "preflight"})
    if not project.schedule.get("dates") or not project.schedule.get("phases"):
        raise GenerationCoreError("VALIDATION_FAILED", "项目排期缺少派生结果",
                                  {"stage": "preflight", "path": "schedule"})
    item_issues = inspect_items(project)
    if item_issues:
        raise GenerationCoreError("VALIDATION_FAILED", "项目业务数据不完整",
                                  {"stage": "preflight", "issues": item_issues})

    from .pictures import PICTURE_SLOTS, validate_slot_table
    try:
        validate_slot_table(PICTURE_SLOTS)
    except ProjectError as error:
        raise GenerationCoreError("PATH_OUTSIDE_PROJECT", str(error), {"stage": "preflight"})
    context = project.context()
    plans = []
    output_ids: Dict[str, str] = {}
    for template_id, path in templates:
        values = context_for_template(path, context, template_id,
                                      project.templates.get('document_versions', {}), catalog)
        check = inspect_template(path, values)
        if check.error:
            raise GenerationCoreError("VALIDATION_FAILED", "模板无法使用",
                                      {"stage": "preflight", "template_id": template_id,
                                       "reason": check.error})
        relative = "outputs/" + planned_output_name(path, context)
        safe_project_output(project.root, relative)
        key = relative.casefold()
        if key in output_ids:
            raise GenerationCoreError("OUTPUT_NAME_CONFLICT", "多个模板映射到同一输出文件",
                                      {"output": relative,
                                       "template_ids": [output_ids[key], template_id]})
        output_ids[key] = template_id
        plans.append(PlannedTemplate(template_id, Path(path), relative, values))
    return plans


def plan_generation(project: Project, asset_root: Path, model: Any,
                    calendar: WorkCalendar,
                    template_ids: Optional[Sequence[str]] = None) -> List[PlannedTemplate]:
    """Select templates and preflight one generation snapshot."""
    if "project_start" not in project.schedule:
        raise GenerationCoreError("PROJECT_UNSCHEDULED", "项目尚未设置开始日期",
                                  {"stage": "preflight"})
    work_hours = project.schedule.get("work_hours", {})
    try:
        expected_schedule = model.calculate(
            date.fromisoformat(project.schedule["project_start"]), work_hours, calendar,
        )
    except (OSError, ValueError) as error:
        raise GenerationCoreError("INVALID_ARGUMENT", "无法计算项目排期",
                                  {"reason": str(error)})
    if expected_schedule != project.schedule:
        raise GenerationCoreError("VALIDATION_FAILED", "派生排期与当前输入不一致",
                                  {"stage": "preflight", "path": "schedule"})

    asset_root = Path(asset_root).resolve()
    available = {
        encode_template_id(path, asset_root, model): path
        for path in discover_template_paths(asset_root, model)
    }
    selected = selected_template_ids(project.templates, available,
                                     project.fields.get('器件类型', ''), template_ids)
    if not selected:
        raise GenerationCoreError("NO_TEMPLATES_SELECTED", "没有可生成的模板", {})
    missing = [template_id for template_id in selected if template_id not in available]
    if missing:
        raise GenerationCoreError("TEMPLATE_NOT_FOUND", "找不到模板",
                                  {"template_ids": missing})
    try:
        templates = [
            (template_id, resolve_template_id(template_id, asset_root, model))
            for template_id in selected
        ]
    except Exception as error:
        raise GenerationCoreError(
            "TEMPLATE_NOT_FOUND", str(error),
            dict(getattr(error, "details", {}) or {}),
        )
    return preflight_generation(project, templates, available)


def render_generation(project: Project, plans: Sequence[PlannedTemplate],
                      calendar: WorkCalendar,
                      destination: Optional[Path] = None) -> Tuple[
                          List[Dict[str, Any]], bool,
                          Dict[str, List[Tuple[str, str, str, str]]],
                      ]:
    """Render independently in a private batch directory and publish successes."""
    root = Path(project.root).resolve()
    stage_root = root if destination is None else Path(destination).resolve()
    stage_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".prism-generate-", dir=str(stage_root))).resolve()
    wrote_output = False
    results: List[Dict[str, Any]] = []
    audit_issues: Dict[str, List[Tuple[str, str, str, str]]] = {}
    try:
        for plan in plans:
            output = None
            message = ""
            try:
                context = plan.context if plan.context is not None else context_for_template(
                    plan.path, project.context(), plan.template_id,
                    project.templates.get('document_versions', {}))
                generated = generate_batch([plan.path], stage, context, project.name,
                                           project.schedule, calendar, finalize=False,
                                           project_root=root)
                if len(generated) != 1:
                    raise RuntimeError("renderer returned an unexpected result count")
                item = generated[0]
                if item.status not in ("成功", "success") or item.output is None:
                    raise RuntimeError(item.message or "render failed")
                staged = Path(item.output).resolve()
                if staged.parent != stage or staged.name != Path(plan.output).name or not staged.is_file():
                    raise RuntimeError("renderer returned an unexpected output path")
                issues = take_finalization_issues(staged) if staged.suffix.lower() == ".docx" else []
                target = generation_target(root, plan.output, destination)
                target.parent.mkdir(parents=True, exist_ok=True)
                generation_target(root, plan.output, destination)
                os.replace(str(staged), str(target))
                output = plan.output if destination is None else str(target)
                message = item.message
                wrote_output = True
                if issues:
                    audit_issues[plan.template_id] = issues
            except Exception as error:
                message = str(error)
            results.append({
                "template_id": plan.template_id,
                "output": output,
                "render_status": "success" if output is not None else "failed",
                "word_finalize_status": "not_applicable",
                "message": message,
                "warnings": [],
            })
    finally:
        if stage.parent == stage_root:
            shutil.rmtree(str(stage), ignore_errors=True)
    return results, wrote_output, audit_issues


def _warning(code: str, message: str, output: Optional[str] = None,
             cleanup_warnings: Sequence[str] = ()) -> Dict[str, Any]:
    details: Dict[str, Any] = {}
    if output is not None:
        details["output"] = output
    if cleanup_warnings:
        details["cleanup_warnings"] = list(cleanup_warnings)
    return {"code": code, "message": message, "details": details}


def finalize_generation(root: Path, plans: Sequence[PlannedTemplate],
                        results: List[Dict[str, Any]],
                        audit_issues: Mapping[str, Sequence[Tuple[str, str, str, str]]],
                        timeout: float = word_pipeline.WORD_TIMEOUT_SECONDS,
                        cleanup_timeout: float = word_pipeline.WORD_CLEANUP_SECONDS,
                        destination: Optional[Path] = None) -> BatchFinalization:
    """Finalize rendered DOCX files and publish one current-batch checklist."""
    root = Path(root).resolve()
    templates = {plan.template_id: plan.path for plan in plans}
    handbook_plans = {plan.template_id: plan for plan in plans
                      if plan.template_id == HANDBOOK_ID}
    warnings: List[Dict[str, Any]] = []
    rows = []
    successful_docs = []
    checklist_errors = []
    for result in results:
        output = result.get("output")
        handbook = handbook_plans.get(result['template_id'])
        if handbook is not None and handbook_not_applicable(handbook.path, handbook.context or {}):
            warning = _warning('VALIDATION_FAILED', NOT_APPLICABLE, output)
            warning['details'].update(reason='HANDBOOK_NOT_APPLICABLE',
                                      device_type=(handbook.context or {}).get('器件类型', ''),
                                      fallback='MCU')
            result['warnings'].append(warning)
            warnings.append(warning)
        if result.get("render_status") != "success" or not output or not output.lower().endswith(".docx"):
            continue
        successful_docs.append(result)
        target = generation_target(root, output if destination is None else Path(output).name,
                                   destination)
        try:
            outcome = word_pipeline.run_word_worker(
                target, root if destination is None else Path(destination).resolve(),
                timeout=timeout, cleanup_timeout=cleanup_timeout,
            )
        except Exception as error:
            outcome = word_pipeline.WordOutcome("failed", str(error), [])
        result["word_finalize_status"] = outcome.status
        if outcome.status != "success" or outcome.cleanup_warnings:
            codes = {
                "unavailable": "WORD_UNAVAILABLE",
                "timeout": "WORD_TIMEOUT",
                "unstable": "WORD_PAGINATION_UNSTABLE",
                "failed": "WORD_FINALIZE_FAILED",
                "success": "WORD_FINALIZE_FAILED",
            }
            warning = _warning(codes[outcome.status], outcome.message, output,
                               outcome.cleanup_warnings)
            result["warnings"].append(warning)
            warnings.append(warning)
        try:
            document_rows = review_rows_for_document(
                templates[result["template_id"]], target,
                audit_issues.get(result["template_id"], ()),
            )
        except Exception as error:
            checklist_errors.append("{}: {}".format(output, error))
            continue
        if outcome.status != "success":
            detail = "Word 处理状态：{}；{}".format(outcome.status, outcome.message)
            fallback = next((index for index, row in enumerate(document_rows)
                             if row[1] == "P5-目录降级"), None)
            if fallback is None:
                document_rows.append((target.name, "P5-目录降级", "目录及页脚 / Word worker", detail,
                                      "用 Word 更新并核对目录、页脚及分页，需视觉核查"))
            else:
                row = document_rows[fallback]
                document_rows[fallback] = (*row[:3], row[3] + "；" + detail, row[4])
        rows.extend(document_rows)
        if document_rows:
            warning = _warning(
                "VALIDATION_FAILED",
                "当前文档包含需要人工审核或补充的项目",
                output,
            )
            warning["details"]["review_items"] = len(document_rows)
            result["warnings"].append(warning)
            warnings.append(warning)

    if not successful_docs:
        return BatchFinalization(None, warnings, False, "", False)
    if checklist_errors:
        return BatchFinalization(None, warnings, True, "; ".join(checklist_errors), False)
    stage_root = root if destination is None else Path(destination).resolve()
    stage_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".prism-checklist-", dir=str(stage_root))).resolve()
    wrote_output = False
    try:
        private = stage / "待补充清单.xlsx"
        write_review_checklist(private, rows)
        target = generation_target(root, "outputs/待补充清单.xlsx", destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        generation_target(root, "outputs/待补充清单.xlsx", destination)
        os.replace(str(private), str(target))
        wrote_output = True
        checklist = "outputs/待补充清单.xlsx" if destination is None else str(target)
        return BatchFinalization(checklist, warnings, False, "", wrote_output)
    except Exception as error:
        return BatchFinalization(None, warnings, True, str(error), wrote_output)
    finally:
        if stage.parent == stage_root:
            shutil.rmtree(str(stage), ignore_errors=True)


def save_generation_outputs(root: Path, outputs: Mapping[str, Any]) -> Project:
    """Reload the latest project and replace only its formal generation records."""
    transaction = ProjectTransaction(root)
    saved = None
    try:
        with transaction:
            saved = transaction.commit_outputs(outputs)
    except LockReleaseError as error:
        if saved is not None:
            error.records_saved = True
            error.saved_project = saved
        raise
    return saved
