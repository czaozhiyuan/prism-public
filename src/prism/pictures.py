"""Insert explicitly configured project and optional test-result pictures."""
from __future__ import annotations

import re
import struct
import zlib
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .project import ProjectError
from .template_ids import relative_parts
from .template_text import template_environment

PICTURE_SLOTS = {
    "system_boundary": "系统组成与软件边界图",
    "software_architecture": "软件架构图",
    "interface_relationship": "接口关系图",
    "main_flow_algorithm": "主要流程算法图",
    "test_environment": "测试环境与接线图",
    "schedule_milestones": "进度里程碑图",
}
MISSING_PICTURE_TEXT = "图片暂缺"
DEFAULT_WIDTH_MM = 140.0
REVIEW_MISSING = "P0-02"
SLOT_PATTERN = re.compile(r"[a-z0-9_-]{1,64}")


def validate_slot_table(slots: Mapping[str, str]) -> None:
    for slot in slots:
        if not isinstance(slot, str) or not SLOT_PATTERN.fullmatch(slot):
            raise ProjectError("图片槽位名必须匹配 ^[a-z0-9_-]{1,64}$：" + str(slot))


def configured_path(root: Path, value: str) -> Path:
    """Absolute local paths are explicit choices; relative paths use the project root."""
    if not isinstance(value, str):
        raise ProjectError("图片路径必须是文本")
    value = value.strip()
    if value.startswith(("\\\\", "//")):
        raise ProjectError("请选择本地图片路径")
    path = Path(value)
    if not path.is_absolute():
        relative_parts(value)
        path = root / path
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise ProjectError("图片格式仅支持 PNG、JPEG")
    return path


def declared_picture_slots(template: Any, project_root: Optional[Path] = None) -> Dict[str, str]:
    return inspect_picture_slots(template)[1]


def inspect_picture_slots(template):
    """Return declared variables and validated fixed picture slots for this document."""
    validate_slot_table(PICTURE_SLOTS)
    declared = template.get_undeclared_template_variables(jinja_env=template_environment())
    slots = {key: value for key, value in PICTURE_SLOTS.items() if key in declared}
    for paragraph in template.get_docx().element.xpath(".//w:p"):
        value = "".join(paragraph.xpath(".//w:t/text()"))
        for slot in slots:
            variable = r"{{\s*" + re.escape(slot) + r"\s*}}"
            if re.search(variable, value) and not re.fullmatch(r"\s*" + variable + r"\s*", value):
                raise ProjectError("图片槽位变量必须独占一段：" + slot)
    return declared, slots


def png_size(path: Path) -> Optional[Tuple[int, int]]:
    """Read dimensions from IHDR; image parsing is checked separately."""
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", header[16:24])
    return (width, height) if width > 0 and height > 0 else None


def _png_blob(picture: Path) -> bytes:
    """Validate PNG chunks and compressed data, then use the existing DOCX parser."""
    from docx.image.image import Image

    data = picture.read_bytes()
    if png_size(picture) is None:
        raise ValueError("PNG signature/IHDR 无效")
    offset = 8
    compressed = bytearray()
    seen_end = False
    while offset < len(data):
        length, tag = struct.unpack(">I4s", data[offset:offset + 8])
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])[0]
        if zlib.crc32(tag + payload) & 0xFFFFFFFF != crc:
            raise ValueError("PNG chunk CRC mismatch: " + repr(tag))
        if tag == b"IDAT":
            compressed.extend(payload)
        offset += 12 + length
        if tag == b"IEND":
            seen_end = length == 0
            break
    if not compressed or not seen_end:
        raise ValueError("PNG 缺少 IDAT/IEND")
    decoder = zlib.decompressobj()
    decoder.decompress(bytes(compressed))
    if not decoder.eof:
        raise ValueError("PNG IDAT 数据截断")
    return Image.from_file(BytesIO(data)).blob


def _picture(template, root, value, location, missing_text, issues, max_width_mm=DEFAULT_WIDTH_MM):
    from docx.image.image import Image
    from docx.shared import Mm
    from docxtpl import InlineImage

    if not value or (isinstance(value, str) and not value.strip()):
        return missing_text
    try:
        picture = configured_path(root, value)
        if picture.suffix.lower() == ".png":
            blob = _png_blob(picture)
        else:
            blob = Image.from_file(str(picture)).blob
        image = Image.from_file(BytesIO(blob))
        section = template.get_docx().sections[0]
        width = min(Mm(max_width_mm), section.page_width - section.left_margin - section.right_margin)
        height = section.page_height - section.top_margin - section.bottom_margin - Mm(20)
        width = min(width, height * image.px_width / image.px_height)
        return InlineImage(template, BytesIO(blob), width=int(width))
    except Exception as error:
        issues.append((REVIEW_MISSING, location, "图片无法插入：" + str(error),
                       "重新选择有效的 PNG 或 JPEG 图片后生成"))
        return missing_text


def resolve_pictures(template: Any, project_root: Optional[Path],
                     slots: Optional[Mapping[str, str]] = None,
                     context: Optional[Mapping[str, Any]] = None) -> Tuple[Dict[str, Any], List[tuple]]:
    slots = PICTURE_SLOTS if slots is None else slots
    validate_slot_table(slots)
    root = Path(project_root) if project_root is not None else Path.cwd()
    context = context or {}
    issues = []
    values = {}
    for slot, field in slots.items():
        value = context.get(field, "")
        if not value or isinstance(value, str) and not value.strip():
            issues.append(("P5-图片未配置", field + " / " + slot, "固定图位未选择图片",
                           "在项目基本数据的图片页选择对应图片；未配置时正文显示图片暂缺"))
        values[slot] = _picture(template, root, value, field,
                                MISSING_PICTURE_TEXT, issues)
    return values, issues


def resolve_test_pictures(template, root, context, declared=None):
    """Render only test-result collections declared by the current template."""
    if declared is None:
        declared = template.get_undeclared_template_variables(jinja_env=template_environment())
    issues = []
    from docx.oxml.ns import qn
    widths = [DEFAULT_WIDTH_MM]
    found = False
    for paragraph in template.get_docx().element.xpath(".//w:p"):
        if "result_picture" not in "".join(paragraph.xpath(".//w:t/text()")):
            continue
        found = True
        cells = paragraph.xpath("ancestor::w:tc")
        if cells:
            size = cells[-1].find("./" + qn("w:tcPr") + "/" + qn("w:tcW"))
            if size is not None and size.get(qn("w:type")) == "dxa":
                # Reserve cell padding in addition to respecting the page margins.
                widths.append(max(1.0, int(size.get(qn("w:w"))) * 25.4 / 1440 - 4))
    if not found:
        return issues
    seen = set()
    for key, rows in context.items():
        if key not in declared or not key.endswith("_test_items") or not isinstance(rows, list):
            continue
        for row in rows:
            if id(row) in seen:
                continue
            seen.add(id(row))
            test = row["test"]
            row["result_picture"] = _picture(template, Path(root), test.get("result_image", ""),
                                              "{}「{}」/{}第{}项「{}」".format(
                                                  row.get("parent_type", "模块"), row.get("parent", {}).get("name", ""),
                                                  key, row.get("number", ""), test.get("name", "")),
                                              "", issues, min(widths))
    return issues
