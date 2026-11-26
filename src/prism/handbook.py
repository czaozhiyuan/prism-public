"""Physical handbook variants behind one stable document identity."""
from pathlib import Path


HANDBOOK_NAME = '14.软件固件保障手册_模板.docx'
HANDBOOK_ID = 'asset:templates/docx/' + HANDBOOK_NAME
NOT_APPLICABLE = '固件保障手册不适用'


def device_type(context):
    """Use the same whitespace/case normalization as executable filenames."""
    return str(context.get('器件类型', '') or '').strip().upper()


def is_builtin_handbook(template):
    template = Path(template)
    return (template.name == HANDBOOK_NAME and template.parent.name == 'docx'
            and template.parent.parent.name == 'templates')


def handbook_template(template, context):
    """Keep MCU as the default; an unavailable FPGA asset must not fall back."""
    template = Path(template)
    if not is_builtin_handbook(template) or device_type(context) != 'FPGA':
        return template
    asset_root = template.parents[2].resolve()
    variant = (asset_root / 'templates/variants/fpga' / HANDBOOK_NAME).resolve()
    variant.relative_to(asset_root)
    return variant


def handbook_not_applicable(template, context):
    return is_builtin_handbook(template) and device_type(context) not in ('MCU', 'FPGA')
