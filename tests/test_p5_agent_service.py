"""P5 acceptance tests for Agent Service project and module write tools."""

import json
from pathlib import Path

import pytest
from p2_helpers import batch, populated

from prism.agent_contract import CONTRACT, IMPLEMENTED_TOOLS, REQUEST_SCHEMAS, TOOL_RESULT_SCHEMAS
from prism.agent_service import AgentService
from prism.project import Project


P5_TOOLS = {
    "project_create", "project_set_fields", "project_validate", "project_recalculate",
    "module_create", "module_update", "module_remove", "module_reorder",
    "project_set_model", "template_configure",
}


@pytest.fixture
def service():
    return AgentService(application_root=Path(__file__).parents[1])


@pytest.fixture
def project(tmp_path):
    return populated(tmp_path / "agent-project")


def assert_success(tool, response, commit_state=None):
    assert response["ok"] is True
    assert response["error"] is None
    if commit_state is not None:
        assert response["commit_state"] == commit_state
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator(TOOL_RESULT_SCHEMAS[tool]).validate(response["data"])
    jsonschema.Draft202012Validator(CONTRACT["tools"][tool]["response_schema"]).validate(response)


def assert_error(response, code):
    assert response["ok"] is False
    assert response["status"] == "failed"
    assert response["commit_state"] == "not_committed"
    assert response["error"]["code"] == code


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file() and path.name != ".prism.lock"
    }


def test_p5_tools_are_published_as_implemented():
    assert P5_TOOLS <= set(IMPLEMENTED_TOOLS)
    assert all(CONTRACT["tools"][name]["implemented"] for name in P5_TOOLS)


def test_side_effect_classes_are_disjoint_and_cover_every_tool():
    classes = CONTRACT["side_effect_classes"]
    classified = [tool for tools in classes.values() for tool in tools]
    assert set(classified) == set(REQUEST_SCHEMAS)
    assert len(classified) == len(set(classified))
    assert P5_TOOLS - {"project_validate"} <= set(classes["business_transaction"])
    assert classes["validation_only"] == ["project_validate"]


def test_project_create_defaults_dry_run_and_rejects_revision(service, tmp_path):
    root = tmp_path / "new-project"
    dry_run = service.call("project_create", {
        "project": str(root), "name": "P5 project", "dry_run": True,
    })
    assert_success("project_create", dry_run, "not_committed")
    assert dry_run["data"]["project"]["model"] == {"id": "gjb5000-simplified", "version": "3.0"}
    assert dry_run["data"]["project"]["schedule"] == {}
    assert not root.exists()

    created = service.call("project_create", {"project": str(root), "name": "P5 project"})
    assert_success("project_create", created, "committed")
    assert Project.load(root).name == "P5 project"

    invalid = service.call("project_create", {
        "project": str(tmp_path / "other"), "name": "Other", "expected_revision": created["revision"],
    })
    assert_error(invalid, "INVALID_ARGUMENT")


@pytest.mark.parametrize("dry_run", [True, False])
def test_project_create_rejects_blank_name_before_writing(service, tmp_path, dry_run):
    root = tmp_path / ("blank-dry-run" if dry_run else "blank-real")
    result = service.call("project_create", {
        "project": str(root), "name": "   ", "dry_run": dry_run,
    })
    assert_error(result, "VALIDATION_FAILED")
    assert not root.exists()


def test_field_patch_semantics_revision_dry_run_readonly_and_outputs(service, project):
    stored = Project.load(project.root)
    stored.outputs = batch(stored.loaded_revision)
    stored.save(expected_revision=stored.loaded_revision)
    before = Project.load(project.root)

    result = service.call("project_set_fields", {
        "project": str(project.root), "expected_revision": before.loaded_revision,
        "fields": {"产品名": "", "用户单位": None},
    })
    assert_success("project_set_fields", result, "committed")
    reloaded = Project.load(project.root)
    assert reloaded.fields["产品名"] == ""
    assert reloaded.fields["用户单位"] is None
    assert reloaded.outputs == before.outputs

    files = snapshot(project.root)
    dry_run = service.call("project_set_fields", {
        "project": str(project.root), "fields": {"产品名": "candidate"}, "dry_run": True,
    })
    assert_success("project_set_fields", dry_run, "not_committed")
    assert dry_run["data"]["changed"] is True
    assert snapshot(project.root) == files

    conflict = service.call("project_set_fields", {
        "project": str(project.root), "expected_revision": before.loaded_revision,
        "fields": {"产品名": "stale"},
    })
    assert_error(conflict, "REVISION_CONFLICT")
    for fields in ({"任务书评审日期": "2026-09-17"}, {"outputs": {}}):
        rejected = service.call("project_set_fields", {"project": str(project.root), "fields": fields})
        assert_error(rejected, "DERIVED_FIELD_READONLY")


def test_module_crud_stable_identity_replace_arrays_and_category_guard(service, project):
    created = service.call("module_create", {
        "project": str(project.root), "data_type": "feature",
        "data": {
            "name": "new feature", "task_description": "task", "requirement_description": "requirement",
            "unit_tests": [{"name": "case", "purpose": "purpose", "procedure": "step",
                            "expected_result": "expected", "result": "passed"}],
        },
    })
    assert_success("module_create", created, "committed")
    module_id = created["data"]["module"]["module_id"]

    updated = service.call("module_update", {
        "project": str(project.root), "module_id": module_id,
        "data": {"requirement_description": "changed", "unit_tests": []},
    })
    assert_success("module_update", updated, "committed")
    assert updated["data"]["module"]["module_id"] == module_id
    assert updated["data"]["module"]["name"] == "new feature"
    assert updated["data"]["module"]["unit_tests"] == []

    invalid = service.call("module_update", {
        "project": str(project.root), "module_id": module_id, "data": {"direction": "input"},
    })
    assert_error(invalid, "INVALID_FIELD")
    null_value = service.call("module_update", {
        "project": str(project.root), "module_id": module_id, "data": {"name": None},
    })
    assert_error(null_value, "INVALID_ARGUMENT")
    duplicate = service.call("module_update", {
        "project": str(project.root), "module_id": module_id, "data": {"name": "module"},
    })
    assert_error(duplicate, "MODULE_NAME_CONFLICT")

    files = snapshot(project.root)
    preview = service.call("module_remove", {
        "project": str(project.root), "module_id": module_id, "delete_file": True, "dry_run": True,
    })
    assert_success("module_remove", preview, "not_committed")
    assert snapshot(project.root) == files
    removed = service.call("module_remove", {
        "project": str(project.root), "module_id": module_id, "delete_file": True,
    })
    assert_success("module_remove", removed, "committed")
    assert module_id not in [item.module_id for item in Project.load(project.root).features]


@pytest.mark.parametrize("tool", ["module_remove", "project_apply"])
@pytest.mark.parametrize("options,keep_file", [
    pytest.param({}, True, id="omitted"),
    pytest.param({"delete_file": False}, True, id="false"),
    pytest.param({"delete_file": True}, False, id="true"),
])
def test_module_removal_file_policy(service, project, tool, options, keep_file):
    module, = project.modules
    path = project.root / module["file"]
    original = path.read_bytes()
    removal = {"module_id": module["module_id"], **options}
    payload = removal if tool == "module_remove" else {"modules": {"remove": [removal]}}

    response = service.call(tool, {"project": str(project.root), **payload})

    assert_success(tool, response, "committed")
    assert response["data"]["changed"] is True
    reopened = Project.load(project.root)
    assert reopened.loaded_revision == response["revision"]
    assert reopened.modules == []
    if keep_file:
        assert path.read_bytes() == original
    else:
        assert not path.exists()


@pytest.mark.parametrize("tool", ["module_create", "module_update"])
def test_module_dry_run_and_real_share_filename_collision_preflight(service, project, tool):
    collision = project.root / "功能-collision.json"
    collision.write_bytes(b"unbound")
    before = snapshot(project.root)
    stored = Project.load(project.root)
    request = {
        "project": str(project.root),
        "data_type": "feature",
        "data": {"name": "collision", "requirement_description": "requirement"},
    }
    if tool == "module_update":
        request = {
            "project": str(project.root),
            "module_id": stored.features[0].module_id,
            "data": {"name": "collision"},
        }

    dry_run = service.call(tool, {**request, "dry_run": True})
    assert_error(dry_run, "VALIDATION_FAILED")
    assert snapshot(project.root) == before

    real = service.call(tool, request)
    assert_error(real, "VALIDATION_FAILED")
    assert snapshot(project.root) == before


def test_write_tools_normalize_malformed_project_load_failure(service, project):
    stored = Project.load(project.root)
    payload_path = project.root / "基本数据.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["fields"] = []
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    calls = [
        ("project_get", {"project": str(project.root)}),
        ("project_set_fields", {"project": str(project.root), "fields": {"产品名": "value"}}),
        ("module_update", {
            "project": str(project.root), "module_id": stored.features[0].module_id,
            "data": {"name": "renamed"},
        }),
        ("template_configure", {
            "project": str(project.root), "templates": {"mode": "default", "enabled_ids": []},
        }),
    ]
    for tool, request in calls:
        assert_error(service.call(tool, request), "PROJECT_LOAD_FAILED")


def test_read_and_write_tools_agree_on_non_project_directory(service, tmp_path):
    root = tmp_path / "not-a-project"
    root.mkdir()

    read = service.call("project_get", {"project": str(root)})
    write = service.call("project_set_fields", {
        "project": str(root), "fields": {"产品名": "value"},
    })

    assert_error(read, "PROJECT_NOT_FOUND")
    assert_error(write, "PROJECT_NOT_FOUND")


def test_module_reorder_requires_full_category(service, project):
    second = service.call("module_create", {
        "project": str(project.root), "data_type": "feature",
        "data": {"name": "second", "requirement_description": "requirement"},
    })
    assert_success("module_create", second, "committed")
    ids = [item.module_id for item in Project.load(project.root).features]

    invalid = service.call("module_reorder", {
        "project": str(project.root), "data_type": "feature", "module_ids": ids[:1],
    })
    assert_error(invalid, "MODULE_ORDER_INVALID")
    reordered = service.call("module_reorder", {
        "project": str(project.root), "data_type": "feature", "module_ids": list(reversed(ids)),
    })
    assert_success("module_reorder", reordered, "committed")
    assert [item.module_id for item in Project.load(project.root).features] == list(reversed(ids))


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


def test_model_switch_and_recalculate_rebuild_derived_schedule(tmp_path, project):
    app_root = Path(__file__).parents[1]
    user_models = tmp_path / "models"
    write_model(user_models)
    service = AgentService(application_root=app_root,
                           model_roots=(app_root / "assets" / "models", user_models))
    stored = Project.load(project.root)
    stored.schedule = {"project_start": "2026-09-17", "work_hours": {"L01": 11, "R01": 12}}
    stored.save(expected_revision=stored.loaded_revision)

    switched = service.call("project_set_model", {
        "project": str(project.root), "model": {"id": "alternate", "version": "1.0"},
    })
    assert_success("project_set_model", switched, "committed")
    schedule = switched["data"]["schedule"]
    assert schedule["work_hours"] == {"L01": 11}
    assert [phase["id"] for phase in schedule["phases"]] == ["launch", "verification"]
    assert {"schedule.launch.start", "schedule.verification.finish"} <= set(schedule["dates"])
    assert not any("requirements" in key for key in schedule["dates"])

    stored = Project.load(project.root)
    stored.schedule["dates"] = {"stale": "value"}
    stored.schedule["phases"] = []
    stored.save(expected_revision=stored.loaded_revision)
    recalculated = service.call("project_recalculate", {"project": str(project.root)})
    assert_success("project_recalculate", recalculated, "committed")
    assert "stale" not in recalculated["data"]["schedule"]["dates"]


def test_unscheduled_model_switch_and_validation_are_saveable(tmp_path, project):
    app_root = Path(__file__).parents[1]
    user_models = tmp_path / "models"
    write_model(user_models)
    service = AgentService(application_root=app_root,
                           model_roots=(app_root / "assets" / "models", user_models))
    switched = service.call("project_set_model", {
        "project": str(project.root), "model": {"id": "alternate", "version": "1.0"},
    })
    assert_success("project_set_model", switched, "committed")
    assert switched["status"] == "success_with_warnings"
    assert {warning["code"] for warning in switched["warnings"]} == {"PROJECT_UNSCHEDULED"}
    assert Project.load(project.root).model_id == "alternate"

    checked = service.call("project_validate", {"project": str(project.root)})
    assert_success("project_validate", checked, "not_committed")
    assert checked["data"]["valid"] is False
    assert "PROJECT_UNSCHEDULED" in {issue["code"] for issue in checked["data"]["issues"]}


def test_template_configuration_validates_ids_and_supports_empty_custom(service, project):
    templates = service.call("template_list", {"project": str(project.root)})
    template_id = templates["data"]["templates"][0]["template_id"]

    empty = service.call("template_configure", {
        "project": str(project.root), "templates": {"mode": "custom", "enabled_ids": []},
    })
    assert_success("template_configure", empty, "committed")
    selected = service.call("template_configure", {
        "project": str(project.root), "templates": {"mode": "custom", "enabled_ids": [template_id]},
    })
    assert_success("template_configure", selected, "committed")
    assert Project.load(project.root).templates["enabled_ids"] == [template_id]

    missing = service.call("template_configure", {
        "project": str(project.root),
        "templates": {"mode": "custom", "enabled_ids": ["asset:templates/docx/missing.docx"]},
    })
    assert_error(missing, "TEMPLATE_NOT_FOUND")
    default = service.call("template_configure", {
        "project": str(project.root), "templates": {"mode": "default", "enabled_ids": [template_id]},
    })
    assert_success("template_configure", default, "committed")
    assert default["data"]["templates"] == {"mode": "default", "enabled_ids": []}
