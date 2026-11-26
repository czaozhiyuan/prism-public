"""Build the public schedule from lifecycle work IDs and independently authored outputs."""

HEADERS = ('阶段', '类型', '目录/里程碑名称', '任务名称', '负责人', '执行人', '工作类型',
           '计划开始日期', '计划完成日期', '计划工作量(工时)', '描述', '输出物', '备注')

# Group equivalent deliverables by stable work ID, including repeated phase activities.
OUTPUT_GROUPS = {
    '团队分工和启动会议记录': 'L01',
    '阶段工作计划': 'L02 L23 R13 D20 T14',
    '配置库目录和访问设置': 'L03',
    '生命周期活动清单': 'L04',
    '功能分解表': 'L05',
    '规模与工作量估算表': 'L06',
    '需求讨论记录': 'L07',
    '软件研制任务书': 'L08',
    '软件开发计划': 'L09',
    '软件质量保证计划': 'L10',
    '软件配置管理计划': 'L11',
    '工作产品检查记录与问题清单': 'L12 R03 D03 D06B D08A D14A T02A T07A A06',
    '测量指标定义表': 'L13',
    '评审意见与处理记录': 'L14 L15 R04 D04 D09 D14 T03 T08 A07',
    '归档清单与配置状态记录': 'L16 R05 D05 D15 T09 A08',
    '基线清单与审核记录': 'L17 R06 A09',
    '过程审核记录与问题清单': 'L18 R10 D17 T11 A12',
    '问题处理与复核记录': 'L19 R11 D18 T12 A13',
    '配置审核报告': 'L20 R08 D15A T09A A10',
    '质量保证审核报告': 'L21 R09 D16 T10 A11',
    '阶段报告与阶段评审记录': 'L22 D19 T13',
    '需求分析记录': 'R01',
    '软件需求规格说明': 'R02',
    '配置库维护记录': 'R07',
    '里程碑报告与评审记录': 'R12',
    '软件架构与接口设计': 'D01',
    '软件设计说明': 'D02',
    '软件源代码': 'D06',
    '代码审查记录': 'D06A',
    '单元或仿真测试计划': 'D07',
    '单元或仿真测试说明': 'D08',
    '单元或仿真测试环境记录': 'D10',
    '单元或仿真执行记录与问题单': 'D11',
    '单元或仿真回归记录': 'D12',
    '单元或仿真测试报告': 'D13',
    '配置项系统软件测试计划': 'T01',
    '配置项系统软件测试说明': 'T02',
    '系统测试环境记录': 'T04',
    '系统测试执行记录与问题单': 'T05',
    '配置项回归记录': 'T06',
    '配置项系统软件测试报告': 'T07',
    '软件版本说明': 'A01',
    '软件固件保障手册': 'A02',
    '软件配置管理报告': 'A03',
    '软件质量保证报告': 'A04',
    '软件研制总结报告': 'A05',
    '项目回顾记录': 'A14',
    '过程资产移交清单': 'A15',
}


def populate_schedule(sheet, model):
    outputs = {}
    for artifact, group in OUTPUT_GROUPS.items():
        for work_id in group.split():
            if work_id in outputs:
                raise ValueError('Duplicate work ID: ' + work_id)
            outputs[work_id] = artifact
    if outputs.keys() != {work['id'] for work in model['works']}:
        raise ValueError('Schedule deliverables do not match lifecycle work IDs')
    sheet.append(HEADERS)
    phases = {phase['id']: phase['name'] for phase in model['phases']}
    mapping = {}
    for row, work in enumerate(model['works'], 2):
        sheet.append([phases[work['phase_id']], '任务', '', work['name'], '待分配', '待分配',
                      work['work_type'], '待排期', '待排期', work['default_hours'], None,
                      outputs[work['id']], None])
        for cell in sheet[row][7:9]:
            cell.number_format = 'yyyy-mm-dd'
        mapping[work['id']] = row
    return mapping
