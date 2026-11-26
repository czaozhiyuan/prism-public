"""Shared, purpose-aware input diagnostics and final DOCX checklist rows."""

import re
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from .items import TEST_TYPES, normalize_test_record, test_record_issues
from .schema import ENVIRONMENT_FIELDS, TOOL_ALIASES, TOOL_FIELDS
from .template_text import template_environment
from .word_finalize import NS

REPORT_CATEGORIES = {"9": ("unit_tests",), "9.1": ("unit_tests",),
                     "12": ("config_tests",), "17": ("unit_tests", "config_tests")}
RECORD_CATEGORIES = dict(REPORT_CATEGORIES, **{"8": ("unit_tests",),
                                             "8.1": ("unit_tests",), "11": ("config_tests",)})
TECHNICAL_FIELDS = {"器件类型", "器件型号", "操作系统", "语言", "外设及内部资源", "调试器"} | set(TOOL_FIELDS)
PARAMETER_LABELS = (
    "主频（MHz）", "Flash 容量（KB）", "外设及内部资源", "测试供电方式",
    "实际使用供电方式", "测试温度范围（℃）", "实际使用温度范围（℃）",
    "测试工作环境（含温度范围）", "实际使用工作环境（含温度范围）",
    "环境差异影响及测试有效性评估",
)
LEGACY_PHRASES = ("已退役的示例产品", "正常终于", "与期望结果一致标志该测试项通过")
PLACEHOLDER = re.compile(r"{{\s*[A-Za-z_\u4e00-\u9fff][^{}\r\n]*(?:}}|$)|{%.*?%}|{#.*?#}", re.DOTALL)
REFERENCE_ERROR = re.compile(
    r"Error[!:]?\s*(?:Reference source not found|Bookmark not defined)|错误[!！].*?(?:引用源|未定义书签)|【待补充(?:交叉|表|图)引用】", re.I)
DUPLICATE_PUNCTUATION = re.compile(r"([。！？])(?:[ \t]*\1)+")


def template_number(template):
    match = re.match(r"^(\d+(?:\.\d+)?)", Path(template).name)
    return match.group(1) if match else ""


def input_review_issues(template, document, context, declared=None):
    """Inspect declared input only; planned records are not execution facts."""
    number = template_number(template)
    issues = test_record_issues(context, REPORT_CATEGORIES.get(number, ()))
    for category in RECORD_CATEGORIES.get(number, ()):
        for row in context.get(category.replace("tests", "test_items"), []):
            facts = normalize_test_record(row["test"])
            if facts["execution_status"] == "not_executed" and facts["actual_date"]:
                location = "{}「{}」/{}第{}项「{}」".format(
                    row["parent_type"], row["parent"]["name"], dict(TEST_TYPES)[category],
                    row["number"], row["test"]["name"] or "未命名")
                issues.append(("P5-状态矛盾", location,
                               "状态为未执行，但存在实际执行日期",
                               "核对真实记录，修正状态或实际记录；不从结果文字或图片推断"))
    if declared is None:
        declared = document.get_undeclared_template_variables(jinja_env=template_environment())
    declared = declared.union(TOOL_ALIASES[alias] for alias in TOOL_ALIASES if alias in declared)
    for key in sorted(declared & TECHNICAL_FIELDS & set(ENVIRONMENT_FIELDS)):
        if not str(context.get(key, "") or "").strip():
            issues.append(("P5-参数缺项", "模板字段「{}」".format(key), key + "未填写",
                           "依据硬件设计、需求或工具记录补充；不使用固定样例值"))
    return issues


def paragraph_text(paragraph):
    """Keep line breaks so an empty parameter line stays distinguishable."""
    return "".join(node.text or "" if node.tag == "{" + NS["w"] + "}t" else "\n"
                   for node in paragraph.iter()
                   if node.tag in ("{" + NS["w"] + "}t", "{" + NS["w"] + "}br"))


def paragraph_location(part, paragraph, index, tables):
    location = "{} 段落{}".format(part, index)
    cells = paragraph.xpath("ancestor::w:tc", namespaces=NS)
    if cells:
        cell = cells[-1]
        row = cell.getparent()
        table = row.getparent()
        location += "；表{}/行{}/单元格{}".format(
            tables[table], list(table.findall("w:tr", NS)).index(row) + 1,
            list(row.findall("w:tc", NS)).index(cell) + 1)
    return location


def parameter_table_rows(root, output):
    """Check only the retained, approved environment/equipment table profiles."""
    profiles = {
        ("序号", "类型", "工具及版本", "符合性", "检查人", "时间"): tuple(range(2, 6)),
        ("序号", "类型", "名称", "版本", "符合性", "检查人", "时间"): tuple(range(2, 7)),
        ("序号", "仪器名称", "检查内容", "检查结果", "符合性", "检查人", "时间"): tuple(range(1, 7)),
        ("分析项", "测试环境", "需求环境", "结论"): (1, 2, 3),
    }
    result = []
    for number, table in enumerate(root.xpath("//w:tbl", namespaces=NS), 1):
        rows = table.findall("w:tr", NS)
        if not rows:
            continue
        headers = tuple("".join(cell.xpath(".//w:t/text()", namespaces=NS)).strip()
                        for cell in rows[0].findall("w:tc", NS))
        required = profiles.get(headers)
        if headers == ("测试环境差异性分析", "测试环境", "需求环境", "结论"):
            headers = ("序号", "分析项", "测试环境", "需求环境", "结论")
            required = (2, 3, 4)
        if required is None:
            continue
        for row_number, row in enumerate(rows[1:], 2):
            cells = row.findall("w:tc", NS)
            if len(cells) != len(headers):
                continue
            missing = [headers[index] for index in required
                       if not "".join(cells[index].xpath(".//w:t/text()", namespaces=NS)).strip()
                       or "待人工填写" in "".join(cells[index].xpath(".//w:t/text()", namespaces=NS))]
            if missing:
                result.append((output.name, "P5-参数缺项",
                               "word/document.xml 表{}/行{}；{}".format(
                                   number, row_number, "、".join(missing)),
                               "必要环境/设备/检查记录为空：" + "、".join(missing),
                               "依据实际环境、设备及校准/检查记录填写；不适用须人工确认"))
    return result


def review_rows_for_document(template, output, issues=()):
    """Read final OOXML without changing output or claiming visual acceptance."""
    output = Path(output)
    rows = [(output.name, *row) for row in issues]
    located_references = []
    with ZipFile(template) as source:
        source_root = etree.fromstring(source.read("word/document.xml"))
        source_text = "".join(source_root.xpath("//w:t/text()", namespaces=NS))
    with ZipFile(output) as archive:
        styles = etree.fromstring(archive.read("word/styles.xml"))
        heading_styles = set(styles.xpath(
            '//w:style[w:name[starts-with(@w:val,"heading ") or starts-with(@w:val,"标题")]]/@w:styleId',
            namespaces=NS))
        for part in archive.namelist():
            if not (part == "word/document.xml" or
                    part.startswith(("word/header", "word/footer")) and part.endswith(".xml")):
                continue
            root = etree.fromstring(archive.read(part))
            tables = {table: index for index, table in enumerate(
                root.xpath("//w:tbl", namespaces=NS), 1)}
            if part == "word/document.xml":
                rows.extend(parameter_table_rows(root, output))
            heading = ""
            for index, paragraph in enumerate(root.xpath("//w:p", namespaces=NS), 1):
                value = paragraph_text(paragraph)
                if paragraph.xpath("./w:pPr/w:pStyle/@w:val", namespaces=NS) and not paragraph.xpath(
                        "ancestor::w:tc", namespaces=NS):
                    style = paragraph.xpath("./w:pPr/w:pStyle/@w:val", namespaces=NS)[0]
                    if style in heading_styles:
                        heading = value.strip()
                location = paragraph_location(part, paragraph, index, tables)
                if heading:
                    location += "；章节「{}」".format(heading)
                if REFERENCE_ERROR.search(value):
                    code = "P1-07" if re.search("【待补充[表图]引用】", value) else "P0-01"
                    located_references.append((output.name, code, location, value,
                                               "补充正确引用目标并更新 Word 域"))
                if PLACEHOLDER.search(value):
                    rows.append((output.name, "P5-占位符", location, value,
                                 "检查模板替换符和上下文字段，修正后重新生成"))
                if DUPLICATE_PUNCTUATION.search(value):
                    rows.append((output.name, "P5-标点", location, value,
                                 "核对字段与模板的句末拼接；仅定向修正重复中文标点"))
                for phrase in LEGACY_PHRASES:
                    if phrase in value and (phrase != "已退役的示例产品" or phrase in source_text):
                        rows.append((output.name, "P5-旧残留", location, phrase,
                                     "核对是否为当前项目真实内容；若为旧模板残留则替换"))
                for line in value.splitlines():
                    if any(re.fullmatch(re.escape(label) + r"[：:]\s*", line.strip())
                           for label in PARAMETER_LABELS):
                        rows.append((output.name, "P5-参数缺项", location, line.strip(),
                                     "按当前器件、环境和配置记录补充，不推断技术事实"))
                if "待人工填写" in value or ("待补充" in value and "引用】" not in value):
                    rows.append((output.name, "人工项", location, value,
                                 "依据真实项目记录补充或确认不适用"))
                if "静态链接目录（页码未重算）" in value:
                    rows.append((output.name, "P5-目录降级", location,
                                 "当前保留静态目录，页码未经 Word 重算",
                                 "使用 Word 更新并核对目录、页脚与分页；需视觉核查"))
    for issue, location, detail in {
        "13": [("P1-08", "发布材料清单", "人工审核发布材料序号、完整性及实际交付范围")],
        "15": [("P1-11", "配置项记录及变更记录", "补充真实初始版本、最后版本、变更次数及变更记录")],
        "16": [("手工填写项", "黄色标记及人工表格", "依据真实记录填写审核、不符合项、评审缺陷及解决情况")],
    }.get(template_number(template), []):
        rows.append((output.name, issue, location, detail, "待人工审核"))
    # Replace coarse rendering diagnostics with final paragraph locations.
    located_codes = {row[1] for row in located_references}
    rows = [row for row in rows if row[1] not in located_codes]
    rows.extend(located_references)
    from .review_checklist import unique_review_rows
    return list(unique_review_rows(rows))
