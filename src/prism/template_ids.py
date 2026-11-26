"""Portable template identities; no GUI or Agent runtime dependencies."""
import re
from pathlib import Path

from .model_identity import MODEL_TOKEN
from .project import ProjectError

# These portable regexes are also published in the v4 JSON Schema.
_RESERVED = r"(?:[cC][oO][nN]|[pP][rR][nN]|[aA][uU][xX]|[nN][uU][lL]|[cC][oO][mM][1-9]|[lL][pP][tT][1-9])"
_COMPONENT = (r"(?!" + _RESERVED + r"(?:\.|/|$))"
              r'[^<>:"\\|?*\x00-\x1f/]*[^<>:"\\|?*\x00-\x1f/ .]')
_RELATIVE = _COMPONENT + "(?:/" + _COMPONENT + ")*"
_END = r"(?![\s\S])"
RELATIVE_PATH_PATTERN = "^" + _RELATIVE + _END
OUTPUT_PATH_PATTERN = "^outputs/" + _RELATIVE + _END
TEMPLATE_ID_PATTERN = (r"^(?:asset:(?:templates/docx|reference/workbooks)/|model:"
                       + MODEL_TOKEN + "@" + MODEL_TOKEN + ":documents/)"
                       + r"(?=[\s\S]*[^/]\.([dD][oO][cC][xX]|[xX][lL][sS][xX])" + _END + ")"
                       + _RELATIVE + _END)

DEFAULT_DISABLED_TEMPLATE_IDS = frozenset((
    "asset:templates/docx/7.1.软件仿真测试计划_模板.docx",
    "asset:templates/docx/8.1软件仿真测试说明.docx",
    "asset:templates/docx/9.1软件仿真测试报告.docx",
))

TEST_TEMPLATE_PAIRS = (
    ('asset:templates/docx/7.软件单元测试计划_模板.docx',
     'asset:templates/docx/7.1.软件仿真测试计划_模板.docx'),
    ('asset:templates/docx/8.软件单元测试说明_模板.docx',
     'asset:templates/docx/8.1软件仿真测试说明.docx'),
    ('asset:templates/docx/9.软件单元测试报告_模板.docx',
     'asset:templates/docx/9.1软件仿真测试报告.docx'),
)


def device_test_template_id(template_id, device_type=''):
    """Route only the bundled test pairs; unknown devices use the MCU side."""
    fpga = str(device_type or '').strip().upper() == 'FPGA'
    for pair in TEST_TEMPLATE_PAIRS:
        if template_id in pair:
            return pair[1 if fpga else 0]
    return template_id


def selected_template_ids(config, available, device_type='', requested=None):
    """Apply the same device routing to default, custom and explicit batches."""
    if requested is not None:
        selected = requested
    elif config.get('mode') == 'custom':
        selected = sorted(config.get('enabled_ids', []))
    else:
        selected = sorted(available)
    return list(dict.fromkeys(device_test_template_id(value, device_type) for value in selected))


def is_key_value_template(path):
    """Identify legacy key-value workbooks retained only as import references."""
    path = Path(path)
    return path.suffix.lower() == ".xlsx" and "键值表" in path.name


def discover_template_paths(asset_root, model=None):
    """Discover supported templates recursively for every product surface."""
    roots = [Path(asset_root) / "templates" / "docx", Path(asset_root) / "reference" / "workbooks"]
    if model is not None:
        roots.append(Path(model.source_dir) / "documents")
    files = []
    for root in roots:
        if root.is_dir():
            files.extend(path.resolve() for path in root.rglob("*")
                         if path.is_file() and not path.is_symlink()
                         and path.suffix.lower() in (".docx", ".xlsx")
                         and not is_key_value_template(path))
    return sorted(set(files), key=lambda path: path.as_posix().casefold())


def relative_parts(value):
    """Validate a canonical relative path without Windows path aliases."""
    if not isinstance(value, str) or not re.fullmatch(RELATIVE_PATH_PATTERN, value):
        raise ProjectError("模板或输出路径必须为安全的 / 分隔相对路径：" + str(value))
    return value.split("/")


def parse_template_id(value):
    """Return (model identity or None, relative path) after syntax checks."""
    if not isinstance(value, str) or not re.fullmatch(TEMPLATE_ID_PATTERN, value):
        raise ProjectError("不支持的模板逻辑 ID：" + str(value))
    if value.startswith("asset:"):
        identity, relative = None, value[6:]
    else:
        identity, relative = value[6:].split(":", 1)
    return identity, relative


def default_template_enabled(template_id, device_type=''):
    """Select the applicable bundled test trio and every other template."""
    parse_template_id(template_id)
    return device_test_template_id(template_id, device_type) == template_id


def resolve_template_id(value, asset_root, model=None):
    """Resolve against this installation and reject links escaping its root."""
    identity, relative = parse_template_id(value)
    if identity is None:
        root = Path(asset_root).resolve()
    else:
        if model is None or identity != "{}@{}".format(model.id, model.version):
            raise ProjectError("模板模型尚未安装或版本不匹配：" + value, "TEMPLATE_NOT_FOUND")
        root = Path(model.source_dir).resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ProjectError("模板路径越出资产或模型根目录：" + value)
    if is_key_value_template(target):
        raise ProjectError("已停止生成键值表，请使用基本数据.json：" + value, "TEMPLATE_NOT_FOUND")
    if not target.is_file():
        raise ProjectError("找不到模板：" + value, "TEMPLATE_NOT_FOUND")
    return target


def encode_template_id(path, asset_root, model=None):
    """Encode a discovered file and verify that decoding selects that file."""
    path = Path(path).resolve()
    roots = [("asset:", Path(asset_root).resolve())]
    if model is not None:
        roots.insert(0, ("model:{}@{}:".format(model.id, model.version), Path(model.source_dir).resolve()))
    for prefix, root in roots:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        value = prefix + relative
        resolve_template_id(value, asset_root, model)
        return value
    raise ProjectError("模板不在资产或模型根目录中：" + str(path))
