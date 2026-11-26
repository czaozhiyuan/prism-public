"""Tests for working-day schedule calculations."""

from datetime import date
from pathlib import Path

import pytest

from prism.schedule import (
    CalendarCoverageError,
    StageDefinition,
    build_stage_schedule,
    load_work_calendar,
)


CALENDAR_PATH = Path(__file__).parents[1] / "assets" / "calendars" / "cn_workdays_2026_2035.json"


def test_weekends_do_not_count_toward_duration() -> None:
    calendar = load_work_calendar(CALENDAR_PATH)
    result = build_stage_schedule(
        date(2026, 1, 9),
        [StageDefinition("阶段", 2)],
        calendar,
    )[0]
    assert result.start == date(2026, 1, 9)
    assert result.finish == date(2026, 1, 12)


def test_adjusted_weekend_is_a_workday() -> None:
    calendar = load_work_calendar(CALENDAR_PATH)
    result = build_stage_schedule(
        date(2026, 1, 1),
        [StageDefinition("阶段", 1)],
        calendar,
    )[0]
    assert result.start == date(2026, 1, 4)
    assert result.finish == date(2026, 1, 4)


def test_stages_start_on_next_workday() -> None:
    calendar = load_work_calendar(CALENDAR_PATH)
    results = build_stage_schedule(
        date(2026, 2, 13),
        [StageDefinition("第一阶段", 2), StageDefinition("第二阶段", 1)],
        calendar,
    )
    assert results[0].finish == date(2026, 2, 14)
    assert results[1].start == date(2026, 2, 24)


def test_calendar_contains_all_requested_years() -> None:
    calendar = load_work_calendar(CALENDAR_PATH)
    assert calendar.covered_years == set(range(2020, 2036))
    assert all(calendar.statuses[year] == "official" for year in range(2020, 2027))
    assert calendar.statuses[2026] == "official"
    assert calendar.statuses[2035] == "statutory-baseline"


def test_out_of_range_date_is_rejected() -> None:
    calendar = load_work_calendar(CALENDAR_PATH)
    with pytest.raises(CalendarCoverageError):
        calendar.is_workday(date(2036, 1, 1))
    with pytest.raises(CalendarCoverageError):
        calendar.is_workday(date(2019, 12, 31))


@pytest.mark.parametrize('start,expected', [
    ('2020-01-23', '2020-02-03'),
    ('2021-02-10', '2021-02-18'),
    ('2022-01-30', '2022-02-07'),
    ('2023-01-20', '2023-01-28'),
    ('2024-02-09', '2024-02-18'),
    ('2025-01-27', '2025-02-05'),
    ('2025-12-31', '2026-01-04'),
    ('2022-12-30', '2023-01-03'),
])
def test_historical_holidays_and_cross_year(start, expected):
    calendar = load_work_calendar(CALENDAR_PATH)
    assert calendar.finish(date.fromisoformat(start), 2) == date.fromisoformat(expected)


def test_calendar_data_integrity():
    import json
    payload = json.loads(CALENDAR_PATH.read_text(encoding='utf-8'))
    for year, entry in payload['years'].items():
        holidays, workdays = entry['holidays'], entry['workdays']
        assert len(holidays) == len(set(holidays))
        assert len(workdays) == len(set(workdays))
        assert not set(holidays).intersection(workdays)
        assert all(date.fromisoformat(day).year == int(year) for day in holidays + workdays)
        assert all(date.fromisoformat(day).weekday() >= 5 for day in workdays)
    calendar = load_work_calendar(CALENDAR_PATH)
    assert not calendar.is_workday(date(2020, 2, 1))
    assert calendar.is_workday(date(2024, 2, 9))


@pytest.mark.parametrize('year', range(2020, 2026))
def test_historical_lifecycle_and_agent_calendar(year, tmp_path):
    from prism.agent_service import AgentService
    from prism.project import Project

    root = Path(__file__).resolve().parents[1]
    project = Project.create(tmp_path / 'project', 'calendar')
    project.schedule = {'project_start': '{}-01-06'.format(year), 'work_hours': {}}
    project.save()
    before = project.file_path.read_bytes()
    response = AgentService(application_root=root).call('project_recalculate', {
        'project': str(project.root), 'dry_run': True,
    })
    assert response['ok'] is True
    assert response['data']['schedule']['dates']['任务书文件年月'].startswith(str(year))
    assert response['data']['schedule']['phases']
    assert project.file_path.read_bytes() == before
