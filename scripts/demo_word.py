"""Compose the public Word catalog from blank documents and project context fields."""

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from demo_chapters import CHAPTERS, FORMS, REFERENCES
from demo_topics import TOPICS


def value(expr):
    return '{{ ' + expr + ' }}'


def grid(doc, headers, rows):
    if doc.element.body[-2].tag == qn('w:tbl'):
        spacer = doc.add_paragraph()
        spacer.paragraph_format.line_spacing = Pt(4)
        spacer.paragraph_format.space_after = Pt(0)
    table = doc.add_table(rows=0, cols=len(headers))
    table.style = 'Light Shading Accent 1'
    for cells in (headers, *rows):
        row = table.add_row()
        for cell, text in zip(row.cells, cells):
            cell.text = text
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
    for cell in table.rows[0].cells:
        cell.paragraphs[0].paragraph_format.keep_with_next = True
    return table


def records(doc, headers, collection, fields, condition=''):
    loop = '{%tr for r in ' + collection + condition + ' %}'
    grid(doc, headers, [(loop,), tuple(value(field) for field in fields),
                        ('{%tr else %}',), ('尚无条目',), ('{%tr endfor %}',)])


def field_rows(doc, rows):
    grid(doc, ('项目', '内容'), [(name, value(expr)) for name, expr in rows])


def review_fields(doc, labels):
    grid(doc, ('核对事项', '实际记录'), [(label, '待人工填写') for label in labels])


def review_value(expr):
    return value(expr + '|default("待人工填写", true)')


def reference_table(doc, codes):
    condition = ' if r.id in ' + repr(codes)
    records(doc, ('引用文件', '文件标识', '版本'), 'initial_configuration_items',
            ('r.name', 'r.identifier', 'r.last_version'), condition)


def picture(doc, title, slot):
    doc.add_paragraph('图1 ' + title, style='Caption')
    doc.add_paragraph(value(slot))


KINDS = (('功能', 'features'), ('性能', 'performances'), ('接口', 'interfaces'), ('可靠性', 'reliabilities'))
PHASES = (('项目启动', '启动开始', '启动结束'), ('需求分析', '策划开始', '策划结束'),
          ('设计与实现', '设计开始', '设计结束'), ('系统测试', '测试开始', '测试结束'),
          ('交付验收', '验收开始', '验收结束'))


def item_details(doc, group, label, source, field):
    doc.add_heading(label, 2)
    records(doc, ('名称', '标识', '说明'), group, ('r.name', 'r.ids.' + source, 'r.' + field))


def interface_details(doc):
    records(doc, ('接口', '方向', '定义', '管脚', '初始值'), 'interfaces',
            ('r.name', 'r.direction', 'r.definition', 'r.pin_definition', 'r.initial_value'))


def chapter_body(doc, block, number):
    system = number in ('10', '11', '12')
    cases = 'config_test_items' if system else 'unit_test_items'
    summary = 'test_summaries.' + ('config_tests' if system else 'unit_tests')
    if block in FORMS:
        title, headers = FORMS[block]
        doc.add_paragraph(title + '由经办人依据实际记录填写。')
        columns = headers.split('|')
        if number in ('15', '16'):
            grid(doc, columns, [('待人工填写',) * len(columns)])
        else:
            grid(doc, columns, [('待人工填写',)])
    elif block == 'boundary':
        field_rows(doc, [('主要功能', '主要功能'), ('输入', '输入'), ('输出', '输出')])
    elif block == 'boundary_picture':
        picture(doc, '系统边界', 'system_boundary')
    elif block == 'platform':
        field_rows(doc, [('器件', '器件型号'), ('器件类型', '器件类型'), ('操作系统', '操作系统'),
                         ('资源', '外设及内部资源')])
    elif block in ('tools', 'design_limits'):
        field_rows(doc, [('开发环境', 'IDE'), ('语言', '语言'), ('构建工具', 'compile_build_tool'),
                         ('仿真工具', 'simulation_tool'), ('编程工具', 'program_debug_tool')])
    elif block == 'roles':
        field_rows(doc, [(role, role) for role in ('软件负责人', '设计人员', '测试人员', '项目配管', '项目软件质量师')])
    elif block == 'task_data':
        for (label, group), field in zip(KINDS, ('task_description', 'description', 'definition', 'task_description')):
            item_details(doc, group, ('接口说明' if group == 'interfaces' else label), 'taskbook', field)
    elif block == 'requirements':
        doc.add_heading('要求的状态和方式', 2)
        field_rows(doc, [('运行平台', '操作系统'), ('软件输入', '输入'), ('软件输出', '输出')])
        for label, group in KINDS:
            item_details(doc, group, {'features': 'CSCI能力需求', 'interfaces': 'CSCI外部接口需求'}.get(group, label + '需求'), 'srs', 'requirement_description')
            if group == 'features':
                doc.add_paragraph('{%p for r in features %}')
                doc.add_paragraph('{{ r.name }}：{{ r.requirement_description }}')
                doc.add_paragraph('{%p endfor %}')
            if group == 'interfaces':
                interface_details(doc)
                picture(doc, '系统边界', 'system_boundary')
        doc.add_heading('软件开发环境需求', 2)
        field_rows(doc, [('开发工具', 'IDE'), ('编译与构建', 'compile_build_tool'), ('工具链语言', '语言')])
        doc.add_heading('计算机资源需求', 2)
        field_rows(doc, [('器件型号', '器件型号'), ('可用资源', '外设及内部资源')])
        for heading, field in [('安全性需求', '安全等级'), ('保密性需求', '密级'),
                               ('验收、交付和包装需求', 'executable_filename')]:
            doc.add_heading(heading, 2)
            doc.add_paragraph(review_value(field))
    elif block == 'qualification':
        for label, group in [('单元或仿真验证', 'unit_test_items'), ('配置项验证', 'config_test_items')]:
            doc.add_heading(label, 2)
            records(doc, ('需求标识', '验证项', '判定依据'), group,
                    ('r.parent.ids.srs', 'r.display_name', 'r.test.expected_result'))
    elif block == 'trace':
        records(doc, ('条目', '任务书标识', '需求标识', '设计标识'), 'items',
                ('r.name', 'r.ids.taskbook', 'r.ids.srs', 'r.ids.design'))
    elif block == 'decisions':
        field_rows(doc, [('配置项输入', 'CSCI输入'), ('配置项输出', 'CSCI输出'), ('行为设计', 'CSCI行为设计决策')])
    elif block == 'architecture':
        doc.add_heading('CSCI部件', 2)
        picture(doc, '软件架构', 'software_architecture')
        records(doc, ('模块', '类别', '设计标识'), 'items', ('r.name', 'r.kind_label', 'r.ids.design'))
        doc.add_heading('接口设计', 2)
        interface_details(doc)
        records(doc, ('接口', '设计说明'), 'interfaces', ('r.name', 'r.design_description'))
        picture(doc, '接口关系', 'interface_relationship')
    elif block == 'detailed':
        for label, group in KINDS[:2]:
            doc.add_paragraph('{%p for r in ' + group + ' %}')
            doc.add_heading('{{ 产品名 }}{{ r.name }}模块（{{ r.ids.design }}）', 2)
            doc.add_paragraph(review_value('r.design_description'))
            doc.add_paragraph('{%p endfor %}')
        picture(doc, '处理流程', 'main_flow_algorithm')
    elif block == 'reliable_design':
        item_details(doc, 'reliabilities', '可靠性措施', 'design', 'design_description')
    elif block in ('milestones', 'lifecycle'):
        grid(doc, ('阶段', '计划开始', '计划完成'), [(name, value(start), value(end)) for name, start, end in PHASES])
    elif block == 'milestone_picture':
        picture(doc, '进度里程碑', 'schedule_milestones')
    elif block == 'development':
        records(doc, ('开发对象', '类别', '实现说明'), 'items', ('r.name', 'r.kind_label', 'r.design_description'))
    elif block == 'audits':
        records(doc, ('审核活动', '计划工作编号', '计划完成'), 'process_audit_rows', ('r.activity', 'r.work_id', 'r.finish'))
    elif block == 'configuration':
        records(doc, ('配置项', '标识', '版本', '计划发布日期'), 'initial_configuration_items',
                ('r.name', 'r.identifier', 'r.last_version', 'r.release_date'))
    elif block == 'baselines':
        records(doc, ('基线标识', '版本', '计划建立日期', '配置项数'), 'initial_baselines',
                ('r.identifier', 'r.version', 'r.release_date', 'r["items"]|length'))
    elif block == 'changes':
        doc.add_paragraph(value('initial_configuration_summary'))
        grid(doc, ('变更编号', '影响版本', '说明', '复核凭据'), [('后续变更时填写',)])
        if number == '15':
            review_fields(doc, ('初始版本确认', '最后版本确认', '变更次数与批准凭据'))
    elif block == 'handover':
        field_rows(doc, [('产品', '产品名'), ('软件版本', '版本'), ('交付文件', 'executable_filename'),
                         ('产品基线', '产品基线')])
    elif block == 'release':
        field_rows(doc, [('软件标识', 'software_id'), ('软件版本', '版本'), ('发布文件', 'executable_filename')])
    elif block == 'install':
        doc.add_paragraph('核对文件标识与目标器件，按本项目确认的操作规程加载文件，并记录使用的工具和版本。')
        field_rows(doc, [('文件', 'executable_filename'), ('器件', '器件型号'), ('工具', 'program_debug_tool')])
        if number == '14':
            doc.add_heading('编程接口与初始状态', 2)
            interface_details(doc)
    elif block == 'metrics':
        field_rows(doc, [('代码规模（千行）', 'delivery_metrics.scale_kloc'), ('代码行数', 'delivery_metrics.code_lines'),
                         ('计划人时', 'delivery_metrics.total_hours'), ('计划人日', 'delivery_metrics.person_days'),
                         ('每计划人日代码行数', 'delivery_metrics.productivity')])
    elif block == 'basis':
        records(doc, ('来源', '需求标识', '验证要求'), 'items', ('r.name', 'r.ids.srs', 'r.requirement_description'))
    elif block == 'environment':
        doc.add_heading('测试环境的影响', 2)
        grid(doc, ('分析项', '测试环境', '需求环境', '结论'), [
            ('{%tr for r in test_setup_rows %}',),
            (value('r.name'), review_value('r.test'), review_value('r.requirement'), review_value('r.conclusion')),
            ('{%tr endfor %}',),
        ])
        doc.add_paragraph('环境差异影响及测试有效性评估　' + review_value('测试环境影响及有效性评估'))
        doc.add_heading('测试环境', 2)
        picture(doc, '测试环境', 'test_environment')
    elif block == 'equipment':
        doc.add_heading('软件项', 2)
        tool = 'simulation_tool' if '.' in number else 'program_debug_tool'
        grid(doc, ('序号', '类型', '工具及版本', '符合性', '检查人', '时间'), [
            (str(index), label, value(field), '', '', '')
            for index, (label, field) in enumerate([('开发环境', 'IDE'), ('运行或仿真工具', tool)], 1)
        ])
        doc.add_heading('硬件和固件项准备', 2)
        grid(doc, ('序号', '仪器名称', '检查内容', '检查结果', '符合性', '检查人', '时间'), [
            ('{%tr for r in ' + ('system' if system else 'unit') + '_hardware_checks %}',),
            (value('r.number'), value('r.name'), '计量或校准状态', '', '', '', ''),
            ('{%tr endfor %}',),
        ])
        doc.add_paragraph('目标器件 {{ 器件型号 }}，调试器 {{ 调试器 }}')
    elif block == 'case_index':
        doc.add_heading('计划执行的测试', 2)
        records(doc, ('序号', '测试项', '对象', '目的'), cases, ('r.number', 'r.display_name', 'r.parent.name', 'r.test.purpose'))
    elif block == 'test_dates':
        prefix = '系统测试' if system else '单元测试'
        grid(doc, ('工作', '计划开始', '计划完成'), [
            (step, value(prefix + step + '开始'), value(prefix + step + '结束')) for step in ('准备', '执行', '回归', '总结')])
    elif block == 'exit_criteria':
        doc.add_paragraph('核对已执行、未执行和待复验用例，由测试负责人记录结束或暂停的依据。')
        grid(doc, ('检查范围', '遗留事项', '决定及依据', '确认人'), [('待人工填写',)])
    elif block == 'case_trace':
        doc.add_heading('正向追踪', 2)
        records(doc, ('测试项', '对象', '任务标识', '需求标识'), cases,
                ('r.display_name', 'r.parent.name', 'r.parent.ids.taskbook', 'r.parent.ids.srs'))
        doc.add_heading('反向追踪', 2)
        children = 'config_tests' if system else 'unit_tests'
        records(doc, ('需求标识', '对象', '已配置的测试项'), 'items',
                ('r.ids.srs', 'r.name', 'r.' + children + '|map(attribute="name")|join("、")'))
    elif block in ('procedures', 'results'):
        if block == 'procedures':
            records(doc, ('测试项', '目的', '操作', '预期'), cases,
                    ('r.display_name', 'r.test.purpose', 'r.test.procedure', 'r.test.expected_result'))
        else:
            doc.add_heading('测试情况', 2)
            actual = 'r.test.record' if system else 'r.test.result'
            records(doc, ('测试项', '执行状态', '实测记录', '记录日期'), cases,
                    ('r.display_name', 'r.execution_label', actual, 'r.test.actual_date'))
        doc.add_paragraph('{%p for r in ' + cases + ' %}')
        doc.add_paragraph('{{ r.parent.name }} / {{ r.display_name }}　预期：{{ r.test.expected_result }}')
        if block == 'results':
            doc.add_paragraph('{%p if r.result_picture %}')
            doc.add_paragraph('{{ r.result_picture }}')
            doc.add_paragraph('{%p endif %}')
        doc.add_paragraph('{%p endfor %}')
    elif block == 'case_record':
        doc.add_paragraph('{%p for r in ' + cases + ' %}')
        doc.add_heading('测试用例{{ r.number }}：{{ r.display_name }}', 2)
        field_rows(doc, [('操作', 'r.test.procedure'), ('预期', 'r.test.expected_result'),
                         ('执行状态', 'r.execution_label'),
                         ('实测记录', 'r.test.record' if system else 'r.test.result')])
        doc.add_paragraph('{%p else %}')
        doc.add_paragraph('尚无用例')
        doc.add_paragraph('{%p endfor %}')
    elif block == 'summary':
        doc.add_heading('对被测试软件的总体评价', 2)
        field_rows(doc, [(label, summary + '.counts.' + key) for label, key in
                         [('通过', 'passed'), ('未通过', 'failed'), ('未执行', 'not_executed'), ('未记录', 'not_recorded')]])
        doc.add_heading('待跟进的测试项', 2)
        records(doc, ('测试项', '状态', '关联需求'), cases, ('r.display_name', 'r.execution_label', 'r.parent.ids.srs'),
                ' if r.test.execution_status != "passed"')
    elif block == 'conclusion':
        doc.add_paragraph(value(summary + '.text'))
    elif block == 'problems':
        records(doc, ('测试项', '状态', '观察结果'), cases, ('r.display_name', 'r.execution_label', 'r.test.result'),
                ' if r.test.execution_status == "failed"')
    elif block == 'test_overview':
        doc.add_paragraph(value('test_summaries.unit_tests.text'))
        doc.add_paragraph(value('test_summaries.config_tests.text'))
    elif block == 'terms':
        doc.add_paragraph('文件标识用于定位项目文档，同一软件版本下的文档可以分别修订。')
    else:
        raise ValueError('Unknown chapter content: ' + block)


def make_word(path, number, title, purpose, platform=''):
    doc = Document()
    title = title.replace('（有几个表要手动填）', '')
    props = doc.core_properties
    props.author = props.last_modified_by = 'PRISM'
    props.title, props.comments = title, ''
    normal = doc.styles['Normal']
    normal.font.name = 'Microsoft YaHei'
    normal.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
    normal.font.size = Pt(10.5)
    section = doc.sections[0]
    section.page_height, section.page_width = Cm(29.7), Cm(21)
    section.left_margin = section.right_margin = Cm(2.5)
    section.header.paragraphs[0].text = title
    doc.add_heading(title, 0)
    meta = doc.add_table(rows=0, cols=2)
    meta.style = 'Light Shading Accent 1'
    for label in ('项目名', '产品名', '文档版本', '软件负责人'):
        a, b = meta.add_row().cells
        a.text, b.text = label, value(label)
    if platform:
        doc.add_paragraph('适用器件：' + platform)
    doc.add_page_break()
    doc.add_paragraph('目录', style='Title')
    paragraph = doc.add_paragraph()
    for kind in ('begin', 'instruction', 'end'):
        if kind == 'instruction':
            node = OxmlElement('w:instrText')
            node.text = r' TOC \o "1-2" \h \z '
        else:
            node = OxmlElement('w:fldChar')
            node.set(qn('w:fldCharType'), kind)
        paragraph.add_run()._r.append(node)
    page = OxmlElement('w:fldSimple')
    page.set(qn('w:instr'), ' PAGE ')
    section.footer.paragraphs[0]._p.append(page)
    doc.add_heading('范围', 1).paragraph_format.page_break_before = True
    doc.add_heading('标识', 2)
    field_rows(doc, [('项目', '项目名'), ('软件标识', 'software_id'), ('软件版本', '版本'),
                     ('器件型号', '器件型号|default("待人工填写", true)')])
    doc.add_heading('系统概述', 2)
    doc.add_paragraph('本项目的软件产品为“{{ 产品名 }}”。主要功能：{{ 主要功能|default("待人工填写", true) }}')
    doc.add_heading('文档概述', 2)
    doc.add_paragraph(purpose)
    for topic, description in TOPICS.get((number, '范围'), ()):
        doc.add_heading(topic, 2)
        doc.add_paragraph(description)
    if number in ('7', '7.1', '10'):
        doc.add_heading('与其他计划之间的关系', 2)
        doc.add_paragraph('按开发、质量和配置计划核对测试基线、评审时间和归档安排。')
    doc.add_heading('引用文档', 1)
    reference_table(doc, REFERENCES[number])
    for heading, blocks in CHAPTERS[number]:
        doc.add_heading(heading, 1)
        for block in blocks.split():
            chapter_body(doc, block, number)
        if number in ('9', '9.1', '12') and heading == '测试结果概述':
            doc.add_heading('改进建议', 2)
            review_fields(doc, ('建议和对应问题记录',))
        if number == '12' and heading == '详细测试结果':
            for topic, labels in [('问题报告', ('问题编号及关联用例',)),
                                  ('问题统计', ('统计范围与数量',)),
                                  ('各问题描述', ('复现记录与影响', '处理与复验凭据'))]:
                doc.add_heading(topic, 2)
                review_fields(doc, labels)
        if number in ('9', '9.1') and heading == '详细测试结果':
            doc.add_heading('回归测试', 2)
            review_fields(doc, ('回归范围', '记录编号及结论'))
        for topic, description in TOPICS.get((number, heading), ()):
            doc.add_heading(topic, 2)
            doc.add_paragraph(description)
        if number == '14' and 'verify' in blocks.split():
            if platform == 'FPGA':
                doc.add_heading('仿真与加载核验', 2)
                field_rows(doc, [('仿真环境', 'simulation_tool'), ('综合工具', 'compile_build_tool')])
                doc.add_paragraph('记录加载文件的版本，核对仿真观察点与项目登记的接口。')
            else:
                doc.add_heading('启动与调试核验', 2)
                field_rows(doc, [('调试器', '调试器'), ('编程工具', 'program_debug_tool')])
                doc.add_paragraph('记录启动后的初始状态，并用项目登记的输入和输出核对运行情况。')
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
