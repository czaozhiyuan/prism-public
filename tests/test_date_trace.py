"""Date trace presentation and real Tk synchronization regression tests."""

from copy import deepcopy
from datetime import date
from pathlib import Path
import gc
import tkinter as tk
from unittest.mock import patch

import pytest

from prism.functional_app import PrismApp, date_trace_rows
from prism.lifecycle import DateNode, LifecycleModel, Phase, Work
from prism.project import Project


def sample_model():
    return LifecycleModel("sample", "1", "Sample", (
        Phase("second", "Second phase", 30), Phase("first", "First phase", 40),
    ), (
        Work("second", "second", "Second work", 240, '项目管理', '软件负责人', ('软件负责人',)),
        Work("first", "first", "First work", 320, '项目管理', '软件负责人', ('软件负责人',)),
    ), (
        DateNode("review", "first", "finish", ("Review",)),
        DateNode("issue", "second", "start", ("Month", "Version")),
        DateNode("boundary", "second", "start"),
    ), Path("."))


def test_rules_formats_group_order_and_values():
    dates = {"Month": "formatted month", "Version": "formatted date", "Review": "stored review", "boundary": "stored ISO"}
    before = deepcopy(dates)
    model = sample_model()
    with patch.object(LifecycleModel, "calculate", side_effect=AssertionError("Display must not calculate")):
        rows = date_trace_rows(model, dates)
    assert list(rows) == ["second", "first"]
    assert rows["second"] == [
        ("Month", "formatted month", "Second work", "工作开始"),
        ("Version", "formatted date", "Second work", "工作开始"),
        ("boundary", "stored ISO", "Second work", "工作开始"),
    ]
    assert rows["first"] == [("Review", "stored review", "First work", "工作完成")]
    assert dates == before
    assert date_trace_rows(model, {})["first"][0][1] == ""


@pytest.fixture
def ui():
    # Dispose previous Tk cycles on the GUI thread before starting a worker.
    # Collecting an old Tcl interpreter in a worker can disrupt the next Tk init.
    gc.collect()
    original = tk.Tk.__init__

    def hidden(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.withdraw()

    with patch.object(tk.Tk, "__init__", hidden):
        app = PrismApp()
    app.geometry("1280x800+20000+20000")
    app.deiconify()
    app.show_page("schedule")
    app.update()
    try:
        yield app
    finally:
        app.destroy()


def assert_trace_values(app):
    assert all(variable.get() == str(app.project.schedule.get("dates", {}).get(key, ""))
               for key, variable in app.date_trace_vars)


def test_recalculation_updates_phase_and_trace_without_changing_rules(ui):
    before = ui.project.schedule.get("dates", {}).copy()
    ui.schedule_start_var.set("2026-09-01")
    ui.schedule_hour_vars[ui.model.works[0].id].set("48")
    durations = {key: int(value.get()) for key, value in ui.schedule_hour_vars.items()}
    expected = ui.model.calculate(date(2026, 9, 1), durations, ui.calendar)
    assert ui._recalculate_schedule()
    assert ui.project.schedule == expected
    assert ui.project.schedule["dates"] != before
    assert_trace_values(ui)
    for phase in expected["phases"]:
        assert ui.schedule_start_labels[phase["id"]].get() == phase["start"]
        assert ui.schedule_finish_labels[phase["id"]].get() == phase["finish"]
    ui.schedule_start_var.set("invalid")
    assert not ui._recalculate_schedule()
    assert ui.project.schedule == expected
    assert_trace_values(ui)


def test_collapsing_wheel_and_read_only_layout(ui):
    phases = ui.model.phases
    assert [group["open"] for group in ui.date_trace_groups.values()] == [True] + [False] * (len(phases) - 1)
    expected = deepcopy(ui.project.schedule)
    canvas = ui.schedule_scroll.canvas
    before_height = canvas.bbox("all")[3]
    ui.date_trace_groups[phases[1].id]["button"].invoke()
    ui.update()
    assert canvas.bbox("all")[3] > before_height
    canvas.event_generate("<MouseWheel>", delta=-120, rootx=canvas.winfo_rootx()+5, rooty=canvas.winfo_rooty()+5)
    ui.update()
    assert canvas.yview()[0] > 0
    for group in ui.date_trace_groups.values():
        assert all(isinstance(child, tk.Label) for child in group["table"].winfo_children())
        assert max(int(child.grid_info()["column"]) for child in group["table"].winfo_children()) == 3
    ui._toggle_date_trace(phases[1].id)
    ui.update()
    assert canvas.bbox("all")[3] == before_height
    assert ui.project.schedule == expected


def test_reopen_and_changed_model_and_empty_dates(ui, tmp_path):
    ui.project = Project(tmp_path / "saved", name="Saved")
    ui.schedule_start_var.set("2026-08-03")
    assert ui._recalculate_schedule()
    ui.project.save()
    saved = deepcopy(ui.project.schedule)
    ui.project = Project.load(ui.project.root)
    ui._load_ui()
    assert ui.project.schedule == saved
    assert_trace_values(ui)
    ui.model = sample_model()
    ui.project.schedule = {}
    ui._rebuild_schedule()
    assert list(ui.date_trace_groups) == ["second", "first"]
    assert [key for key, _ in ui.date_trace_vars] == ["Month", "Version", "boundary", "Review"]
    assert_trace_values(ui)
    assert ui.date_trace_groups["second"]["button"].cget("text") == "▼ Second phase"
    ui.project.schedule = {}
    ui._refresh_date_trace()
    assert ui.date_trace_hint.winfo_manager() == "pack"
    assert all(variable.get() == "" for _, variable in ui.date_trace_vars)


def test_gui_saves_unscheduled_v7_and_clears_stale_dates(ui, tmp_path, monkeypatch):
    import json
    from unittest.mock import Mock
    from prism import functional_app
    errors = Mock()
    monkeypatch.setattr(functional_app.messagebox, 'showerror', errors)
    ui.project = Project.create(tmp_path / 'draft', 'GUI draft')
    ui._load_ui()
    ui.schedule_start_var.set('2026-01-05')
    assert ui._save_project()
    assert Project.load(ui.project.root).schedule['dates']
    ui.schedule_start_var.set('')
    ui.schedule_hour_vars['D11'].set('10')
    assert ui._save_project()
    reopened = Project.load(ui.project.root)
    assert reopened.schedule == {'work_hours': {'D11': 10}}
    assert json.loads(reopened.file_path.read_text(encoding='utf-8'))['format_version'] == 7
    assert reopened.context()['项目年'] == ''
    assert not errors.called
