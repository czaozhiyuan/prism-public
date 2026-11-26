"""Loss-minimizing generation for the controlled progress-plan workbook."""

from __future__ import annotations

import html
import json
import math
import os
import re
import tempfile
import zipfile
from xml.etree import ElementTree
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from .schedule import WorkCalendar


TARGET_COLUMNS = ("E", "F", "G", "H", "I", "J")


class ScheduleTemplateError(ValueError):
    """Raised when the controlled progress template is incompatible."""


def _find_schedule_sheet(workbook: Any) -> Any:
    for sheet in workbook.worksheets:
        headers = [sheet.cell(1, column).value for column in range(1, 10)]
        if headers[4:9] == ["负责人", "执行人", "工作类型", "计划开始日期", "计划完成日期"]:
            return sheet
    raise ScheduleTemplateError("模板中未找到受控进度计划工作表")


def calculate_template_values(
    template: Path,
    context: Mapping[str, Any],
    schedule: Mapping[str, Any],
    calendar: WorkCalendar,
) -> Tuple[str, Dict[str, Any]]:
    """Fill model-backed people, type, dates and integer total hours."""
    try:
        from openpyxl import load_workbook
    except ImportError as error:
        raise ScheduleTemplateError("缺少 openpyxl，无法读取进度模板") from error

    workbook = load_workbook(str(template), read_only=False, data_only=False)
    try:
        sheet = _find_schedule_sheet(workbook)
        if 'Sheet1' in workbook.sheetnames:
            raise ScheduleTemplateError('进度模板不能保留旧 Sheet1')
        mapping = json.loads(template.with_suffix(".mapping.json").read_text(encoding="utf-8"))
        rows = mapping.get("work_rows", {})
        work_items = schedule.get("work_items", [])
        works = {work["id"]: work for work in work_items}
        if (mapping.get("format_version") != 2 or mapping.get("sheet") != sheet.title or not rows
                or len(works) != len(work_items) or set(rows) != set(works)
                or any(type(row) is not int or row < 2 for row in rows.values())
                or len(set(rows.values())) != len(rows)):
            raise ScheduleTemplateError("进度模板工作ID与当前排期不一致")
        from .lifecycle import WORK_TYPES
        from .work_people import work_people
        values: Dict[str, Any] = {}
        for identifier, row in rows.items():
            work = works[identifier]
            if sheet["D{}".format(row)].value != work["name"]:
                raise ScheduleTemplateError("工作行名称与排期不一致：" + identifier)
            if (work.get('work_type') not in WORK_TYPES or type(work.get('hours')) is not int
                    or work['hours']<1 or work.get('duration')!=(work['hours']+7)//8):
                raise ScheduleTemplateError('工作类型、单人工时或工作日无效：'+identifier)
            try:owner,executors,count=work_people(work,context)
            except (ValueError,KeyError) as error:raise ScheduleTemplateError(str(error)) from error
            values['E{}'.format(row)]=owner
            values['F{}'.format(row)]='，'.join(executors)
            values['G{}'.format(row)]=work['work_type']
            values['J{}'.format(row)]=work['hours']*count
            values["H{}".format(row)] = date.fromisoformat(work["start"])
            values["I{}".format(row)] = date.fromisoformat(work["finish"])
            if values["H{}".format(row)] > values["I{}".format(row)]:
                raise ScheduleTemplateError("工作开始日期晚于完成日期：" + identifier)
        return sheet.title, values
    finally:
        workbook.close()


def _replace_cell(xml: bytes, address: str, value: Any, epoch: datetime) -> bytes:
    pattern = re.compile(rb'(<c\b[^>]*\br="' + address.encode("ascii") + rb'"[^>]*>).*?</c>', re.DOTALL)
    match = pattern.search(xml)
    if match is None:
        raise ScheduleTemplateError("模板缺少单元格 {}".format(address))
    opening = match.group(1)
    opening = re.sub(rb'\s+t="[^"]*"', b"", opening)
    if isinstance(value, date):
        from openpyxl.utils.datetime import to_excel
        body = "<v>{}</v></c>".format(to_excel(value, epoch)).encode("ascii")
    elif type(value) is int:
        body = '<v>{}</v></c>'.format(value).encode('ascii')
    else:
        opening = opening[:-1] + b' t="inlineStr">'
        text = html.escape(str(value), quote=False)
        preserve = ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() else ""
        body = "<is><t{}>{}</t></is></c>".format(preserve, text).encode("utf-8")
    return xml[: match.start()] + opening + body + xml[match.end() :]


def _worksheet_path(source: zipfile.ZipFile, sheet_name: str) -> str:
    workbook_root = ElementTree.fromstring(source.read("xl/workbook.xml"))
    relationship_id = None
    for element in workbook_root.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheets/{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"):
        if element.attrib.get("name") == sheet_name:
            relationship_id = element.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            break
    if relationship_id is None:
        raise ScheduleTemplateError("无法定位进度计划工作表")
    relationships = ElementTree.fromstring(source.read("xl/_rels/workbook.xml.rels"))
    for element in relationships:
        if element.attrib.get("Id") == relationship_id:
            target = element.attrib["Target"].replace("\\", "/")
            return target.lstrip("/") if target.startswith("/xl/") else "xl/" + target.lstrip("/")
    raise ScheduleTemplateError("进度计划工作表关系缺失")


def export_schedule_template(
    template: Path,
    output: Path,
    context: Mapping[str, Any],
    schedule: Mapping[str, Any],
    calendar: WorkCalendar,
) -> Path:
    """Copy the controlled workbook while changing only E/F/G/H/I/J data cells."""
    try:
        from openpyxl import load_workbook
    except ImportError as error:
        raise ScheduleTemplateError("缺少 openpyxl，无法生成进度计划") from error

    template = Path(template)
    output = Path(output)
    sheet_name, values = calculate_template_values(template, context, schedule, calendar)
    workbook = load_workbook(str(template), read_only=True, data_only=False)
    try:
        epoch = workbook.epoch
    finally:
        workbook.close()
    with zipfile.ZipFile(str(template), "r") as source:
        sheet_path = _worksheet_path(source, sheet_name)
        worksheet_xml = source.read(sheet_path)
        for address, value in values.items():
            worksheet_xml = _replace_cell(worksheet_xml, address, value, epoch)
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='.prism-schedule-', suffix='.xlsx', dir=str(output.parent))
        os.close(descriptor)
        candidate = Path(name)
        try:
            with zipfile.ZipFile(str(candidate), "w") as destination:
                for info in source.infolist():
                    if info.filename == "xl/calcChain.xml":
                        continue
                    payload = worksheet_xml if info.filename == sheet_path else source.read(info.filename)
                    if info.filename == "[Content_Types].xml":
                        payload = _remove_package_entry(payload, b"Override", "PartName", "/xl/calcChain.xml")
                    elif info.filename == "xl/_rels/workbook.xml.rels":
                        payload = _remove_package_entry(payload, b"Relationship", "Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain")
                    destination.writestr(info, payload)
            validate_schedule_workbook(candidate, template, context, schedule, calendar)
            os.replace(str(candidate), str(output))
        finally:
            if candidate.exists():
                candidate.unlink()
    return output


def _quality_error(work_id, row, column, value, reason):
    raise ScheduleTemplateError('进度表校验失败：工作ID={}，行={}，列={}，异常值={!r}；{}'.format(
        work_id, row, column, value, reason))


def validate_schedule_workbook(output, template, context, schedule, calendar):
    """Read the actual XLSX against the snapshot checked by shared preflight."""
    from openpyxl import load_workbook
    from .lifecycle import WORK_TYPES
    from .work_people import parse_people, work_people

    mapping = json.loads(Path(template).with_suffix('.mapping.json').read_text(encoding='utf-8'))
    rows = mapping.get('work_rows', {})
    works = schedule.get('work_items', [])
    if (mapping.get('format_version') != 2 or not works or not isinstance(rows, dict)
            or len(rows) != len(works) or set(rows) != {w['id'] for w in works}
            or any(type(row) is not int for row in rows.values())):
        _quality_error('-', 0, '映射', rows, '工作ID、任务数量或行顺序与排期不一致')
    for row, work in enumerate(works, 2):
        if rows[work['id']] != row:
            _quality_error(work['id'], row, '映射', rows[work['id']], '工作ID对应行顺序与排期不一致')
    for key, value in schedule.get('dates', {}).items():
        if context.get(key) != value:
            _quality_error('-', 0, key, context.get(key), '文档日期上下文与排期快照不一致')
    try:
        # Reject non-finite literals before openpyxl's numeric parser loses the cell location.
        with zipfile.ZipFile(str(output)) as archive:
            try:
                sheet_path = _worksheet_path(archive, mapping['sheet'])
            except ScheduleTemplateError as error:
                _quality_error('-', 0, '工作表', mapping['sheet'], str(error))
            sheet_xml = ElementTree.fromstring(archive.read(sheet_path))
            row_ids = {row: identifier for identifier, row in rows.items()}
            namespace = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
            for cell in sheet_xml.iter(namespace+'c'):
                address = cell.get('r', '')
                value = cell.find(namespace+'v')
                if re.fullmatch(r'J[0-9]+', address) and cell.get('t', 'n') == 'n' and value is not None:
                    try:
                        finite = math.isfinite(float(value.text))
                    except (TypeError, ValueError):
                        finite = False
                    if not finite:
                        row = int(address[1:])
                        _quality_error(row_ids.get(row, '-'), row, 'J', value.text, '总工时必须为有限的正整数数值常量')
        workbook = load_workbook(str(output), data_only=False)
    except ScheduleTemplateError:
        raise
    except Exception as error:
        _quality_error('-', 0, '文件', Path(output).name, '无法读取最终XLSX：'+str(error))
    try:
        if workbook.sheetnames != [mapping['sheet']]:
            _quality_error('-', 0, '工作表', workbook.sheetnames, '工作表必须与映射一致且不能含Sheet1')
        sheet = workbook[mapping['sheet']]
        if sheet.max_row != len(works)+1:
            _quality_error('-', sheet.max_row, '行数', sheet.max_row-1, '实际任务行数量与排期不一致')
        phases = {p['id']: p for p in schedule.get('phases', [])}
        previous = None
        for work in works:
            identifier, row = work['id'], rows[work['id']]
            def fail(column, reason):
                _quality_error(identifier, row, column, sheet['{}{}'.format(column,row)].value, reason)
            phase = phases.get(work['phase_id'])
            if phase is None or sheet['A'+str(row)].value != phase['name']:
                fail('A', '任务所属阶段与排期不一致')
            if sheet['D'+str(row)].value != work['name']:
                fail('D', '任务名称、工作ID或顺序与映射不一致')
            try:
                owner = parse_people(sheet['E'+str(row)].value)
            except ValueError as error:
                fail('E', str(error))
            if len(owner) != 1:
                fail('E', '负责人必须恰好一名自然人')
            try:
                executors = parse_people(sheet['F'+str(row)].value)
            except ValueError as error:
                fail('F', str(error))
            identities = [name for name,_ in executors]
            if len(identities) != len(set(identities)):
                fail('F', '执行人存在重复自然人')
            if owner[0][0] not in identities:
                fail('F', '执行人未包含负责人')
            expected_owner, expected_executors, count = work_people(work, context)
            if sheet['E'+str(row)].value != expected_owner:
                fail('E', '负责人不符合项目岗位配置')
            if sheet['F'+str(row)].value != '，'.join(expected_executors):
                fail('F', '执行人或首次出现顺序不符合项目岗位配置')
            if len(identities) != count:
                fail('F', '实际执行人数与工时计算人数不一致')
            kind = sheet['G'+str(row)]
            if kind.value not in WORK_TYPES or kind.value != work['work_type']:
                fail('G', '工作类型必须为模型指定的单一枚举值')
            hours = sheet['J'+str(row)]
            if hours.data_type != 'n' or type(hours.value) is not int or hours.value < 1:
                fail('J', '总工时必须为正整数数值常量，不能是公式、文本或空值')
            if hours.value != work['hours']*count:
                fail('J', '总工时不等于单人计划工时H乘去重人数N')
            dates = []
            for column, key in (('H','start'), ('I','finish')):
                cell = sheet[column+str(row)]
                if (cell.data_type != 'd' or not isinstance(cell.value, datetime)
                        or cell.value.time() != datetime.min.time()):
                    fail(column, '必须为真实Excel日期，不能是公式、文本、无效日期或带时间的值')
                actual = cell.value.date()
                if not calendar.is_workday(actual) or actual.isoformat() != work[key]:
                    fail(column, '日期不是工作日或与排期快照不一致')
                dates.append(actual)
            days = (work['hours']+7)//8
            expected_start = calendar.after(previous) if previous else calendar.on_or_after(date.fromisoformat(schedule['project_start']))
            if dates[0] != expected_start:
                fail('H', '工作开始日期未按统一工作日历依次衔接')
            if dates[0] > dates[1] or calendar.finish(dates[0], days) != dates[1]:
                fail('I', '起止日期或工作日数量不符合ceil(H/8)')
            previous = dates[1]
        for phase in phases.values():
            members = [w for w in works if w['phase_id']==phase['id']]
            if (not members or phase['start'] != members[0]['start'] or phase['finish'] != members[-1]['finish']
                    or phase['duration'] != sum((w['hours']+7)//8 for w in members)):
                _quality_error(members[0]['id'] if members else '-', rows[members[0]['id']] if members else 0,
                               'H/I', phase, '阶段起止日期或工作日合计与任务不一致')
        formulas = []
        for cells in sheet.iter_rows():
            for cell in cells:
                if cell.data_type in ('f','e') and '#REF!' in str(cell.value):
                    _quality_error(row_ids.get(cell.row, '-'), cell.row, cell.column_letter, cell.value, '存在失效引用')
                if cell.data_type == 'f':
                    formulas.append((cell.row, cell.column_letter, str(cell.value)))
        with zipfile.ZipFile(str(output)) as archive:
            root = ElementTree.fromstring(archive.read('xl/workbook.xml'))
            formulas.extend((0, '名称', node.text or '') for node in root.findall(
                '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}definedNames/{http://schemas.openxmlformats.org/spreadsheetml/2006/main}definedName'))
            for row, column, formula in formulas:
                references = re.findall(r"(?:'((?:[^']|'')+)'|([\w.]+))!", formula)
                if ('#REF!' in formula or re.search(r'\[[^]]+\][^!]*!', formula)
                        or any((a.replace("''", "'") or b) not in workbook.sheetnames for a,b in references)):
                    _quality_error(row_ids.get(row, '-'), row, column, formula, '工作表或名称存在失效引用')
    finally:
        workbook.close()


def _remove_package_entry(xml: bytes, tag: bytes, attribute: str, value: str) -> bytes:
    """Remove obsolete calculation metadata without reserializing other XML."""
    pattern = re.compile(rb"<" + tag + rb"\b[^>]*/>")
    return pattern.sub(lambda match: b"" if ElementTree.fromstring(match.group(0)).get(attribute) == value else match.group(0), xml)
