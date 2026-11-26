"""Authoritative schedule inputs at basic import and export boundaries."""
from copy import deepcopy
from datetime import date
import os
from pathlib import Path
import sys

from .lifecycle import load_model
from .project import ProjectError
from .schedule import load_work_calendar


def calculated_schedule(project, model, calendar):
    """Calculate from inputs only, using the exact referenced model identity."""
    if (model.id, model.version) != (project.model_id, project.model_version):
        raise ProjectError('未安装项目引用的生命周期模型', 'MODEL_NOT_FOUND')
    try:
        start = project.schedule.get('project_start')
        return model.calculate(date.fromisoformat(start) if start else None,
                               project.schedule.get('work_hours', {}), calendar)
    except (OSError, ValueError) as error:
        raise ProjectError('无法计算项目排期：{}'.format(error), 'INVALID_ARGUMENT') from error


def require_current_schedule(project, model, calendar):
    """Reject stale caches without changing a loaded snapshot or its revision."""
    if project.schedule != calculated_schedule(project, model, calendar):
        raise ProjectError('派生排期与当前输入不一致，请重新计算并保存。', 'VALIDATION_FAILED')


def imported_schedule(project, application_root=None):
    """Rebuild imported caches before committing; missing-model drafts stay valid."""
    root = Path(application_root or getattr(sys, '_MEIPASS', None) or Path(__file__).resolve().parents[2])
    library = Path(os.environ.get('APPDATA', str(Path.home()))) / 'PRISM' / 'models'
    model_id, version = project.model_id, project.model_version
    candidates = (root / 'assets/models' / model_id / 'model.json',
                  root / 'assets/models' / '{}-{}'.format(model_id, version) / 'model.json',
                  library / '{}-{}'.format(model_id, version) / 'model.json')
    model = None
    for path in candidates:
        if path.is_file():
            try:
                found = load_model(path)
            except (OSError, ValueError) as error:
                raise ProjectError('生命周期模型无效：{}'.format(error), 'MODEL_INCOMPATIBLE') from error
            if (found.id, found.version) == (model_id, version):
                model = found
                break
    if model is None:
        if 'project_start' in project.schedule:
            raise ProjectError('请先安装对应生命周期模型，再导入已排期基本数据。', 'MODEL_NOT_FOUND')
        return deepcopy(project.schedule)
    calendar = load_work_calendar(root / 'assets/calendars/cn_workdays_2026_2035.json')
    return calculated_schedule(project, model, calendar)
