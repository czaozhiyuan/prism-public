"""Transport-neutral, non-interactive Agent Service v1 read API."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from . import __version__
from .agent_contract import (
    AGENT_API_VERSION,
    ERRORS,
    IMPLEMENTED_TOOLS,
    REQUEST_SCHEMAS,
    contract_copy,
)
from .items import ITEM_FIELDS, ITEM_TYPES, TEST_TYPES, inspect_items, new_item, test_fields
from .generation_core import (
    BatchFinalization,
    GenerationCoreError,
    finalize_generation,
    plan_generation,
    render_generation,
    save_generation_outputs,
)
from .generation_lock import GenerationLock
from .lifecycle import MODEL_FORMAT_VERSION, load_model
from .project import PROJECT_FILENAME, PROJECT_FORMAT_VERSION, Project, ProjectError
from .project_lock import LockReleaseError
from .revision import project_revision
from .schedule import load_work_calendar
from .delivery_fields import DELIVERY_FIELDS
from .schema import DATE_FIELDS, FIELD_LABELS, PROJECT_FIELD_SECTIONS, TOOL_FIELDS
from .storage import KINDS, TYPES, ProjectTransaction, create_project, name_key, prepare_project
from .storage import apply_group_import, read_json, validate_basic
from .template_ids import (
    selected_template_ids,
    discover_template_paths,
    encode_template_id,
)

MODULE_FORMAT_VERSION = 1


def _build_commit(application_root: Path) -> str:
    build_info = application_root / "BUILD_INFO.json"
    if build_info.is_file():
        try:
            value = json.loads(build_info.read_text(encoding="utf-8"))["build_commit"]
            if re.fullmatch(r"[0-9a-f]{40}", value, re.I):
                return value.lower()
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            pass
    value = os.environ.get("PRISM_BUILD_COMMIT", "")
    if re.fullmatch(r"[0-9a-f]{7,40}", value, re.I):
        return value.lower()
    git_dir = application_root / ".git"
    try:
        if git_dir.is_file():
            marker = git_dir.read_text(encoding="utf-8").strip()
            if not marker.startswith("gitdir: "):
                return "unknown"
            git_dir = (application_root / marker[8:]).resolve()
        head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-f]{40}", head):
            return head
        if not head.startswith("ref: "):
            return "unknown"
        reference = head[5:]
        loose = git_dir / reference
        if loose.is_file():
            return loose.read_text(encoding="ascii").strip()
        for line in (git_dir / "packed-refs").read_text(encoding="ascii").splitlines():
            if line and not line.startswith(("#", "^")):
                commit, name = line.split(" ", 1)
                if name == reference:
                    return commit
    except (OSError, UnicodeError, ValueError):
        pass
    return "unknown"


class AgentFault(Exception):
    """Internal normalized fault converted to the public error envelope."""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None,
                 retryable: Optional[bool] = None, commit_state: str = "not_committed"):
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.retryable = ERRORS.get(code, (False, 2))[0] if retryable is None else retryable
        self.commit_state = commit_state


def _application_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (candidate / "assets").is_dir():
            return candidate
    return Path.cwd().resolve()


def _json_type(value: Any, expected: Any) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    checks = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "boolean": lambda: type(value) is bool,
        "integer": lambda: type(value) is int,
        "number": lambda: type(value) in (int, float),
        "null": lambda: value is None,
    }
    return any(checks[name]() for name in names if name in checks)


def _validate_schema(value: Any, schema: Dict[str, Any], location: str = "request") -> None:
    if not schema:
        return
    for alternative in schema.get('allOf', []):
        _validate_schema(value, alternative, location)
    if "oneOf" in schema:
        matches = 0
        for alternative in schema["oneOf"]:
            try:
                _validate_schema(value, alternative, location)
                matches += 1
            except AgentFault:
                pass
        if matches != 1:
            raise AgentFault("INVALID_ARGUMENT", "{} 不匹配唯一 Schema 分支".format(location),
                             {"location": location})
        return
    if "type" in schema and not _json_type(value, schema["type"]):
        raise AgentFault("INVALID_ARGUMENT", "{} 类型无效".format(location), {"location": location})
    if "const" in schema and value != schema["const"]:
        raise AgentFault("INVALID_ARGUMENT", "{} 值无效".format(location), {"location": location})
    if "enum" in schema and value not in schema["enum"]:
        raise AgentFault("INVALID_ARGUMENT", "{} 值无效".format(location), {"location": location})
    if type(value) in (int, float) and "minimum" in schema and value < schema["minimum"]:
        raise AgentFault("INVALID_ARGUMENT", "{} 小于最小值".format(location), {"location": location})
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise AgentFault("INVALID_ARGUMENT", "{} 不能为空".format(location), {"location": location})
        if schema.get("pattern") and not re.fullmatch(schema["pattern"], value):
            raise AgentFault("INVALID_ARGUMENT", "{} 格式无效".format(location), {"location": location})
        if schema.get("format") == "date":
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                parsed = None
            if parsed is None or parsed.isoformat() != value:
                raise AgentFault("INVALID_ARGUMENT", "{} 日期格式无效".format(location), {"location": location})
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise AgentFault("INVALID_ARGUMENT", "请求缺少必需字段", {"missing": missing})
        unknown = sorted(set(value) - set(properties)) if schema.get("additionalProperties") is False else []
        if unknown:
            raise AgentFault("INVALID_ARGUMENT", "请求包含未知字段", {"unknown": unknown})
        for key, item in value.items():
            if 'propertyNames' in schema:
                _validate_schema(key, schema['propertyNames'], location + '.<key>')
            if key in properties:
                _validate_schema(item, properties[key], location + "." + key)
            elif isinstance(schema.get("additionalProperties"), dict):
                _validate_schema(item, schema["additionalProperties"], location + "." + key)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            raise AgentFault("INVALID_ARGUMENT", "{} 数组长度无效".format(location), {"location": location})
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value}) != len(value):
            raise AgentFault("INVALID_ARGUMENT", "{} 不得包含重复项".format(location), {"location": location})
        for index, item in enumerate(value):
            _validate_schema(item, schema.get("items", {}), "{}[{}]".format(location, index))


def _validate_readonly_rules(tool: str, request: Dict[str, Any]) -> None:
    fields = request.get("fields")
    if isinstance(fields, dict):
        readonly = sorted(set(fields) & set(DATE_FIELDS))
        if readonly:
            raise AgentFault("DERIVED_FIELD_READONLY", "派生日期字段不可写", {"fields": readonly})
    schedule = request.get("schedule")
    if isinstance(schedule, dict):
        readonly = sorted(set(schedule) & {"dates", "phases", "work_items"})
        if readonly:
            raise AgentFault("DERIVED_FIELD_READONLY", "排期派生字段不可写", {"fields": readonly})
    if "outputs" in request or (isinstance(request.get("fields"), dict)
                                and "outputs" in request["fields"]):
        raise AgentFault("DERIVED_FIELD_READONLY", "outputs 只读", {"fields": ["outputs"]})


def validate_request(tool: str, request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate and normalize one v1 request using its published Schema."""
    if tool not in REQUEST_SCHEMAS:
        raise AgentFault("INVALID_ARGUMENT", "未知 Agent API tool", {"tool": tool})
    normalized = {} if request is None else deepcopy(request)
    if not isinstance(normalized, dict):
        _validate_schema(normalized, REQUEST_SCHEMAS[tool])
    _validate_readonly_rules(tool, normalized)
    _validate_schema(normalized, REQUEST_SCHEMAS[tool])
    if tool == "project_generate":
        if "mode" in normalized and "template_ids" in normalized:
            raise AgentFault("INVALID_ARGUMENT", "mode 与 template_ids 不能同时提供",
                             {"fields": ["mode", "template_ids"]})
        if "template_ids" in normalized and not normalized["template_ids"]:
            raise AgentFault("NO_TEMPLATES_SELECTED", "template_ids 不能为空", {})
        if "template_ids" not in normalized:
            normalized["mode"] = "enabled"
    if tool == "project_create" and "model" not in normalized:
        normalized["model"] = {"id": "gjb5000-simplified", "version": "3.0"}
    if tool == "module_remove" and "delete_file" not in normalized:
        normalized["delete_file"] = False
    return normalized


def _success(data: Any, revision: Optional[str] = None, warnings: Sequence[Dict[str, Any]] = (),
             commit_state: str = "not_committed") -> Dict[str, Any]:
    return {
        "ok": True, "status": "success_with_warnings" if warnings else "success", "data": data,
        "warnings": list(warnings), "revision": revision, "commit_state": commit_state,
        "records_saved": None, "review_checklist": None, "error": None,
    }


def _failure(error: AgentFault) -> Dict[str, Any]:
    return {
        "ok": False, "status": "failed", "data": None, "warnings": [], "revision": None,
        "commit_state": error.commit_state, "records_saved": None, "review_checklist": None,
        "error": {"code": error.code, "message": str(error), "retryable": error.retryable,
                  "commit_state": error.commit_state, "details": error.details},
    }


def _generation_failure(error: AgentFault) -> Dict[str, Any]:
    data = getattr(error, "generation_data", None)
    if data is None and error.code == "NO_TEMPLATES_SELECTED":
        data = {"preflight": False, "expected_outputs": [], "results": []}
    details = dict(error.details)
    if getattr(error, "lock_release_error", None):
        details["lock_release_error"] = error.lock_release_error
    return {
        "ok": False, "status": "failed",
        "data": data,
        "warnings": [], "revision": getattr(error, "revision", None),
        "commit_state": error.commit_state, "records_saved": False,
        "review_checklist": None,
        "error": {"code": error.code, "message": str(error), "retryable": error.retryable,
                  "commit_state": error.commit_state, "details": details},
    }


def _generation_response(data: Dict[str, Any], revision: str, status: str,
                         commit_state: str, records_saved: Optional[bool],
                         error: Optional[AgentFault] = None,
                         warnings: Sequence[Dict[str, Any]] = (),
                         review_checklist: Optional[str] = None) -> Dict[str, Any]:
    return {
        "ok": error is None, "status": status, "data": data, "warnings": list(warnings),
        "revision": revision, "commit_state": commit_state,
        "records_saved": records_saved, "review_checklist": review_checklist,
        "error": None if error is None else {
            "code": error.code, "message": str(error), "retryable": error.retryable,
            "commit_state": commit_state, "details": error.details,
        },
    }


class AgentService:
    """Machine-readable Agent Service; adapters may call it without UI dependencies."""

    def __init__(self, application_root: Optional[Path] = None,
                 model_roots: Optional[Iterable[Path]] = None):
        self.application_root = Path(application_root or _application_root()).resolve()
        self.asset_root = self.application_root / "assets"
        self.build_commit = _build_commit(self.application_root)
        if model_roots is None:
            appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
            model_roots = (self.asset_root / "models", appdata / "PRISM" / "models")
        self.model_roots = tuple(Path(value).resolve() for value in model_roots)

    def call(self, tool: str, request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Validate and execute a v1 tool, always returning a JSON-compatible envelope."""
        try:
            request = validate_request(tool, request)
            if tool not in IMPLEMENTED_TOOLS:
                raise AgentFault("INVALID_ARGUMENT", "该 tool 已冻结但尚未在 P4 实现", {"tool": tool})
            result = getattr(self, "_" + tool)(request)
            json.dumps(result, ensure_ascii=False, allow_nan=False)
            return result
        except AgentFault as error:
            return _generation_failure(error) if tool == "project_generate" else _failure(error)
        except ProjectError as error:
            fault = self._project_fault(error)
            return _generation_failure(fault) if tool == "project_generate" else _failure(fault)
        except Exception as error:
            fault = AgentFault("PROJECT_LOAD_FAILED", "Agent Service 内部执行失败", {"reason": str(error)})
            return _generation_failure(fault) if tool == "project_generate" else _failure(fault)

    @staticmethod
    def _project_fault(error: ProjectError, default: str = "VALIDATION_FAILED") -> AgentFault:
        code = getattr(error, "code", default)
        if code == "PROJECT_ALREADY_EXISTS":
            code = "INVALID_ARGUMENT"
        elif code not in ERRORS:
            code = default
        details = dict(getattr(error, "details", {}) or {})
        if getattr(error, "lock_release_error", None):
            details["lock_release_error"] = error.lock_release_error
        return AgentFault(code, str(error), details, getattr(error, "retryable", None),
                          getattr(error, "commit_state", "not_committed"))

    def _prism_info(self, request: Dict[str, Any]) -> Dict[str, Any]:
        manifest = self.asset_root / "ASSET_MANIFEST.csv"
        if not manifest.is_file():
            raise AgentFault("VALIDATION_FAILED", "assets manifest 缺失", {"path": str(manifest)})
        try:
            identifier = "sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest()
        except OSError as error:
            raise AgentFault("VALIDATION_FAILED", "assets manifest 无法读取",
                             {"path": str(manifest), "reason": str(error)})
        return _success({
            "product_version": __version__, "build_commit": self.build_commit,
            "agent_api_version": AGENT_API_VERSION, "project_format_version": PROJECT_FORMAT_VERSION,
            "module_format_version": MODULE_FORMAT_VERSION, "model_format_version": MODEL_FORMAT_VERSION,
            "assets_manifest": identifier,
        })

    def _schema_get(self, request: Dict[str, Any]) -> Dict[str, Any]:
        schemas = {
            "agent-api-v2": None,
            "project-basic-v7": self.asset_root / "schemas" / "project-basic-v7.schema.json",
            "project-people-v1": self.asset_root / "schemas" / "project-people-v1.schema.json",
            "project-test-environment-v1": self.asset_root / "schemas" / "project-test-environment-v1.schema.json",
            "project-module-v1": self.asset_root / "schemas" / "project-module-v1.schema.json",
        }
        name = request.get("name")
        if name is None:
            return _success({"schemas": sorted(schemas)})
        if name not in schemas:
            raise AgentFault("INVALID_ARGUMENT", "未知 schema", {"name": name, "available": sorted(schemas)})
        schema = contract_copy() if schemas[name] is None else json.loads(schemas[name].read_text(encoding="utf-8"))
        return _success({"name": name, "schema": schema})

    def _project_describe_fields(self, request: Dict[str, Any]) -> Dict[str, Any]:
        sections = []
        for key, title, fields in PROJECT_FIELD_SECTIONS:
            sections.append({"id": key, "title": title,
                             "fields": [{"name": name, "label": FIELD_LABELS[name],
                                         "type": "string" if key in ("people", "pictures", "test_environment") or name in TOOL_FIELDS + ("调试器",) + DELIVERY_FIELDS else "scalar", "writable": True,
                                         "nullable": key not in ("people", "pictures", "test_environment") and name not in TOOL_FIELDS + ("调试器",) + DELIVERY_FIELDS, "derived": False} for name in fields]})
        sections.append({"id": "derived_dates", "title": "派生日期",
                         "fields": [{"name": name, "type": "string", "writable": False,
                                     "nullable": False, "derived": True} for name in DATE_FIELDS]})
        modules = {}
        for kind, label in ITEM_TYPES:
            modules[TYPES[kind]] = {
                "label": label,
                "fields": [{"name": name, "label": field_label, "type": "string", "writable": True}
                           for name, field_label in ITEM_FIELDS[kind]],
                "test_collections": {test_kind: [{"name": name, "label": field_label,
                                                   "type": "string", "writable": True}
                                                   for name, field_label in test_fields(test_kind)]
                                     for test_kind, _ in TEST_TYPES},
            }
        return _success({"field_sections": sections, "schedule": {
            "project_start": {"writable": True, "type": "date", "nullable": True},
            "work_hours": {"writable": True, "type": "positive_integer_by_work_id"},
            "dates": {"writable": False, "derived": True}, "phases": {"writable": False, "derived": True},
            "work_items": {"writable": False, "derived": True},
        }, "outputs": {"writable": False}, "modules": modules})

    def _project_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            raise AgentFault("INVALID_ARGUMENT", "project 必须是绝对路径", {"project": value})
        if path.name == PROJECT_FILENAME and not path.is_dir():
            path = path.parent
        if not path.is_dir():
            raise AgentFault("PROJECT_NOT_FOUND", "项目目录不存在", {"project": value})
        return path.resolve()

    def _load_project(self, value: str) -> Project:
        root = self._project_path(value)
        if not (root / PROJECT_FILENAME).is_file():
            raise AgentFault("PROJECT_NOT_FOUND", "目录中没有 基本数据.json", {"project": str(root)})
        try:
            return Project.load(root)
        except ProjectError as error:
            code = getattr(error, "code", "PROJECT_LOAD_FAILED")
            if code not in ERRORS:
                code = "PROJECT_LOAD_FAILED"
            raise AgentFault(code, str(error), getattr(error, "details", {}),
                             getattr(error, "retryable", None), getattr(error, "commit_state", "not_committed"))

    @staticmethod
    def _project_state(project: Project) -> Dict[str, Any]:
        return {
            "format_version": PROJECT_FORMAT_VERSION, "data_type": "basic", "name": project.name,
            "model": {"id": project.model_id, "version": project.model_version},
            "fields": deepcopy(project.fields), "schedule": deepcopy(project.schedule),
            "templates": deepcopy(project.templates), "outputs": deepcopy(project.outputs),
            "modules": AgentService._module_payloads(project),
        }

    @staticmethod
    def _unscheduled_warning() -> Dict[str, Any]:
        return {"code": "PROJECT_UNSCHEDULED", "message": "项目尚未设置开始日期。", "details": {}}

    @staticmethod
    def _storage_warnings(project: Project) -> list:
        result = []
        for message in project._storage_warnings:
            code = "MODULE_FILE_CLEANUP_FAILED" if "旧文件未删除" in message else "VALIDATION_FAILED"
            result.append({"code": code, "message": message, "details": {}})
        return result

    def _write_candidate(self, request: Dict[str, Any], mutate: Any,
                         warn_unscheduled: bool = False) -> Tuple[Project, Any, bool, str, str, str, list]:
        root = self._project_path(request["project"])
        stage = "project_load"
        try:
            with ProjectTransaction(root, expected_revision=request.get("expected_revision")) as transaction:
                stage = "candidate"
                candidate = deepcopy(transaction.project)
                before = candidate.loaded_revision
                value = mutate(candidate)
                after = project_revision(candidate)
                changed = after != before or bool(candidate._delete_files)
                if request.get("dry_run", False):
                    prepare_project(candidate)
                    project = candidate
                    commit_state = "not_committed"
                else:
                    project = transaction.commit(candidate)
                    after = project.loaded_revision
                    commit_state = "committed" if changed else "not_committed"
        except ProjectError as error:
            default = "PROJECT_LOAD_FAILED" if stage == "project_load" else "VALIDATION_FAILED"
            raise self._project_fault(error, default)
        except OSError as error:
            raise AgentFault(
                "PROJECT_LOAD_FAILED", "项目写入失败",
                {"reason": str(error), "rollback": "succeeded"},
                commit_state=getattr(error, "commit_state", "not_committed"),
            )
        warnings = self._storage_warnings(project)
        if warn_unscheduled and "project_start" not in project.schedule:
            warnings.append(self._unscheduled_warning())
        return project, value, changed, before, after, commit_state, warnings

    def _model_entry(self, model_ref: Dict[str, str]) -> Any:
        entry = self._installed_models().get((model_ref["id"], model_ref["version"]))
        if entry is None:
            raise AgentFault("MODEL_NOT_FOUND", "找不到已安装模型", model_ref)
        return entry[0]

    def _calculate_schedule(self, schedule: Dict[str, Any], model: Any) -> Dict[str, Any]:
        try:
            calendar = load_work_calendar(self.asset_root / "calendars" / "cn_workdays_2026_2035.json")
            start = schedule.get("project_start")
            return model.calculate(date.fromisoformat(start) if start else None,
                                   schedule.get("work_hours", {}), calendar)
        except (OSError, ValueError) as error:
            raise AgentFault("INVALID_ARGUMENT", "无法计算项目排期", {"reason": str(error)})

    def _schedule_consistency_fault(self, project: Project, model: Any) -> Optional[AgentFault]:
        if "project_start" not in project.schedule:
            return AgentFault("PROJECT_UNSCHEDULED", "项目尚未设置开始日期",
                              {"stage": "preflight"})
        try:
            expected = self._calculate_schedule(project.schedule, model)
        except AgentFault as error:
            return error
        if expected != project.schedule:
            return AgentFault("VALIDATION_FAILED", "派生排期与当前输入不一致",
                              {"stage": "preflight", "path": "schedule"})
        return None

    def _project_create(self, request: Dict[str, Any]) -> Dict[str, Any]:
        path = Path(request["project"])
        if not path.is_absolute():
            raise AgentFault("INVALID_ARGUMENT", "project 必须是绝对路径", {"project": request["project"]})
        if path.name == PROJECT_FILENAME:
            path = path.parent
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise AgentFault("INVALID_ARGUMENT", "新项目目标目录必须不存在或为空", {"project": str(path)})
        if not path.exists() and not path.parent.is_dir():
            raise AgentFault("PROJECT_NOT_FOUND", "新项目父目录不存在", {"project": str(path.parent)})
        model = self._model_entry(request["model"])
        from .schema import ORGANIZATION_DEFAULTS
        initial_fields = {"项目名": request["name"]}
        initial_fields.update(ORGANIZATION_DEFAULTS)
        project = Project(root=path.resolve(), name=request["name"], fields=initial_fields,
                          model_id=model.id, model_version=model.version)
        if 'templates' in request:
            project.templates = self._configured_templates(request['templates'], project.templates, model)
        revision = project_revision(project)
        if request.get("dry_run", False):
            try:
                prepare_project(project)
            except ProjectError as error:
                raise self._project_fault(error)
            commit_state = "not_committed"
        else:
            try:
                create_project(project)
            except ProjectError as error:
                raise self._project_fault(error)
            revision = project.loaded_revision
            commit_state = "committed"
        return _success({"changed": True, "project": self._project_state(project)}, revision,
                        [self._unscheduled_warning()], commit_state)

    def _project_rename(self, request: Dict[str, Any]) -> Dict[str, Any]:
        def mutate(candidate: Project) -> None:
            candidate.name = request["name"].strip()

        project, _, changed, before, after, state, warnings = self._write_candidate(request, mutate)
        diff = {"changed_paths": ["name"] if changed else [],
                "before_revision": before, "after_revision": after}
        return _success({"changed": changed, "diff": diff}, after, warnings, state)

    def _project_set_fields(self, request: Dict[str, Any]) -> Dict[str, Any]:
        changed_paths = []

        def mutate(candidate: Project) -> None:
            for name, value in request["fields"].items():
                if candidate.fields.get(name) != value or name not in candidate.fields:
                    candidate.fields[name] = value
                    changed_paths.append("fields." + name)

        project, _, changed, before, after, state, warnings = self._write_candidate(
            request, mutate, warn_unscheduled=True)
        diff = {"changed_paths": changed_paths, "before_revision": before, "after_revision": after}
        return _success({"changed": changed, "diff": diff}, after, warnings, state)

    def _project_import_group(self, request: Dict[str, Any]) -> Dict[str, Any]:
        source = Path(request['source'])
        if not source.is_absolute():
            raise AgentFault('INVALID_ARGUMENT', 'source 必须是绝对 JSON 路径')

        def mutate(candidate):
            payload = read_json(source)
            if isinstance(payload, dict) and payload.get('data_type') == 'basic':
                validate_basic(payload)
            return apply_group_import(candidate, payload)

        _, preview, changed, before, after, state, warnings = self._write_candidate(request, mutate)
        diff = {'changed_paths': ['fields.' + row['field'] for row in preview['changes']],
                'before_revision': before, 'after_revision': after}
        return _success(dict(preview, changed=changed, will_write=changed, diff=diff), after, warnings, state)

    @staticmethod
    def _project_apply_conflicts(request: Dict[str, Any]) -> None:
        modules = request.get("modules", {})
        updates = [entry["module_id"] for entry in modules.get("update", [])]
        removals = [entry["module_id"] for entry in modules.get("remove", [])]
        duplicate_ids = sorted({value for value in updates + removals
                                if (updates + removals).count(value) > 1})
        if duplicate_ids:
            raise AgentFault("INVALID_ARGUMENT", "同一模块不能重复修改或同时修改和删除",
                             {"module_ids": duplicate_ids})
        reorder_types = [entry["data_type"] for entry in modules.get("reorder", [])]
        duplicate_types = sorted({value for value in reorder_types if reorder_types.count(value) > 1})
        if duplicate_types:
            raise AgentFault("INVALID_ARGUMENT", "同一模块类别不能重复排序",
                             {"data_types": duplicate_types})
        created_types = {entry["data_type"] for entry in modules.get("create", [])}
        conflicts = sorted(created_types & set(reorder_types))
        if conflicts:
            raise AgentFault("INVALID_ARGUMENT", "同一请求不支持同类别 create 与 reorder",
                             {"data_types": conflicts})

    @staticmethod
    def _model_schedule_inputs(candidate: Project, model: Any, model_changed: bool = False) -> Dict[str, Any]:
        schedule = {}
        if "project_start" in candidate.schedule:
            schedule["project_start"] = candidate.schedule["project_start"]
        configured = candidate.schedule.get("work_hours", {})
        schedule["work_hours"] = ({work.id: configured[work.id] for work in model.works if work.id in configured}
                                      if model_changed else deepcopy(configured))
        return schedule

    def _project_apply(self, request: Dict[str, Any]) -> Dict[str, Any]:
        self._project_apply_conflicts(request)

        def mutate(candidate: Project) -> Dict[str, Any]:
            before = self._project_state(candidate)
            created_ids = []

            model = None
            if "model" in request:
                model = self._model_entry(request["model"])
                candidate.schedule = self._model_schedule_inputs(candidate, model, model_changed=True)
                candidate.model_id = model.id
                candidate.model_version = model.version

            if "schedule" in request:
                if model is None:
                    model = self._model_entry({"id": candidate.model_id, "version": candidate.model_version})
                    candidate.schedule = self._model_schedule_inputs(candidate, model)
                patch = request["schedule"]
                if "project_start" in patch:
                    if patch["project_start"] is None:
                        candidate.schedule.pop("project_start", None)
                    else:
                        candidate.schedule["project_start"] = patch["project_start"]
                if "work_hours" in patch:
                    candidate.schedule.setdefault("work_hours", {}).update(patch["work_hours"])

            for name, value in request.get("fields", {}).items():
                candidate.fields[name] = deepcopy(value)

            if "templates" in request:
                template_model = (self._model_entry({"id": candidate.model_id,
                                                     "version": candidate.model_version})
                                  if request['templates']['mode'] == 'custom' else None)
                candidate.templates = self._configured_templates(request['templates'], candidate.templates,
                                                                  template_model)

            modules = request.get("modules", {})
            for entry in modules.get("remove", []):
                self._remove_module(candidate, entry)
            for entry in modules.get("update", []):
                self._update_module(candidate, entry)
            for entry in modules.get("create", []):
                created_ids.append(self._create_module(candidate, entry))
            for entry in modules.get("reorder", []):
                self._reorder_modules(candidate, entry, batch=True)

            if model is not None:
                candidate.schedule = self._calculate_schedule(candidate.schedule, model)

            after = self._project_state(candidate)
            changed_paths = []
            for key in ("model", "schedule", "templates", "modules"):
                if before[key] != after[key]:
                    changed_paths.append(key)
            for name in request.get("fields", {}):
                if (name not in before["fields"]
                        or before["fields"][name] != after["fields"].get(name)):
                    changed_paths.append("fields." + name)
            return {"created_ids": created_ids, "changed_paths": changed_paths}

        project, value, changed, before, after, state, warnings = self._write_candidate(
            request, mutate, warn_unscheduled=True)
        payloads = {item["module_id"]: item for item in self._module_payloads(project)}
        created = [payloads[module_id] for module_id in value["created_ids"]]
        diff = {"changed_paths": value["changed_paths"],
                "before_revision": before, "after_revision": after}
        return _success({"changed": changed, "diff": diff, "created_modules": created},
                        after, warnings, state)

    def _project_validate(self, request: Dict[str, Any]) -> Dict[str, Any]:
        project = self._load_project(request["project"])
        issues = []
        entry = self._installed_models().get((project.model_id, project.model_version))
        if entry is None:
            issues.append({"code": "MODEL_NOT_FOUND", "severity": "error",
                           "message": "项目引用的生命周期模型未安装。", "path": "model"})
        else:
            error = self._schedule_consistency_fault(project, entry[0])
            if error is not None:
                path = "schedule.project_start" if error.code == "PROJECT_UNSCHEDULED" else "schedule"
                issues.append({"code": error.code, "severity": "error",
                               "message": str(error), "path": path})
        if entry is not None and project.templates.get("mode") == "custom":
            available = self._available_template_ids(entry[0])
            for template_id in project.templates.get("enabled_ids", []):
                if template_id not in available:
                    issues.append({"code": "TEMPLATE_NOT_FOUND", "severity": "error",
                                   "message": "找不到已配置模板。", "path": "templates.enabled_ids"})
        for message in inspect_items(project):
            issues.append({"code": "VALIDATION_FAILED", "severity": "error",
                           "message": message, "path": "modules"})
        return _success({"valid": not issues, "issues": issues}, project.loaded_revision)

    def _project_recalculate(self, request: Dict[str, Any]) -> Dict[str, Any]:
        def mutate(candidate: Project) -> None:
            model = self._model_entry({"id": candidate.model_id, "version": candidate.model_version})
            candidate.schedule = self._calculate_schedule(candidate.schedule, model)

        project, _, changed, _, after, state, warnings = self._write_candidate(
            request, mutate, warn_unscheduled=True)
        return _success({"changed": changed, "schedule": deepcopy(project.schedule)},
                        after, warnings, state)

    def _generation_plan(self, project: Project, request: Dict[str, Any]) -> Any:
        model = self._model_entry({"id": project.model_id, "version": project.model_version})
        try:
            calendar = load_work_calendar(
                self.asset_root / "calendars/cn_workdays_2026_2035.json",
            )
            return plan_generation(
                project, self.asset_root, model, calendar,
                request.get("template_ids") if "template_ids" in request else None,
            )
        except GenerationCoreError as error:
            raise AgentFault(error.code, str(error), error.details)

    @staticmethod
    def _generation_release_failure(response: Dict[str, Any], error: LockReleaseError) -> Dict[str, Any]:
        result = deepcopy(response)
        detail = str(error)
        if result["error"] is None:
            result["ok"] = False
            result["status"] = "failed"
            result["error"] = {
                "code": "PROJECT_LOCKED", "message": "生成锁释放失败：{}".format(detail),
                "retryable": False, "commit_state": result["commit_state"],
                "details": {"lock_release_error": detail},
            }
        else:
            result["error"]["details"] = dict(result["error"]["details"])
            result["error"]["details"]["lock_release_error"] = detail
        return result

    def _completed_generation_response(self, data: Dict[str, Any], results: list,
                                       revision: str,
                                       finalization: Optional[BatchFinalization] = None,
                                       cleanup_error: Optional[LockReleaseError] = None) -> Dict[str, Any]:
        finalization = finalization or BatchFinalization(None, [], False, "", False)
        failures = [item for item in results if item["render_status"] == "failed"]
        if failures or finalization.checklist_failed:
            status = "partial_failure" if len(failures) < len(results) else "failed"
            details = {"failed_template_ids": [item["template_id"] for item in failures]}
            if finalization.checklist_failed:
                status = "partial_failure"
                details.update(stage="review_checklist",
                               reason=finalization.checklist_message)
            if cleanup_error is not None:
                details["lock_release_error"] = str(cleanup_error)
            fault = AgentFault("GENERATION_FAILED", "一个或多个模板生成失败", details,
                               commit_state="committed")
            return _generation_response(
                data, revision, status, "committed", True, fault,
                finalization.warnings, finalization.review_checklist,
            )
        if cleanup_error is not None:
            fault = AgentFault(
                "PROJECT_LOCKED", "项目锁释放失败：{}".format(cleanup_error),
                {"lock_release_error": str(cleanup_error)}, retryable=False,
                commit_state="committed",
            )
            return _generation_response(
                data, revision, "failed", "committed", True, fault,
                finalization.warnings, finalization.review_checklist,
            )
        status = "success_with_warnings" if finalization.warnings else "success"
        return _generation_response(
            data, revision, status, "committed", True, warnings=finalization.warnings,
            review_checklist=finalization.review_checklist,
        )

    @staticmethod
    def _persisted_generation_results(results: list) -> list:
        persisted = deepcopy(results)
        for result in persisted:
            result["warnings"] = [
                warning if isinstance(warning, str) else "{}: {}".format(
                    warning.get("code", "WARNING"), warning.get("message", ""),
                )
                for warning in result["warnings"]
            ]
        return persisted

    def _project_generate_locked(self, root: Path, request: Dict[str, Any]) -> Dict[str, Any]:
        with ProjectTransaction(root, expected_revision=request.get("expected_revision")) as transaction:
            snapshot = deepcopy(transaction.project)
        source_revision = snapshot.loaded_revision
        try:
            plans = self._generation_plan(snapshot, request)
        except AgentFault as error:
            error.revision = source_revision
            error.generation_data = {
                "preflight": False, "expected_outputs": [], "results": [],
            }
            raise
        expected = [{"template_id": plan.template_id, "output": plan.output} for plan in plans]
        data = {"preflight": bool(request.get("dry_run", False)),
                "expected_outputs": expected, "results": []}
        if request.get("dry_run", False):
            return _generation_response(data, source_revision, "success", "not_committed", False)

        try:
            calendar = load_work_calendar(
                self.asset_root / "calendars" / "cn_workdays_2026_2035.json")
            results, wrote_output, audit_issues = render_generation(snapshot, plans, calendar)
        except (OSError, ValueError, GenerationCoreError) as error:
            fault = AgentFault(getattr(error, "code", "GENERATION_FAILED"),
                               "生成批次执行失败", {"reason": str(error)})
            return _generation_response(data, source_revision, "failed", "not_committed",
                                        False, fault)

        finalization = finalize_generation(root, plans, results, audit_issues)
        wrote_output = wrote_output or finalization.wrote_output
        data["results"] = results
        outputs = {
            "source_revision": source_revision,
            "time": datetime.now().replace(microsecond=0).isoformat(),
            "results": self._persisted_generation_results(results),
        }
        try:
            latest = save_generation_outputs(root, outputs)
        except Exception as error:
            if getattr(error, "records_saved", False):
                saved = error.saved_project
                return self._completed_generation_response(data, results, saved.loaded_revision,
                                                           finalization=finalization,
                                                           cleanup_error=error)
            state = getattr(error, "commit_state", "not_committed")
            if state == "not_committed" and wrote_output:
                state = "committed"
            fault = AgentFault("OUTPUT_RECORD_SAVE_FAILED", "生成文件已处理，但 outputs 记录保存失败",
                               {"reason": str(error)}, retryable=True, commit_state=state)
            return _generation_response(
                data, source_revision, "failed", state, False, fault,
                finalization.warnings, finalization.review_checklist,
            )

        return self._completed_generation_response(
            data, results, latest.loaded_revision, finalization=finalization,
        )

    def _project_generate(self, request: Dict[str, Any]) -> Dict[str, Any]:
        root = self._project_path(request["project"])
        response = None
        try:
            with GenerationLock(root):
                response = self._project_generate_locked(root, request)
        except LockReleaseError as error:
            if response is None:
                raise
            return self._generation_release_failure(response, error)
        return response

    @staticmethod
    def _find_module(project: Project, module_id: str) -> Tuple[str, int, Any]:
        for kind, _ in ITEM_TYPES:
            for index, item in enumerate(getattr(project, kind)):
                if item.module_id == module_id:
                    return kind, index, item
        raise AgentFault("MODULE_NOT_FOUND", "找不到 module_id", {"module_id": module_id})

    @staticmethod
    def _check_module_name(project: Project, kind: str, name: str,
                           excluding: Optional[str] = None) -> None:
        for item in getattr(project, kind):
            if item.module_id != excluding and name_key(item["name"]) == name_key(name):
                raise AgentFault("MODULE_NAME_CONFLICT", "同类别模块名称重复", {"name": name})

    def _create_module(self, candidate: Project, request: Dict[str, Any]) -> str:
        kind = KINDS[request["data_type"]]
        data = request["data"]
        self._check_module_name(candidate, kind, data["name"])
        item = new_item(kind)
        for name, value in data.items():
            item[name] = deepcopy(value)
        getattr(candidate, kind).append(item)
        return item.module_id

    def _module_create(self, request: Dict[str, Any]) -> Dict[str, Any]:
        def mutate(candidate: Project) -> str:
            return self._create_module(candidate, request)

        project, created_id, changed, _, after, state, warnings = self._write_candidate(request, mutate)
        kind, _, _ = self._find_module(project, created_id)
        module = next(item for item in self._module_payloads(project) if item["module_id"] == created_id)
        assert TYPES[kind] == request["data_type"]
        return _success({"changed": changed, "module": module}, after, warnings, state)

    def _update_module(self, candidate: Project, request: Dict[str, Any]) -> None:
        kind, _, item = self._find_module(candidate, request["module_id"])
        allowed = {name for name, _ in ITEM_FIELDS[kind]} | {name for name, _ in TEST_TYPES}
        invalid = sorted(set(request["data"]) - allowed)
        if invalid:
            raise AgentFault("INVALID_FIELD", "字段不属于目标模块类别", {"fields": invalid})
        name = request["data"].get("name", item["name"])
        self._check_module_name(candidate, kind, name, excluding=item.module_id)
        for field, value in request["data"].items():
            item[field] = deepcopy(value)

    def _module_update(self, request: Dict[str, Any]) -> Dict[str, Any]:
        def mutate(candidate: Project) -> None:
            self._update_module(candidate, request)

        project, _, changed, _, after, state, warnings = self._write_candidate(request, mutate)
        module = next(item for item in self._module_payloads(project)
                      if item["module_id"] == request["module_id"])
        return _success({"changed": changed, "module": module}, after, warnings, state)

    def _remove_module(self, candidate: Project, request: Dict[str, Any]) -> None:
        kind, index, item = self._find_module(candidate, request["module_id"])
        if request.get("delete_file", False):
            entry = candidate._module_files.get(item.module_id)
            if entry:
                candidate._delete_files.append(entry["file"])
        del getattr(candidate, kind)[index]

    def _module_remove(self, request: Dict[str, Any]) -> Dict[str, Any]:
        def mutate(candidate: Project) -> None:
            self._remove_module(candidate, request)

        _, _, changed, _, after, state, warnings = self._write_candidate(request, mutate)
        return _success({"changed": changed, "module_id": request["module_id"]},
                        after, warnings, state)

    def _reorder_modules(self, candidate: Project, request: Dict[str, Any], batch: bool = False) -> None:
        kind = KINDS[request["data_type"]]
        values = getattr(candidate, kind)
        current = {item.module_id: item for item in values}
        if set(request["module_ids"]) != set(current) or len(request["module_ids"]) != len(values):
            message = "module_ids 必须完整覆盖同类别最终模块" if batch else "module_ids 必须完整覆盖同类别模块"
            raise AgentFault("MODULE_ORDER_INVALID", message,
                             {"expected": list(current), "actual": request["module_ids"]})
        setattr(candidate, kind, [current[module_id] for module_id in request["module_ids"]])

    def _module_reorder(self, request: Dict[str, Any]) -> Dict[str, Any]:
        def mutate(candidate: Project) -> None:
            self._reorder_modules(candidate, request)

        project, _, changed, _, after, state, warnings = self._write_candidate(request, mutate)
        kind = KINDS[request["data_type"]]
        module_ids = [item.module_id for item in getattr(project, kind)]
        return _success({"changed": changed, "module_ids": module_ids}, after, warnings, state)

    def _project_set_model(self, request: Dict[str, Any]) -> Dict[str, Any]:
        model = self._model_entry(request["model"])

        def mutate(candidate: Project) -> None:
            work_ids = {work.id for work in model.works}
            schedule = deepcopy(candidate.schedule)
            schedule["work_hours"] = {
                work_id: hours for work_id, hours in schedule.get("work_hours", {}).items()
                if work_id in work_ids
            }
            candidate.model_id = model.id
            candidate.model_version = model.version
            candidate.schedule = self._calculate_schedule(schedule, model)

        project, _, changed, _, after, state, warnings = self._write_candidate(
            request, mutate, warn_unscheduled=True)
        return _success({"changed": changed, "model": deepcopy(request["model"]),
                         "schedule": deepcopy(project.schedule)}, after, warnings, state)

    def _available_template_ids(self, model: Any) -> set:
        return {encode_template_id(path, self.asset_root, model)
                for path in discover_template_paths(self.asset_root, model)}

    def _template_configure(self, request: Dict[str, Any]) -> Dict[str, Any]:
        def mutate(candidate: Project) -> None:
            model = (self._model_entry({"id": candidate.model_id, "version": candidate.model_version})
                     if request['templates']['mode'] == 'custom' else None)
            candidate.templates = self._configured_templates(request['templates'], candidate.templates, model)

        project, _, changed, _, after, state, warnings = self._write_candidate(request, mutate)
        return _success({"changed": changed, "templates": deepcopy(project.templates)},
                        after, warnings, state)

    def _configured_templates(self, config, current, model):
        from .document_versions import merge_template_config
        try:
            result = merge_template_config(deepcopy(config), current)
        except ProjectError as error:
            raise AgentFault('INVALID_ARGUMENT', str(error)) from error
        if result['mode'] == 'default':
            result['enabled_ids'] = []
        else:
            missing = sorted(set(result['enabled_ids']) - self._available_template_ids(model))
            if missing:
                raise AgentFault('TEMPLATE_NOT_FOUND', '找不到模板', {'template_ids': missing})
        return result

    @staticmethod
    def _module_payloads(project: Project) -> list:
        result = []
        for kind, _ in ITEM_TYPES:
            data_type = TYPES[kind]
            for order, item in enumerate(getattr(project, kind)):
                result.append({"module_format_version": MODULE_FORMAT_VERSION, "data_type": data_type,
                               "module_id": item.module_id, "order": order, **deepcopy(dict(item))})
        return result

    def _project_get(self, request: Dict[str, Any]) -> Dict[str, Any]:
        project = self._load_project(request["project"])
        return _success({"project": self._project_state(project)}, project.loaded_revision)

    def _module_list(self, request: Dict[str, Any]) -> Dict[str, Any]:
        project = self._load_project(request["project"])
        modules = self._module_payloads(project)
        if request.get("data_type"):
            modules = [item for item in modules if item["data_type"] == request["data_type"]]
        return _success({"modules": modules}, project.loaded_revision)

    def _module_get(self, request: Dict[str, Any]) -> Dict[str, Any]:
        project = self._load_project(request["project"])
        for item in self._module_payloads(project):
            if item["module_id"] == request["module_id"]:
                return _success({"module": item}, project.loaded_revision)
        raise AgentFault("MODULE_NOT_FOUND", "找不到 module_id", {"module_id": request["module_id"]})

    def _installed_models(self) -> Dict[Tuple[str, str], Any]:
        found = {}
        for root_index, root in enumerate(self.model_roots):
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/model.json")):
                try:
                    model = load_model(path)
                except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                    raise AgentFault("MODEL_INCOMPATIBLE", "已安装模型无效", {"path": str(path), "reason": str(error)})
                key = (model.id, model.version)
                if key in found:
                    raise AgentFault("MODEL_INCOMPATIBLE", "检测到重复模型 id/version",
                                     {"id": model.id, "version": model.version,
                                      "paths": [str(found[key][0].source_dir), str(model.source_dir)]})
                found[key] = (model, "bundled" if root_index == 0 else "user")
        return found

    @staticmethod
    def _model_payload(model: Any, source: str) -> Dict[str, Any]:
        return {
            "format_version": MODEL_FORMAT_VERSION, "id": model.id, "version": model.version,
            "name": model.name, "source": source,
            "phases": [{"id": item.id, "name": item.name, "default_workdays": item.default_workdays}
                       for item in model.phases],
            "works": [{"id": item.id, "phase_id": item.phase_id, "name": item.name,
                       "default_hours": item.default_hours, "default_workdays": item.default_workdays,
                       "work_type": item.work_type, "owner_role": item.owner_role,
                       "executor_roles": list(item.executor_roles)} for item in model.works],
            "date_nodes": [{"key": item.key, "work_id": item.work_id, "anchor": item.anchor,
                            "formats": list(item.formats)} for item in model.date_nodes],
        }

    def _model_list(self, request: Dict[str, Any]) -> Dict[str, Any]:
        models = [self._model_payload(model, source) for model, source in self._installed_models().values()]
        return _success({"models": sorted(models, key=lambda item: (item["id"], item["version"]))})

    def _model_get(self, request: Dict[str, Any]) -> Dict[str, Any]:
        entry = self._installed_models().get((request["id"], request["version"]))
        if entry is None:
            raise AgentFault("MODEL_NOT_FOUND", "找不到已安装模型", {"id": request["id"], "version": request["version"]})
        return _success({"model": self._model_payload(*entry)})

    def _template_list(self, request: Dict[str, Any]) -> Dict[str, Any]:
        from .document_versions import DEFAULT_DOCUMENT_VERSION, normalize_versions
        project = self._load_project(request["project"]) if "project" in request else None
        model = None
        if project is not None:
            entry = self._installed_models().get((project.model_id, project.model_version))
            if entry is None:
                raise AgentFault("MODEL_NOT_FOUND", "项目引用的模型未安装",
                                 {"id": project.model_id, "version": project.model_version})
            model = entry[0]
        paths = discover_template_paths(self.asset_root, model)
        identities = {path: encode_template_id(path, self.asset_root, model) for path in paths}
        enabled = set(selected_template_ids(project.templates if project else {}, identities.values(),
                                           project.fields.get('器件类型', '') if project else ''))
        templates = []
        versions = normalize_versions(project.templates.get('document_versions', {})) if project else {}
        for path in sorted(paths, key=lambda item: str(item).casefold()):
            template_id = identities[path]
            templates.append({"template_id": template_id, "kind": path.suffix[1:].upper(),
                              "source": "model" if template_id.startswith("model:") else "asset",
                              "document_version": versions.get(template_id, DEFAULT_DOCUMENT_VERSION)
                              if path.suffix.lower() == '.docx' else None,
                              "enabled": template_id in enabled})
        return _success({"templates": templates}, project.loaded_revision if project else None)


def create_service(application_root: Optional[Path] = None,
                   model_roots: Optional[Iterable[Path]] = None) -> AgentService:
    """Create the service without importing CLI, MCP, or GUI modules."""
    return AgentService(application_root=application_root, model_roots=model_roots)
