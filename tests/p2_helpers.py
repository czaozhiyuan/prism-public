"""Small project fixtures and bounded subprocess coordination for P2 tests."""

import json
from pathlib import Path
import subprocess
import sys
import time

from prism.items import new_item
from prism.project import Project


def populated(root):
    project = Project.create(root, 'P2 fixture')
    item = new_item('features')
    item.update(name='module', requirement_description='old')
    project.features.append(item)
    project.fields['marker'] = 'old'
    project.save()
    return project


def batch(revision):
    return {'source_revision': revision, 'time': '2026-09-16T10:00:00', 'results': []}


def snapshot(root):
    return {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}


def wait_status(path, states, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if value['state'] in states:
                return value
        except (OSError, ValueError):
            pass
        time.sleep(0.01)
    raise AssertionError('worker status timeout: {}'.format(path))


def worker(mode, root, status, start, release):
    script = Path(__file__).with_name('p2_worker.py')
    return subprocess.Popen([sys.executable, str(script), mode, str(root), str(status),
                             str(start), str(release)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)


def finish(process):
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, (stdout, stderr)
