"""V3.1 logical business projection; no persistent revision or filesystem hash."""

from copy import deepcopy
import hashlib
import json


def business_state(project):
    from .items import ITEM_TYPES, ModuleItem
    from .project import ProjectError
    from .storage import prepare_project, validate_module
    for kind, _ in ITEM_TYPES:
        if not all(isinstance(item, ModuleItem) for item in getattr(project, kind)):
            raise ProjectError('计算 revision 前模块必须具有稳定身份', 'INVALID_PROJECT')
    basic, modules = prepare_project(project, check_files=False)
    config = basic['templates']
    templates = {'mode': config['mode'], 'enabled_ids': (
        [] if config['mode'] == 'default' else sorted(config['enabled_ids']))}
    if config.get('document_versions'):
        templates['document_versions'] = deepcopy(config['document_versions'])
    state = {key: deepcopy(basic[key]) for key in ('name', 'model', 'fields', 'schedule')}
    state['fields'] = deepcopy(project.fields)
    state['templates'] = templates
    state['modules'] = {kind: [] for kind in ('feature', 'performance', 'interface', 'reliability')}
    for _, _, _, _, payload in modules:
        _, item = validate_module(payload)
        state['modules'][payload['data_type']].append({
            'module_id': item.module_id, 'data': deepcopy(dict(item))})
    return state


def canonical_bytes(project):
    return json.dumps(business_state(project), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def project_revision(project):
    return 'sha256:' + hashlib.sha256(canonical_bytes(project)).hexdigest()
