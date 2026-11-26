"""Structured project objects and derived template views."""

from copy import deepcopy
from datetime import date
import re
from uuid import uuid4
from .document_fields import chapter_positions


class ModuleItem(dict):
    """Business mapping with persistence identity outside template/payload keys."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.module_id = str(uuid4())

ITEM_TYPES = (("features", "功能"), ("performances", "性能"), ("interfaces", "接口"),
              ("reliabilities", "可靠性"))
# Category prefixes retained from the 438B templates and their trace tables.
REQUIREMENT_PREFIXES = {
    "features": ("RWGN", "XQGN", "SJSM"),
    "performances": ("RWXN", "XQXN", "SJXN"),
    "interfaces": ("RWJK", "XQJK", "SJJK"),
    "reliabilities": ("RWKK", "XQKK", "SJKK"),
}
ITEM_FIELDS = {
    "reliabilities": (("name", "可靠性名称"), ("code", "可靠性标号"),
                      ("task_description", "任务描述"), ("requirement_description", "需求描述"),
                      ("design_description", "设计说明")),
    "features": (("name", "功能名称"), ("code", "功能标号"), ("task_description", "任务/功能描述"),
                 ("requirement_description", "需求描述"), ("design_description", "设计说明")),
    "performances": (("name", "性能名称"), ("code", "性能标号"), ("description", "性能描述"),
                     ("requirement_description", "需求描述"), ("design_description", "设计说明")),
    "interfaces": (("name", "接口名称"), ("code", "接口标号"), ("direction", "方向"),
                   ("definition", "接口定义"), ("pin_definition", "管脚定义"),
                   ("initial_value", "初始化值"), ("requirement_description", "接口需求"),
                   ("design_description", "接口设计")),
}
TEST_TYPES = (("unit_tests", "单元测试"), ("config_tests", "配置项测试"))


EXECUTION_STATUSES = {"not_recorded": "未记录", "not_executed": "未执行",
                      "passed": "通过", "failed": "未通过"}


def normalize_test_record(test):
    """Validate explicit execution facts without inferring them from free text."""
    status = test.get("execution_status", "")
    if not isinstance(status, str) or status not in ("", *EXECUTION_STATUSES):
        raise ValueError("测试项 execution_status 必须为 not_recorded、not_executed、passed 或 failed")
    actual_date = test.get("actual_date", "")
    if not isinstance(actual_date, str):
        raise ValueError("测试项 actual_date 必须是文本")
    if actual_date:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", actual_date):
            raise ValueError("测试项 actual_date 必须为 YYYY-MM-DD 或空字符串")
        try:
            date.fromisoformat(actual_date)
        except ValueError as error:
            raise ValueError("测试项 actual_date 不是有效日期") from error
    executor = test.get("actual_executor", "")
    if not isinstance(executor, str):
        raise ValueError("测试项 actual_executor 必须是文本")
    return {"execution_status": status or "not_recorded",
            "actual_date": actual_date, "actual_executor": executor}


def test_fields(kind):
    return (("name", "测试项名称"), ("purpose", "测试目的"), ("procedure", "测试过程"),
            ("expected_result", "期望结果"),
            ("result", "测试结果") if kind == "unit_tests" else ("record", "测试记录"),
            ("result_image", "测试结果图片"),
            ("execution_status", "执行状态"), ("actual_date", "实际执行日期（YYYY-MM-DD）"))


def new_test(kind):
    result = {key: "" for key, _ in test_fields(kind)}
    result["execution_status"] = "not_recorded"
    # Retain the legacy storage slot without exposing an editable per-case person.
    result["actual_executor"] = ""
    return result


def new_item(kind):
    result = ModuleItem({key: "" for key, _ in ITEM_FIELDS[kind]})
    result.update(unit_tests=[], config_tests=[])
    return result


def read_items(value, kind):
    """Validate business data without adding storage keys."""
    if not isinstance(value, list):
        raise ValueError("{} 必须是列表".format(kind))
    result = []
    for source in value:
        if not isinstance(source, dict):
            raise ValueError("{} 条目必须是对象".format(kind))
        item = new_item(kind)
        if isinstance(source, ModuleItem):
            item.module_id = source.module_id
        for key, _ in ITEM_FIELDS[kind]:
            if not isinstance(source.get(key, ""), str):
                raise ValueError("{} 必须是文本".format(key))
            item[key] = source.get(key, "")
        for category, _ in TEST_TYPES:
            tests = source.get(category, [])
            if not isinstance(tests, list):
                raise ValueError("{} 必须是列表".format(category))
            for test in tests:
                if not isinstance(test, dict):
                    raise ValueError("测试项必须是对象")
                data = new_test(category)
                for key in data:
                    if not isinstance(test.get(key, ""), str):
                        raise ValueError("测试项 {} 必须是文本".format(key))
                    data[key] = test.get(key, "")
                data.update(normalize_test_record(test))
                item[category].append(data)
        result.append(item)
    return result


def item_context(project):
    """Create temporary views; parents and tests retain one authoritative source."""
    context = {}
    product = project.fields.get("产品名", "")
    product = "" if product is None else str(product)
    context["chapter_positions"] = {}
    context["items"] = []
    for category, _ in TEST_TYPES:
        context[category.replace("tests", "test_items")] = []
    for kind, label in ITEM_TYPES:
        parents = deepcopy(getattr(project, kind))
        for number, parent in enumerate(parents, 1):
            # Display order and category-local requirement numbers are distinct.
            parent["number"] = len(context["items"]) + number
            parent["kind_label"] = label
            parent["chapters"] = chapter_positions(kind, number, len(project.features),
                                                    parent["name"], product)
            context["chapter_positions"][parent.module_id] = parent["chapters"]
            if kind in ("features", "performances"):
                index = number + (len(project.features) if kind == "performances" else 0)
                parent["detailed_design_label"] = '5.{} {}{}模块'.format(
                    index, product, parent["name"])
            parent["ids"] = {source: '{}{:03d}-{}'.format(prefix, number, parent['code'])
                             for source, prefix in zip(('taskbook', 'srs', 'design'),
                                                       REQUIREMENT_PREFIXES[kind])}
        context[kind] = parents
        context["items"].extend(parents)
        for category, _ in TEST_TYPES:
            rows = [{"parent": parent, "parent_type": label, "test": test}
                    for parent in parents for test in parent[category]]
            view = category.replace("tests", "test_items")
            for number, row in enumerate(rows, len(context[view]) + 1):
                row["number"] = number
                facts = normalize_test_record(row["test"])
                row["execution_label"] = EXECUTION_STATUSES[facts["execution_status"]]
                row["execution_detail"] = {"not_recorded": "未记录", "not_executed": "未执行",
                                           "passed": "已执行", "failed": "已执行"}[facts["execution_status"]]
                dates = project.schedule.get("dates", {})
                date_key = ("单元测试执行结束" if category == "unit_tests"
                            else "系统测试执行结束")
                person_key = "软件负责人" if category == "unit_tests" else "测试人员"
                row["actual_date"] = facts["actual_date"].replace("-", "/") or str(dates.get(date_key, "") or "")
                row["actual_executor"] = str(project.fields.get(person_key, "") or "")
                name = row["test"]["name"].strip()
                prefix = row["parent"]["name"] + " / "
                row["display_name"] = (name[len(prefix):] if name.startswith(prefix) else name) or "测试项{}".format(number)
                row["requirement_links"] = [
                    {"source": source, "id": row["parent"]["ids"][source]}
                    for source in ("taskbook", "srs")]
            context[kind + "_" + view] = rows
            context[view].extend(rows)
    context["test_summaries"] = {}
    for category, title in TEST_TYPES:
        rows = context[category.replace("tests", "test_items")]
        counts = {status: 0 for status in EXECUTION_STATUSES}
        for row in rows:
            counts[normalize_test_record(row["test"])["execution_status"]] += 1
        total = len(rows)
        conclusion = ("没有配置用例" if not total else
                      "本范围内全部用例记录为通过" if counts["passed"] == total else
                      "本范围内存在未通过、未执行或未记录的用例")
        context["test_summaries"][category] = {
            "total": total, "counts": counts,
            "text": "{}：共{}项，通过{}项，未通过{}项，未执行{}项，未记录{}项；{}。".format(
                title, total, counts["passed"], counts["failed"], counts["not_executed"],
                counts["not_recorded"], conclusion)}
    return context


def test_record_issues(context, categories):
    """Return report-only record gaps for the existing generation checklist."""
    issues = []
    for category in categories:
        for row in context.get(category.replace("tests", "test_items"), []):
            facts = normalize_test_record(row["test"])
            missing = []
            if facts["execution_status"] == "not_recorded":
                missing.append("执行状态未记录")
            elif facts["execution_status"] in ("passed", "failed"):
                result_key = "result" if category == "unit_tests" else "record"
                if not row["test"].get(result_key, "").strip():
                    missing.append("测试结果说明缺失" if category == "unit_tests" else "测试记录缺失")
                if not row["actual_date"]:
                    missing.append("实际执行日期缺失")
                if not row["actual_executor"].strip():
                    missing.append("实际执行人缺失")
            if missing:
                location = "{}「{}」/{}第{}项「{}」".format(
                    row["parent_type"], row["parent"]["name"], dict(TEST_TYPES)[category],
                    row["number"], row["test"]["name"] or "未命名")
                issues.append(("P1-记录", location, "、".join(missing),
                               "依据真实执行记录补充；保留显式状态，不从计划或图片推断"))
    return issues


def inspect_items(project):
    """Locate missing fields without comparing document display numbers."""
    issues = []
    for kind, label in ITEM_TYPES:
        for index, parent in enumerate(getattr(project, kind), 1):
            location = '{} {}「{}」'.format(label, index, parent["name"] or "未命名")
            required = ["name", "requirement_description"]
            if kind == "interfaces":
                required += ["direction", "definition"]
            labels = dict(ITEM_FIELDS[kind])
            missing = [labels[key] for key in required if not parent[key].strip()]
            if missing:
                issues.append(location + " 缺少：" + "、".join(missing))
            for category, title in TEST_TYPES:
                for number, test in enumerate(parent[category], 1):
                    missing = [text for key, text in test_fields(category)
                               if key in ("procedure", "expected_result") and not test[key].strip()]
                    if missing:
                        issues.append('{} / {}第 {} 项「{}」 缺少：{}'.format(
                            location, title, number, test["name"] or "未命名", "、".join(missing)))
    return issues
