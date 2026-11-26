"""Chapter and table purposes for the independently authored public catalog."""

PLAN = (
    ('测试依据', 'basis'), ('软件测试环境', 'environment equipment'),
    ('测试标识', 'case_index'), ('测试进度', 'test_dates'),
    ('测试终止条件', 'exit_criteria'), ('需求的可追踪性', 'case_trace'), ('注释', 'terms'))
SPEC = (('测试准备', 'environment equipment'), ('测试说明', 'procedures'),
        ('需求的可追踪性', 'case_trace'), ('注释', 'terms'))
REPORT = (('测试结果概述', 'summary environment'), ('详细测试结果', 'results'),
          ('测试结论', 'conclusion'), ('需求的可追踪性', 'case_trace'), ('注释', 'terms'))
APPENDIX = ('附录　测试用例记录', 'case_record')
SIM_APPENDIX = ('附录　仿真测试用例记录', 'case_record')
CHAPTERS = {
    '1': (('运行环境要求', 'platform'), ('技术要求', 'task_data'), ('设计约束', 'design_limits'),
          ('质量控制要求', 'quality'), ('验收和交付', 'handover'), ('软件保障要求', 'support'),
          ('进度和里程碑', 'milestones milestone_picture')),
    '2': (('组织和职责', 'roles'), ('标准、条例和约定', 'policy'),
          ('活动审核（过程审核）', 'audits'), ('工作产品审核（产品审核）', 'quality'),
          ('不符合问题的解决', 'issues'), ('工具、技术和方法', 'tools'),
          ('对供货单位的控制', 'supplier'), ('记录的收集、维护和保存', 'archive'), ('注释', 'terms')),
    '3': (('组织和职责', 'roles'), ('软件配置活动管理', 'configuration baselines changes'),
          ('工具、技术和方法', 'tools'), ('对供货单位的控制', 'supplier'), ('进度表', 'milestones'),
          ('培训', 'training'), ('注释', 'terms')),
    '4': (('策划背景概述', 'boundary'), ('软件开发活动的总体实施计划', 'lifecycle'),
          ('详细的软件开发活动实施计划', 'development quality issues'),
          ('进度表和活动网络图', 'milestones milestone_picture'), ('项目组织和资源', 'roles platform'),
          ('培训', 'training'), ('注释', 'terms')),
    '5': (('需求', 'requirements'), ('合格性规定', 'qualification'), ('需求可追踪性', 'trace')),
    '6': (('CSCI级设计决策', 'decisions'), ('CSCI体系结构设计', 'architecture'),
          ('CSCI详细设计', 'detailed'), ('可靠性设计', 'reliable_design'),
          ('需求可追踪性', 'trace'), ('注释', 'terms')),
    '7': PLAN, '7.1': PLAN, '10': PLAN,
    '8': (('软件测试环境准备', 'environment equipment'), ('详细测试步骤', 'procedures'),
          ('需求的可追踪性', 'case_trace'), ('注释', 'terms')),
    '8.1': SPEC + (SIM_APPENDIX,), '11': SPEC + (APPENDIX,),
    '9': REPORT[:2] + (('问题统计', 'problems'),) + REPORT[2:],
    '9.1': REPORT + (SIM_APPENDIX,), '12': REPORT + (APPENDIX,),
    '13': (('版本说明', 'release configuration changes install'), ('注释', 'terms')),
    '14': (('固件编程指令', 'platform tools install verify'), ('注释', 'terms')),
    '15': (('软件配置管理情况综述', 'release'), ('软件配置管理基本信息', 'configuration'),
           ('专业组划分及权限分配', 'roles access'), ('变更记录', 'changes'), ('基线记录', 'baselines'),
           ('入库记录', 'inbound'), ('出库记录', 'outbound'), ('审核记录', 'audits'),
           ('备份记录', 'backup'), ('测量', 'metrics'), ('注释', 'terms')),
    '16': (('软件研制概述', 'boundary'), ('软件质量保证情况', 'audits quality issues'),
           ('软件配置管理情况', 'baselines'), ('第三方评测情况', 'external'), ('注释', 'terms')),
    '17': (('软件概述', 'boundary'), ('软件研制过程', 'lifecycle'),
           ('软件满足任务指标情况', 'qualification trace'), ('质量保证情况', 'test_overview issues'),
           ('配置管理情况', 'baselines'), ('测量和分析', 'metrics'), ('提交过程资产库资产', 'handover'),
           ('结论', 'acceptance'), ('注释', 'terms')),
}

# A reference resolves through the current device's configuration list.
REFERENCES = {
    '1': ('SDP',), '2': ('SDTD', 'SDP'), '3': ('SDTD', 'SDP'), '4': ('SDTD', 'SQAP', 'SCMP'),
    '5': ('SDTD',), '6': ('SDTD', 'SRS'), '7': ('SRS', 'SDD'), '7.1': ('SRS', 'SDD'),
    '8': ('STP-UT', 'SRS'), '8.1': ('STP-SIM', 'SRS'), '9': ('STD-UT', 'STP-UT'),
    '9.1': ('STD-SIM', 'STP-SIM'), '10': ('SDTD', 'SRS'), '11': ('STP-CSCI', 'SRS'),
    '12': ('STP-CSCI', 'STD-CSCI'), '13': ('SRS', 'SCMR'), '14': ('SVD', 'SDD'),
    '15': ('SCMP', 'SVD'), '16': ('SQAP', 'SCMR'), '17': ('SDTD', 'SQAR', 'SCMR'),
}

# These records require an actual decision, signature, or observation from the user.
FORMS = {
    'quality': ('工作产品检查', '检查产品|核对内容|检查人|意见与证据'),
    'issues': ('问题跟踪', '问题与出处|处理人|处理措施|复核结果'),
    'training': ('培训安排', '主题|参加角色|时间|培训记录'),
    'supplier': ('外部交付控制', '交付方|交付物|验收依据|接收记录'),
    'archive': ('记录归档', '材料|归档位置|维护人|保存期限'),
    'access': ('权限核对', '角色|配置库范围|授权操作|批准记录'),
    'inbound': ('入库登记', '配置项与版本|入库日期|提交人|凭据'),
    'outbound': ('出库登记', '配置项与版本|领取日期|接收人|用途'),
    'backup': ('备份与恢复', '备份范围|位置|执行日期|恢复验证'),
    'external': ('第三方评测记录', '评测范围|报告编号|评测日期|评测结论'),
    'acceptance': ('交付评定', '评定依据|遗留问题|结论|确认人'),
    'policy': ('适用约定', '依据名称|版本|适用活动|核对方式'),
    'verify': ('操作后验证', '检查点|预期现象|观察结果|记录人'),
    'support': ('保障安排', '支持事项|责任角色|所需资料|联系方式'),
}
