"""Exercise the complete teaching catalog with fictional project data."""
from datetime import date
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
import pytest

from prism.generation_core import plan_generation, render_generation
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
        assert "{{" not in "\n".join(p.text for p in parsed.paragraphs)
    requirement, = [p for p in docs if "软件需求规格说明" in p.name]
    assert any("每次保存新增一行" in p.text for p in Document(str(requirement)).paragraphs)
    report, = [p for p in docs if "配置项系统软件测试报告" in p.name]
    assert any("记录数增加一条" in p.text for p in Document(str(report)).paragraphs)
    handbook, = [p for p in docs if "软件固件保障手册" in p.name]
    assert any(device in p.text for p in Document(str(handbook)).paragraphs)
    schedule, = output.glob("*.xlsx")
    book = load_workbook(schedule, read_only=True)
    try:
        sheet = book["进度计划制定"]
        assert sheet.max_row == len(project.schedule["work_items"]) + 1
        assert sheet["E2"].value == project.fields["软件负责人"]
        assert sheet["H2"].value.date() == date(2026, 1, 5)
        assert sheet["J2"].value > 0
    finally:
        book.close()
