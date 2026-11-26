"""Versioned work-based lifecycle models and deterministic working-day schedules."""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .schedule import StageDefinition, WorkCalendar, build_stage_schedule
from .model_identity import is_model_token
from .schema import PEOPLE_FIELDS

MODEL_FORMAT_VERSION = 3
WORK_TYPES = ('项目管理', '需求分析', '软件设计', '软件开发', '软件测试', '配置管理', '质量保证', '交付验收')


class ModelError(ValueError):
    """Raised when a lifecycle model or its work inputs are invalid."""


@dataclass(frozen=True)
class Phase:
    id: str
    name: str
    default_workdays: int


@dataclass(frozen=True)
class Work:
    id: str
    phase_id: str
    name: str
    default_hours: int
    work_type: str
    owner_role: str
    executor_roles: Sequence[str]

    @property
    def default_workdays(self):
        """Working days are derived from per-person hours, never edited."""
        return (self.default_hours + 7) // 8


@dataclass(frozen=True)
class DateNode:
    key: str
    work_id: str
    anchor: str
    formats: Sequence[str] = ()


@dataclass(frozen=True)
class LifecycleModel:
    id: str
    version: str
    name: str
    phases: Sequence[Phase]
    works: Sequence[Work]
    date_nodes: Sequence[DateNode]
    source_dir: Path

    def calculate(self, project_start: Optional[date], work_hours: Mapping[str, int],
                  calendar: WorkCalendar) -> Dict[str, Any]:
        """Calculate one sequential work chain; an absent start is a valid draft."""
        defaults = {work.id: work.default_hours for work in self.works}
        if not isinstance(work_hours, dict) or set(work_hours) - set(defaults):
            raise ModelError("单人计划工时包含未知工作 ID")
        if any(type(value) is not int or value < 1 for value in work_hours.values()):
            raise ModelError("单人计划工时必须为正整数小时")
        overrides = {key: value for key, value in work_hours.items() if value != defaults[key]}
        inputs = {"work_hours": overrides} if overrides else {}
        if project_start is None:
            return inputs
        hours = {work.id: overrides.get(work.id, work.default_hours) for work in self.works}
        definitions = [StageDefinition(work.name, (hours[work.id] + 7) // 8) for work in self.works]
        results = build_stage_schedule(project_start, definitions, calendar)
        by_id = {work.id: result for work, result in zip(self.works, results)}
        phases = []
        for phase in self.phases:
            members = [by_id[work.id] for work in self.works if work.phase_id == phase.id]
            phases.append(dict(id=phase.id, name=phase.name, duration=sum(item.duration for item in members),
                               start=members[0].start.isoformat(), finish=members[-1].finish.isoformat()))
        values = {}
        for node in self.date_nodes:
            value = getattr(by_id[node.work_id], node.anchor)
            values[node.key] = value.isoformat()
            values.update({alias: format_date(value, alias) for alias in node.formats})
        return dict(inputs, project_start=project_start.isoformat(), phases=phases,
                    work_items=[dict(id=work.id, phase_id=work.phase_id, name=work.name, hours=hours[work.id],
                                     work_type=work.work_type, owner_role=work.owner_role,
                                     executor_roles=list(work.executor_roles), duration=result.duration,
                                     start=result.start.isoformat(), finish=result.finish.isoformat())
                                for work, result in zip(self.works, results)], dates=values)


def format_date(value: date, key: str) -> str:
    """Format a shared date for the existing document aliases."""
    if "年月" in key and "日" not in key:
        return "{}年{}月".format(value.year, value.month)
    return value.strftime("%Y/%m/%d")


def _unique(values: Iterable[str], label: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ModelError(label + "存在重复标识")


def _record(value, required, allowed, label):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(allowed):
        raise ModelError(label + "字段无效")


def validate_model(payload: Mapping[str, Any]) -> None:
    """Reject unsupported formats and invalid or ambiguous work/date bindings."""
    if not isinstance(payload, dict) or type(payload.get("format_version")) is not int or payload["format_version"] != MODEL_FORMAT_VERSION:
        raise ModelError("不支持的生命周期模型版本；仅支持格式3")
    keys = ('format_version', 'id', 'version', 'name', 'phases', 'works', 'date_nodes')
    _record(payload, keys, keys, '模型')
    if not all(is_model_token(payload[key]) for key in ('id', 'version')):
        raise ModelError("模型 id/version 只能含 ASCII 字母、数字、下划线、点和连字符，且不能为 . 或 ..")
    if payload['id'] == 'gjb5000-simplified' and payload['version'] != '3.0':
        raise ModelError("不支持旧内置模型；请使用3.0工时模型")
    if not isinstance(payload['name'], str) or not payload['name'].strip():
        raise ModelError("模型名称无效")
    for key in ('phases', 'works', 'date_nodes'):
        if not isinstance(payload[key], list) or (key != 'date_nodes' and not payload[key]):
            raise ModelError(key + "必须是有效数组")
    for phase in payload['phases']:
        _record(phase, ('id', 'name'), ('id', 'name'), '阶段')
        if not is_model_token(phase['id']) or not isinstance(phase['name'], str) or not phase['name'].strip():
            raise ModelError("阶段标识或名称无效")
    phase_ids = [phase['id'] for phase in payload['phases']]
    _unique(phase_ids, '阶段')
    for work in payload['works']:
        keys = ('id', 'phase_id', 'name', 'default_hours', 'work_type', 'owner_role', 'executor_roles')
        _record(work, keys, keys, '工作')
        if (not is_model_token(work['id']) or work['phase_id'] not in phase_ids
                or not isinstance(work['name'], str) or not work['name'].strip()
                or type(work['default_hours']) is not int or work['default_hours'] < 1):
            raise ModelError("工作标识、阶段或默认单人工时无效")
        if (work['work_type'] not in WORK_TYPES or work['owner_role'] not in PEOPLE_FIELDS
                or not isinstance(work['executor_roles'], list) or not work['executor_roles']
                or any(role not in PEOPLE_FIELDS for role in work['executor_roles'])
                or len(work['executor_roles']) != len(set(work['executor_roles']))
                or work['owner_role'] not in work['executor_roles']):
            raise ModelError("工作类型或人员岗位无效；执行岗位必须非空且包含唯一负责人岗位")
    work_ids = [work['id'] for work in payload['works']]
    _unique(work_ids, '工作')
    expected = [work for phase in phase_ids for work in payload['works'] if work['phase_id'] == phase]
    if expected != payload['works'] or any(not any(w['phase_id'] == phase for w in expected) for phase in phase_ids):
        raise ModelError("工作必须按阶段顺序分组，且每个阶段至少包含一项工作")
    names = []
    for node in payload['date_nodes']:
        keys = ('key', 'work_id', 'anchor', 'formats')
        _record(node, ('key', 'work_id', 'anchor'), keys, '日期节点')
        if (not isinstance(node['key'], str) or not node['key'].strip() or node['work_id'] not in work_ids
                or node['anchor'] not in ('start', 'finish') or not isinstance(node.get('formats', []), list)
                or any(not isinstance(v, str) or not v.strip() for v in node.get('formats', []))):
            raise ModelError("日期节点绑定或格式别名无效")
        names.extend([node['key']] + node.get('formats', []))
    _unique(names, '日期节点及别名')


def load_model(path: Path) -> LifecycleModel:
    model_path = Path(path)
    payload = json.loads(model_path.read_text(encoding='utf-8'))
    validate_model(payload)
    works = [Work(**item) for item in payload['works']]
    phases = [Phase(item['id'], item['name'], sum(w.default_workdays for w in works if w.phase_id == item['id']))
              for item in payload['phases']]
    nodes = [DateNode(item['key'], item['work_id'], item['anchor'], tuple(item.get('formats', [])))
             for item in payload['date_nodes']]
    return LifecycleModel(payload['id'], payload['version'], payload['name'], phases, works, nodes, model_path.parent)


def import_model_package(package: Path, library: Path) -> Path:
    """Validate and install a .prismmodel ZIP package."""
    package = Path(package)
    if package.suffix.lower() != ".prismmodel":
        raise ModelError("模型包扩展名必须是 .prismmodel")
    with tempfile.TemporaryDirectory(prefix="prism-model-") as temporary:
        temp_dir = Path(temporary)
        with zipfile.ZipFile(str(package), "r") as archive:
            for member in archive.infolist():
                target = (temp_dir / member.filename).resolve()
                if temp_dir.resolve() not in target.parents and target != temp_dir.resolve():
                    raise ModelError("模型包包含不安全路径")
            archive.extractall(str(temp_dir))
        model_path = temp_dir / "model.json"
        if not model_path.is_file():
            raise ModelError("模型包缺少 model.json")
        model = load_model(model_path)
        destination = Path(library) / "{}-{}".format(model.id, model.version)
        if destination.exists():
            raise ModelError("该模型版本已经存在")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(temp_dir), str(destination))
        return destination


def export_model_package(model_dir: Path, output: Path) -> Path:
    """Export one installed model directory as a .prismmodel package."""
    model_dir = Path(model_dir)
    load_model(model_dir / "model.json")
    output = Path(output).with_suffix(".prismmodel")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(output), "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(model_dir.rglob("*")):
            if path.is_file():
                archive.write(str(path), str(path.relative_to(model_dir)))
    return output
