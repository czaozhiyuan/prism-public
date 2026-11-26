"""Real Windows process used by the permanent P2 concurrency tests."""

import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from prism import storage
from prism.project import Project, ProjectError
from prism.project_lock import _process_start


def wait(path):
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('barrier timeout: {}'.format(path))
        time.sleep(0.01)


def main():
    mode, root, status, start, release = sys.argv[1:]
    root, status, start, release = map(Path, (root, status, start, release))

    def report(**value):
        status.write_text(json.dumps(value), encoding='utf-8')

    if mode == 'identity':
        report(state='done', pid=os.getpid(), process_start=_process_start(os.getpid()))
        return
    report(state='ready')
    wait(start)
    try:
        if mode == 'create':
            Project.create(root, 'concurrent create')
        elif mode in ('writer', 'hold-write', 'hold-group-write'):
            with storage.ProjectTransaction(root) as transaction:
                transaction.project.fields['marker'] = 'new'
                if mode == 'hold-group-write':
                    transaction.project.fields.update({'软件负责人': '新人员', '测试仪器名称': '新仪器'})
                else:
                    transaction.project.features[0]['requirement_description'] = 'new'
                if mode in ('hold-write', 'hold-group-write'):
                    original = storage.os.replace
                    paused = [False]

                    def replace(source, target):
                        original(source, target)
                        if not paused[0] and Path(target).parent == root and (
                                mode == 'hold-write' or Path(target).name == '人员职责.json'):
                            paused[0] = True
                            report(state='held')
                            wait(release)

                    storage.os.replace = replace
                transaction.commit()
        else:
            raise ValueError(mode)
        report(state='success')
    except ProjectError as error:
        report(state='error', code=error.code)


if __name__ == '__main__':
    main()
