"""Long-lived generation coordination using the project lock ownership model."""

from pathlib import Path

from .project import ProjectError
from .project_lock import ProjectLock, ProjectLocked


GENERATION_LOCK_FILENAME = ".prism-generate.lock"


class GenerationBusy(ProjectError):
    """The project already has an active or unverified generation owner."""

    def __init__(self, error):
        super().__init__("生成锁不可用：{}".format(error), "GENERATION_BUSY")
        self.retryable = True
        self.commit_state = "not_committed"
        self.details = dict(getattr(error, "details", {}) or {})


class GenerationLock(ProjectLock):
    """A separate lock held for one complete generation batch."""

    def __init__(self, root, timeout=0.0, process_type="prism-generation"):
        super().__init__(root, timeout=timeout, process_type=process_type)
        self.path = Path(self.root) / GENERATION_LOCK_FILENAME

    def acquire(self):
        try:
            return super().acquire()
        except ProjectLocked as error:
            raise GenerationBusy(error) from error
