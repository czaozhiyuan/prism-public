"""Independent DOCX versions, sparse overrides and strict reference lookup."""
import re
from pathlib import Path

from .project import ProjectError
from .template_ids import parse_template_id

DEFAULT_DOCUMENT_VERSION = 'V1.0.0'
VERSION_OVERRIDES_KEY = '_document_version_overrides'
VERSION_PATTERN = r'^(?=[\s\S]*\S)[^\x00-\x1f\x7f-\x9f\u2028\u2029]+(?![\s\S])'


def normalize_version(value):
    if not isinstance(value, str) or not re.fullmatch(VERSION_PATTERN, value):
        raise ProjectError('文档版本必须为非空单行文本，不能包含控制字符')
    return value.strip().upper()


def normalize_versions(values):
    if not isinstance(values, dict):
        raise ProjectError('templates.document_versions 必须是对象')
    result = {}
    for template_id, value in values.items():
        _, relative = parse_template_id(template_id)
        if not relative.lower().endswith('.docx'):
            raise ProjectError('只有 DOCX 可以配置文档版本：' + template_id)
        version = normalize_version(value)
        if version != DEFAULT_DOCUMENT_VERSION:
            result[template_id] = version
    return result


def normalize_template_config(config):
    result = dict(config)
    versions = normalize_versions(config.get('document_versions', {}))
    result.pop('document_versions', None)
    if versions:
        result['document_versions'] = versions
    return result


def merge_template_config(config, current):
    result = dict(config)
    if 'document_versions' not in result and 'document_versions' in current:
        result['document_versions'] = current['document_versions']
    return normalize_template_config(result)


class VersionReferenceError(ValueError):
    """An explicit reference must identify an available DOCX."""


class DocumentVersions(dict):
    def __missing__(self, key):
        raise VersionReferenceError('无法解析被引用的 DOCX 身份：' + str(key))

    def get(self, key, default=None):
        # A missing document cannot silently inherit another document's version.
        return self[key]


def bundled_document_ids():
    current = Path(__file__).resolve()
    for parent in current.parents:
        folder = parent / 'assets/templates/docx'
        if folder.is_dir():
            return {'asset:' + path.relative_to(parent / 'assets').as_posix()
                    for path in folder.rglob('*')
                    if path.is_file() and not path.is_symlink() and path.suffix.lower() == '.docx'}
    return set()


def effective_document_versions(overrides=None, catalog=None):
    """Resolve the full available catalog without consulting batch selection."""
    known = bundled_document_ids() if catalog is None else set(catalog)
    versions = normalize_versions(overrides or {})
    return DocumentVersions({key: versions.get(key, DEFAULT_DOCUMENT_VERSION)
                             for key in known if key.lower().endswith('.docx')})


def context_for_template(template, context, template_id=None, overrides=None, catalog=None):
    """Construct a new context; XLSX and the shared software version stay intact."""
    values = dict(context)
    if Path(template).suffix.lower() != '.docx':
        return values
    if (template_id is None and overrides is None and catalog is None
            and isinstance(context.get('document_versions'), DocumentVersions)):
        return values
    known = bundled_document_ids() if catalog is None else set(catalog)
    known = {value for value in known if value.lower().endswith('.docx')}
    versions = normalize_versions(context.get(VERSION_OVERRIDES_KEY, {})
                                  if overrides is None else overrides)
    values[VERSION_OVERRIDES_KEY] = versions
    if template_id is None:
        # Direct renderer callers have no model metadata; bundled files remain portable.
        path = Path(template).resolve()
        for value in known:
            if value.startswith('asset:'):
                relative = Path(value[6:])
                if tuple(path.parts[-len(relative.parts):]) == relative.parts:
                    template_id = value
                    break
    values['文档版本'] = versions.get(template_id, DEFAULT_DOCUMENT_VERSION)
    values['document_versions'] = effective_document_versions(versions, known)
    from .initial_delivery import configuration_context
    values.update(configuration_context(values))
    return values


def validate_version_references(document, versions):
    """Check declared lookups without rendering loops or changing Jinja defaults."""
    from jinja2 import nodes

    from .template_text import template_environment

    document.init_docx(reload=False)
    parts = [document.get_xml()]
    for uri in (document.HEADER_URI, document.FOOTER_URI):
        parts.extend(document.get_part_xml(part) for _, part in document.get_headers_footers(uri))
    for part in parts:
        parsed = template_environment().parse(document.patch_xml(part))
        for node in parsed.find_all((nodes.Getitem, nodes.Call)):
            if isinstance(node, nodes.Getitem):
                owner, key = node.node, node.arg
            elif isinstance(node.node, nodes.Getattr) and node.node.attr == 'get' and node.args:
                owner, key = node.node.node, node.args[0]
            else:
                continue
            if isinstance(owner, nodes.Name) and owner.name == 'document_versions':
                if not isinstance(key, nodes.Const) or not isinstance(key.value, str):
                    raise VersionReferenceError('文档版本引用必须使用明确的模板逻辑 ID 字符串')
                versions[key.value]
