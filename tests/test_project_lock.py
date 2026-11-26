"""Permanent lock ownership, stale recovery, and release-error contracts."""

from datetime import datetime, timezone
import json
import os
import socket
import time
from uuid import uuid4

import pytest

from prism.project import ProjectError
from prism.project_lock import LockReleaseError, ProjectLock, ProjectLocked, _process_start
from p2_helpers import finish, wait_status, worker


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows project lock backend')


def record(pid=None, stamp=None):
    return dict(pid=os.getpid() if pid is None else pid,
                process_start=_process_start(os.getpid()) if stamp is None else stamp,
                owner_token=uuid4().hex, created_at=datetime.now(timezone.utc).isoformat(),
                process_type='test', host=socket.gethostname())


def test_active_lock_and_finite_timeout(tmp_path):
    with ProjectLock(tmp_path):
        with pytest.raises(ProjectLocked) as busy:
            ProjectLock(tmp_path).acquire()
        assert busy.value.retryable is True
        start = time.monotonic()
        with pytest.raises(ProjectLocked):
            ProjectLock(tmp_path, timeout=0.1).acquire()
        assert 0.08 <= time.monotonic() - start < 3
    assert not (tmp_path / '.prism.lock').exists()


@pytest.mark.parametrize('kind', ['broken', 'partial', 'missing', 'live', 'foreign'])
def test_unknown_or_live_lock_is_not_reclaimed(tmp_path, kind):
    value = record()
    if kind == 'foreign':
        value['host'] = 'not-this-host'
    if kind == 'missing':
        del value['owner_token']
    raw = {'broken': 'broken', 'partial': '{"pid":'}.get(kind, json.dumps(value))
    path = tmp_path / '.prism.lock'
    path.write_text(raw, encoding='utf-8')
    with pytest.raises(ProjectLocked):
        ProjectLock(tmp_path).acquire()
    assert path.read_text(encoding='utf-8') == raw


def test_exited_process_lock_is_reclaimed(tmp_path):
    status = tmp_path / 'identity.json'
    process = worker('identity', tmp_path, status, tmp_path / 'start', tmp_path / 'release')
    identity = wait_status(status, {'done'})
    finish(process)
    path = tmp_path / '.prism.lock'
    path.write_text(json.dumps(record(identity['pid'], identity['process_start'])), encoding='utf-8')
    with ProjectLock(tmp_path):
        pass
    assert not path.exists()


def test_reused_pid_does_not_keep_stale_owner(tmp_path):
    path = tmp_path / '.prism.lock'
    path.write_text(json.dumps(record(stamp=_process_start(os.getpid()) + 1)), encoding='utf-8')
    with ProjectLock(tmp_path):
        pass
    assert not path.exists()


def test_token_change_retains_lock_file(tmp_path):
    lock = ProjectLock(tmp_path).acquire()
    value = lock._record()
    value['owner_token'] = uuid4().hex
    lock._file.seek(0)
    lock._file.write(json.dumps(value).encode('utf-8'))
    lock._file.truncate()
    lock._file.flush()
    with pytest.raises(ProjectLocked):
        lock.release()
    assert (tmp_path / '.prism.lock').exists()
    assert lock._file is None


def fail_delete(self):
    raise OSError('injected handle cleanup failure')


def test_generic_lock_does_not_invent_business_commit_state(tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectLock, '_delete_owned_handle', fail_delete)
    with pytest.raises(LockReleaseError) as result:
        with ProjectLock(tmp_path):
            pass
    assert not hasattr(result.value, 'commit_state')
    assert result.value.retryable is False


@pytest.mark.parametrize('state', ['not_committed', 'committed', 'unknown'])
def test_cleanup_preserves_original_exception(tmp_path, monkeypatch, state):
    monkeypatch.setattr(ProjectLock, '_delete_owned_handle', fail_delete)
    original = ProjectError('original business error')
    original.commit_state = state
    with pytest.raises(ProjectError) as result:
        with ProjectLock(tmp_path):
            raise original
    assert result.value is original
    assert result.value.commit_state == state
    assert 'cleanup failure' in result.value.lock_release_error
