"""Author example assets from blank documents; never load a private template."""
import json

from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

root = Path(__file__).resolve().parents[1]
assets = root / 'assets'
documents = [
    ('1','软件研制任务书','任务范围','说明项目希望解决的问题，列出交付时可观察到的结果。','features','task_description'),
    ('2','软件质量保证计划','质量检查','在每次交付前核对功能清单，记录负责人和需要复查的问题。','features','requirement_description'),
    ('3','软件配置管理计划','配置范围','为每项功能登记标号，使用项目版本区分本次交付和后续修改。','features','code'),
    ('4','软件开发计划','开发工作','将需求分解为可实现的工作，从进度表核对开始日期和责任人。','features','design_description'),
    ('5','软件需求规格说明','功能需求','逐项描述软件在什么条件下应该产生什么可观察的结果。','features','requirement_description'),
    ('6','软件设计说明','功能实现','说明每个模块怎样接收输入、处理数据并给出输出。','features','design_description'),
    ('7','软件单元测试计划','测试范围','选择需要单独验证的模块，为每项测试明确可观察的通过条件。','features','requirement_description'),
    ('7.1','软件仿真测试计划','仿真范围','列出准备在仿真环境中验证的模块和预期行为。','features','requirement_description'),
    ('8','软件单元测试说明','准备与步骤','按功能标号找到被测模块，根据需求准备输入数据。','features','code'),
    ('8.1','软件仿真测试说明','仿真输入','记录仿真数据的输入时刻，并检查模块是否按设计响应。','interfaces','definition'),
    ('9','软件单元测试报告','验证记录','逐项记录实测结果和复查结论。','features','code'),
    ('9.1','软件仿真测试报告','仿真记录','记录观察到的接口输出，并与预期结果逐项核对。','interfaces','definition'),
    ('10','配置项系统软件测试计划','系统测试范围','根据用户使用过程安排端到端的验证任务。','features','requirement_description'),
    ('11','配置项系统软件测试说明','测试操作','根据需求描述准备操作步骤，并逐个检查用户可见的结果。','features','requirement_description'),
    ('12','配置项系统软件测试报告','执行记录','记录配置项测试结果，标明需要复验的功能。','features','code'),
    ('13','软件版本说明','本版内容','以功能标号列出本次交付覆盖的功能。','features','code'),
    ('14','软件固件保障手册','维护检查','准备可靠的输入与输出样例，在维护后验证基本功能。','interfaces','definition'),
    ('15','软件配置管理报告','交付检查','核对版本、模块标识和归档材料。','features','code'),
    ('16','软件质量保证报告（有几个表要手动填）','检查记录','按需求核对检查范围，记录待跟进的差异。','features','requirement_description'),
    ('17','软件研制总结报告','项目回顾','汇总实现的功能与本次交付取得的结果。','features','design_description'),
]
def make_word(path, title, section, advice, rows, field, platform=''):
    doc=Document()
    doc.core_properties.author='PRISM'
    doc.core_properties.last_modified_by='PRISM'
    doc.core_properties.title=title
    doc.core_properties.comments=''
    normal=doc.styles['Normal']
    normal.font.name='Microsoft YaHei'
    normal.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    normal.font.size=Pt(11)
    doc.sections[0].left_margin=Cm(2.5)
    doc.sections[0].right_margin=Cm(2.5)
    doc.add_paragraph('PRISM  /  模板练习').runs[0].font.color.rgb=RGBColor.from_string('346879')
    doc.add_heading(title.replace('（有几个表要手动填）',''),0)
    table=doc.add_table(rows=0,cols=2)
    table.style='Light Shading Accent 1'
    for label, variable in [('项目','项目名'),('文档版本','文档版本'),('产品','产品名')]:
        a,b=table.add_row().cells
        a.text=label
        b.text='{{ '+variable+' }}'
    doc.add_heading(section,1)
    doc.add_paragraph(advice)
    if platform:doc.add_paragraph('适用器件：'+platform)
    doc.add_heading('条目明细',2)
    doc.add_paragraph('{%p for row in '+rows+' %}')
    doc.add_paragraph('{{ row.name }}　{{ row.'+field+' }}',style='List Bullet')
    doc.add_paragraph('{%p else %}')
    doc.add_paragraph('项目尚未登记此类条目。')
    doc.add_paragraph('{%p endfor %}')
    if '测试' in title:
        cases='config_test_items' if '配置项' in title else 'unit_test_items'
        doc.add_heading('用例与执行记录',2)
        doc.add_paragraph('{%p for case in '+cases+' %}')
        doc.add_paragraph('{{ case.test.name }} / {{ case.execution_label }}',style='List Bullet')
        doc.add_paragraph('操作：{{ case.test.procedure }}')
        doc.add_paragraph('预期：{{ case.test.expected_result }}')
        doc.add_paragraph('{%p else %}')
        doc.add_paragraph('尚未登记测试用例。')
        doc.add_paragraph('{%p endfor %}')
    path.parent.mkdir(parents=True,exist_ok=True)
    doc.save(path)

for number,title,section,advice,rows,field in documents:
    filename=number+'.'+title+('' if number in ('8.1','9.1') else '_模板')+'.docx'
    if number in ('8.1','9.1'):
        filename=number+title+'.docx'
    if number=='16':
        filename='16.软件质量保证报告_模板（有几个表要手动填）.docx'
    make_word(assets/'templates/docx'/filename,title,section,advice,rows,field,'MCU' if number=='14' else '')
    if number=='14':make_word(assets/'templates/variants/fpga'/filename,title,'维护与仿真',advice,rows,field,'FPGA')

model=json.loads((assets/'models/gjb5000-simplified/model.json').read_text(encoding='utf-8'))
folder=assets/'reference/workbooks'
folder.mkdir(parents=True,exist_ok=True)
def workbook():
    book=Workbook()
    book.properties.creator='PRISM'
    book.properties.lastModifiedBy='PRISM'
    return book
def style_sheet(sheet,widths):
    sheet.freeze_panes='B2'
    for cell in sheet[1]:
        cell.font=Font(name='Microsoft YaHei',color='FFFFFF',bold=True)
        cell.fill=PatternFill('solid',fgColor='23566A')
        cell.alignment=Alignment(vertical='center')
    sheet.row_dimensions[1].height=24
    for index,width in enumerate(widths,1):sheet.column_dimensions[get_column_letter(index)].width=width
book=workbook()
sheet=book.active
sheet.title='进度计划制定'
sheet.append(['阶段','工作编号','顺序','工作名称','负责人','执行人','工作类型','计划开始日期','计划完成日期','工时'])
phases={p['id']:p['name'] for p in model['phases']}
mapping={}
for num,work in enumerate(model['works'],1):
    sheet.append([phases[work['phase_id']],work['id'],num,work['name'],'待分配','待分配',work['work_type'],'待排期','待排期',work['default_hours']])
    for cell in sheet[num+1][7:9]:cell.number_format='yyyy-mm-dd'
    mapping[work['id']]=num+1
style_sheet(sheet,[24,12,9,45,18,28,15,18,18,12])
schedule=folder/'进度计划制定模板v1.2(简化模型).xlsx'
book.save(schedule)
schedule.with_suffix('.mapping.json').write_bytes((json.dumps({'format_version':2,'sheet':sheet.title,'work_rows':mapping},ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
book=workbook()
sheet=book.active
sheet.title='项目示例'
sheet.append(['项目名','产品名','软件负责人','测试人员','用户单位','主要功能'])
sheet.append(['温度记录演示','桌面记录器','演示成员甲','演示成员乙','示例使用单位','将一次温度观测保存到本地。'])
style_sheet(sheet,[24,22,20,20,22,48])
book.save(folder/'键值表 (简化模型)v1.2.xlsx')

font='STSong-Light'
pdfmetrics.registerFont(UnicodeCIDFont(font))
c=canvas.Canvas(str(assets/'guides/PRISM使用手册.pdf'),pagesize=A4)
c.setAuthor('PRISM')
c.setTitle('PRISM 演示版使用手册')
c.setFillColorRGB(.09,.24,.30)
c.setFont(font,27)
c.drawString(48,747,'第一次生成项目文档')
c.setFont(font,12)
c.drawString(49,711,'PRISM 演示版使用手册')
steps=[
 ('建立演示项目','点击“新建项目”，输入“温度记录演示”，选择一个空目录。'),
 ('填写数据','在“项目基本数据”填写产品名。在“功能性能接口”添加一条功能，','例如“保存一次温度记录”，填写需求描述和设计说明后保存。'),
 ('安排进度','进入“进度规划”，设置开始日期并保存排期。','如需导出完整进度表，请先在项目数据里填写各岗位的演示人员。'),
 ('选取模板','在“模板与文档”选中“软件需求规格说明”。初次体验可只选一份。'),
 ('生成并核对','在“批量生成”执行预检，按提示补充缺项，再生成文档。','在“生成结果”打开输出目录，核对需求说明中的项目名和功能条目。'),
]
y=650
for i,(title,*lines) in enumerate(steps,1):
    c.setFont(font,16);c.setFillColorRGB(.09,.33,.42)
    c.drawString(49,y,str(i)+'  '+title)
    c.setFont(font,12);c.setFillColorRGB(.13,.17,.20)
    for j,line in enumerate(lines):c.drawString(73,y-27-j*20,line)
    y-=96
c.setFont(font,10);c.drawString(49,60,'模板、数据和说明均用于演示。')
c.save()
