"""Values-only review workbook emitted by the existing openpyxl pipeline."""

from math import ceil
from unicodedata import east_asian_width

CATEGORIES = {
    'P0-01': '引用错误', 'P1-07': '引用错误', 'P0-02': '图片路径失效',
    'P5-图片未配置': '固定图片未配置', 'P1-记录': '执行记录缺项',
    'P5-状态矛盾': '状态与实际记录矛盾', 'P5-占位符': '未解析占位符',
    'P5-旧残留': '旧模板残留', 'P5-标点': '重复中文标点',
    'P5-参数缺项': '必要参数缺项', 'P5-目录降级': '目录降级',
    'P1-05/P1-06': 'Word 域未更新',
}


def unique_review_rows(rows):
    """Ignore repeated suggestions for the same located diagnostic."""
    seen = set()
    for row in rows:
        row = tuple(str(value).strip() for value in row)
        key = row[:4]
        if key not in seen:
            seen.add(key)
            yield row


def write_review_checklist(output, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '待补充清单'
    sheet.append(['序号', '文档', '审计项', '分类', '位置', '待补充内容', '处理要求', '状态'])
    for index, row in enumerate(unique_review_rows(rows), 1):
        document, code, location, detail, suggestion = row
        sheet.append([index, document, code, CATEGORIES.get(code, '人工审核'),
                      location, detail, suggestion, '待审核'])
    for row in sheet:
        for cell in row:
            # User text is never an Excel formula.
            if isinstance(cell.value, str): cell.data_type = 's'
            cell.font = Font(name='Microsoft YaHei', size=11)
            cell.alignment = Alignment(vertical='top', wrap_text=True)
    for cell in sheet[1]:
        cell.fill = PatternFill('solid', fgColor='1F4E78')
        cell.font = Font(name='Microsoft YaHei', color='FFFFFF', bold=True)
    for column, width in zip('ABCDEFGH', (8, 48, 18, 24, 45, 65, 65, 12)):
        sheet.column_dimensions[column].width = width
    for row in sheet:
        lines = 1
        for cell in row:
            width = sheet.column_dimensions[cell.column_letter].width - 2
            for line in str(cell.value or '').splitlines():
                units = sum(2 if east_asian_width(char) in ('W', 'F') else 1 for char in line)
                lines = max(lines, ceil(units / width))
        sheet.row_dimensions[row[0].row].height = min(409, max(18, lines * 17))
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    workbook.close()
