"""P6 acceptance tests for atomic project_apply transactions."""

import json
from pathlib import Path

import pytest
from p2_helpers import batch, populated

from prism import storage
from prism.agent_contract import CONTRACT, IMPLEMENTED_TOOLS, REQUEST_SCHEMAS, TOOL_RESULT_SCHEMAS
from prism.agent_service import AgentService
from prism.items import new_item
from prism.project import Project


@pytest.fixture
def service():
    return AgentService(application_root=Path(__file__).parents[1])


@pytest.fixture
def project(tmp_path):
    return populated(tmp_path / "agent-project")


def assert_success(response, commit_state=None):
    assert response["ok"] is True
    assert response["error"] is None
    if commit_state is not None:
        assert response["commit_state"] == commit_state
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator(TOOL_RESULT_SCHEMAS["project_apply"]).validate(response["data"])
    jsonschema.Draft202012Validator(CONTRACT["tools"]["project_apply"]["response_schema"]).validate(response)


def assert_error(response, code, commit_state="not_committed"):
    assert response["ok"] is False
    assert response["status"] == "failed"
    assert response["commit_state"] == commit_state
    assert response["error"]["code"] == code
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator(CONTRACT["tools"]["project_apply"]["response_schema"]).validate(response)


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file() and path.name != ".prism.lock"
    }


def add_feature(project, name):
    stored = Project.load(project.root)
    item = new_item("features")
    item.update(name=name, requirement_description="requirement")
    stored.features.append(item)
    stored.save(expected_revision=stored.loaded_revision)
    return item.module_id


def write_model(root):
    model_dir = root / "alternate"
    model_dir.mkdir(parents=True)
    (model_dir / "model.json").write_text(json.dumps({
        "format_version": 3, "id": "alternate", "version": "1.0", "name": "Alternate",
        "phases": [{"id": "launch", "name": "Launch"}, {"id": "verification", "name": "Verification"}],
        "works": [
            {"id": "L01", "phase_id": "launch", "name": "Setup", "default_hours": 160, "work_type": "项目管理", "owner_role": "软件负责人", "executor_roles": ["软件负责人"]},
            {"id": "V01", "phase_id": "verification", "name": "Verify", "default_hours": 56, "work_type": "项目管理", "owner_role": "软件负责人", "executor_roles": ["软件负责人"]},
        ],
        "date_nodes": [
            {"key": "schedule.launch.start", "work_id": "L01", "anchor": "start", "formats": ["Start"]},
            {"key": "schedule.verification.finish", "work_id": "V01", "anchor": "finish", "formats": ["Finish"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")


def test_project_apply_combines_domains_in_one_commit(tmp_path, project, monkeypatch):
    app_root = Path(__file__).parents[1]
    user_models = tmp_path / "models"
    write_model(user_models)
    service = AgentService(application_root=app_root,
                           model_roots=(app_root / "assets" / "models", user_models))
    module_id = Project.load(project.root).features[0].module_id
    commits = []
    original_commit = storage._commit_project_locked

    def count_commit(candidate, lock):
        commits.append(candidate.revision)
        return original_commit(candidate, lock)

    monkeypatch.setattr(storage, "_commit_project_locked", count_commit)
    response = service.call("project_apply", {
        "project": str(project.root),
        "fields": {"产品名": "P6 product"},
        "model": {"id": "alternate", "version": "1.0"},
        "schedule": {"project_start": "2026-09-21", "work_hours": {"L01": 11}},
        "templates": {"mode": "custom", "enabled_ids": []},
        "modules": {
            "update": [{"module_id": module_id, "data": {"requirement_description": "updated"}}],
            "create": [{"data_type": "performance", "data": {"name": "performance-a"}}],
        },
    })

    assert_success(response, "committed")
    assert len(commits) == 1
    stored = Project.load(project.root)
    assert stored.fields["产品名"] == "P6 product"
    assert (stored.model_id, stored.model_version) == ("alternate", "1.0")
    assert stored.schedule["work_hours"] == {"L01": 11}
    assert "schedule.launch.start" in stored.schedule["dates"]
    assert stored.templates == {"mode": "custom", "enabled_ids": []}
    assert stored.features[0]["requirement_description"] == "updated"
    assert [item["name"] for item in stored.performances] == ["performance-a"]
    assert response["data"]["created_modules"][0]["data_type"] == "performance"


def test_project_apply_failure_is_all_or_nothing(service, project):
    before = snapshot(project.root)
    module_id = Project.load(project.root).features[0].module_id
    cases = [
        ({"model": {"id": "missing", "version": "1.0"}}, "MODEL_NOT_FOUND"),
        ({"templates": {"mode": "custom", "enabled_ids": [
            "asset:templates/docx/missing.docx",
        ]}}, "TEMPLATE_NOT_FOUND"),
        ({"modules": {"update": [{
            "module_id": module_id, "data": {"direction": "input"},
        }]}}, "INVALID_FIELD"),
    ]
    for patch, error_code in cases:
        response = service.call("project_apply", dict(
            {"project": str(project.root), "fields": {"产品名": "must-not-persist"}},
            **patch,
        ))
        assert_error(response, error_code)
        assert snapshot(project.root) == before


@pytest.mark.parametrize("modules", [
    lambda module_id: {"update": [
        {"module_id": module_id, "data": {"name": "one"}},
        {"module_id": module_id, "data": {"name": "two"}},
    ]},
    lambda module_id: {
        "update": [{"module_id": module_id, "data": {"name": "one"}}],
        "remove": [{"module_id": module_id}],
    },
    lambda module_id: {"remove": [
        {"module_id": module_id},
        {"module_id": module_id},
    ]},
])
def test_project_apply_rejects_duplicate_and_conflicting_targets(service, project, modules):
    module_id = Project.load(project.root).features[0].module_id
    before = snapshot(project.root)
    response = service.call("project_apply", {
        "project": str(project.root), "modules": modules(module_id),
    })
    assert_error(response, "INVALID_ARGUMENT")
    assert snapshot(project.root) == before


def test_project_apply_update_and_remove_with_reorder(service, project):
    second_id = add_feature(project, "second")
    stored = Project.load(project.root)
    first_id = stored.features[0].module_id
    updated = service.call("project_apply", {
        "project": str(project.root),
        "modules": {
            "update": [{"module_id": first_id, "data": {"name": "renamed"}}],
            "reorder": [{"data_type": "feature", "module_ids": [second_id, first_id]}],
        },
    })
    assert_success(updated, "committed")
    assert [item["name"] for item in Project.load(project.root).features] == ["second", "renamed"]

    before = snapshot(project.root)
    invalid = service.call("project_apply", {
        "project": str(project.root),
        "modules": {
            "remove": [{"module_id": first_id}],
            "reorder": [{"data_type": "feature", "module_ids": [second_id, first_id]}],
        },
    })
    assert_error(invalid, "MODULE_ORDER_INVALID")
    assert snapshot(project.root) == before

    removed = service.call("project_apply", {
        "project": str(project.root),
        "modules": {
            "remove": [{"module_id": first_id}],
            "reorder": [{"data_type": "feature", "module_ids": [second_id]}],
        },
    })
    assert_success(removed, "committed")
    assert [item.module_id for item in Project.load(project.root).features] == [second_id]


def test_project_apply_rejects_create_and_reorder_same_category(service, project):
    stored = Project.load(project.root)
    before = snapshot(project.root)
    response = service.call("project_apply", {
        "project": str(project.root),
        "modules": {
            "create": [{"data_type": "feature", "data": {"name": "created"}}],
            "reorder": [{"data_type": "feature",
                         "module_ids": [stored.features[0].module_id]}],
        },
    })
    assert_error(response, "INVALID_ARGUMENT")
    assert snapshot(project.root) == before


def test_project_apply_create_order_and_dry_run_ids(service, project):
    request = {
        "project": str(project.root),
        "modules": {"create": [
            {"data_type": "performance", "data": {"name": "first"}},
            {"data_type": "performance", "data": {"name": "second"}},
        ]},
    }
    before = snapshot(project.root)
    dry_run = service.call("project_apply", dict(request, dry_run=True))
    assert_success(dry_run, "not_committed")
    assert snapshot(project.root) == before
    assert not (project.root / ".prism.lock").exists()

    real = service.call("project_apply", request)
    assert_success(real, "committed")
    dry_ids = [item["module_id"] for item in dry_run["data"]["created_modules"]]
    real_ids = [item["module_id"] for item in real["data"]["created_modules"]]
    assert set(dry_ids).isdisjoint(real_ids)
    assert [item["name"] for item in Project.load(project.root).performances] == ["first", "second"]


def test_model_schedule_order_discards_old_derived_values(tmp_path, project):
    app_root = Path(__file__).parents[1]
    user_models = tmp_path / "models"
    write_model(user_models)
    service = AgentService(application_root=app_root,
                           model_roots=(app_root / "assets" / "models", user_models))
    stored = Project.load(project.root)
    stored.schedule = {
        "project_start": "2026-09-21", "work_hours": {"L01": 12, "R01": 99},
        "dates": {"stale": "value"}, "phases": [],
    }
    stored.save(expected_revision=stored.loaded_revision)

    response = service.call("project_apply", {
        "project": str(project.root), "model": {"id": "alternate", "version": "1.0"},
        "schedule": {"work_hours": {"V01": 9}},
    })
    assert_success(response, "committed")
    schedule = Project.load(project.root).schedule
    assert schedule["work_hours"] == {"L01": 12, "V01": 9}
    assert "stale" not in schedule["dates"]
    assert "requirements" not in schedule["work_hours"]


def test_project_apply_revision_and_latest_snapshot(service, project):
    original = Project.load(project.root)
    changed = Project.load(project.root)
    changed.fields["用户单位"] = "latest"
    changed.save(expected_revision=changed.loaded_revision)
    before = snapshot(project.root)

    stale = service.call("project_apply", {
        "project": str(project.root), "expected_revision": original.loaded_revision,
        "fields": {"产品名": "stale"},
    })
    assert_error(stale, "REVISION_CONFLICT")
    assert snapshot(project.root) == before

    current_revision = Project.load(project.root).loaded_revision
    current = service.call("project_apply", {
        "project": str(project.root), "expected_revision": current_revision,
        "fields": {"产品名": "current"},
    })
    assert_success(current, "committed")

    changed_again = Project.load(project.root)
    changed_again.fields["用户单位"] = "newer"
    changed_again.save(expected_revision=changed_again.loaded_revision)
    omitted = service.call("project_apply", {
        "project": str(project.root), "fields": {"产品名": "applied"},
    })
    assert_success(omitted, "committed")
    assert Project.load(project.root).fields["用户单位"] == "newer"


def test_project_apply_missing_field_to_null_reports_diff(service, project):
    assert "用户单位" not in Project.load(project.root).fields
    request = {"project": str(project.root), "fields": {"用户单位": None}}
    before = snapshot(project.root)

    dry_run = service.call("project_apply", dict(request, dry_run=True))
    assert_success(dry_run, "not_committed")
    assert dry_run["data"]["changed"] is True
    assert dry_run["data"]["diff"]["changed_paths"] == ["fields.用户单位"]
    assert dry_run["data"]["diff"]["before_revision"] != dry_run["data"]["diff"]["after_revision"]
    assert snapshot(project.root) == before
    assert "用户单位" not in Project.load(project.root).fields

    committed = service.call("project_apply", request)
    assert_success(committed, "committed")
    assert committed["data"]["changed"] is True
    assert committed["data"]["diff"] == dry_run["data"]["diff"]
    stored = Project.load(project.root)
    assert "用户单位" in stored.fields
    assert stored.fields["用户单位"] is None


def test_project_apply_dry_run_matches_real_preflight(service, project):
    collision = project.root / "功能-collision.json"
    collision.write_bytes(b"unbound")
    request = {
        "project": str(project.root),
        "modules": {"create": [{"data_type": "feature", "data": {"name": "collision"}}]},
    }
    before = snapshot(project.root)
    dry_run = service.call("project_apply", dict(request, dry_run=True))
    real = service.call("project_apply", request)
    assert_error(dry_run, "VALIDATION_FAILED")
    assert_error(real, "VALIDATION_FAILED")
    assert snapshot(project.root) == before
    assert not (project.root / ".prism.lock").exists()


def test_project_apply_empty_and_noop(service, project):
    stored = Project.load(project.root)
    stale_revision = stored.loaded_revision
    stored.fields["产品名"] = "same"
    stored.save(expected_revision=stored.loaded_revision)
    stored = Project.load(project.root)
    before = snapshot(project.root)
    for request in (
        {"project": str(project.root)},
        {"project": str(project.root), "fields": {"产品名": stored.fields["产品名"]},
         "modules": {"remove": [], "update": [], "create": [], "reorder": []}},
    ):
        response = service.call("project_apply", request)
        assert_success(response, "not_committed")
        assert response["data"]["changed"] is False
        assert response["revision"] == stored.loaded_revision
        assert snapshot(project.root) == before

    for request in (
        {"project": str(project.root), "expected_revision": stale_revision},
        {"project": str(project.root), "expected_revision": stale_revision,
         "fields": {"产品名": stored.fields["产品名"]}},
    ):
        response = service.call("project_apply", request)
        assert_error(response, "REVISION_CONFLICT")
        assert snapshot(project.root) == before


def test_project_apply_preserves_latest_outputs(service, project):
    stored = Project.load(project.root)
    stored.outputs = batch(stored.loaded_revision)
    stored.save(expected_revision=stored.loaded_revision)
    expected = Project.load(project.root).outputs
    response = service.call("project_apply", {
        "project": str(project.root), "fields": {"产品名": "changed"},
    })
    assert_success(response, "committed")
    assert Project.load(project.root).outputs == expected


def test_project_apply_cleanup_success(service, project):
    stored = Project.load(project.root)
    module_id = stored.features[0].module_id
    old_file = stored.root / stored._module_files[module_id]["file"]
    response = service.call("project_apply", {
        "project": str(project.root),
        "modules": {"remove": [{"module_id": module_id, "delete_file": True}]},
    })
    assert_success(response, "committed")
    assert not old_file.exists()
    assert not Project.load(project.root).features


def test_project_apply_cleanup_failure_is_committed_warning(service, project, monkeypatch):
    stored = Project.load(project.root)
    module_id = stored.features[0].module_id
    old_file = stored.root / stored._module_files[module_id]["file"]
    original_unlink = Path.unlink

    def fail_old(path, *args, **kwargs):
        if path == old_file:
            raise PermissionError("injected cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_old)
    response = service.call("project_apply", {
        "project": str(project.root),
        "modules": {"remove": [{"module_id": module_id, "delete_file": True}]},
    })
    assert_success(response, "committed")
    assert response["status"] == "success_with_warnings"
    assert "MODULE_FILE_CLEANUP_FAILED" in {
        warning["code"] for warning in response["warnings"]
    }
    assert not Project.load(project.root).features
    assert old_file.exists()


def test_project_apply_storage_failure_rolls_back(service, project, monkeypatch):
    before = snapshot(project.root)
    original_replace = storage.os.replace
    calls = []

    def fail_write_once(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise PermissionError("injected write failure")
        return original_replace(source, target)

    monkeypatch.setattr(storage.os, "replace", fail_write_once)
    response = service.call("project_apply", {
        "project": str(project.root), "fields": {"产品名": "changed"},
    })
    assert_error(response, "PROJECT_LOAD_FAILED")
    assert response["error"]["details"]["rollback"] == "succeeded"
    assert snapshot(project.root) == before


def test_project_apply_rollback_failure_keeps_recovery(service, project, monkeypatch):
    original_replace = storage.os.replace
    calls = []

    def fail_write_and_rollback(source, target):
        calls.append(target)
        if len(calls) >= 2:
            raise PermissionError("injected write or rollback failure")
        return original_replace(source, target)

    monkeypatch.setattr(storage.os, "replace", fail_write_and_rollback)
    response = service.call("project_apply", {
        "project": str(project.root), "fields": {"产品名": "changed"},
    })
    assert_error(response, "STORAGE_RECOVERY_REQUIRED", "unknown")
    assert response["error"]["retryable"] is False
    assert list(project.root.glob(".prism-save-*"))


def test_project_apply_project_load_error_boundaries(service, project, tmp_path):
    missing = service.call("project_apply", {"project": str(tmp_path / "missing")})
    assert_error(missing, "PROJECT_NOT_FOUND")

    empty = tmp_path / "empty"
    empty.mkdir()
    not_project = service.call("project_apply", {"project": str(empty)})
    assert_error(not_project, "PROJECT_NOT_FOUND")

    basic = project.root / "基本数据.json"
    basic.write_text("{broken", encoding="utf-8")
    broken = service.call("project_apply", {"project": str(project.root)})
    assert_error(broken, "PROJECT_LOAD_FAILED")


def test_project_apply_contract_parity(service, project):
    assert "project_apply" in IMPLEMENTED_TOOLS
    assert CONTRACT["tools"]["project_apply"]["implemented"] is True
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(REQUEST_SCHEMAS["project_apply"],
                                                format_checker=jsonschema.FormatChecker())
    validator.validate({"project": str(project.root), "modules": {"create": []}})

    success = service.call("project_apply", {"project": str(project.root)})
    assert_success(success, "not_committed")
    failure = service.call("project_apply", {
        "project": str(project.root), "modules": {"update": [{
            "module_id": Project.load(project.root).features[0].module_id,
            "data": {"direction": "input"},
        }]},
    })
    assert_error(failure, "INVALID_FIELD")
