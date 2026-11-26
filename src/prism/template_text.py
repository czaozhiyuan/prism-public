"""Targeted punctuation adapters for explicitly marked Chinese descriptions."""
import re


def cn_text(value):
    """Collapse only repeated final Chinese periods; preserve internal text."""
    if value is None:
        return ''
    text = str(value)
    if not text.strip():
        return ''
    return re.sub(r'。{2,}([”’」』）》）]*\s*)$', r'。\1', text)


def cn_sentence(value):
    """End a nonempty Chinese description without touching technical strings."""
    text = cn_text(value).rstrip()
    if not text or not re.search(r'[\u3400-\u9fff]', text):
        return text
    end = text.rstrip('”’」』）》）')
    if end.endswith(('。', '！', '？', '!', '?', '.', '…', '；', ';', '：', ':')):
        return text
    return text + '。'


def template_environment():
    """Use the same filters for preflight, picture discovery and rendering."""
    from jinja2 import Environment
    environment = Environment()
    from .document_fields import filled
    environment.filters.update(cn_text=cn_text, cn_sentence=cn_sentence, filled=filled)
    return environment
