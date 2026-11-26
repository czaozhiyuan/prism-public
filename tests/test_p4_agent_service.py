"""P4 acceptance tests for the transport-neutral read-only Agent Service."""

import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from p2_helpers import populated

from prism.agent_contract import (
    CONTRACT,
    ERRORS,
    GENERATION_RESULT_SCHEMA,
    IMPLEMENTED_TOOLS,
    MODULE_SCHEMA,
    OUTPUT_RESULT_SCHEMA,
    OUTPUTS_SCHEMA,
    REQUEST_SCHEMAS,
    TOOL_RESULT_SCHEMAS,
    cli_exit_code,
)
from prism.agent_service import AgentFault, AgentService, validate_request
from prism.lifecycle import load_model
from prism.project import Project
from prism.schema import DATE_FIELDS


@pytest.fixture
def service():
    return AgentService(application_root=Path(__file__).parents[1])


@pytest.fixture
def project(tmp_path):
    return populated(tmp_path / "agent-project")


def assert_success(response):
    assert response["ok"] is True
    assert response["status"] in ("success", "success_with_warnings")
    assert response["commit_state"] == "not_committed"
    assert response["error"] is None
    json.dumps(response, ensure_ascii=False, allow_nan=False)


def test_system_schema_and_field_description_are_machine_readable(service):
    info = service.call("prism_info")
    assert_success(info)
    assert info["data"]["product_version"] == "2.0.0"
    assert info["data"]["agent_api_version"] == 2
    assert info["data"]["project_format_version"] == 7
    assert info["data"]["module_format_version"] == 1
    assert info["data"]["model_format_version"] == 3
    assert info["data"]["build_commit"] == "unknown" or len(info["data"]["build_commit"]) == 40
    assert info["data"]["assets_manifest"].startswith("sha256:")

    index = service.call("schema_get")
    assert_success(index)
    assert index["data"]["schemas"] == ["agent-api-v2", "project-basic-v7", "project-module-v1", "project-people-v1", "project-test-environment-v1"]
    contract = service.call("schema_get", {"name": "agent-api-v2"})
    assert_success(contract)
    assert set(contract["data"]["schema"]["tools"]) == set(REQUEST_SCHEMAS)
    for name in ("project-basic-v7", "project-module-v1"):
        result = service.call("schema_get", {"name": name})
        assert_success(result)
        assert result["data"]["schema"]["$schema"].endswith("2020-12/schema")

    fields = service.call("project_describe_fields")
    assert_success(fields)
    derived = fields["data"]["field_sections"][-1]["fields"]
    assert {item["name"] for item in derived} == set(DATE_FIELDS)
    assert all(item["derived"] and not item["writable"] for item in derived)
    assert fields["data"]["outputs"] == {"writable": False}


def test_project_get_is_complete_stable_and_includes_revision_and_module_id(service, project):
    result = service.call("project_get", {"project": str(project.root)})
    assert_success(result)
    assert result["revision"] == Project.load(project.root).loaded_revision
    state = result["data"]["project"]
    assert set(state) == {"format_version", "data_type", "name", "model", "fields", "schedule",
                          "templates", "outputs", "modules"}
    assert state["format_version"] == 7
    assert state["modules"][0]["module_id"] == project.features[0].module_id
    assert state["modules"][0]["data_type"] == "feature"
    assert state["outputs"] == {}

    second = service.call("project_get", {"project": str(project.root)})
    assert second["revision"] == result["revision"]
    assert second["data"] == result["data"]


def test_module_list_get_filter_and_not_found(service, project):
    listed = service.call("module_list", {"project": str(project.root), "data_type": "feature"})
    assert_success(listed)
    assert len(listed["data"]["modules"]) == 1
    module_id = listed["data"]["modules"][0]["module_id"]
    fetched = service.call("module_get", {"project": str(project.root), "module_id": module_id})
    assert_success(fetched)
    assert fetched["data"]["module"] == listed["data"]["modules"][0]
    missing = service.call("module_get", {"project": str(project.root),
                                          "module_id": "00000000-0000-0000-0000-000000000000"})
    assert missing["error"]["code"] == "MODULE_NOT_FOUND"
    malformed = service.call("module_get", {"project": str(project.root), "module_id": "missing"})
    assert malformed["error"]["code"] == "INVALID_ARGUMENT"


def test_model_and_template_discovery_use_stable_identities(service, project):
    models = service.call("model_list")
    assert_success(models)
    identities = {(item["id"], item["version"]) for item in models["data"]["models"]}
    assert ("gjb5000-simplified", "3.0") in identities
    model = service.call("model_get", {"id": "gjb5000-simplified", "version": "3.0"})
    assert_success(model)
    assert model["data"]["model"]["phases"]
    missing = service.call("model_get", {"id": "none", "version": "1"})
    assert missing["error"]["code"] == "MODEL_NOT_FOUND"

    templates = service.call("template_list", {"project": str(project.root)})
    assert_success(templates)
    values = templates["data"]["templates"]
    assert values
    assert all(item["template_id"].startswith(("asset:", "model:")) for item in values)
    assert len({item["template_id"] for item in values}) == len(values)


def test_duplicate_installed_model_identity_is_a_stable_error(tmp_path):
    repository = Path(__file__).parents[1]
    first = tmp_path / "models-one"
    second = tmp_path / "models-two"
    source = repository / "assets" / "models" / "gjb5000-simplified"
    shutil.copytree(str(source), str(first / source.name))
    shutil.copytree(str(source), str(second / source.name))
    service = AgentService(application_root=repository, model_roots=(first, second))
    result = service.call("model_list")
    assert result["error"]["code"] == "MODEL_INCOMPATIBLE"
    assert len(result["error"]["details"]["paths"]) == 2


@pytest.mark.parametrize("payload", [
    [],
    {"format_version": 1, "id": "bad", "version": "1", "name": "bad", "phases": ["not-an-object"]},
    {"format_version": 1, "id": "bad", "version": "1", "name": "bad",
     "phases": [{"id": "p", "name": "p", "default_workdays": {}}]},
])
def test_malformed_installed_models_always_use_model_incompatible(tmp_path, payload):
    root = tmp_path / "models" / "bad"
    root.mkdir(parents=True)
    (root / "model.json").write_text(json.dumps(payload), encoding="utf-8")
    service = AgentService(application_root=Path(__file__).parents[1], model_roots=(root.parent,))
    result = service.call("model_list")
    assert result["error"]["code"] == "MODEL_INCOMPATIBLE"


@pytest.mark.parametrize("payload,code", [
    ({"project": "relative"}, "INVALID_ARGUMENT"),
    ({"project": "X:\\does-not-exist\\prism"}, "PROJECT_NOT_FOUND"),
    ({"project": None}, "INVALID_ARGUMENT"),
    ({"project": "X:\\x", "unknown": True}, "INVALID_ARGUMENT"),
])
def test_invalid_project_and_request_shapes_are_structured(service, payload, code):
    result = service.call("project_get", payload)
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"]["code"] == code
    assert result["error"]["commit_state"] == "not_committed"


def test_runtime_rejects_contract_readonly_fields_before_future_tool_dispatch(service, project):
    derived = service.call("project_set_fields", {
        "project": str(project.root), "fields": {DATE_FIELDS[0]: "2026"},
    })
    assert derived["error"]["code"] == "DERIVED_FIELD_READONLY"
    schedule = service.call("project_apply", {
        "project": str(project.root), "schedule": {"dates": {}},
    })
    assert schedule["error"]["code"] == "DERIVED_FIELD_READONLY"
    outputs = service.call("project_apply", {
        "project": str(project.root), "outputs": {},
    })
    assert outputs["error"]["code"] == "DERIVED_FIELD_READONLY"


def test_project_generate_selection_contract_is_normalized_and_rejected_consistently(project):
    base = {"project": str(project.root), "dry_run": True}
    assert validate_request("project_generate", base)["mode"] == "enabled"
    assert validate_request("project_generate", dict(base, mode="enabled"))["mode"] == "enabled"
    selected = ["asset:templates/docx/1.软件研制任务书_模板.docx"]
    assert validate_request("project_generate", dict(base, template_ids=selected))["template_ids"] == selected
    with pytest.raises(AgentFault) as conflict:
        validate_request("project_generate", dict(base, mode="enabled", template_ids=selected))
    assert conflict.value.code == "INVALID_ARGUMENT"
    with pytest.raises(AgentFault) as empty:
        validate_request("project_generate", dict(base, template_ids=[]))
    assert empty.value.code == "NO_TEMPLATES_SELECTED"
    duplicate = dict(base, template_ids=selected * 2)
    with pytest.raises(AgentFault) as repeated:
        validate_request("project_generate", duplicate)
    assert repeated.value.code == "INVALID_ARGUMENT"


def test_project_generate_request_failures_match_published_response_schema(service, project):
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(
        CONTRACT["tools"]["project_generate"]["response_schema"])
    selected = ["asset:templates/docx/1.软件研制任务书_模板.docx"]
    calls = [
        {"project": str(project.root), "mode": "enabled", "template_ids": selected},
        {"project": str(project.root), "template_ids": ["bad.docx"]},
        {"project": "", "mode": "enabled"},
    ]
    for payload in calls:
        response = service.call("project_generate", payload)
        assert response["ok"] is False
        assert response["data"] is None
        assert response["error"]["code"] == "INVALID_ARGUMENT"
        validator.validate(response)


@pytest.mark.parametrize("payload", [
    {"project": "C:\\p", "schedule": {"project_start": "abc"}},
    {"project": "C:\\p", "schedule": {"project_start": "2026-02-30"}},
    {"project": "C:\\p", "schedule": {"work_hours": {"design": 0}}},
    {"project": "C:\\p", "schedule": {"work_hours": {"design": None}}},
    {"project": "C:\\p", "model": {"id": "../bad", "version": "1"}},
])
def test_runtime_enforces_published_format_minimum_and_model_identity(payload):
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(
        REQUEST_SCHEMAS["project_apply"], format_checker=jsonschema.FormatChecker())
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(payload)
    with pytest.raises(AgentFault) as caught:
        validate_request("project_apply", payload)
    assert caught.value.code == "INVALID_ARGUMENT"


def test_runtime_enforces_template_and_module_identity(project):
    jsonschema = pytest.importorskip("jsonschema")
    template_payload = {"project": str(project.root), "template_ids": ["bad.docx"]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(REQUEST_SCHEMAS["project_generate"]).validate(template_payload)
    with pytest.raises(AgentFault) as template:
        validate_request("project_generate", template_payload)
    assert template.value.code == "INVALID_ARGUMENT"
    module_payload = {"project": str(project.root), "module_id": "not-a-uuid"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(REQUEST_SCHEMAS["module_get"]).validate(module_payload)
    with pytest.raises(AgentFault) as module:
        validate_request("module_get", module_payload)
    assert module.value.code == "INVALID_ARGUMENT"


def test_frozen_contract_covers_all_tools_errors_defaults_and_generation_states(service):
    assert set(IMPLEMENTED_TOOLS) == set(REQUEST_SCHEMAS)
    required_errors = {
        "PROJECT_NOT_FOUND", "PROJECT_FORMAT_UNSUPPORTED", "PROJECT_LOAD_FAILED",
        "STORAGE_RECOVERY_REQUIRED", "PROJECT_LOCKED", "GENERATION_BUSY", "INVALID_ARGUMENT",
        "INVALID_FIELD", "DERIVED_FIELD_READONLY", "REVISION_CONFLICT", "MODULE_NOT_FOUND",
        "MODULE_NAME_CONFLICT", "MODULE_ID_CONFLICT", "MODULE_ORDER_INVALID",
        "MODULE_FILE_CLEANUP_FAILED", "MODEL_NOT_FOUND",
        "MODEL_INCOMPATIBLE", "TEMPLATE_NOT_FOUND", "TEMPLATE_ID_CONFLICT", "NO_TEMPLATES_SELECTED",
        "OUTPUT_NAME_CONFLICT", "VALIDATION_FAILED", "GENERATION_FAILED", "WORD_UNAVAILABLE",
        "WORD_TIMEOUT", "WORD_FINALIZE_FAILED", "WORD_PAGINATION_UNSTABLE", "PATH_OUTSIDE_PROJECT", "OUTPUT_RECORD_SAVE_FAILED",
        "PROJECT_UNSCHEDULED",
    }
    assert set(ERRORS) == required_errors
    assert CONTRACT["request_rules"]["no_op"].startswith("validates")
    assert CONTRACT["project_apply_order"][-1].endswith("single_commit")
    assert "INVALID_FIELD" in CONTRACT["module_field_rules"]["update"]
    cases = {row["case"]: row for row in CONTRACT["generation_state_table"]}
    assert cases["preflight_rejected"]["commit_state"] == "not_committed"
    assert cases["partial_failure"]["records_saved"] is True
    assert cases["review_checklist_failed"]["status"] == "partial_failure"
    assert cases["record_save_failed"]["commit_state"] == "committed"
    assert CONTRACT["generation_examples"]["partial_failure"]["ok"] is False
    assert CONTRACT["generation_examples"]["word_warning_success"]["ok"] is True
    assert CONTRACT["generation_examples"]["old_checklist_not_reused"]["review_checklist"] is None
    assert CONTRACT["generation_examples"]["partial_failure"]["review_checklist"] == "outputs/待补充清单.xlsx"
    assert CONTRACT["generation_examples"]["record_save_failed"]["review_checklist"] == "outputs/待补充清单.xlsx"
    checklist_failure = CONTRACT["generation_examples"]["review_checklist_failed"]
    assert checklist_failure["review_checklist"] is None
    assert checklist_failure["error"]["details"]["stage"] == "review_checklist"
    dry_run = CONTRACT["generation_examples"]["dry_run_preflight_success"]
    assert dry_run["data"]["preflight"] is True
    assert dry_run["commit_state"] == "not_committed"
    assert dry_run["records_saved"] is False
    assert CONTRACT["generation_examples"]["unscheduled_preflight_rejected"]["error"]["code"] == "PROJECT_UNSCHEDULED"
    assert CONTRACT["project_create_initial_state"] == {
        "schedule": {}, "templates": {"mode": "default", "enabled_ids": []}, "outputs": {},
    }
    assert {case["outcome"] for case in CONTRACT["template_selection_cases"]} >= {
        "INVALID_ARGUMENT", "TEMPLATE_NOT_FOUND", "allowed_for_this_generation",
        "OUTPUT_NAME_CONFLICT", "NO_TEMPLATES_SELECTED",
    }
    assert ERRORS["REVISION_CONFLICT"][0] is False
    assert CONTRACT["response_examples"]["success_with_warnings"]["warnings"][0]["code"] == "WORD_UNAVAILABLE"
    assert cli_exit_code(CONTRACT["response_examples"]["success_with_warnings"]) == 0
    assert cli_exit_code(CONTRACT["response_examples"]["failed"]) != 0
    for code, metadata in CONTRACT["errors"].items():
        assert metadata["cli_exit_code"] > 0, code
    jsonschema = pytest.importorskip("jsonschema")
    for schema in REQUEST_SCHEMAS.values():
        jsonschema.Draft202012Validator.check_schema(schema)
    for schema in TOOL_RESULT_SCHEMAS.values():
        jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator.check_schema(CONTRACT["response_schema"])
    jsonschema.Draft202012Validator.check_schema(CONTRACT["error_schema"])
    response_validator = jsonschema.Draft202012Validator(CONTRACT["response_schema"])
    for example in CONTRACT["response_examples"].values():
        response_validator.validate(example)
    for example in CONTRACT["generation_examples"].values():
        response_validator.validate(example)
        jsonschema.Draft202012Validator(TOOL_RESULT_SCHEMAS["project_generate"]).validate(example["data"])
        jsonschema.Draft202012Validator(
            CONTRACT["tools"]["project_generate"]["response_schema"]
        ).validate(example)
    for example in CONTRACT["transaction_examples"].values():
        response_validator.validate(example)
    for name in ("rollback_succeeded", "rollback_failed"):
        jsonschema.Draft202012Validator(
            CONTRACT["tools"]["project_apply"]["response_schema"]
        ).validate(CONTRACT["transaction_examples"][name])
    cleanup = CONTRACT["transaction_examples"]["module_file_cleanup_failed"]
    jsonschema.Draft202012Validator(
        CONTRACT["tools"]["module_remove"]["response_schema"]
    ).validate(cleanup)
    assert cli_exit_code(cleanup) == 0
    rollback_failed = CONTRACT["transaction_examples"]["rollback_failed"]
    assert rollback_failed["error"]["retryable"] is False
    assert rollback_failed["commit_state"] == "unknown"
    model_switch = CONTRACT["unscheduled_examples"]["model_switch"]
    jsonschema.Draft202012Validator(
        CONTRACT["tools"]["project_set_model"]["response_schema"]
    ).validate(model_switch)
    assert cli_exit_code(model_switch) == 0
    field_save = CONTRACT["unscheduled_examples"]["field_save"]
    jsonschema.Draft202012Validator(
        CONTRACT["tools"]["project_set_fields"]["response_schema"]
    ).validate(field_save)
    assert cli_exit_code(field_save) == 0
    invalid_error = dict(CONTRACT["response_examples"]["failed"])
    invalid_error["error"] = {"foo": "bar"}
    with pytest.raises(jsonschema.ValidationError):
        response_validator.validate(invalid_error)
    empty_warning = dict(CONTRACT["response_examples"]["success_with_warnings"])
    empty_warning["warnings"] = []
    with pytest.raises(jsonschema.ValidationError):
        response_validator.validate(empty_warning)
    invalid_revision = dict(CONTRACT["response_examples"]["success"])
    invalid_revision["revision"] = "not-a-revision"
    with pytest.raises(jsonschema.ValidationError):
        response_validator.validate(invalid_revision)
    invalid_status = dict(CONTRACT["response_examples"]["success"])
    invalid_status["status"] = "done"
    with pytest.raises(jsonschema.ValidationError):
        response_validator.validate(invalid_status)
    invalid_checklist = dict(CONTRACT["generation_examples"]["word_warning_success"])
    invalid_checklist["review_checklist"] = "C:\\outside.xlsx"
    with pytest.raises(jsonschema.ValidationError):
        response_validator.validate(invalid_checklist)
    module_validator = jsonschema.Draft202012Validator(MODULE_SCHEMA)
    valid_module = {
        "module_format_version": 1, "data_type": "feature",
        "module_id": "00000000-0000-0000-0000-000000000000", "order": 0, "name": "x",
    }
    module_validator.validate(valid_module)
    invalid_category_field = {**valid_module, "direction": "input"}
    with pytest.raises(jsonschema.ValidationError):
        module_validator.validate(invalid_category_field)
    for invalid in ({**valid_module, "module_id": "bad"},
                    {key: value for key, value in valid_module.items() if key != "module_id"},
                    {**valid_module, "order": "0"}):
        with pytest.raises(jsonschema.ValidationError):
            module_validator.validate(invalid)


def test_module_create_schema_binds_data_to_category():
    jsonschema = pytest.importorskip("jsonschema")
    valid = {"project": "C:\\project", "data_type": "feature", "data": {"name": "Feature A"}}
    invalid = {**valid, "data": {"name": "Feature A", "direction": "input"}}
    validator = jsonschema.Draft202012Validator(REQUEST_SCHEMAS["module_create"])
    validator.validate(valid)
    validate_request("module_create", valid)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)
    with pytest.raises(AgentFault) as caught:
        validate_request("module_create", invalid)
    assert caught.value.code == "INVALID_ARGUMENT"
    empty_name = {**valid, "data": {"name": ""}}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(empty_name)
    with pytest.raises(AgentFault) as caught:
        validate_request("module_create", empty_name)
    assert caught.value.code == "INVALID_ARGUMENT"


def test_module_patch_name_and_text_field_semantics():
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(REQUEST_SCHEMAS["module_update"])
    base = {"project": "C:\\project", "module_id": "00000000-0000-0000-0000-000000000000"}
    for data in ({}, {"task_description": ""}, {"unit_tests": []}):
        payload = {**base, "data": data}
        validator.validate(payload)
        validate_request("module_update", payload)
    for data in ({"name": ""}, {"name": "   "}, {"task_description": None}, {"unit_tests": None}):
        payload = {**base, "data": data}
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(payload)
        with pytest.raises(AgentFault) as caught:
            validate_request("module_update", payload)
        assert caught.value.code == "INVALID_ARGUMENT"


def test_output_result_status_and_time_invariants():
    jsonschema = pytest.importorskip("jsonschema")
    stored = {
        "template_id": "asset:templates/docx/example.docx", "output": "outputs/example.docx",
        "render_status": "success", "word_finalize_status": "success", "message": "", "warnings": [],
    }
    generated = dict(stored)
    jsonschema.Draft202012Validator(OUTPUT_RESULT_SCHEMA).validate(stored)
    jsonschema.Draft202012Validator(GENERATION_RESULT_SCHEMA).validate(generated)
    for schema, value in ((OUTPUT_RESULT_SCHEMA, stored), (GENERATION_RESULT_SCHEMA, generated)):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(schema).validate({**value, "output": None})
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(schema).validate(
                {**value, "render_status": "failed", "output": "outputs/old.docx"})
    outputs_validator = jsonschema.Draft202012Validator(OUTPUTS_SCHEMA)
    batch = {"source_revision": "sha256:" + "0" * 64,
             "time": "2026-09-17T12:34:56", "results": [stored]}
    outputs_validator.validate(batch)
    with pytest.raises(jsonschema.ValidationError):
        outputs_validator.validate({**batch, "time": "2026-09-17 12:34:56"})


def test_implemented_success_data_matches_its_published_schema(service, project):
    jsonschema = pytest.importorskip("jsonschema")
    calls = {
        "prism_info": {},
        "schema_get": {},
        "project_describe_fields": {},
        "project_get": {"project": str(project.root)},
        "module_list": {"project": str(project.root)},
        "module_get": {"project": str(project.root), "module_id": project.features[0].module_id},
        "model_list": {},
        "model_get": {"id": "gjb5000-simplified", "version": "3.0"},
        "template_list": {"project": str(project.root)},
    }
    for tool, payload in calls.items():
        response = service.call(tool, payload)
        assert_success(response)
        jsonschema.Draft202012Validator(TOOL_RESULT_SCHEMAS[tool]).validate(response["data"])
        jsonschema.Draft202012Validator(CONTRACT["tools"][tool]["response_schema"]).validate(response)


def test_tool_response_schemas_accept_normal_failures(service, project):
    jsonschema = pytest.importorskip("jsonschema")
    calls = {
        "module_get": {"project": str(project.root),
                       "module_id": "00000000-0000-0000-0000-000000000000"},
        "model_get": {"id": "not-installed", "version": "1"},
        "project_get": {"project": "relative"},
    }
    expected = {
        "module_get": "MODULE_NOT_FOUND", "model_get": "MODEL_NOT_FOUND",
        "project_get": "INVALID_ARGUMENT",
    }
    for tool, payload in calls.items():
        response = service.call(tool, payload)
        assert response["error"]["code"] == expected[tool]
        assert response["data"] is None
        jsonschema.Draft202012Validator(CONTRACT["tools"][tool]["response_schema"]).validate(response)


def test_prism_info_reports_missing_manifest_as_structured_failure(tmp_path):
    service = AgentService(application_root=tmp_path, model_roots=())
    response = service.call("prism_info")
    assert response["error"]["code"] == "VALIDATION_FAILED"
    assert response["error"]["details"]["path"].endswith("ASSET_MANIFEST.csv")


def test_agent_service_import_does_not_import_cli_mcp_or_tkinter(monkeypatch):
    for name in ("prism.agent_service", "prism.cli", "tkinter"):
        sys.modules.pop(name, None)
    module = importlib.import_module("prism.agent_service")
    assert module.AGENT_API_VERSION == 2
    assert "prism.cli" not in sys.modules
    assert "tkinter" not in sys.modules


def test_gui_and_agent_share_recursive_template_discovery(tmp_path):
    from prism.functional_app import discover_all_templates

    nested = tmp_path / "assets" / "templates" / "docx" / "nested" / "sample.docx"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"fixture")
    model = load_model(Path(__file__).parents[1] / "assets" / "models" / "gjb5000-simplified" / "model.json")
    service = AgentService(application_root=tmp_path, model_roots=())
    agent_paths = {item["template_id"] for item in service.call("template_list")["data"]["templates"]}
    gui_paths = {Path(item[2]).resolve() for item in discover_all_templates(tmp_path, model)}
    assert "asset:templates/docx/nested/sample.docx" in agent_paths
    assert nested.resolve() in gui_paths
