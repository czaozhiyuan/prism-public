"""Shared parsing of the project-level instrument name string."""


def instrument_names(value):
    """Preserve order and duplicates, omitting empty LF/CRLF-separated rows."""
    return [line.strip() for line in value.splitlines() if line.strip()]


def test_setup_context(context):
    """Derive document rows without writing project facts or test records."""
    from .schema import TEST_ENVIRONMENT_ROWS
    rows = [dict(name=name, test=context.get(keys[0], ""),
                 requirement=context.get(keys[1], ""), conclusion=context.get(keys[2], ""))
            for name, keys in TEST_ENVIRONMENT_ROWS]
    names = instrument_names(context.get("测试仪器名称", "")) or [""]
    result = {"test_setup_rows": rows}
    for kind, person, date in (("unit", "软件负责人", "设计开始"),
                               ("system", "测试人员", "测试开始")):
        result[kind + "_hardware_checks"] = [
            dict(number=index, name=name, check="经过计量，在有效使用期内",
                 result="一致", conformity="符合", person=context.get(person, ""),
                 date=context.get(date, "")) for index, name in enumerate(names, 1)]
    return result
