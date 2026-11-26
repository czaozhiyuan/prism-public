"""Frozen revision projection and current v7 persistence contract."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from prism.items import ModuleItem
from prism.project import Project
from prism.revision import business_state, canonical_bytes, project_revision
from p2_helpers import snapshot


@pytest.fixture
def vector():
    return json.loads((Path(__file__).parent / 'fixtures' / 'revision_vectors.json').read_text(encoding='utf-8'))


@pytest.fixture
def project(vector, tmp_path):
    source = vector['input']
    project = Project(root=tmp_path, name=source['name'], model_id=source['model']['id'],
                      model_version=source['model']['version'], fields=deepcopy(source['fields']),
                      schedule=deepcopy(source['schedule']), templates=deepcopy(source['templates']))
    kinds = {'feature': 'features', 'performance': 'performances',
             'interface': 'interfaces', 'reliability': 'reliabilities'}
    for kind, entries in source['modules'].items():
        for entry in entries:
            item = ModuleItem(deepcopy(entry['data']))
            item.module_id = entry['module_id']
            getattr(project, kinds[kind]).append(item)
    return project


def test_frozen_projection_bytes_and_digest(project, vector):
    assert business_state(project) == vector['projection']
    assert canonical_bytes(project) == vector['canonical_utf8'].encode('utf-8')
    assert project_revision(project) == vector['revision']
    assert vector['revision'] == 'sha256:c90647261909151d45182f7a071aa85618c4e4275ed9dea85e24adb50f258282'


def test_frozen_vector_persists_as_valid_v7_without_revision_drift(project, vector):
    import jsonschema
    from prism import storage

    schemas = Path(__file__).resolve().parents[1] / 'assets/schemas'
    project.save()
    basic = storage.read_json(project.file_path)
    assert basic['format_version'] == 7 and basic['groups'] == storage.GROUP_FILES
    jsonschema.validate(basic, storage.read_json(schemas / 'project-basic-v7.schema.json'))
    for kind, filename in basic['groups'].items():
        jsonschema.validate(storage.read_json(project.root / filename),
                            storage.read_json(schemas / ('project-' + kind.replace('_', '-') + '-v1.schema.json')))
    for entry in basic['modules']:
        jsonschema.validate(storage.read_json(project.root / entry['file']),
                            storage.read_json(schemas / 'project-module-v1.schema.json'))
    before = snapshot(project.root)
    loaded = Project.load(project.root)
    assert loaded.loaded_revision == vector['revision']
    assert canonical_bytes(loaded) == vector['canonical_utf8'].encode('utf-8')
    loaded.save()
    assert snapshot(project.root) == before


def test_object_keys_and_custom_selection_are_unordered(project):
    before = project_revision(project)
    project.fields = dict(reversed(list(project.fields.items())))
    project.schedule = dict(reversed(list(project.schedule.items())))
    project.templates['enabled_ids'].reverse()
    assert project_revision(project) == before


@pytest.mark.parametrize('kind', ['modules', 'tests'])
def test_business_array_order_changes_revision(project, kind):
    before = project_revision(project)
    if kind == 'modules':
        project.features.reverse()
    else:
        project.features[0]['unit_tests'].reverse()
    assert project_revision(project) != before


def test_outputs_and_storage_location_are_excluded(project, tmp_path):
    before = project_revision(project)
    project.outputs = {'source_revision': before, 'time': '2026-09-16T10:00:00', 'results': []}
    project.root = tmp_path / 'relocated'
    project._module_files = {project.features[0].module_id: {'file': 'other.json'}}
    assert project_revision(project) == before


def test_default_selection_is_ignored_but_custom_is_not(project):
    project.templates = {'mode': 'default', 'enabled_ids': []}
    before = project_revision(project)
    project.templates['enabled_ids'] = ['asset:templates/docx/a.docx']
    assert project_revision(project) == before
    project.templates['mode'] = 'custom'
    assert project_revision(project) != before


@pytest.mark.parametrize('value', [1.0, True])
def test_scalar_types_remain_distinct(project, value):
    project.fields['typed'] = 1
    before = project_revision(project)
    project.fields['typed'] = value
    assert project_revision(project) != before


def test_missing_and_null_remain_distinct(project):
    project.fields['nullable'] = None
    before = project_revision(project)
    del project.fields['nullable']
    assert project_revision(project) != before


@pytest.mark.parametrize('category', ['unit_tests', 'config_tests'])
def test_result_image_path_changes_revision(project, category):
    before = project_revision(project)
    project.features[0][category][0]['result_image'] = 'pictures/selected.png'
    assert project_revision(project) != before
