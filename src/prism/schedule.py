"""Working-day schedule calculations for PRISM."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Set


class CalendarCoverageError(ValueError):
    """Raised when a date is outside the bundled calendar range."""


@dataclass(frozen=True)
class StageDefinition:
    """A project phase and its duration in working days."""

    name: str
    duration: int


@dataclass(frozen=True)
class StageResult:
    """Calculated start and finish dates for a project phase."""

    name: str
    duration: int
    start: date
    finish: date


class WorkCalendar:
    """Offline work calendar with holidays and adjusted working weekends."""

    def __init__(
        self,
        holidays: Iterable[date],
        workdays: Iterable[date],
        covered_years: Iterable[int],
        statuses: Mapping[int, str],
    ) -> None:
        self.holidays: Set[date] = set(holidays)
        self.workdays: Set[date] = set(workdays)
        self.covered_years: Set[int] = set(covered_years)
        self.statuses: Dict[int, str] = dict(statuses)

    def is_workday(self, day: date) -> bool:
        """Return whether a day is a working day in the loaded calendar."""
        self._require_coverage(day)
        if day in self.workdays:
            return True
        if day in self.holidays:
            return False
        return day.weekday() < 5

    def on_or_after(self, day: date) -> date:
        """Return the first working day on or after the supplied date."""
        current = day
        while not self.is_workday(current):
            current += timedelta(days=1)
        return current

    def after(self, day: date) -> date:
        """Return the first working day after the supplied date."""
        return self.on_or_after(day + timedelta(days=1))

    def finish(self, start: date, duration: int) -> date:
        """Return the inclusive finish date for a working-day duration."""
        if duration < 1:
            raise ValueError("阶段持续时间必须大于0")
        current = self.on_or_after(start)
        remaining = duration - 1
        while remaining:
            current += timedelta(days=1)
            if self.is_workday(current):
                remaining -= 1
        return current

    def shift(self, day: date, working_days: int) -> date:
        """Shift a workday by a signed number of working days."""
        current = self.on_or_after(day)
        step = 1 if working_days >= 0 else -1
        remaining = abs(working_days)
        while remaining:
            current += timedelta(days=step)
            if self.is_workday(current):
                remaining -= 1
        return current

    def _require_coverage(self, day: date) -> None:
        if day.year not in self.covered_years:
            years = sorted(self.covered_years)
            raise CalendarCoverageError(
                "日期超出预置工作日历范围：{}-{}".format(years[0], years[-1])
            )


def load_work_calendar(path: Path) -> WorkCalendar:
    """Load the bundled JSON work calendar."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    holidays: List[date] = []
    workdays: List[date] = []
    statuses: Dict[int, str] = {}
    for year_text, entry in payload["years"].items():
        year = int(year_text)
        statuses[year] = entry["status"]
        holidays.extend(date.fromisoformat(value) for value in entry["holidays"])
        workdays.extend(date.fromisoformat(value) for value in entry["workdays"])
    return WorkCalendar(holidays, workdays, statuses.keys(), statuses)


def build_stage_schedule(
    project_start: date,
    stages: Sequence[StageDefinition],
    calendar: WorkCalendar,
) -> List[StageResult]:
    """Calculate sequential project phases using inclusive working days."""
    results: List[StageResult] = []
    next_start = calendar.on_or_after(project_start)
    for stage in stages:
        finish = calendar.finish(next_start, stage.duration)
        results.append(StageResult(stage.name, stage.duration, next_start, finish))
        next_start = calendar.after(finish)
    return results
