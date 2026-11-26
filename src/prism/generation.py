"""DOCX and XLSX template inspection and generation services."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .project import sanitize_filename
from .schedule import WorkCalendar
from .schedule_template import export_schedule_template
from .template_ids import is_key_value_template
from .template_text import template_environment
from .document_versions import context_for_template, validate_version_references
from .handbook import handbook_template, handbook_not_applicable, NOT_APPLICABLE


PLACEHOLDER = re.compile(r"{{\s*([^{}]+?)\s*}}")
_FINALIZATION_ISSUES: Dict[Path, List[Tuple[str, str, str, str]]] = {}


class GenerationError(RuntimeError):
    """Raised when a template cannot be generated safely."""


@dataclass(frozen=True)
class TemplateCheck:
    """Preflight result for one template."""

    path: Path
    kind: str
    placeholders: Set[str]
    missing: Set[str]
    error: str = ""

    @property
    def usable(self) -> bool:
        return not self.error


@dataclass(frozen=True)
class GeneratedFile:
    """Result of generating one output file."""

    template: Path
    output: Optional[Path]
    status: str
    message: str


def _normalize_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return value


def _load_sidecar(template: Path) -> Dict[str, Any]:
    path = template.with_suffix(".mapping.json")
    if not path.is_file():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8")))


def inspect_docx(template: Path, context: Mapping[str, Any]) -> TemplateCheck:
    """Inspect Jinja variables through docxtpl's parser."""
    try:
        from docxtpl import DocxTemplate
        from .document_context import document_context
        context = context_for_template(template, context)
        context.update(document_context(context))
        document = DocxTemplate(str(handbook_template(template, context)))
        validate_version_references(document, context['document_versions'])
        from .pictures import inspect_picture_slots
        placeholders, picture_slots = inspect_picture_slots(document)
        missing = {key for key in placeholders
                   if key not in picture_slots and not str(context.get(key, "")).strip()}
        from .schema import TOOL_ALIASES
        missing = {TOOL_ALIASES.get(key, key) for key in missing}
        if 'baseline_ids' in placeholders and any(not str(context.get(k, '')).strip() for k in ('项目名', '器件类型', '版本')):
            return TemplateCheck(template, "DOCX", placeholders, missing, '未配置基线标识规则：请填写项目名、器件类型和版本')
        return TemplateCheck(template, "DOCX", placeholders, missing)
    except Exception as error:
        return TemplateCheck(template, "DOCX", set(), set(), str(error))


def inspect_xlsx(template: Path, context: Mapping[str, Any]) -> TemplateCheck:
    """Inspect cell placeholders and mapped defined names in an XLSX template."""
    try:
        from openpyxl import load_workbook
        workbook = load_workbook(str(template), read_only=False, data_only=False)
        placeholders: Set[str] = set()
        try:
            for sheet in workbook.worksheets:
                for row in sheet.iter_rows():
                    for cell in row:
                        if isinstance(cell.value, str):
                            placeholders.update(value.strip() for value in PLACEHOLDER.findall(cell.value))
            sidecar = _load_sidecar(template)
            placeholders.update(str(value) for value in sidecar.get("defined_names", {}).values())
            placeholders.update(str(value) for value in sidecar.get("cells", {}).values())
        finally:
            workbook.close()
        missing = {key for key in placeholders if not str(context.get(key, "")).strip()}
        return TemplateCheck(template, "XLSX", placeholders, missing)
    except Exception as error:
        return TemplateCheck(template, "XLSX", set(), set(), str(error))


def inspect_template(template: Path, context: Mapping[str, Any]) -> TemplateCheck:
    suffix = template.suffix.lower()
    if is_key_value_template(template):
        return TemplateCheck(template, "XLSX", set(), set(), "已停止生成键值表，请使用基本数据.json")
    if suffix == ".docx":
        return inspect_docx(template, context)
    if suffix == ".xlsx":
        return inspect_xlsx(template, context)
    return TemplateCheck(template, suffix.upper().lstrip("."), set(), set(), "仅支持 DOCX/XLSX")


def _replace_cell_text(value: str, context: Mapping[str, Any]) -> str:
    def replacement(match: Any) -> str:
        return str(_normalize_value(context.get(match.group(1).strip(), "")))
    return PLACEHOLDER.sub(replacement, value)


def _output_name(template: Path) -> str:
    stem = template.stem.replace("_模板", "").replace("（模板）", "")
    return sanitize_filename(stem) + template.suffix.lower()


def planned_output_name(template: Path, context: Mapping[str, Any]) -> str:
    """Return the exact filename produced by the current template renderer."""
    if template.suffix.lower() == ".docx":
        title = re.sub(r'^\d+(?:\.\d+)?\.?', '', Path(_output_name(template)).stem)
        title = title.replace('（有几个表要手动填）', '')
        return sanitize_filename('☆{}☆-{}-{}'.format(
            context.get('密级', ''), context.get('项目名', ''), title)) + '.docx'
    if "进度计划" in template.name:
        return template.name
    return _output_name(template)


def render_docx(template: Path, output_dir: Path, context: Mapping[str, Any],
                project_root: Optional[Path] = None) -> Path:
    """Render one DOCX template without automating Microsoft Word."""
    try:
        from docxtpl import DocxTemplate
    except ImportError as error:
        raise GenerationError("缺少 docxtpl，无法生成 DOCX") from error
    from .document_context import document_context
    from .pictures import inspect_picture_slots, resolve_pictures, resolve_test_pictures
    from .word_finalize import finalize_docx
    values = {key: _normalize_value(value) for key, value in context_for_template(template, context).items()}
    values.update(document_context(values))
    document = DocxTemplate(str(handbook_template(template, context)))
    validate_version_references(document, values['document_versions'])
    # Only variables declared by this template participate in picture resolution.
    if project_root is None:
        # Callers that render straight into <project>/outputs can be inferred;
        # generation_core renders into a staging directory and passes the root.
        project_root = Path(output_dir).resolve().parent
    declared, slots = inspect_picture_slots(document)
    picture_values, picture_issues = resolve_pictures(document, Path(project_root), slots, values)
    values.update(picture_values)
    picture_issues.extend(resolve_test_pictures(document, Path(project_root), values, declared))
    if 'baseline_ids' in declared and any(not str(values.get(k, '')).strip() for k in ('项目名', '器件类型', '版本')):
        raise GenerationError('未配置基线标识规则：请填写项目名、器件类型和版本')
    from .document_review import input_review_issues
    input_issues = input_review_issues(template, document, context, declared)
    if handbook_not_applicable(template, context):
        input_issues.append(('P5-保障手册适用性', '器件类型', NOT_APPLICABLE,
                             '器件类型应为 MCU 或 FPGA；本次已按 MCU 模板生成，请人工核对适用性'))
    document.render(values, jinja_env=template_environment(), autoescape=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / planned_output_name(template, values)
    document.save(str(output))
    issues = finalize_docx(output)
    issues.extend(picture_issues)
    issues.extend(input_issues)
    # Keep diagnostics in this generation process; only the requested Excel
    # checklist is emitted into the user's output directory.
    _FINALIZATION_ISSUES[output.resolve()] = issues
    return output


def _write_defined_names(workbook: Any, mapping: Mapping[str, str], context: Mapping[str, Any]) -> None:
    for defined_name, key in mapping.items():
        item = workbook.defined_names.get(defined_name)
        if item is None:
            raise GenerationError("XLSX 缺少定义名称：{}".format(defined_name))
        for sheet_name, coordinate in item.destinations:
            workbook[sheet_name][coordinate] = _normalize_value(context.get(key, ""))


def render_xlsx_template(template: Path, output_dir: Path, context: Mapping[str, Any]) -> Path:
    """Fill string placeholders, mapped cells, and defined names in an XLSX template."""
    if is_key_value_template(template):
        raise GenerationError("已停止生成键值表，请使用基本数据.json")
    try:
        from openpyxl import load_workbook
    except ImportError as error:
        raise GenerationError("缺少 openpyxl，无法生成 XLSX") from error
    workbook = load_workbook(str(template), data_only=False)
    sidecar = _load_sidecar(template)
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and "{{" in cell.value:
                        cell.value = _replace_cell_text(cell.value, context)
        for address, key in sidecar.get("cells", {}).items():
            if "!" not in address:
                raise GenerationError("无效单元格映射：{}".format(address))
            sheet_name, coordinate = address.rsplit("!", 1)
            workbook[sheet_name][coordinate] = _normalize_value(context.get(str(key), ""))
        _write_defined_names(workbook, sidecar.get("defined_names", {}), context)
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / _output_name(template)
        workbook.save(str(output))
        return output
    finally:
        workbook.close()


def generate_batch(
    templates: Sequence[Path],
    output_dir: Path,
    context: Mapping[str, Any],
    project_name: str,
    schedule: Mapping[str, Any],
    calendar: WorkCalendar,
    finalize: bool = True,
    project_root: Optional[Path] = None,
) -> List[GeneratedFile]:
    """Generate selected templates and continue after per-file failures."""
    results: List[GeneratedFile] = []
    for template in templates:
        try:
            if template.suffix.lower() == ".docx":
                output = render_docx(template, output_dir, context, project_root)
            elif "进度计划" in template.name:
                output = export_schedule_template(template, output_dir / template.name, context, schedule, calendar)
            else:
                output = render_xlsx_template(template, output_dir, context)
            results.append(GeneratedFile(template, output, "成功", "已生成"))
        except Exception as error:
            results.append(GeneratedFile(template, None, "失败", str(error)))
    if finalize:
        finish_batch(results, output_dir)
    return results


def take_finalization_issues(path: Path) -> List[Tuple[str, str, str, str]]:
    """Return and forget OOXML audit issues collected for one private render."""
    return _FINALIZATION_ISSUES.pop(Path(path).resolve(), [])


def review_rows_for_document(template: Path, output: Path,
                             issues: Sequence[Tuple[str, str, str, str]] = ()) -> list:
    """Inspect one final DOCX through the shared purpose-aware review rules."""
    from .document_review import review_rows_for_document as collect_rows
    return collect_rows(template, output, issues)


def finish_batch(results: List[GeneratedFile], output_dir: Path) -> None:
    """Finalize this run and append one review workbook to its visible outputs."""
    from .word_finalize import update_word_fields
    from .review_checklist import write_review_checklist
    docs = [r for r in results if r.output and r.output.suffix == '.docx' and r.output.exists()]
    if not docs:
        return
    rows = []
    word_unavailable = None
    for result in docs:
        issues = take_finalization_issues(result.output)
        try:
            if word_unavailable:
                raise RuntimeError(word_unavailable)
            update_word_fields(result.output)
        except (ImportError, ModuleNotFoundError) as error:
            word_unavailable = str(error)
            rows.append((result.output.name, 'P1-05/P1-06', '目录及页脚', 'Word 域未重算', '请用 Word 打开文档更新页码；目录已按当前正文重建。'+str(error)))
        except Exception as error:
            rows.append((result.output.name, 'P1-05/P1-06', '目录及页脚', 'Word 域更新失败', '请人工更新并核对：'+str(error)))
        rows.extend(review_rows_for_document(result.template, result.output, issues))
    output = output_dir / '待补充清单.xlsx'
    try:
        write_review_checklist(output, rows)
        results.append(GeneratedFile(Path('待补充清单'), output, '成功', '已汇总本次生成文档的待补充及人工审核项'))
    except Exception as error:
        results.append(GeneratedFile(Path('待补充清单'), None, '失败', str(error)))
