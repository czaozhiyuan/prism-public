"""Writable first-delivery fields shared by storage, discovery and adapters."""
import re

DELIVERY_FIELDS = ("交付文件名", "软件规模")
SCALE_PATTERN = r"^(?:[ \t]*(?:[0-9]+(?:\.[0-9]+)?)[ \t]*|[ \t]*)(?![\s\S])"
DELIVERY_SCHEMAS = {
    "交付文件名": {"type": "string"},
    "软件规模": {"type": "string", "pattern": SCALE_PATTERN},
}


def validate_delivery_fields(fields):
    """Keep absent/empty v7 values distinct; never normalize stored strings."""
    from .project import ProjectError
    for key in DELIVERY_FIELDS:
        if not isinstance(fields.get(key, ""), str):
            raise ProjectError(key + "必须是文本")
    if not re.fullmatch(SCALE_PATTERN, fields.get("软件规模", "")):
        raise ProjectError("软件规模必须为空或非负十进制数，单位为千行")
