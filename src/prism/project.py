"""Project persistence and legacy workbook import for PRISM."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


PROJECT_FILENAME = "基本数据.json"
PROJECT_FORMAT_VERSION = 7


class ProjectError(ValueError):
    """Raised when a project cannot be loaded or validated."""

    def __init__(self, message, code="INVALID_PROJECT"):
        super().__init__(message)
        self.code = code


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError("Unsupported value: {!r}".format(value))


def sanitize_filename(value: str, fallback: str = "PRISM项目") -> str:
    """Return a Windows-safe file or directory name."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")
    return cleaned or fallback


@dataclass
class Project:
    """A self-contained local PRISM project."""

    root: Path
    name: str = "新建项目"
    fields: Dict[str, Any] = field(default_factory=dict)
    schedule: Dict[str, Any] = field(default_factory=dict)
    templates: Dict[str, Any] = field(default_factory=lambda: {"mode": "default", "enabled_ids": []})
    outputs: Dict[str, Any] = field(default_factory=dict)
    model_id: str = "gjb5000-simplified"
    model_version: str = "3.0"
    features: List[Dict[str, Any]] = field(default_factory=list)
    performances: List[Dict[str, Any]] = field(default_factory=list)
    interfaces: List[Dict[str, Any]] = field(default_factory=list)
    reliabilities: List[Dict[str, Any]] = field(default_factory=list)

    _module_files: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _storage_root: Optional[Path] = field(default=None, repr=False, compare=False)
    _delete_files: List[str] = field(default_factory=list, repr=False, compare=False)
    _overwrite_files: List[str] = field(default_factory=list, repr=False, compare=False)
    _storage_warnings: List[str] = field(default_factory=list, repr=False, compare=False)
    _loaded_revision: Optional[str] = field(default=None, repr=False, compare=False)

    @property
    def loaded_revision(self):
        """The last successfully loaded or committed business snapshot."""
        return self._loaded_revision

    @property
    def revision(self):
        from .revision import project_revision
        return project_revision(self)

    @property
    def file_path(self) -> Path:
        return self.root / PROJECT_FILENAME

    @property
    def output_dir(self) -> Path:
        return self.root / "outputs"

    @property
    def modules(self):
        from .storage import prepare_project
        return prepare_project(self, check_files=False)[0]["modules"]

    def save(self, expected_revision=None) -> None:
        """Save a bound snapshot, or create an unbound project in an empty root."""
        from .storage import save_project
        save_project(self, expected_revision=expected_revision)

    @classmethod
    def create(cls, root: Path, name: str) -> "Project":
        from .storage import create_project
        from .schema import ORGANIZATION_DEFAULTS
        fields = {"项目名": name}
        fields.update(ORGANIZATION_DEFAULTS)
        project = cls(root=Path(root), name=name, fields=fields)
        create_project(project)
        return project

    @classmethod
    def load(cls, root_or_file: Path) -> "Project":
        from .storage import load_project
        return load_project(root_or_file)

    def context(self) -> Dict[str, Any]:
        """Return one flat template context including calculated dates."""
        from .schema import ALL_FIELDS
        from .lifecycle import format_date

        context = {key: value for key, value in self.fields.items() if key in ALL_FIELDS}
        context.update(self.schedule.get("dates", {}))
        # This is a document view; persisted work dates remain ISO strings.
        context['work_schedule'] = {row['id']: dict(row, workdays=row['duration'],
            start=format_date(date.fromisoformat(row['start']), '工作开始'),
            finish=format_date(date.fromisoformat(row['finish']), '工作完成'))
            for row in self.schedule.get('work_items', [])}
        # Derive the year from the requested start, not the adjusted first workday.
        start = self.schedule.get("project_start")
        context["项目年"] = str(date.fromisoformat(start).year) if start else ""
        for key in ALL_FIELDS:
            context.setdefault(key, "")
        # 旧项目可能没有单位信息字段：仅在渲染上下文回退到默认值，
        # 不写回项目文件、不改变 revision。
        from .schema import ORGANIZATION_DEFAULTS
        for key, default in ORGANIZATION_DEFAULTS.items():
            if not str(context.get(key) or "").strip():
                context[key] = default
        from .items import item_context
        context.update(item_context(self))
        from .document_fields import document_fields
        context.update(document_fields(context))
        from .initial_delivery import configuration_context, scheduled_delivery_context
        from .document_versions import VERSION_OVERRIDES_KEY, normalize_versions
        # Carry generation inputs without binding a document version or changing storage.
        context[VERSION_OVERRIDES_KEY] = normalize_versions(self.templates.get('document_versions', {}))
        context.update(configuration_context(context))
        context.update(scheduled_delivery_context(self, context))
        return {key: "" if value is None else value for key, value in context.items()}


def find_header_row(values: Sequence[Sequence[Any]], max_rows: int = 20) -> int:
    """Find the likely unique field-name row in a legacy key-value sheet."""
    best_index = -1
    best_score = -1
    for index, row in enumerate(values[:max_rows]):
        labels = [str(value).strip() for value in row if value not in (None, "")]
        score = len(labels) if len(labels) == len(set(labels)) else len(set(labels)) - 10
        if score > best_score:
            best_index, best_score = index, score
    if best_index < 0 or best_score < 2:
        raise ProjectError("未找到键值表字段行")
    return best_index


def import_legacy_workbook(path: Path) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Read records from the first sheet of an old key-value workbook."""
    try:
        from openpyxl import load_workbook
    except ImportError as error:
        raise ProjectError("缺少 openpyxl，无法读取 XLSX") from error

    workbook = load_workbook(str(path), read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
    finally:
        workbook.close()
    header_index = find_header_row(rows)
    headers = [str(value).strip() if value not in (None, "") else "" for value in rows[header_index]]
    last_column = max((index for index, value in enumerate(headers) if value), default=-1) + 1
    headers = headers[:last_column]
    if len(headers) != len(set(headers)):
        duplicates = sorted({value for value in headers if value and headers.count(value) > 1})
        raise ProjectError("键值表存在重复字段：{}".format("、".join(duplicates)))

    records: List[Dict[str, Any]] = []
    for row in rows[header_index + 1 :]:
        values = list(row[:last_column])
        if not any(value not in (None, "") for value in values):
            continue
        records.append({key: value for key, value in zip(headers, values) if key})
    if not records:
        raise ProjectError("键值表中没有数据记录")
    return headers, records


def select_record_label(record: Mapping[str, Any], index: int) -> str:
    """Build a short label for a record-selection dialog."""
    name = record.get("项目名") or record.get("产品名") or record.get("配置项")
    return "第 {} 条 - {}".format(index + 1, name or "未命名")
