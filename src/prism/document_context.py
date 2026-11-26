"""Authoritative identifiers shared by covers, references and trace matrices."""

DOCUMENT_CODES = ('SDTD', 'SQAP', 'SCMP', 'SDP', 'SRS', 'SDD',
                  'STP-UT', 'STD-UT', 'STR-UT',
                  'STP-CSCI', 'STD-CSCI', 'STR-CSCI',
                  'SVD', 'FSM', 'SCMR', 'SQAR', 'SDSR',
                  'YCX', 'EXE', 'STP-SIM', 'STD-SIM', 'STR-SIM')


def identification_context(context):
    """Keep software, document and baseline identifiers on the existing rule."""
    project = str(context.get('项目名', '')).strip()
    device = str(context.get('器件类型', '')).strip()
    base = 'R/{}/01 {}'.format(project, device)
    version = str(context.get('版本', '')).strip()
    return {
        'document_ids': {code: base + '.' + code for code in DOCUMENT_CODES},
        'software_id': base,
        'baseline_ids': {stage: '{}-{}_{}_for_{}'.format(base, version, project, stage)
                         for stage in ('GN', 'FP', 'RELEASE')},
    }


def document_context(context):
    from .document_fields import document_fields
    from .initial_delivery import configuration_context
    values = dict(document_fields(context), **identification_context(context))
    values.update(configuration_context(context))
    return values
