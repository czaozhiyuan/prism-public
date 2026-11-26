"""Permanent snapshot, real-process concurrency, and transaction outcomes."""

import json
import os
from pathlib import Path

import pytest

from prism import storage
from prism.project import Project, ProjectError
from prism.project_lock import LockReleaseError, ProjectLock, ProjectLocked
from p2_helpers import batch, finish, populated, snapshot, wait_status, worker


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows project lock backend')


def test_old_snapshot_and_expected_revision_conflict(tmp_path):
    project = populated(tmp_path / 'project')
    stale = Project.load(project.root)
    project.fields['marker'] = 'new'
    project.save()
    for candidate, expected in ((stale, None), (project, 'sha256:' + '0' * 64)):
        with pytest.raises(ProjectError) as result:
            candidate.save(expected_revision=expected)
        assert result.value.code == 'REVISION_CONFLICT'
        assert result.value.commit_state == 'not_committed'
    assert Project.load(project.root).fields['marker'] == 'new'


def test_business_save_preserves_latest_outputs(tmp_path):
    project = populated(tmp_path / 'project')
    payload = json.loads(project.file_path.read_text(encoding='utf-8'))
    payload['outputs'] = batch(project.revision)
    project.file_path.write_text(json.dumps(payload), encoding='utf-8')
    project.fields['marker'] = 'new'
    project.save()
    assert Project.load(project.root).outputs == payload['outputs']


@pytest.mark.parametrize('reformat_module', [False, True])
def test_outputs_replace_only_changed_files(tmp_path, monkeypatch, reformat_module):
    project = populated(tmp_path / 'project')
    module = project.root / project.modules[0]['file']
    canonical = module.read_bytes()
    if reformat_module:
        module.write_bytes(canonical + b'\n')
    replaced = []
    original = os.replace

    def record(source, target):
        replaced.append(Path(target))
        return original(source, target)

    monkeypatch.setattr(storage.os, 'replace', record)
    outputs = batch(project.revision)
    with storage.ProjectTransaction(project.root) as transaction:
        transaction.commit_outputs(outputs)

    assert replaced == ([module] if reformat_module else []) + [project.file_path]
    assert module.read_bytes() == canonical
    current = Project.load(project.root)
    assert current.loaded_revision == project.loaded_revision
    assert current.outputs == outputs


def test_outputs_index_failure_restores_normalized_files(tmp_path, monkeypatch):
    project = populated(tmp_path / 'project')
    module = project.root / project.modules[0]['file']
    module.write_bytes(module.read_bytes() + b'\n')
    before = snapshot(project.root)
    original = os.replace

    def reject_index(source, target):
        if Path(target) == project.file_path:
            raise PermissionError('injected index write failure')
        return original(source, target)

    monkeypatch.setattr(storage.os, 'replace', reject_index)
    with pytest.raises(PermissionError) as result:
        with storage.ProjectTransaction(project.root) as transaction:
            transaction.commit_outputs(batch(project.revision))
    assert result.value.commit_state == 'not_committed'
    assert snapshot(project.root) == before


def test_real_writer_blocks_second_writer_and_partial_read(tmp_path):
    project = populated(tmp_path / 'project')
    start, release = tmp_path / 'start', tmp_path / 'release'
    a_status, b_status = tmp_path / 'a.json', tmp_path / 'b.json'
    a = worker('hold-write', project.root, a_status, start, release)
    b = None
    try:
        wait_status(a_status, {'ready'})
        start.touch()
        assert wait_status(a_status, {'held', 'error'})['state'] == 'held'
        with pytest.raises(ProjectLocked):
            Project.load(project.root)
        b = worker('writer', project.root, b_status, start, release)
        assert wait_status(b_status, {'success', 'error'}) == {'state': 'error', 'code': 'PROJECT_LOCKED'}
        finish(b)
    finally:
        start.touch(exist_ok=True)
        release.touch(exist_ok=True)
        finish(a)
        if b is not None and b.poll() is None:
            finish(b)
    assert wait_status(a_status, {'success', 'error'})['state'] == 'success'
    current = Project.load(project.root)
    assert current.fields['marker'] == current.features[0]['requirement_description'] == 'new'
    assert not (project.root / '.prism.lock').exists()


@pytest.mark.parametrize('existing', [False, True])
def test_concurrent_create_has_one_winner(tmp_path, existing):
    root = tmp_path / 'project'
    if existing:
        root.mkdir()
    start, release = tmp_path / 'start', tmp_path / 'release'
    statuses = [tmp_path / 'a.json', tmp_path / 'b.json']
    workers = [worker('create', root, status, start, release) for status in statuses]
    try:
        for status in statuses:
            wait_status(status, {'ready'})
        start.touch()
        results = [wait_status(status, {'success', 'error'}) for status in statuses]
    finally:
        start.touch(exist_ok=True)
        for process in workers:
            finish(process)
    assert sum(result['state'] == 'success' for result in results) == 1
    loser = next(result for result in results if result['state'] == 'error')
    assert loser['code'] in ('PROJECT_LOCKED', 'PROJECT_ALREADY_EXISTS')
    assert Project.load(root).name == 'concurrent create'
    assert not (root / '.prism.lock').exists()


def test_replace_failure_rolls_back(tmp_path, monkeypatch):
    project = populated(tmp_path / 'project')
    before = snapshot(project.root)
    original = storage.os.replace
    calls = []

    def replace(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise OSError('injected replacement failure')
        return original(source, target)

    monkeypatch.setattr(storage.os, 'replace', replace)
    project.fields['marker'] = 'new'
    with pytest.raises(OSError) as result:
        project.save()
    assert result.value.commit_state == 'not_committed'
    assert snapshot(project.root) == before


def fail_delete(self):
    raise OSError('injected handle cleanup failure')


@pytest.mark.parametrize('operation,state', [
    ('read', 'not_committed'), ('read_transaction', 'not_committed'),
    ('noop', 'not_committed'), ('write', 'committed'), ('create', 'committed'),
])
def test_release_failure_reports_actual_business_outcome(tmp_path, monkeypatch, operation, state):
    root = tmp_path / 'project'
    if operation != 'create':
        populated(root)
    with monkeypatch.context() as patch:
        patch.setattr(ProjectLock, '_delete_owned_handle', fail_delete)
        with pytest.raises(LockReleaseError) as result:
            if operation == 'read':
                Project.load(root)
            elif operation == 'create':
                Project.create(root, 'new')
            else:
                with storage.ProjectTransaction(root) as transaction:
                    if operation == 'write':
                        transaction.project.fields['marker'] = 'new'
                    if operation != 'read_transaction':
                        transaction.commit()
        assert result.value.commit_state == state
        assert result.value.retryable is False
    # The injected failure retained this test's own lock; remove only that file.
    (root / '.prism.lock').unlink()
    current = Project.load(root)
    if operation == 'write':
        assert current.fields['marker'] == 'new'
    elif operation == 'create':
        assert current.name == 'new'
    else:
        assert current.fields['marker'] == 'old'


@pytest.mark.parametrize('rollback_fails,state', [(False, 'not_committed'), (True, 'unknown')])
def test_original_rollback_state_survives_release_failure(tmp_path, monkeypatch, rollback_fails, state):
    project = populated(tmp_path / 'project')
    project.fields['marker'] = 'new'
    original = storage.os.replace
    calls = []

    def replace(source, target):
        calls.append(target)
        if len(calls) == 2 or (rollback_fails and len(calls) > 2):
            raise OSError('injected write/rollback failure')
        return original(source, target)

    monkeypatch.setattr(storage.os, 'replace', replace)
    monkeypatch.setattr(ProjectLock, '_delete_owned_handle', fail_delete)
    with pytest.raises((ProjectError, OSError)) as result:
        project.save()
    assert result.value.commit_state == state
    assert 'cleanup failure' in result.value.lock_release_error
    if rollback_fails:
        assert result.value.code == 'STORAGE_RECOVERY_REQUIRED'
        assert list(project.root.glob('.prism-save-*'))
