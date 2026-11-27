"""Exercise the complete teaching catalog with fictional project data."""
from datetime import date
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
import pytest

from prism.generation_core import plan_generation, render_generation
from prism.document_review import review_rows_for_document
from prism.items import new_item, new_test
from prism.lifecycle import load_model
from prism.project import Project
from prism.schedule import load_work_calendar
from prism.schema import PEOPLE_FIELDS


ASSETS = Path(__file__).resolve().parents[1] / "assets"


@pytest.mark.parametrize("device", ["MCU", "FPGA"])
def test_complete_demo_catalog(tmp_path, device):
    project = Project.create(tmp_path / "project", "温度记录演示")
    project.fields.update({"产品名": "桌面记录器", "器件类型": device, "版本": "V1.0"})
    project.fields.update({"测试供电方式": "演示电源", "需求供电方式": "演示电源",
                           "供电方式差异结论": "供电方式一致"})
    for index, role in enumerate(PEOPLE_FIELDS, 1):
        project.fields[role] = "Demo Member {}".format(index)
    feature = new_item("features")
    feature.update(name="保存观测值", code="SAVE", task_description="保存读数",
                   requirement_description="每次保存新增一行", design_description="追加到本地记录")
    for kind in ("unit_tests", "config_tests"):
        case = new_test(kind)
        case.update(name="保存单次观测", purpose="核对写入行为", procedure="保存一次读数",
                    expected_result="记录数增加一条")
        feature[kind].append(case)
    project.features.append(feature)
    interface = new_item("interfaces")
    interface.update(name="数据输出", code="DATA", direction="输出", definition="一条观测记录",
                     pin_definition="引脚甲", initial_value="空闲", requirement_description="保存后可读")
    unit = new_test("unit_tests")
    unit.update(name="数据输出 / 读取一条记录", procedure="从输出接口读取一条记录", expected_result="返回保存的读数",
                execution_status="passed", result="已读回一条",
                actual_date="2026-01-12")
    interface["unit_tests"].append(unit)
    project.interfaces.append(interface)
    for group, label, code in (("performances", "刷新周期", "RATE"), ("reliabilities", "异常恢复", "REC")):
        item = new_item(group)
        item.update(name=label, code=code, requirement_description=label + "需求", design_description=label + "设计")
        getattr(project, group).append(item)
    unnamed = new_test("config_tests")
    unnamed.update(procedure="模拟恢复", expected_result="恢复后可以保存")
    project.reliabilities[0]["config_tests"].append(unnamed)
    model = load_model(ASSETS / "models/gjb5000-simplified/model.json")
    calendar = load_work_calendar(ASSETS / "calendars/cn_workdays_2026_2035.json")
    project.schedule = model.calculate(date(2026, 1, 5), {}, calendar)
    project.save()

    plans = plan_generation(project, ASSETS, model, calendar)
    results, published, _ = render_generation(project, plans, calendar)
    output = project.root / "outputs"
    assert published and len(plans) == len(results) == 18
    docs = sorted(output.glob("*.docx"))
    assert len(docs) == 17
    for file in docs:
        parsed = Document(str(file))
        assert parsed.tables[0].cell(0, 1).text == project.name
        cells = [cell.text for table in parsed.tables for row in table.rows for cell in row.cells]
        text = "\n".join(cells + [p.text for p in parsed.paragraphs])
        assert "{{" not in text and "{%" not in text
        assert any(p.text.endswith("引用文档") for p in parsed.paragraphs)
        assert len(parsed.tables) >= 4
    requirement, = [p for p in docs if "软件需求规格说明" in p.name]
    assert any("每次保存新增一行" in p.text for p in Document(str(requirement)).paragraphs)
    data = [[c.text for c in row.cells] for t in Document(str(requirement)).tables for row in t.rows]
    assert ["保存观测值", "RWGN001-SAVE", "XQGN001-SAVE", "SJSM001-SAVE"] in data
    assert ["数据输出", "输出", "一条观测记录", "引脚甲", "空闲"] in data
    assert ["刷新周期", "XQXN001-RATE", "刷新周期需求"] in data
    assert ["异常恢复", "XQKK001-REC", "异常恢复需求"] in data
    report, = [p for p in docs if "配置项系统软件测试报告" in p.name]
    assert any("记录数增加一条" in p.text for p in Document(str(report)).paragraphs)
    report_rows = [[c.text for c in row.cells] for t in Document(str(report)).tables for row in t.rows]
    assert ["保存单次观测", "未记录", "", ""] in report_rows
    assert ["XQGN001-SAVE", "保存观测值", "保存单次观测"] in report_rows
    assert ["XQJK001-DATA", "数据输出", ""] in report_rows
    assert ["测试项2", "异常恢复", "RWKK001-REC", "XQKK001-REC"] in report_rows
    assert ["XQKK001-REC", "异常恢复", "测试项2"] in report_rows
    headings = {p.text.split(" ", 1)[1] for p in Document(str(report)).paragraphs if p.style.name == "Heading 2"}
    assert {"测试环境的影响", "问题报告", "正向追踪", "反向追踪",
            "测试用例1：保存单次观测"} <= headings
    source = ASSETS / "templates/docx/12.配置项系统软件测试报告_模板.docx"
    audit = review_rows_for_document(source, report)
    assert sum(row[1] == "P5-参数缺项" and "必要环境" in row[3] for row in audit) == 2
    assert any(row[1] == "人工项" and "改进建议" in row[2] for row in audit)
    assert not any(row[1] == "P5-占位符" for row in audit)
    test_label = "仿真测试" if device == "FPGA" else "单元测试"
    other_label = "单元测试" if device == "FPGA" else "仿真测试"
    test_report, = [p for p in docs if "软件" + test_label + "报告" in p.name]
    parsed_report = Document(str(test_report))
    unit_rows = [[c.text for c in row.cells] for t in parsed_report.tables for row in t.rows]
    assert ["读取一条记录", "通过", "已读回一条", "2026-01-12"] in unit_rows
    assert ["读取一条记录", "数据输出", "RWJK001-DATA", "XQJK001-DATA"] in unit_rows
    assert ["XQJK001-DATA", "数据输出", "读取一条记录"] in unit_rows
    assert all("已读回一条" not in row for row in report_rows)
    summary, = [p for p in docs if "软件研制总结报告" in p.name]
    for parsed in (parsed_report, Document(str(summary))):
        text = "\n".join(p.text for p in parsed.paragraphs)
        assert test_label + "：共2项，通过1项" in text
        assert other_label not in text
    assert "软件实现和" + test_label in text
    release, = [p for p in docs if "软件版本说明" in p.name]
    contents, = [table for table in Document(str(release)).tables if table.cell(0, 0).text == "条目类别"]
    assert ["功能", "保存观测值", "SAVE", "XQGN001-SAVE", "SJSM001-SAVE"] in [
        [c.text for c in row.cells] for row in contents.rows]
    handbook, = [p for p in docs if "软件固件保障手册" in p.name]
    assert any(device in p.text for p in Document(str(handbook)).paragraphs)
    schedule, = output.glob("*.xlsx")
    book = load_workbook(schedule, read_only=True)
    try:
        sheet = book["进度计划制定"]
        assert list(next(sheet.values)) == ["阶段", "类型", "目录/里程碑名称", "任务名称", "负责人",
                                           "执行人", "工作类型", "计划开始日期", "计划完成日期",
                                           "计划工作量(工时)", "描述", "输出物", "备注"]
        rows = list(sheet.values)[1:]
        assert [row[3] for row in rows] == [work.name for work in model.works]
        assert all(row[1] == "任务" and row[11] for row in rows)
        assert sheet.max_row == len(project.schedule["work_items"]) + 1
        assert sheet["E2"].value == project.fields["软件负责人"]
        assert sheet["H2"].value.date() == date(2026, 1, 5)
        assert sheet["J2"].value > 0
    finally:
        book.close()
