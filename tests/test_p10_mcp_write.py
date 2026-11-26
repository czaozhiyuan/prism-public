"""P10 MCP write-tool contract, protocol, and cross-adapter tests."""

import asyncio
from contextlib import suppress
import io
import json
import os
from pathlib import Path
import shutil
import sys
import threading

import jsonschema
import pytest

from p2_helpers import populated, snapshot
from prism import storage
from prism.agent_contract import CONTRACT
from prism.agent_service import create_service
from prism.generation_lock import GenerationLock
from prism_agent.cli import run as run_cli


pytestmark = pytest.mark.agent_only


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def server_process():
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=os.environ.get("PRISM_MCP_SERVER_PYTHON", sys.executable),
        args=["-m", "prism_agent.mcp_server"],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8"),
        cwd=ROOT,
    )


async def call(client, name, request, expected_ok=True):
    result = await client.call_tool(name, request)
    response = result.structured_content
    assert result.is_error is (not expected_ok), (name, response)
    assert response["ok"] is expected_ok, (name, response)
    assert json.loads(result.content[0].text) == response
    return response


@pytest.mark.anyio
async def test_v5_work_inputs_and_nullable_start_roundtrip_over_stdio(tmp_path):
    from mcp import Client
    request = {'project': str(tmp_path / 'v5-draft')}
    async with Client(server_process(), mode='legacy') as client:
        created = await call(client, 'project_create', dict(request, name='V5 draft'))
        assert created['data']['project']['format_version'] == 7
        assert created['data']['project']['model']['version'] == '3.0'
        await call(client, 'project_apply', dict(request, schedule={'work_hours': {'D11': 10}}))
        draft = await call(client, 'project_get', request)
        assert draft['data']['project']['schedule'] == {'work_hours': {'D11': 10}}
        scheduled = await call(client, 'project_apply', dict(request, schedule={'project_start': '2026-01-05'}))
        assert scheduled['ok']
        await call(client, 'project_apply', dict(request, schedule={'project_start': None}))
        cleared = await call(client, 'project_get', request)
        assert cleared['data']['project']['schedule'] == {'work_hours': {'D11': 10}}


def cli_call(service, name, request):
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = run_cli(
        [name, "--request", json.dumps(request, ensure_ascii=False)],
        lambda **unused: service, stdout, stderr,
    )
    assert stderr.getvalue() == ""
    return code, json.loads(stdout.getvalue())


@pytest.mark.anyio
async def test_p10_all_tools_use_contract_schemas_and_explicit_project():
    from mcp import Client
    from prism_agent.mcp_server import MCP_TOOLS, READ_ONLY_TOOLS, WRITE_TOOLS, create_server

    async with Client(create_server(), mode="legacy") as client:
        listed = await client.list_tools()
    tools = {tool.name: tool for tool in listed.tools}
    assert tuple(tools) == MCP_TOOLS
    assert set(MCP_TOOLS) == set(CONTRACT["tools"])
    assert set(WRITE_TOOLS) == (set(CONTRACT["side_effect_classes"]["business_transaction"])
                                | set(CONTRACT["side_effect_classes"]["generation"]))
    assert not set(WRITE_TOOLS) & set(READ_ONLY_TOOLS)
    for name in WRITE_TOOLS:
        schema = CONTRACT["tools"][name]["request_schema"]
        expected = dict(schema, type="object")
        assert tools[name].input_schema == expected
        branches = schema.get("oneOf", [schema])
        for branch in branches:
            assert "project" in branch["required"]
            assert "project" in branch["properties"]
            if name == "project_create":
                assert "expected_revision" not in branch["properties"]
            else:
                assert "expected_revision" in branch["properties"]
    module_schema = CONTRACT["tools"]["module_create"]["request_schema"]
    examples = [
        {"project": "C:/project", "data_type": "feature", "data": {"name": "feature"}},
        {"project": "C:/project", "data_type": "feature", "data": {}},
        {"project": "C:/project", "data_type": "unknown", "data": {"name": "feature"}},
        {"project": "C:/project", "data_type": "feature", "data": {"name": "feature"}, "extra": 1},
        [],
        None,
    ]
    for value in examples:
        assert jsonschema.Draft202012Validator(module_schema).is_valid(value) == (
            jsonschema.Draft202012Validator(tools["module_create"].input_schema).is_valid(value))


@pytest.mark.anyio
async def test_p10_every_write_requires_project_and_forwards_revision(tmp_path):
    from mcp import Client
    from prism_agent.mcp_server import WRITE_TOOLS, create_server

    service = create_service(application_root=ROOT)
    project = populated(tmp_path / "preconditions")
    path = str(project.root)
    module_id = project.features[0].module_id
    requests = {
        "project_rename": {"project": path, "name": "renamed"},
        "project_set_fields": {"project": path, "fields": {"项目名": "new"}},
        "project_import_group": {"project": path, "source": str(project.root/'人员职责.json'),
                                 "expected_revision": project.revision},
        "project_apply": {"project": path, "fields": {"项目名": "new"}},
        "project_recalculate": {"project": path},
        "project_set_model": {"project": path,
                              "model": {"id": "gjb5000-simplified", "version": "3.0"}},
        "project_generate": {"project": path, "dry_run": True},
        "module_create": {"project": path, "data_type": "feature", "data": {"name": "new"}},
        "module_update": {"project": path, "module_id": module_id, "data": {"name": "new"}},
        "module_remove": {"project": path, "module_id": module_id},
        "module_reorder": {"project": path, "data_type": "feature", "module_ids": [module_id]},
        "template_configure": {"project": path,
                               "templates": {"mode": "default", "enabled_ids": []}},
    }
    assert set(requests) == set(WRITE_TOOLS) - {"project_create"}
    before = snapshot(project.root)
    async with Client(create_server(lambda: service), mode="legacy") as client:
        for name, request in requests.items():
            missing = dict(request)
            del missing["project"]
            response = await call(client, name, missing, expected_ok=False)
            assert response["error"]["code"] == "INVALID_ARGUMENT", name

            stale = dict(request, expected_revision="sha256:" + "0" * 64)
            direct = service.call(name, stale)
            response = await call(client, name, stale, expected_ok=False)
            assert response == direct, name
            assert response["error"]["code"] == "REVISION_CONFLICT", name
        assert snapshot(project.root) == before


@pytest.mark.anyio
async def test_p10_real_stdio_full_business_flow_and_error_recovery(tmp_path):
    from prism.schema import PEOPLE_FIELDS
    from mcp import Client
    from prism_agent.mcp_server import MCP_TOOLS

    project = (tmp_path / "中文 空格 MCP 写入项目").resolve()
    async with Client(server_process(), mode="legacy") as client:
        listed = await client.list_tools()
        assert tuple(tool.name for tool in listed.tools) == MCP_TOOLS
        invalid = await call(client, "project_create", {
            "project": str(project), "name": "P10", "expected_revision": "sha256:" + "0" * 64,
        }, expected_ok=False)
        assert invalid["error"]["code"] == "INVALID_ARGUMENT"
        assert not project.exists()

        created = await call(client, "project_create", {"project": str(project), "name": "P10"})
        revision = created["revision"]
        fields = await call(client, "project_set_fields", {
            "project": str(project), "fields": dict({role:"Person"+str(i) for i,role in enumerate(PEOPLE_FIELDS)}, 项目名="P10 项目"),
            "expected_revision": revision,
        })
        revision = fields["revision"]
        applied = await call(client, "project_apply", {
            "project": str(project), "schedule": {"project_start": "2026-09-21"},
            "expected_revision": revision,
        })
        revision = applied["revision"]

        module_ids = []
        for name in ("功能一", "功能二"):
            result = await call(client, "module_create", {
                "project": str(project), "data_type": "feature",
                "data": {"name": name, "requirement_description": name},
                "expected_revision": revision,
            })
            module_ids.append(result["data"]["module"]["module_id"])
            revision = result["revision"]
        updated = await call(client, "module_update", {
            "project": str(project), "module_id": module_ids[0],
            "data": {"name": "功能一更新"}, "expected_revision": revision,
        })
        revision = updated["revision"]
        reordered = await call(client, "module_reorder", {
            "project": str(project), "data_type": "feature",
            "module_ids": list(reversed(module_ids)), "expected_revision": revision,
        })
        revision = reordered["revision"]
        removed = await call(client, "module_remove", {
            "project": str(project), "module_id": module_ids[1],
            "expected_revision": revision,
        })
        revision = removed["revision"]

        model = await call(client, "project_set_model", {
            "project": str(project),
            "model": {"id": "gjb5000-simplified", "version": "3.0"},
            "expected_revision": revision,
        })
        revision = model["revision"]
        templates = await call(client, "template_list", {"project": str(project)})
        xlsx_id = next(item["template_id"] for item in templates["data"]["templates"]
                       if item["kind"] == "XLSX")
        configured = await call(client, "template_configure", {
            "project": str(project),
            "templates": {"mode": "custom", "enabled_ids": [xlsx_id]},
            "expected_revision": revision,
        })
        revision = configured["revision"]
        recalculated = await call(client, "project_recalculate", {
            "project": str(project), "expected_revision": revision,
        })
        revision = recalculated["revision"]
        await call(client, "project_validate", {"project": str(project)})
        generated = await call(client, "project_generate", {
            "project": str(project), "template_ids": [xlsx_id],
            "expected_revision": revision,
        })
        assert generated["commit_state"] == "committed"
        assert generated["records_saved"] is True
        assert generated["data"]["results"]

        failed = await call(client, "project_set_fields", {
            "project": str(project), "fields": {"项目名": "stale"},
            "expected_revision": "sha256:" + "0" * 64,
        }, expected_ok=False)
        assert failed["error"]["code"] == "REVISION_CONFLICT"
        current = await call(client, "project_get", {"project": str(project)})
        assert current["data"]["project"]["fields"]["项目名"] == "P10 项目"


@pytest.mark.anyio
async def test_p10_dry_run_revision_conflict_and_cli_service_parity(tmp_path):
    from mcp import Client
    from prism_agent.mcp_server import create_server

    service = create_service(application_root=ROOT)
    project = (tmp_path / "parity MCP 项目").resolve()
    created = service.call("project_create", {"project": str(project), "name": "Parity"})
    assert created["ok"] is True
    request = {"project": str(project), "fields": {"项目名": "dry-run"},
               "expected_revision": created["revision"], "dry_run": True}
    before = snapshot(project)
    direct = service.call("project_set_fields", request)
    _, cli = cli_call(service, "project_set_fields", request)
    async with Client(create_server(lambda: service), mode="legacy") as client:
        mcp = await call(client, "project_set_fields", request)
        assert mcp == cli == direct
        assert mcp["commit_state"] == "not_committed"
        assert snapshot(project) == before

        stale = dict(request, dry_run=False, expected_revision="sha256:" + "0" * 64)
        direct_error = service.call("project_set_fields", stale)
        _, cli_error = cli_call(service, "project_set_fields", stale)
        mcp_error = await call(client, "project_set_fields", stale, expected_ok=False)
        assert mcp_error == cli_error == direct_error
        assert mcp_error["error"]["code"] == "REVISION_CONFLICT"
        assert snapshot(project) == before

        committed = await call(client, "project_set_fields", dict(request, dry_run=False))
        assert committed["commit_state"] == "committed"
        assert committed["revision"] != created["revision"]
        state = service.call("project_get", {"project": str(project)})
        assert state["revision"] == committed["revision"]
        assert state["data"]["project"]["fields"]["项目名"] == "dry-run"


@pytest.mark.anyio
async def test_p10_committed_cli_mcp_service_state_parity(tmp_path):
    from mcp import Client
    from prism_agent.mcp_server import create_server

    service = create_service(application_root=ROOT)
    source = (tmp_path / "source").resolve()
    created = service.call("project_create", {"project": str(source), "name": "Parity"})
    assert created["ok"] is True
    paths = [(tmp_path / name).resolve() for name in ("cli", "mcp", "service")]
    for path in paths:
        shutil.copytree(source, path)
    requests = [{"project": str(path), "fields": {"项目名": "same value"},
                 "expected_revision": created["revision"]} for path in paths]
    _, cli = cli_call(service, "project_set_fields", requests[0])
    async with Client(create_server(lambda: service), mode="legacy") as client:
        mcp = await call(client, "project_set_fields", requests[1])
    direct = service.call("project_set_fields", requests[2])
    assert all(result["ok"] for result in (cli, mcp, direct))
    assert len({result["revision"] for result in (cli, mcp, direct)}) == 1
    assert {result["commit_state"] for result in (cli, mcp, direct)} == {"committed"}
    for path in paths:
        current = service.call("project_get", {"project": str(path)})
        assert current["revision"] == mcp["revision"]
        assert current["data"]["project"]["fields"]["项目名"] == "same value"


@pytest.mark.anyio
async def test_p10_transaction_rollback_and_corrupt_project_preserve_service_errors(
        tmp_path, monkeypatch):
    from mcp import Client
    from prism_agent.mcp_server import create_server

    service = create_service(application_root=ROOT)
    project = populated(tmp_path / "rollback")
    before = snapshot(project.root)
    original_replace = storage.os.replace
    calls = []

    def fail_once(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise PermissionError("injected write failure")
        return original_replace(source, target)

    monkeypatch.setattr(storage.os, "replace", fail_once)
    async with Client(create_server(lambda: service), mode="legacy") as client:
        failed = await call(client, "project_apply", {
            "project": str(project.root), "fields": {"产品名": "changed"},
        }, expected_ok=False)
        assert failed["error"]["code"] == "PROJECT_LOAD_FAILED"
        assert failed["error"]["details"]["rollback"] == "succeeded"
        assert failed["commit_state"] == "not_committed"
        assert snapshot(project.root) == before

        broken = tmp_path / "corrupt"
        broken.mkdir()
        (broken / "基本数据.json").write_text("{broken", encoding="utf-8")
        response = await call(client, "project_apply", {
            "project": str(broken), "fields": {"产品名": "changed"},
        }, expected_ok=False)
        assert response == service.call("project_apply", {
            "project": str(broken), "fields": {"产品名": "changed"},
        })
        assert response["error"]["code"] == "PROJECT_LOAD_FAILED"
        assert (broken / "基本数据.json").read_text(encoding="utf-8") == "{broken"

        inconsistent = populated(tmp_path / "inconsistent")
        basic = inconsistent.root / "基本数据.json"
        payload = json.loads(basic.read_text(encoding="utf-8"))
        payload["modules"][0]["name"] = "index differs from module"
        basic.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        before_inconsistent = basic.read_bytes()
        response = await call(client, "module_update", {
            "project": str(inconsistent.root),
            "module_id": inconsistent.features[0].module_id,
            "data": {"name": "new"},
        }, expected_ok=False)
        assert response["error"]["code"] == "PROJECT_LOAD_FAILED"
        assert basic.read_bytes() == before_inconsistent


@pytest.mark.anyio
async def test_p10_rollback_failure_reports_unknown_without_adapter_retry(tmp_path, monkeypatch):
    from mcp import Client
    from prism_agent.mcp_server import create_server

    service = create_service(application_root=ROOT)
    project = populated(tmp_path / "recovery")
    original_replace = storage.os.replace
    calls = []

    def fail_write_and_rollback(source, target):
        calls.append(target)
        if len(calls) >= 2:
            raise PermissionError("injected write or rollback failure")
        return original_replace(source, target)

    monkeypatch.setattr(storage.os, "replace", fail_write_and_rollback)
    async with Client(create_server(lambda: service), mode="legacy") as client:
        response = await call(client, "project_apply", {
            "project": str(project.root), "fields": {"产品名": "changed"},
        }, expected_ok=False)
    assert response["error"]["code"] == "STORAGE_RECOVERY_REQUIRED"
    assert response["commit_state"] == "unknown"
    assert response["error"]["retryable"] is False
    assert len(calls) >= 2
    assert list(project.root.glob(".prism-save-*"))


@pytest.mark.anyio
async def test_p10_generation_busy_preserves_service_result(tmp_path):
    from mcp import Client
    from prism_agent.mcp_server import create_server

    service = create_service(application_root=ROOT)
    root = (tmp_path / "busy").resolve()
    assert service.call("project_create", {"project": str(root), "name": "Busy"})["ok"]
    lock = GenerationLock(root).acquire()
    try:
        request = {"project": str(root), "dry_run": True}
        direct = service.call("project_generate", request)
        _, cli = cli_call(service, "project_generate", request)
        async with Client(create_server(lambda: service), mode="legacy") as client:
            mcp = await call(client, "project_generate", request, expected_ok=False)
        assert mcp == cli == direct
        assert mcp["error"]["code"] == "GENERATION_BUSY"
    finally:
        lock.release()


@pytest.mark.anyio
@pytest.mark.parametrize("example", [
    "word_warning_success", "partial_failure", "review_checklist_failed",
    "record_save_failed", "all_render_failed_and_recorded",
])
async def test_p10_generation_result_fields_are_not_reinterpreted(example):
    from mcp import Client
    from prism_agent.mcp_server import create_server

    expected = CONTRACT["generation_examples"][example]

    class ExampleService:
        def call(self, name, request):
            assert name == "project_generate"
            return expected

    service = ExampleService()
    async with Client(create_server(lambda: service), mode="legacy") as client:
        mcp = await call(client, "project_generate", {"project": "C:/example"}, expected["ok"])
    _, cli = cli_call(service, "project_generate", {"project": "C:/example"})
    assert mcp == cli == expected
    assert mcp["records_saved"] == expected["records_saved"]
    assert mcp["review_checklist"] == expected["review_checklist"]
    assert mcp["commit_state"] == expected["commit_state"]


@pytest.mark.anyio
async def test_p10_lost_write_response_reconciles_without_replay(tmp_path):
    from mcp import Client
    from prism_agent.mcp_server import create_server
    import anyio

    service = create_service(application_root=ROOT)
    project = (tmp_path / "lost response").resolve()
    assert service.call("project_create", {"project": str(project), "name": "Lost"})["ok"]
    entered = threading.Event()
    release = threading.Event()

    class DelayedService:
        calls = 0

        def call(self, name, request):
            if name == "project_set_fields":
                self.calls += 1
                entered.set()
                assert release.wait(timeout=10)
            return service.call(name, request)

    wrapper = DelayedService()
    try:
        async with Client(create_server(lambda: wrapper), mode="legacy") as client:
            pending = asyncio.create_task(client.call_tool("project_set_fields", {
                "project": str(project), "fields": {"项目名": "committed once"},
            }))
            assert await anyio.to_thread.run_sync(entered.wait, 10)
            pending.cancel()
            release.set()
            with suppress(asyncio.CancelledError):
                await pending
    finally:
        release.set()
    assert wrapper.calls == 1
    async with Client(create_server(lambda: service), mode="legacy") as client:
        reconciled = await call(client, "project_get", {"project": str(project)})
    assert reconciled["data"]["project"]["fields"]["项目名"] == "committed once"
