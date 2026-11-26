"""Ordinary project fields; structured objects live in items.py."""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple


BASIC_FIELDS = [
    "项目名", "产品名", "阶段", "版本", "密级", "安全等级", "用户单位",
]

ORGANIZATION_FIELDS = ["组织名称", "组织简称", "研发部门"]
ORGANIZATION_DEFAULTS = dict.fromkeys(ORGANIZATION_FIELDS, "")


PEOPLE_FIELDS = [
    "软件负责人", "项目软件质量师", "测试人员", "项目配管", "所级质量师", "部门领导", "产品库配管", "产品负责人", "设计人员", "科研生产处主管领导",
]

ENVIRONMENT_FIELDS = [
    "操作系统",
    "器件型号", "器件类型", "质量等级", "交付文件名", "软件规模", "外设及内部资源", "主要功能", "IDE", "编译/综合/布局布线工具", "仿真工具", "下载/编程/在线调试工具", "调试器", "语言", "输入", "输出", "CSCI输入", "CSCI输出", "CSCI行为设计决策",
]

TOOL_FIELDS = ("IDE", "编译/综合/布局布线工具", "仿真工具", "下载/编程/在线调试工具")
TOOL_ALIASES = {"compile_build_tool": TOOL_FIELDS[1], "simulation_tool": TOOL_FIELDS[2],
                "program_debug_tool": TOOL_FIELDS[3]}

PICTURE_FIELDS = [
    "系统组成与软件边界图", "软件架构图", "接口关系图", "主要流程算法图",
    "测试环境与接线图", "进度里程碑图",
]

TEST_ENVIRONMENT_ROWS = (
    ("软件编码编译设计环境", ("测试环境软件工具", "需求环境软件工具", "软件工具环境差异结论")),
    ("供电方式", ("测试供电方式", "需求供电方式", "供电方式差异结论")),
    ("工作环境（含温度范围）", ("测试工作环境", "需求工作环境", "工作环境差异结论")),
)
TEST_ENVIRONMENT_FIELDS = [name for _, fields in TEST_ENVIRONMENT_ROWS for name in fields] + [
    "测试环境影响及有效性评估", "测试仪器名称",
]

DATE_FIELDS = [
    "项目年",
    "启动开始", "任务书文件年月", "任务书版本年月日", "任务书评审日期", "功能基线日期", "开发计划文件年月",
    "开发计划版本年月日", "开发计划评审日期", "质保版本年月日", "质保计划年月", "质保评审日期", "配管版本年月日",
    "配管计划年月", "配管评审日期", "功能基线", "启动结束", "策划开始", "需规年月", "需规版本年月日",
    "需规评审日期", "分配基线", "里程碑日期", "策划结束", "设计开始", "设计说明年月", "设计说明版本年月日",
    "设计说明评审日期", "单元测试计划年月", "单元测试计划版本年月日", "单元测试计划评审日期", "单元测试说明年月",
    "单元测试说明版本年月日", "单元测试说明评审日期", "单元测试报告年月", "单元测试报告版本年月日",
    "单元测试报告评审日期", "设计结束", "测试开始", "系统测试计划年月", "系统测试计划版本年月日",
    "系统测试计划评审日期", "系统测试说明年月", "系统测试说明版本年月日", "系统测试说明评审日期",
    "系统测试报告年月", "系统测试报告版本年月日", "系统测试报告评审日期", "代码入库", "测试结束", "验收开始",
    "版本说明年月", "版本说明年月日", "保障手册年月", "保障手册年月日", "配管报告年月", "配管报告年月日",
    "质保报告年月", "质保报告年月日", "研制总结年月", "研制总结年月日", "总结评审日期", "测试后代码入库",
    "产品基线", "验收结束",
]

DATE_FIELDS.extend(label + boundary
                   for label in ("单元测试准备", "单元测试执行", "单元测试回归", "单元测试总结",
                                 "系统测试准备", "系统测试执行", "系统测试回归", "系统测试总结")
                   for boundary in ("开始", "结束"))


FIELD_SECTIONS: Sequence[Tuple[str, str, Sequence[str]]] = (
    ("basic", "基本信息", BASIC_FIELDS),
    ("organization", "单位信息", ORGANIZATION_FIELDS),
    ("people", "人员职责", PEOPLE_FIELDS),
    ("environment", "软件环境", ENVIRONMENT_FIELDS),
    ("pictures", "图片", PICTURE_FIELDS),
)

PROJECT_FIELD_SECTIONS = FIELD_SECTIONS + (("test_environment", "测试环境", TEST_ENVIRONMENT_FIELDS),)

ALL_FIELDS: List[str] = []
for _key, _title, _fields in PROJECT_FIELD_SECTIONS:
    ALL_FIELDS.extend(_fields)
ALL_FIELDS.extend(DATE_FIELDS)

FIELD_LABELS: Dict[str, str] = {field: field for field in ALL_FIELDS}
FIELD_LABELS.update({"版本": "软件版本", "下载/编程/在线调试工具": "烧写软件",
                     "软件规模": "软件规模（千行）", "质量等级": "器件质量等级", "操作系统": "开发操作系统"})
