"""Resolve work roles to people without counting role names as headcount."""
import re
from .schema import PEOPLE_FIELDS


def person_display(value):
    """Preserve explicit account spelling and transliterate Chinese names."""
    from pypinyin import Style, lazy_pinyin
    pattern = r'[(（][^()（）]*[)）]|[\u3400-\u9fff]+(?:\s*[(（][^()（）]*[)）])?'
    def convert(match):
        name=match.group(0)
        return name if '(' in name or '（' in name else '{}({})'.format(
            name,''.join(lazy_pinyin(name,style=Style.NORMAL)))
    return re.sub(pattern,convert,value)


def parse_people(value):
    """Accept explicit separators and adjacent name(account) records."""
    if not isinstance(value,str) or not value.strip():
        raise ValueError('人员不能为空')
    separated=re.sub(r'([)）])(?=[\u3400-\u9fffA-Za-z])',r'\1、',value.strip())
    result=[]
    for token in re.split(r'[、,，;；/／\r\n]+',separated):
        token=token.strip()
        match=re.fullmatch(r"([\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z0-9 .·'\-]*?)(?:\s*[(（][^()（）]+[)）])?",token)
        if not match or re.search(r'[\u3400-\u9fff]\s+[\u3400-\u9fff]',match[1]):
            raise ValueError('人员格式无法确认；多名人员请用顿号、逗号、分号或换行分隔')
        name=match[1].strip()
        if name in PEOPLE_FIELDS or name in ('待人工填写','待定','未填写','无','TBD','N/A'):
            raise ValueError('需要填写自然人姓名，不能使用岗位或占位文字')
        result.append((name,person_display(token)))
    return result


def work_people(work,context):
    """Return one owner and ordered unique executors from the same identities."""
    try:
        owner=parse_people(context.get(work['owner_role']))
        if len(owner)!=1:
            raise ValueError('负责人必须恰好一名自然人')
        executors=[]
        for role in work['executor_roles']:
            try:executors.extend(parse_people(context.get(role)))
            except ValueError as error:raise ValueError('{}：{}'.format(role,error)) from error
        unique={}
        for name,display in executors:
            # Keep the owner's explicit spelling consistent in both columns.
            unique.setdefault(name,owner[0][1] if name==owner[0][0] else display)
        if not unique or owner[0][0] not in unique:
            raise ValueError('执行人必须非空并包含负责人')
        return owner[0][1],list(unique.values()),len(unique)
    except ValueError as error:
        raise ValueError('{}（{}）：{}'.format(work['id'],work['owner_role'],error)) from error
