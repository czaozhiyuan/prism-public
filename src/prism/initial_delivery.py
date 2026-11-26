"""Generation-only first-release views; no execution or change-history records."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext

from .document_fields import MANUAL, filled

# Keep the reviewed report order and baseline membership, independent of selection.
CONFIGURATIONS = (
    ('SDTD', '软件研制任务书', '1.软件研制任务书_模板.docx', '任务书版本年月日'),
    ('SRS', '软件需求规格说明', '5.软件需求规格说明_模板.docx', '需规版本年月日'),
    ('SDP', '软件开发计划', '4.软件开发计划_模板.docx', '开发计划版本年月日'),
    ('SCMP', '软件配置管理计划', '3.软件配置管理计划_模板.docx', '配管版本年月日'),
    ('SQAP', '软件质量保证计划', '2.软件质量保证计划_模板.docx', '质保版本年月日'),
    ('SDD', '软件设计说明', '6.软件设计说明_模板.docx', '设计说明版本年月日'),
    ('STP-UT', '单元测试计划', '7.软件单元测试计划_模板.docx', '单元测试计划版本年月日'),
    ('STD-UT', '单元测试说明', '8.软件单元测试说明_模板.docx', '单元测试说明版本年月日'),
    ('STR-UT', '单元测试报告', '9.软件单元测试报告_模板.docx', '单元测试报告版本年月日'),
    ('YCX', '源程序', None, '测试后代码入库'),
    ('EXE', '可执行程序', None, '测试后代码入库'),
    ('STP-CSCI', '配置项系统软件测试计划', '10.配置项系统软件测试计划_模板.docx', '系统测试计划版本年月日'),
    ('STD-CSCI', '配置项系统软件测试说明', '11.配置项系统软件测试说明_模板.docx', '系统测试说明版本年月日'),
    ('STR-CSCI', '配置项系统软件测试报告', '12.配置项系统软件测试报告_模板.docx', '系统测试报告版本年月日'),
    ('SVD', '软件版本说明', '13.软件版本说明_模板.docx', '版本说明年月日'),
    ('FSM', '固件保障手册', '14.软件固件保障手册_模板.docx', '保障手册年月日'),
    ('SDSR', '研制总结报告', '17.软件研制总结报告_模板.docx', '研制总结年月日'),
    ('SQAR', '质量保证报告', '16.软件质量保证报告_模板（有几个表要手动填）.docx', '质保报告年月日'),
    ('SCMR', '配置管理报告', '15.软件配置管理报告_模板.docx', '配管报告年月日'),
)

# This is the existing process-audit table, not evidence of an executed audit.
AUDIT_MODEL = ('gjb5000-simplified', '3.0')
PROCESS_AUDITS = (
    ('项目管理过程（策划）审核检查单', 'launch', 'L18'),
    ('评审过程检查单', 'launch', 'L18'),
    ('配置管理过程审核检查单', 'launch', 'L18'),
    ('软件质量保证过程审核检查单', 'launch', 'L18'),
    ('项目管理过程（项目跟踪与监控）审核检查单', 'requirements', 'R10'),
    ('需求管理过程审核检查单', 'requirements', 'R10'),
    ('里程碑审核检查单', 'requirements', 'R10'),
    ('评审过程审核检查单', 'requirements', 'R10'),
    ('配置管理过程审核检查单', 'requirements', 'R10'),
    ('测量分析过程审核检查单', 'requirements', 'R10'),
    ('需求分析过程审核检查单', 'requirements', 'R10'),
    ('软件质量保证过程审核检查单', 'requirements', 'R10'),
    ('项目管理过程（项目跟踪与监控）审核检查单', 'development', 'D17'),
    ('测量分析过程审核检查单', 'development', 'D17'),
    ('单元测试计划及测试说明评审过程审核', 'development', 'D17'),
    ('软件实现过程审核检查单', 'development', 'D17'),
    ('单元测试报告评审过程审核', 'development', 'D17'),
    ('软件测试过程审核检查单', 'development', 'D17'),
    ('配置管理过程审核', 'development', 'D17'),
    ('需求管理过程审核检查单', 'development', 'D17'),
    ('项目管理过程（策划）审核检查单', 'development', 'D17'),
    ('项目管理过程（项目跟踪与监控）审核检查单', 'development', 'D17'),
    ('测量分析过程审核检查单', 'development', 'D17'),
    ('配置管理审核', 'development', 'D15'),
    ('变更控制过程审核', 'development', 'D17'),
    ('产品测试计划和测试说明评审过程审核', 'testing', 'T11'),
    ('产品测试报告评审过程审核', 'testing', 'T11'),
    ('配置管理过程审核', 'testing', 'T11'),
    ('需求管理过程审核检查单', 'testing', 'T11'),
    ('项目管理过程（策划）审核检查单', 'testing', 'T11'),
    ('变更控制过程审核', 'testing', 'T11'),
    ('项目管理过程（项目跟踪与监控）审核检查单', 'testing', 'T11'),
    ('测量分析过程审核检查单', 'testing', 'T11'),
    ('配置管理审核', 'testing', 'T09'),
    ('软件测试过程审核检查单', 'testing', 'T11'),
    ('质量保证过程审核', 'testing', 'T11'),
    ('里程碑审核', 'testing', 'T11'),
    ('评审过程审核', 'delivery', 'A12'),
)


def configuration_context(context, overrides=None):
    """Use each routed document's effective version, including unselected files."""
    from .document_versions import VERSION_OVERRIDES_KEY, effective_document_versions
    from .template_ids import device_test_template_id
    from .document_context import identification_context

    versions = context.get('document_versions')
    if versions is None:
        versions = effective_document_versions(context.get(VERSION_OVERRIDES_KEY, {})
                                               if overrides is None else overrides)
    identities = identification_context(context)
    rows = []
    for code, name, filename, release_key in CONFIGURATIONS:
        template_id = 'asset:templates/docx/' + filename if filename else None
        if template_id:
            routed = device_test_template_id(template_id, context.get('器件类型', ''))
            if routed != template_id:
                code = code.replace('-UT', '-SIM')
                name = name.replace('单元', '仿真')
                template_id = routed
            version = versions[template_id] if template_id in versions else MANUAL
        else:
            version = filled(context.get('版本'))
        identifier = MANUAL
        if str(context.get('项目名', '')).strip() and str(context.get('器件类型', '')).strip():
            identifier = identities['document_ids'][code]
        rows.append(dict(id=code, name=name, template_id=template_id, identifier=identifier,
                         version_source=template_id or '版本', release_source=release_key,
                         release_date=filled(context.get(release_key)),
                         initial_version=version, last_version=version, change_count=0,
                         management='基线管理'))
    contents = {
        'GN': ('SDTD',), 'FP': ('SDTD', 'SRS'),
        'RELEASE': tuple(row['id'] for row in rows if row['id'] not in ('SDP', 'SCMP', 'SQAP')),
    }
    return dict(initial_configuration_items=rows,
                initial_baselines=[dict(id=key, version=filled(context.get('版本')),
                    identifier=(identities['baseline_ids'][key] if all(str(context.get(k, '')).strip()
                                for k in ('项目名', '器件类型', '版本')) else MANUAL),
                    release_date=filled(context.get({'GN': '功能基线', 'FP': '分配基线',
                                                     'RELEASE': '产品基线'}[key])),
                    items=[row for row in rows if row['id'] in ids]) for key, ids in contents.items()],
                initial_configuration_summary='初始发布，无配置项变更')


def scheduled_delivery_context(project, context):
    """Consume a verified snapshot; unavailable local metrics stay manual."""
    from .lifecycle import format_date
    from .project import ProjectError
    from .schedule_state import imported_schedule
    from .work_people import work_people

    schedule = {}
    try:
        if project.schedule.get('project_start') and imported_schedule(project) == project.schedule:
            schedule = project.schedule
    except (ProjectError, OSError, ValueError):
        pass
    works = {row['id']: row for row in schedule.get('work_items', [])}
    audits = []
    for number, (activity, phase, work_id) in enumerate(PROCESS_AUDITS, 1):
        work = works.get(work_id) if (project.model_id, project.model_version) == AUDIT_MODEL else None
        finish = (format_date(date.fromisoformat(work['finish']), '审核完成')
                  if work and work['phase_id'] == phase else MANUAL)
        audits.append(dict(number=number, activity=activity, phase_id=phase,
                           work_id=work_id, finish=finish))
    dates = schedule.get('dates', {})
    def period(start, finish):
        return '{} 至 {}'.format(dates[start], dates[finish]) if dates.get(start) and dates.get(finish) else MANUAL
    metrics = dict(scale_kloc=filled(context.get('软件规模')), code_lines=MANUAL,
                   total_hours=MANUAL, person_days=MANUAL, productivity=MANUAL,
                   basis='按计划工作量计算', unit='行/人日', defect_rate=MANUAL)
    scale = str(context.get('软件规模', '')).strip()
    lines = None
    if scale:
        with localcontext() as decimal_context:
            decimal_context.prec = max(28, len(scale) + 10)
            lines = Decimal(scale) * 1000
            metrics['code_lines'] = format(lines, 'f')
    if works:
        try:
            total = sum(work['hours'] * work_people(work, context)[2] for work in works.values())
        except (ValueError, KeyError):
            total = None
        if total:
            metrics['total_hours'] = str(total)
            with localcontext() as decimal_context:
                decimal_context.prec = max(28, len(scale) + len(str(total)) + 10)
                metrics['person_days'] = format(Decimal(total) / 8, 'f')
                if lines is not None:
                    metrics['productivity'] = format((lines * 8 / total).quantize(
                        Decimal('.01'), rounding=ROUND_HALF_UP), 'f')
    return dict(process_audit_rows=audits, delivery_metrics=metrics,
                unit_hardware_usage=period('单元测试准备开始', '单元测试总结结束'),
                system_hardware_usage=period('测试开始', '测试结束'))
