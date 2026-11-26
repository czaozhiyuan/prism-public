"""P7B acceptance tests for isolated Word finalization and checklist publication."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from p2_helpers import populated
from prism import agent_service, generation_core, word_finalize, word_pipeline
from prism.agent_contract import CONTRACT
from prism.agent_service import AgentService
from prism.generation import GeneratedFile, TemplateCheck, planned_output_name
from prism.project import Project


@pytest.fixture
def service():
    return AgentService(application_root=Path(__file__).parents[1])


@pytest.fixture
def project(service, tmp_path):
    value = populated(tmp_path / "generation-project")
    response = service.call("project_apply", {
        "project": str(value.root),
        "schedule": {"project_start": "2026-09-21"},
    })
    assert response["ok"] is True
    return Project.load(value.root)


@pytest.fixture(autouse=True)
def usable_templates(monkeypatch):
    monkeypatch.setattr(generation_core, "inspect_template", lambda path, context: TemplateCheck(
        Path(path), Path(path).suffix[1:].upper(), set(), set(),
    ))


def _template_ids(service, project, kind):
    response = service.call("template_list", {"project": str(project.root)})
    assert response["ok"] is True
    return [item["template_id"] for item in response["data"]["templates"]
            if item["kind"] == kind]


def _fake_success(templates, output_dir, context, *args, **kwargs):
    template = Path(templates[0])
    output = Path(output_dir) / planned_output_name(template, context)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(template), str(output))
    return [GeneratedFile(template, output, "成功", "done")]


def _validate(response):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator(
        CONTRACT["tools"]["project_generate"]["response_schema"]
    ).validate(response)


def test_word_ownership_uses_application_window_when_available():
    class Word:
        Hwnd = 123

        class Documents:
            @staticmethod
            def Add():
                pytest.fail("probe document must not be created")

    recorded = []
    word_finalize._record_word_ownership(Word(), recorded.append)

    assert recorded == [123]


def test_word_ownership_uses_live_probe_window_when_application_hwnd_is_missing():
    events = []

    class Probe:
        class ActiveWindow:
            Hwnd = 456

        def Close(self, save_changes):
            events.append(("closed", save_changes))

    class Documents:
        @staticmethod
        def Add():
            events.append(("created", None))
            return Probe()

    class Word:
        def __init__(self):
            self.Documents = Documents()

        @property
        def Hwnd(self):
            raise AttributeError("Word.Application.Hwnd")

    def record(hwnd):
        events.append(("recorded", hwnd))

    word_finalize._record_word_ownership(Word(), record)

    assert events == [("created", None), ("recorded", 456), ("closed", False)]


def test_parent_publishes_only_successful_private_copy(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "formal.docx"
    source.write_bytes(b"rendered")

    class CompletedWorker:
        returncode = 0

        def __init__(self, command, **kwargs):
            self.private = Path(command[-2])
            self.status = Path(command[-1])
            self.private.write_bytes(b"finalized")
            self.status.write_text(json.dumps({
                "state": "success", "message": "updated",
            }), encoding="utf-8")

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            pytest.fail("completed worker must not be terminated")

    monkeypatch.setattr(word_pipeline.subprocess, "Popen", CompletedWorker)
    outcome = word_pipeline.run_word_worker(source, root)

    assert outcome.status == "success"
    assert source.read_bytes() == b"finalized"
    assert not list(root.glob(".prism-word-*"))


def test_timeout_keeps_formal_doc_and_uses_bounded_owned_cleanup(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "formal.docx"
    source.write_bytes(b"rendered")
    cleanup = []

    class TimedOutWorker:
        returncode = None

        def __init__(self, command, **kwargs):
            Path(command[-1]).write_text(json.dumps({
                "state": "running", "word_pid": 321, "word_start": 654,
            }), encoding="utf-8")

        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("worker", timeout)
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 1

    def terminate_owned(pid, started, deadline):
        cleanup.append((pid, started, deadline))
        return None

    monkeypatch.setattr(word_pipeline.subprocess, "Popen", TimedOutWorker)
    monkeypatch.setattr(word_pipeline, "_terminate_owned_process", terminate_owned)
    outcome = word_pipeline.run_word_worker(source, root, timeout=0.01, cleanup_timeout=0.01)

    assert outcome.status == "timeout"
    assert source.read_bytes() == b"rendered"
    assert cleanup and cleanup[0][:2] == (321, 654)
    assert not list(root.glob(".prism-word-*"))


def test_timeout_cleanup_uses_one_shared_deadline(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "formal.docx"
    source.write_bytes(b"rendered")
    clock = {"now": 100.0}
    waits = []
    owned_cleanup = []

    class StuckWorker:
        returncode = None

        def __init__(self, command, **kwargs):
            Path(command[-1]).write_text(json.dumps({
                "state": "running", "word_pid": 321, "word_start": 654,
            }), encoding="utf-8")

        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) > 1:
                clock["now"] += timeout
            raise subprocess.TimeoutExpired("worker", timeout)

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

    def terminate_owned(pid, started, deadline):
        owned_cleanup.append((pid, started, deadline, clock["now"]))
        return "private Word process did not exit within the cleanup deadline"

    monkeypatch.setattr(word_pipeline.subprocess, "Popen", StuckWorker)
    monkeypatch.setattr(word_pipeline.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(word_pipeline, "_terminate_owned_process", terminate_owned)
    outcome = word_pipeline.run_word_worker(source, root, timeout=120, cleanup_timeout=10)

    assert outcome.status == "timeout"
    assert source.read_bytes() == b"rendered"
    assert waits == [120, 10, 0]
    assert owned_cleanup == [(321, 654, 110.0, 110.0)]
    assert any("remained active" in warning for warning in outcome.cleanup_warnings)
    assert not list(root.glob(".prism-word-*"))


def test_owned_cleanup_refuses_pid_identity_mismatch(monkeypatch):
    monkeypatch.setattr(word_pipeline, "process_start_identity", lambda pid: 999)
    monkeypatch.setattr(word_pipeline.ctypes, "WinDLL",
                        lambda *args, **kwargs: pytest.fail("mismatched PID must not be opened"))

    warning = word_pipeline._terminate_owned_process(123, 456, 0.0)

    assert "could not be verified" in warning


@pytest.mark.parametrize(("state", "code"), [
    ("unavailable", "WORD_UNAVAILABLE"),
    ("failed", "WORD_FINALIZE_FAILED"),
    ("timeout", "WORD_TIMEOUT"),
    ("unstable", "WORD_PAGINATION_UNSTABLE"),
])
def test_word_problem_is_warning_and_formal_render_is_retained(
        service, project, monkeypatch, state, code):
    selected = _template_ids(service, project, "DOCX")[:1]
    monkeypatch.setattr(generation_core, "generate_batch", _fake_success)
    monkeypatch.setattr(generation_core, "review_rows_for_document", lambda *args: [])
    before = {}

    def word_failure(path, root, **kwargs):
        before["bytes"] = Path(path).read_bytes()
        return word_pipeline.WordOutcome(state, "injected Word condition", [])

    monkeypatch.setattr(word_pipeline, "run_word_worker", word_failure)
    response = service.call("project_generate", {
        "project": str(project.root), "template_ids": selected,
    })

    _validate(response)
    result = response["data"]["results"][0]
    assert response["ok"] is True and response["status"] == "success_with_warnings"
    assert response["warnings"][0]["code"] == code
    assert result["render_status"] == "success" and result["word_finalize_status"] == state
    assert (project.root / result["output"]).read_bytes() == before["bytes"]
    assert response["review_checklist"] == "outputs/待补充清单.xlsx"
    assert all(isinstance(item, str) for item in Project.load(project.root).outputs["results"][0]["warnings"])


@pytest.mark.parametrize("first_state", ["failed", "timeout"])
def test_word_failures_do_not_stop_later_documents(
        service, project, monkeypatch, first_state):
    selected = _template_ids(service, project, "DOCX")[:2]
    monkeypatch.setattr(generation_core, "generate_batch", _fake_success)
    monkeypatch.setattr(generation_core, "review_rows_for_document", lambda *args: [])
    outcomes = iter([
        word_pipeline.WordOutcome(first_state, "first failed", []),
        word_pipeline.WordOutcome("success", "second succeeded", []),
    ])
    calls = []

    def word_result(path, root, **kwargs):
        calls.append(Path(path).name)
        return next(outcomes)

    monkeypatch.setattr(word_pipeline, "run_word_worker", word_result)
    response = service.call("project_generate", {
        "project": str(project.root), "template_ids": selected,
    })

    assert len(calls) == 2
    assert [item["word_finalize_status"] for item in response["data"]["results"]] == [
        first_state, "success",
    ]
    assert response["status"] == "success_with_warnings"


def test_successful_docx_builds_current_checklist(
        service, project, monkeypatch):
    selected = _template_ids(service, project, "DOCX")[:1]
    monkeypatch.setattr(generation_core, "generate_batch", _fake_success)
    monkeypatch.setattr(word_pipeline, "run_word_worker", lambda *args, **kwargs:
                        word_pipeline.WordOutcome("success", "updated", []))
    monkeypatch.setattr(generation_core, "review_rows_for_document", lambda *args: [
        ("formal.docx", "P7B", "正文", "人工确认", "完成审核"),
    ])

    response = service.call("project_generate", {
        "project": str(project.root), "template_ids": selected,
    })

    _validate(response)
    assert response["status"] == "success_with_warnings"
    assert response["warnings"][0]["code"] == "VALIDATION_FAILED"
    assert response["warnings"][0]["details"]["review_items"] == 1
    assert response["review_checklist"] == "outputs/待补充清单.xlsx"
    assert (project.root / response["review_checklist"]).is_file()
    assert response["data"]["results"][0]["word_finalize_status"] == "success"


def test_xlsx_only_does_not_reuse_stale_checklist(service, project, monkeypatch):
    selected = _template_ids(service, project, "XLSX")[:1]
    stale = project.output_dir / "待补充清单.xlsx"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"stale")
    monkeypatch.setattr(generation_core, "generate_batch", _fake_success)

    response = service.call("project_generate", {
        "project": str(project.root), "template_ids": selected,
    })

    assert response["ok"] is True and response["review_checklist"] is None
    assert response["data"]["results"][0]["word_finalize_status"] == "not_applicable"
    assert stale.read_bytes() == b"stale"


def test_all_docx_render_failures_do_not_reuse_stale_checklist(
        service, project, monkeypatch):
    selected = _template_ids(service, project, "DOCX")[:1]
    stale = project.output_dir / "待补充清单.xlsx"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"stale")
    monkeypatch.setattr(generation_core, "generate_batch", lambda templates, *args, **kwargs: [
        GeneratedFile(Path(templates[0]), None, "失败", "injected render failure"),
    ])

    response = service.call("project_generate", {
        "project": str(project.root), "template_ids": selected,
    })

    assert response["status"] == "failed" and response["review_checklist"] is None
    assert response["data"]["results"][0]["render_status"] == "failed"
    assert stale.read_bytes() == b"stale"


def test_checklist_failure_is_partial_and_records_are_saved(
        service, project, monkeypatch):
    selected = _template_ids(service, project, "DOCX")[:1]
    monkeypatch.setattr(generation_core, "generate_batch", _fake_success)
    monkeypatch.setattr(word_pipeline, "run_word_worker", lambda *args, **kwargs:
                        word_pipeline.WordOutcome("success", "updated", []))
    monkeypatch.setattr(generation_core, "review_rows_for_document", lambda *args: [])
    monkeypatch.setattr(generation_core, "write_review_checklist",
                        lambda *args: (_ for _ in ()).throw(OSError("injected checklist failure")))

    response = service.call("project_generate", {
        "project": str(project.root), "template_ids": selected,
    })

    _validate(response)
    assert response["status"] == "partial_failure" and response["ok"] is False
    assert response["error"]["code"] == "GENERATION_FAILED"
    assert response["error"]["details"]["stage"] == "review_checklist"
    assert response["records_saved"] is True and response["review_checklist"] is None
    assert Project.load(project.root).outputs["results"][0]["render_status"] == "success"


def test_review_collection_failure_does_not_skip_later_word_finalize(
        service, project, monkeypatch):
    selected = _template_ids(service, project, "DOCX")[:2]
    monkeypatch.setattr(generation_core, "generate_batch", _fake_success)
    word_calls = []
    review_calls = []

    def finalize(path, root, **kwargs):
        word_calls.append(Path(path).name)
        return word_pipeline.WordOutcome("success", "updated", [])

    def collect(template, output, issues):
        review_calls.append(Path(output).name)
        if len(review_calls) == 1:
            raise OSError("injected review collection failure")
        return []

    monkeypatch.setattr(word_pipeline, "run_word_worker", finalize)
    monkeypatch.setattr(generation_core, "review_rows_for_document", collect)
    response = service.call("project_generate", {
        "project": str(project.root), "template_ids": selected,
    })

    _validate(response)
    assert len(word_calls) == 2 and len(review_calls) == 2
    assert [item["word_finalize_status"] for item in response["data"]["results"]] == [
        "success", "success",
    ]
    assert response["status"] == "partial_failure" and response["ok"] is False
    assert response["error"]["code"] == "GENERATION_FAILED"
    assert response["error"]["details"]["stage"] == "review_checklist"
    assert response["records_saved"] is True and response["review_checklist"] is None


def test_record_save_failure_keeps_published_checklist_precedence(
        service, project, monkeypatch):
    selected = _template_ids(service, project, "DOCX")[:1]
    monkeypatch.setattr(generation_core, "generate_batch", _fake_success)
    monkeypatch.setattr(word_pipeline, "run_word_worker", lambda *args, **kwargs:
                        word_pipeline.WordOutcome("success", "updated", []))
    monkeypatch.setattr(generation_core, "review_rows_for_document", lambda *args: [])
    monkeypatch.setattr(agent_service, "save_generation_outputs",
                        lambda *args: (_ for _ in ()).throw(OSError("injected record failure")))

    response = service.call("project_generate", {
        "project": str(project.root), "template_ids": selected,
    })

    _validate(response)
    assert response["error"]["code"] == "OUTPUT_RECORD_SAVE_FAILED"
    assert response["records_saved"] is False and response["commit_state"] == "committed"
    assert response["review_checklist"] == "outputs/待补充清单.xlsx"
    assert (project.root / response["review_checklist"]).is_file()
