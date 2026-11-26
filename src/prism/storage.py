"""Project Format v7 assembly, validation and transactional local storage.

Only the basic-file index determines project membership. Unreferenced files are
never discovered or garbage-collected. UUIDs live on ModuleItem attributes, so
business mappings remain suitable for document templates.
"""
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path, PureWindowsPath
from uuid import UUID

from .items import ITEM_TYPES, ITEM_FIELDS, TEST_TYPES, ModuleItem, read_items, test_fields
from .project import Project, ProjectError, PROJECT_FILENAME, PROJECT_FORMAT_VERSION, _json_default, sanitize_filename
from .template_ids import parse_template_id, relative_parts
from .model_identity import is_model_token
from .schema import PEOPLE_FIELDS, TEST_ENVIRONMENT_FIELDS

KINDS = dict(zip(('feature', 'performance', 'interface', 'reliability'),
                 (kind for kind, _ in ITEM_TYPES)))
TYPES = {kind: data_type for data_type, kind in KINDS.items()}
LABELS = dict(ITEM_TYPES)
GROUP_FILES = {'people': '人员职责.json', 'test_environment': '测试环境.json'}
GROUP_FIELDS = {'people': PEOPLE_FIELDS, 'test_environment': TEST_ENVIRONMENT_FIELDS}
GROUP_KEYS = frozenset(PEOPLE_FIELDS + TEST_ENVIRONMENT_FIELDS)


class DataConflict(ProjectError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def name_key(name):
    return name.strip().casefold()


def _require(condition, message):
    if not condition:
        raise ProjectError(message)


def _object(value, required, allowed, where):
    _require(isinstance(value, dict), where + ' 必须是对象')
    _require(set(required) <= set(value), where + ' 缺少必需字段')
    _require(set(value) <= set(allowed), where + ' 包含未知字段')


def _name(value):
    _require(isinstance(value, str) and bool(value.strip()), '名称必须是非空文本')


def _uuid(value):
    try:
        _require(isinstance(value, str) and str(UUID(value)) == value, 'module_id 必须是规范 UUID')
    except (ValueError, AttributeError):
        raise ProjectError('module_id 必须是规范 UUID')


def safe_filename(value):
    _require(isinstance(value, str) and bool(value), '模块文件名无效')
    _require(not PureWindowsPath(value).drive and not re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
             and value not in ('.', '..') and value == value.rstrip(' .')
             and value.lower().endswith('.json') and len(value) <= 255,
             '模块文件必须是项目根目录内的安全 JSON 文件名：' + value)
    _require(not re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', value, re.I),
             '模块文件名为 Windows 保留名称')
    _require(value.casefold() != PROJECT_FILENAME.casefold(), '模块不能引用基本数据文件')
    _require(value.casefold() not in {name.casefold() for name in GROUP_FILES.values()},
             '模块不能引用固定组文件')
    return value


def module_filename(kind, name):
    _name(name)
    return safe_filename(LABELS[kind] + '-' + sanitize_filename(name) + '.json')


def read_json(path):
    def pairs(entries):
        obj = {}
        for key, value in entries:
            _require(key not in obj, '重复 JSON 键：' + key)
            obj[key] = value
        return obj
    def invalid(value):
        raise ProjectError('无效 JSON 数值：' + value)
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=pairs,
                          parse_constant=invalid)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProjectError('无法读取 {}：{}'.format(path, error)) from error


def _iso_date(value, label):
    from datetime import date
    try:
        _require(isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value), label + ' 日期无效')
        date.fromisoformat(value)
    except ValueError as error:
        raise ProjectError(label + ' 日期无效') from error


def validate_group(payload, expected_type=None):
    """Validate a sparse, independently reusable field group."""
    keys = ('format_version', 'data_type', 'fields')
    _object(payload, keys, keys, '字段组')
    kind = payload['data_type']
    _require(isinstance(kind, str) and kind in GROUP_FIELDS, '未知字段组 data_type')
    _require(expected_type is None or kind == expected_type, '组文件身份与 groups 引用不一致')
    _require(type(payload['format_version']) is int and payload['format_version'] == 1,
             '字段组仅支持 format_version 1')
    _object(payload['fields'], (), GROUP_FIELDS[kind], '组 fields')
    _require(all(isinstance(value, str) for value in payload['fields'].values()), '组字段必须是文本')
    return payload


def group_payloads(fields):
    _require(isinstance(fields, dict), 'fields 必须是对象')
    return {kind: validate_group(dict(format_version=1, data_type=kind,
                    fields={key: fields[key] for key in keys if key in fields}))
            for kind, keys in GROUP_FIELDS.items()}


def _group_path(root, kind):
    path = root / GROUP_FILES[kind]
    _require(not path.is_symlink() and path.resolve().parent == root.resolve(),
             '组文件必须位于项目根目录且不能使用符号链接')
    _require(not path.exists() or path.is_file(), '组文件目标不是文件：' + path.name)
    return path


def group_import_preview(project, payload):
    """Compute a patch without changing the snapshot, files or source payload."""
    validate_group(payload)
    changes = [{'field': key, 'before': project.fields.get(key), 'after': value}
               for key, value in payload['fields'].items()
               if key not in project.fields or project.fields[key] != value]
    return {'data_type': payload['data_type'], 'changes': changes}


def apply_group_import(project, payload):
    preview = group_import_preview(project, payload)
    project.fields.update(payload['fields'])
    return preview


def validate_basic(payload):
    _require(isinstance(payload, dict), '基本数据必须是对象')
    if type(payload.get('format_version')) is not int or payload['format_version'] != PROJECT_FORMAT_VERSION:
        error = ProjectError('当前版本仅支持 Project Format v7，不读取或迁移旧项目（检测到格式：{}）'.format(
                             payload.get('format_version')), 'PROJECT_FORMAT_UNSUPPORTED')
        error.details = {'detected_format_version': payload.get('format_version'), 'supported_format_version': 7}
        raise error
    keys = ('format_version', 'data_type', 'name', 'model', 'fields', 'groups', 'schedule', 'templates', 'outputs', 'modules')
    _object(payload, keys, keys, '基本数据')
    _require(payload['data_type'] == 'basic', '基本数据的 data_type 必须为 basic')
    _require(payload['groups'] == GROUP_FILES, 'groups 必须明确引用项目根目录中的两个固定组文件')
    _name(payload['name'])
    _object(payload['model'], ('id', 'version'), ('id', 'version'), 'model')
    for value in payload['model'].values():
        _require(is_model_token(value), 'model.id/version 只能含 ASCII 字母、数字、下划线、点和连字符，且不能为 . 或 ..')
    _require(isinstance(payload['fields'], dict), 'fields 必须是对象')
    _require(not (set(payload['fields']) & GROUP_KEYS), 'v7 基本数据 fields 不能重复存储人员职责或测试环境字段')
    _require(all(v is None or isinstance(v, (str, int, float, bool)) for v in payload['fields'].values()),
             'fields 必须是标量字段对象')
    from .delivery_fields import validate_delivery_fields
    validate_delivery_fields(payload['fields'])
    from .schema import PICTURE_FIELDS, TOOL_FIELDS
    _require(all(isinstance(payload['fields'].get(key, ''), str) for key in PICTURE_FIELDS),
             '图片路径必须是文本')
    _require(all(isinstance(payload['fields'].get(key, ''), str) for key in TOOL_FIELDS),
             '工具名称及版本必须是文本')
    _require(isinstance(payload['fields'].get('调试器', ''), str), '调试器必须是文本')
    _require(isinstance(payload['schedule'], dict), 'schedule 必须是对象')
    schedule = payload['schedule']
    _object(schedule, (), ('project_start', 'work_hours', 'dates', 'phases', 'work_items'), 'schedule')
    if 'project_start' in schedule:
        _iso_date(schedule['project_start'], 'schedule.project_start')
    if 'work_hours' in schedule:
        _require(isinstance(schedule['work_hours'], dict) and
                 all(is_model_token(k) and type(v) is int and v > 0 for k, v in schedule['work_hours'].items()),
                 'schedule.work_hours 必须是工作 ID 对应正整数的对象')
    if 'dates' in schedule:
        _require(isinstance(schedule['dates'], dict) and all(isinstance(v, str) for v in schedule['dates'].values()),
                 'schedule.dates 必须是文本对象')
    for collection in ('phases', 'work_items'):
        if collection not in schedule:
            continue
        _require(isinstance(schedule[collection], list), collection + ' 必须是数组')
        identities = set()
        for entry in schedule[collection]:
            keys = ('id', 'name', 'duration', 'start', 'finish')
            if collection == 'work_items':
                keys += ('phase_id', 'hours', 'work_type', 'owner_role', 'executor_roles')
            _object(entry, keys, keys, collection)
            _require(is_model_token(entry['id']) and isinstance(entry['name'], str) and bool(entry['name'].strip())
                     and type(entry['duration']) is int and entry['duration'] > 0, '排期工作或阶段内容无效')
            _require(entry['id'] not in identities, '排期标识重复')
            identities.add(entry['id'])
            if collection == 'work_items':
                _require(is_model_token(entry['phase_id']), '工作阶段标识无效')
                from .lifecycle import WORK_TYPES
                from .schema import PEOPLE_FIELDS
                _require(type(entry['hours']) is int and entry['hours'] > 0
                         and entry['duration'] == (entry['hours'] + 7) // 8, '工作工时与工作日不一致')
                _require(entry['work_type'] in WORK_TYPES and entry['owner_role'] in PEOPLE_FIELDS
                         and isinstance(entry['executor_roles'], list) and bool(entry['executor_roles'])
                         and all(role in PEOPLE_FIELDS for role in entry['executor_roles'])
                         and len(set(entry['executor_roles'])) == len(entry['executor_roles'])
                         and entry['owner_role'] in entry['executor_roles'], '工作类型或人员岗位无效')
            for key in ('start', 'finish'):
                _iso_date(entry[key], collection + '.' + key)
            _require(entry['start'] <= entry['finish'], '计划开始日期晚于完成日期')
    if 'project_start' not in schedule:
        _require(not any(schedule.get(key) for key in ('dates', 'phases', 'work_items')),
                 '未排期项目不能包含派生日期缓存')
    validate_templates(payload['templates'])
    validate_outputs(payload['outputs'])
    _require(isinstance(payload['modules'], list), 'modules 必须是数组')
    ids, names, files = set(), set(), set()
    for entry in payload['modules']:
        keys = ('data_type', 'module_id', 'name', 'file')
        _object(entry, keys, keys, '模块索引')
        _require(isinstance(entry['data_type'], str) and entry['data_type'] in KINDS, '未知模块 data_type')
        _uuid(entry['module_id'])
        _name(entry['name'])
        safe_filename(entry['file'])
        identity = entry['module_id']
        name = (entry['data_type'], name_key(entry['name']))
        filename = entry['file'].casefold()
        _require(identity not in ids, '重复 module_id：' + identity)
        _require(name not in names, '同类别模块名称重复：' + entry['name'])
        _require(filename not in files, '重复模块文件引用：' + entry['file'])
        ids.add(identity)
        names.add(name)
        files.add(filename)
    # Reject NaN/Infinity and unsupported nested objects even for in-memory saves.
    try:
        json.dumps(payload, allow_nan=False, default=_json_default)
    except (ValueError, TypeError) as error:
        raise ProjectError(str(error)) from error
    return payload


def validate_templates(config):
    _object(config, ('mode', 'enabled_ids'), ('mode', 'enabled_ids', 'document_versions'), 'templates')
    _require(config['mode'] in ('default', 'custom'), 'templates.mode 必须为 default 或 custom')
    _require(isinstance(config['enabled_ids'], list), 'templates.enabled_ids 必须是数组')
    for value in config['enabled_ids']:
        parse_template_id(value)
    _require(len(config['enabled_ids']) == len(set(config['enabled_ids'])), '模板 ID 不得重复')
    from .document_versions import normalize_versions
    normalize_versions(config.get('document_versions', {}))


def validate_outputs(outputs):
    """Validate template results; the ancillary checklist is not a result row."""
    _require(isinstance(outputs, dict), 'outputs 必须是对象')
    if not outputs:
        return
    keys = ('source_revision', 'time', 'results')
    _object(outputs, keys, keys, 'outputs')
    _require(isinstance(outputs['source_revision'], str) and
             re.fullmatch(r'sha256:[0-9a-f]{64}', outputs['source_revision']), 'outputs.source_revision 无效')
    _require(isinstance(outputs['time'], str) and
             re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', outputs['time']), 'outputs.time 无效')
    _require(isinstance(outputs['results'], list), 'outputs.results 必须是数组')
    for result in outputs['results']:
        keys = ('template_id', 'output', 'render_status', 'word_finalize_status', 'message', 'warnings')
        _object(result, keys, keys, 'outputs.results[]')
        parse_template_id(result['template_id'])
        _require(result['render_status'] in ('success', 'failed'), 'render_status 无效')
        _require(result['word_finalize_status'] in ('success', 'failed', 'unavailable', 'timeout', 'unstable', 'not_applicable'),
                 'word_finalize_status 无效')
        if result['output'] is not None:
            parts = relative_parts(result['output'])
            _require(len(parts) > 1 and parts[0] == 'outputs', '输出文件必须在项目 outputs/ 内')
        _require((result['output'] is not None) == (result['render_status'] == 'success'),
                 'render 失败时 output 必须为 null，成功时必须有输出路径')
        _require(isinstance(result['message'], str), '结果 message 必须是文本')
        _require(isinstance(result['warnings'], list) and all(isinstance(v, str) for v in result['warnings']),
                 '结果 warnings 必须是文本数组')


def validate_module(payload):
    _require(isinstance(payload, dict), '模块必须是对象')
    data_type = payload.get('data_type')
    _require(isinstance(data_type, str) and data_type in KINDS, '未知模块 data_type')
    kind = KINDS[data_type]
    metadata = ('module_format_version', 'data_type', 'module_id')
    fields = [key for key, _ in ITEM_FIELDS[kind]] + [key for key, _ in TEST_TYPES]
    _object(payload, metadata + ('name',), list(metadata) + fields, '模块')
    _require(type(payload['module_format_version']) is int and payload['module_format_version'] == 1,
             '仅支持 module_format_version 1')
    _uuid(payload['module_id'])
    _name(payload['name'])
    for category, _ in TEST_TYPES:
        values = payload.get(category, [])
        _require(isinstance(values, list), category + ' 必须是数组')
        for test in values:
            _object(test, (), [key for key, _ in test_fields(category)] + ['actual_executor'], category)
    try:
        item = read_items([{key: payload[key] for key in fields if key in payload}], kind)[0]
    except ValueError as error:
        raise ProjectError(str(error)) from error
    item.module_id = payload['module_id']
    return kind, item


def _local(root, filename):
    path = root / safe_filename(filename)
    _require(not path.is_symlink() and path.resolve().parent == root.resolve(), '模块路径越出项目或使用符号链接')
    return path


def _project_root(root_or_file):
    path = Path(root_or_file)
    if path.name == PROJECT_FILENAME and not path.is_dir():
        path = path.parent
    _require(path.is_dir(), '项目目录不存在：' + str(path))
    return path.resolve()


def load_project(root_or_file):
    """Read the basic index and every active module under one short lock."""
    from .project_lock import LockReleaseError, ProjectLock
    root = _project_root(root_or_file)
    try:
        with ProjectLock(root) as lock:
            return _load_project_locked(root, lock)
    except LockReleaseError as error:
        error.commit_state = 'not_committed'
        raise


def _load_project_locked(root, lock):
    from .revision import project_revision
    lock.require_owned(root)
    basic = root / PROJECT_FILENAME
    if not (basic.is_file() and not basic.is_symlink() and basic.resolve().parent == root):
        raise ProjectError(
            '所选目录中没有 基本数据.json；仅支持 Project Format v7，不提供旧项目迁移',
            'PROJECT_NOT_FOUND',
        )
    payload = validate_basic(read_json(basic))
    project = Project(root=basic.parent, name=payload['name'], model_id=payload['model']['id'],
                      model_version=payload['model']['version'],
                      **{key: payload[key] for key in ('fields', 'schedule', 'templates', 'outputs')})
    from .document_versions import normalize_template_config
    project.templates = normalize_template_config(project.templates)
    for kind in payload['groups']:
        group = validate_group(read_json(_group_path(root, kind)), kind)
        project.fields.update(group['fields'])
    for entry in payload['modules']:
        kind, item = validate_module(read_json(_local(project.root, entry['file'])))
        _require(TYPES[kind] == entry['data_type'] and item.module_id == entry['module_id']
                 and item['name'] == entry['name'], '模块索引与文件的类型、ID 或名称不一致：' + entry['file'])
        getattr(project, kind).append(item)
    project._module_files = {e['module_id']: dict(e) for e in payload['modules']}
    project._storage_root = project.root.resolve()
    project._loaded_revision = project_revision(project)
    return project


def prepare_project(project, check_files=True):
    """Pure preflight, also used by dry-run. Returns serialized candidate data."""
    from .document_versions import normalize_template_config
    validate_templates(project.templates)
    group_payloads(project.fields)
    basic = dict(format_version=PROJECT_FORMAT_VERSION, data_type='basic', name=project.name,
                 model=dict(id=project.model_id, version=project.model_version),
                 fields={key: value for key, value in project.fields.items() if key not in GROUP_KEYS},
                 groups=dict(GROUP_FILES),
                 schedule=project.schedule, templates=normalize_template_config(project.templates), outputs=project.outputs, modules=[])
    modules = []
    bound = project._module_files if project._storage_root == project.root.resolve() else {}
    for kind, _ in ITEM_TYPES:
        _require(isinstance(getattr(project, kind), list), kind + ' 必须是列表')
        for source in getattr(project, kind):
            _require(isinstance(source, dict), '模块业务数据必须是对象')
            item = source if isinstance(source, ModuleItem) else ModuleItem(source)
            payload = dict(item, module_format_version=1, data_type=TYPES[kind], module_id=item.module_id)
            _, item = validate_module(payload)
            payload = dict(item, module_format_version=1, data_type=TYPES[kind], module_id=item.module_id)
            filename = module_filename(kind, item['name'])
            entry = dict(data_type=TYPES[kind], module_id=item.module_id, name=item['name'], file=filename)
            basic['modules'].append(entry)
            modules.append((kind, source, item, filename, payload))
    validate_basic(basic)
    if check_files:
        for kind in GROUP_FILES:
            _group_path(project.root, kind)
        for _, _, item, filename, _ in modules:
            target = _local(project.root, filename)
            old = bound.get(item.module_id)
            authorized = old and old['file'].casefold() == filename.casefold()
            if target.exists() and not authorized and filename not in project._overwrite_files:
                raise DataConflict('FILE_CONFLICT', '本地文件已存在且未绑定此模块，需明确确认覆盖：' + filename)
            _require(not target.exists() or target.is_file(), '模块目标不是文件：' + filename)
    return basic, modules


def _check_revision(expected, actual):
    if expected is not None and (not isinstance(expected, str) or
                                 not re.fullmatch(r'sha256:[0-9a-f]{64}', expected)):
        raise ProjectError('expected_revision 格式无效', 'INVALID_ARGUMENT')
    if expected is not None and expected != actual:
        error = ProjectError('项目已被其他操作修改，请重新打开后再保存；当前输入未提交',
                             'REVISION_CONFLICT')
        error.retryable = False
        error.commit_state = 'not_committed'
        error.details = {'expected_revision': expected, 'actual_revision': actual}
        raise error


def _candidate(project):
    """Assign new module identities once, without mutating caller input."""
    candidate = deepcopy(project)
    from .document_versions import normalize_template_config
    validate_templates(candidate.templates)
    candidate.templates = normalize_template_config(candidate.templates)
    _, modules = prepare_project(candidate, check_files=False)
    for kind, source, item, _, _ in modules:
        if source is not item:
            values = getattr(candidate, kind)
            values[next(index for index, value in enumerate(values) if value is source)] = item
    return candidate


def _adopt(target, candidate):
    target.__dict__.update(candidate.__dict__)


def create_project(project):
    """Create only in a missing or empty directory, serialized with all readers."""
    from .project_lock import LOCK_FILENAME, LockReleaseError, ProjectLock
    candidate = _candidate(project)
    candidate.root = Path(candidate.root).resolve()
    candidate.outputs = {}
    candidate._module_files = {}
    candidate._storage_root = None
    candidate._loaded_revision = None
    candidate._delete_files = []
    candidate._overwrite_files = []
    # Concurrent creators may share mkdir, but only one passes the locked check.
    candidate.root.mkdir(parents=True, exist_ok=True)
    write_committed = False
    try:
        with ProjectLock(candidate.root) as lock:
            if any(path.name != LOCK_FILENAME for path in candidate.root.iterdir()):
                raise ProjectError('新项目目标目录必须为空：' + str(candidate.root), 'PROJECT_ALREADY_EXISTS')
            _commit_project_locked(candidate, lock)
            write_committed = True
            _adopt(project, candidate)
    except LockReleaseError as error:
        error.commit_state = 'committed' if write_committed else 'not_committed'
        raise


def save_project(project, expected_revision=None):
    """Never treat an arbitrary snapshot as a patch against the latest state."""
    if project._loaded_revision is None:
        if expected_revision is not None or project._storage_root is not None:
            raise ProjectError('缺少已加载项目的 revision 基准', 'REVISION_CONFLICT')
        create_project(project)
        return
    root = _project_root(project.root)
    if project._storage_root != root:
        raise ProjectError('项目根目录已改变；请使用显式创建入口', 'REVISION_CONFLICT')
    with ProjectTransaction(root, expected_revision=expected_revision) as transaction:
        # An explicit expected_revision must not bypass the snapshot baseline.
        _check_revision(project._loaded_revision, transaction.project.loaded_revision)
        candidate = transaction.commit(project)
        _adopt(project, candidate)


class ProjectTransaction:
    """One outer lock; callers patch the freshly loaded transaction.project."""

    def __init__(self, root, expected_revision=None, timeout=0.0):
        from .project_lock import ProjectLock
        self.root = _project_root(root)
        self.lock = ProjectLock(self.root, timeout=timeout)
        self.expected_revision = expected_revision
        self.project = None
        self._latest = None
        self._committed = False
        self._write_committed = False

    def __enter__(self):
        self.lock.acquire()
        try:
            self._latest = _load_project_locked(self.root, self.lock)
            _check_revision(self.expected_revision, self._latest.loaded_revision)
            self.project = deepcopy(self._latest)
            return self
        except BaseException as error:
            self.lock.__exit__(type(error), error, error.__traceback__)
            raise

    def commit(self, project=None):
        from .revision import project_revision
        self.lock.require_owned(self.root)
        if self._committed:
            raise ProjectError('同一事务只能提交一次', 'INVALID_ARGUMENT')
        candidate = _candidate(self.project if project is None else project)
        if candidate.root.resolve() != self.root or candidate._storage_root != self.root:
            raise ProjectError('事务不能切换项目目录', 'INVALID_ARGUMENT')
        _check_revision(candidate.loaded_revision, self._latest.loaded_revision)
        if candidate.loaded_revision is None:
            raise ProjectError('事务候选缺少加载基准', 'REVISION_CONFLICT')
        candidate.outputs = deepcopy(self._latest.outputs)
        # Filenames may have changed without changing the logical revision.
        candidate._module_files = deepcopy(self._latest._module_files)
        prepare_project(candidate)
        if project_revision(candidate) == self._latest.loaded_revision and not candidate._delete_files:
            self.project = deepcopy(self._latest)
        else:
            _commit_project_locked(candidate, self.lock)
            self._write_committed = True
            self.project = candidate
        self._committed = True
        return self.project

    def commit_outputs(self, outputs):
        """Replace only generation records on the latest locked project."""
        self.lock.require_owned(self.root)
        if self._committed:
            raise ProjectError('同一事务只能提交一次', 'INVALID_ARGUMENT')
        candidate = deepcopy(self._latest)
        candidate.outputs = deepcopy(outputs)
        prepare_project(candidate)
        if candidate.outputs != self._latest.outputs:
            _commit_project_locked(candidate, self.lock, outputs_only=True)
            self._write_committed = True
            self.project = candidate
        else:
            self.project = deepcopy(self._latest)
        self._committed = True
        return self.project

    def __exit__(self, error_type, error, traceback):
        from .project_lock import LockReleaseError
        try:
            return self.lock.__exit__(error_type, error, traceback)
        except LockReleaseError as release_error:
            release_error.commit_state = 'committed' if self._write_committed else 'not_committed'
            raise


def _commit_project_locked(project, lock, outputs_only=False):
    """Reuse the existing staged replacement/rollback under the caller's lock."""
    from .revision import project_revision
    lock.require_owned(project.root)
    basic, modules = prepare_project(project)
    revision = project_revision(project)
    root = project.root
    bound = project._module_files if project._storage_root == root.resolve() else {}
    cleanup = set(project._delete_files) if project._storage_root == root.resolve() else set()
    for entry in basic['modules']:
        old = bound.get(entry['module_id'])
        if old and old['file'] != entry['file']:
            cleanup.add(old['file'])
    active = {entry['file'].casefold() for entry in basic['modules']}
    cleanup = [name for name in cleanup if name.casefold() not in active]
    for name in cleanup:
        _local(root, name)
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.prism-save-', dir=str(root)))
    changed = []
    keep_recovery = False
    storage_warnings = []
    try:
        contents = [(filename, payload) for _, _, _, filename, payload in modules]
        contents.extend((GROUP_FILES[kind], payload) for kind, payload in group_payloads(project.fields).items())
        contents.append((PROJECT_FILENAME, basic))
        replacements = []
        for index, (filename, payload) in enumerate(contents):
            serialized = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False,
                                    default=_json_default)
            target = root / filename
            if outputs_only and filename != PROJECT_FILENAME and target.is_file():
                # Match text-mode serialization, including native newlines. Noncanonical
                # files still follow the normal normalization and rollback path.
                if target.read_bytes() == serialized.replace('\n', os.linesep).encode('utf-8'):
                    continue
            (stage / str(index)).write_text(serialized, encoding='utf-8')
            replacements.append((index, target))
        for index, target in replacements:
            backup = stage / ('backup-' + str(index))
            existed = target.exists()
            if existed:
                backup.write_bytes(target.read_bytes())
            os.replace(str(stage / str(index)), str(target))
            changed.append((target, backup if existed else None))
    except Exception as original:
        failures = []
        for target, backup in reversed(changed):
            try:
                if backup is None:
                    target.unlink()
                else:
                    os.replace(str(backup), str(target))
            except OSError as error:
                failures.append(str(error))
        if failures:
            keep_recovery = True
            error = ProjectError('保存失败且回滚未完成；恢复副本保留在 {}：{}；{}'.format(
                stage, original, '；'.join(failures)), 'STORAGE_RECOVERY_REQUIRED')
            error.retryable = False
            error.commit_state = 'unknown'
            raise error from original
        original.commit_state = 'not_committed'
        raise
    finally:
        if not keep_recovery:
            try:
                for file in stage.iterdir():
                    file.unlink()
                stage.rmdir()
            except OSError as error:
                # Cleanup must not replace the transaction's actual outcome.
                storage_warnings.append('保存临时材料未清理：{}：{}'.format(stage, error))
    # The basic index is now committed. Only known renamed/explicitly removed
    # files may be unlinked; ordinary list replacement leaves files untouched.
    project._storage_root = root.resolve()
    project._loaded_revision = revision
    project._module_files = {e['module_id']: dict(e) for e in basic['modules']}
    project._overwrite_files = []
    for kind, source, item, _, _ in modules:
        if source is not item:
            values = getattr(project, kind)
            values[next(i for i, value in enumerate(values) if value is source)] = item
    project._delete_files = cleanup
    project._storage_warnings = storage_warnings
    for name in list(cleanup):
        try:
            path = _local(root, name)
            if path.exists():
                path.unlink()
            project._delete_files.remove(name)
        except (OSError, ProjectError) as error:
            project._storage_warnings.append('项目已保存，但旧文件未删除（可稍后重试保存）：{}：{}'.format(name, error))


def import_data(project, source, overwrite_basic=False, replace_existing=False, application_root=None):
    """Copy/replace validated data. Caller state changes only after a commit."""
    with ProjectTransaction(project.root) as transaction:
        if project.loaded_revision is None:
            raise ProjectError('请先保存或重新打开项目', 'REVISION_CONFLICT')
        _check_revision(project.loaded_revision, transaction.project.loaded_revision)
        return _import_data_locked(project, source, overwrite_basic, replace_existing, transaction, application_root)


def _import_data_locked(project, source, overwrite_basic, replace_existing, transaction, application_root=None):
    source = Path(source)
    payload = read_json(source)
    _require(isinstance(payload, dict), '导入数据必须是 JSON 对象')
    _require(isinstance(payload.get('data_type'), str), '导入数据 data_type 必须是文本')
    candidate = deepcopy(project)
    if payload.get('data_type') == 'basic':
        validate_basic(payload)
        if not overwrite_basic:
            raise DataConflict('BASIC_CONFLICT', '导入将替换项目名称、模型、非组基本字段和排期；保留人员职责、测试环境、模板、模块和输出。是否继续？')
        candidate.name = payload['name']
        candidate.model_id, candidate.model_version = payload['model']['id'], payload['model']['version']
        candidate.fields = dict(deepcopy(payload['fields']), **{
            key: value for key, value in candidate.fields.items() if key in GROUP_KEYS})
        candidate.schedule = deepcopy(payload['schedule'])
        from .schedule_state import imported_schedule
        candidate.schedule = imported_schedule(candidate, application_root)
    elif payload.get('data_type') in GROUP_FILES:
        preview = group_import_preview(candidate, payload)
        if preview['changes'] and not replace_existing:
            error = DataConflict('GROUP_CONFLICT', '导入将更新以下组字段：\n' + '\n'.join(
                '{}：{!r} → {!r}'.format(row['field'], row['before'], row['after']) for row in preview['changes']))
            error.details = preview
            raise error
        apply_group_import(candidate, payload)
    else:
        kind, item = validate_module(payload)
        matches = []
        for current_kind, _ in ITEM_TYPES:
            for index, current in enumerate(getattr(candidate, current_kind)):
                if getattr(current, 'module_id', None) == item.module_id or (
                        current_kind == kind and name_key(current['name']) == name_key(item['name'])):
                    matches.append((current_kind, index, current))
        _require(len(matches) <= 1, '导入 ID 与名称分别指向不同模块，无法自动替换')
        filename = module_filename(kind, item['name'])
        local = _local(candidate.root, filename)
        if matches and not replace_existing:
            old_kind, _, old = matches[0]
            detail = '；同时覆盖本地文件 ' + filename if local.exists() else ''
            raise DataConflict('MODULE_CONFLICT', '将用 {}“{}”替换 {}“{}”{}，是否继续？'.format(
                LABELS[kind], item['name'], LABELS[old_kind], old['name'], detail))
        if local.exists():
            same_source = source.resolve() == local.resolve()
            old_file = matches and candidate._module_files.get(getattr(matches[0][2], 'module_id', ''), {}).get('file')
            if not (same_source or replace_existing):
                raise DataConflict('FILE_CONFLICT', '本地已存在文件，是否覆盖：' + filename)
            if old_file or same_source or replace_existing:
                candidate._overwrite_files.append(filename)
        if matches:
            old_kind, index, old = matches[0]
            old_entry = candidate._module_files.get(getattr(old, 'module_id', ''))
            if old_entry and old_entry['file'] != filename:
                candidate._delete_files.append(old_entry['file'])
            if old_kind == kind:
                getattr(candidate, kind)[index] = item
            else:
                del getattr(candidate, old_kind)[index]
                getattr(candidate, kind).append(item)
        else:
            getattr(candidate, kind).append(item)
    candidate = transaction.commit(candidate)
    project.__dict__.update(candidate.__dict__)
    return {'data_type': payload['data_type'], 'modules': project.modules, 'warnings': project._storage_warnings}


def remove_module(project, module_id, delete_file=False):
    with ProjectTransaction(project.root) as transaction:
        if project.loaded_revision is None:
            raise ProjectError('请先保存或重新打开项目', 'REVISION_CONFLICT')
        _check_revision(project.loaded_revision, transaction.project.loaded_revision)
        return _remove_module_locked(project, module_id, delete_file, transaction)


def _remove_module_locked(project, module_id, delete_file, transaction):
    candidate = deepcopy(project)
    for kind, _ in ITEM_TYPES:
        values = getattr(candidate, kind)
        for index, item in enumerate(values):
            if getattr(item, 'module_id', None) == module_id:
                old = candidate._module_files.get(module_id)
                if delete_file and old:
                    candidate._delete_files.append(old['file'])
                del values[index]
                candidate = transaction.commit(candidate)
                project.__dict__.update(candidate.__dict__)
                return {'module_id': module_id, 'delete_file': delete_file, 'modules': project.modules,
                        'warnings': project._storage_warnings}
    raise ProjectError('找不到 module_id：' + str(module_id))
