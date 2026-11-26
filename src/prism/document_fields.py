"""Shared values for built-in document fields and dynamic source chapters."""

MANUAL = "待人工填写"


def filled(value):
    """Retain zero and technical text; mark only an empty display value."""
    return str(value) if value is not None and str(value).strip() else MANUAL


def chapter_positions(kind, number, feature_count, name, product):
    """Mirror the retained built-in heading hierarchy in one shared location."""
    bases = {
        "taskbook": {"features": "4.1", "performances": "4.2", "interfaces": "4.5",
                     "reliabilities": "4.7.1"},
        "srs": {"features": "3.2", "performances": "3.2", "interfaces": "3.3",
                "reliabilities": "3.11.1"},
        "design": {"features": "4.1", "performances": "4.1", "interfaces": "4.3",
                   "reliabilities": "6"},
    }
    result = {}
    for source, prefixes in bases.items():
        index = number + (feature_count if source in ("srs", "design") and kind == "performances" else 0)
        title = str(product or "") + name if source == "design" and kind in ("features", "performances") else name
        chapter = "{}.{}".format(prefixes[kind], index)
        result[source] = {"number": chapter, "name": title, "label": chapter + " " + title}
    return result


def document_fields(context):
    from .schema import TOOL_ALIASES
    from .test_environment import test_setup_context
    values = {alias: context.get(key, "") for alias, key in TOOL_ALIASES.items()}
    values["executable_filename"] = filled(context.get("交付文件名"))
    values.update(test_setup_context(context))
    return values
