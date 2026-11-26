"""P9 MCP read-only adapter and real stdio protocol tests."""

import json
import os
from pathlib import Path
import socket
import sys

import pytest

from prism.agent_contract import CONTRACT
from prism.agent_service import create_service


pytestmark = pytest.mark.agent_only


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def populated_project(tmp_path):
    service = create_service(application_root=ROOT)
    project = (tmp_path / "中文 空格 MCP 项目").resolve()
    created = service.call("project_create", {"project": str(project), "name": "MCP 验收"})
    assert created["ok"] is True
    module = service.call("module_create", {
        "project": str(project), "data_type": "feature",
        "data": {"name": "功能一", "requirement_description": "检查 MCP 读取"},
    })
    assert module["ok"] is True
    return service, project, module["data"]["module"]["module_id"]


def tool_arguments(project, module_id):
    path = str(project)
    return {
        "prism_info": {},
        "schema_get": {"name": "agent-api-v2"},
        "project_describe_fields": {},
        "project_get": {"project": path},
        "module_list": {"project": path},
        "module_get": {"project": path, "module_id": module_id},
        "model_list": {},
        "model_get": {"id": "gjb5000-simplified", "version": "3.0"},
        "template_list": {"project": path},
        "project_validate": {"project": path},
    }


@pytest.mark.anyio
async def test_mcp_adapter_does_not_bind_network_socket(populated_project, monkeypatch):
    from mcp import Client
    from prism_agent.mcp_server import create_server

    service, _, _ = populated_project

    def reject_bind(self, address):
        raise AssertionError("MCP adapter attempted to bind a network socket")

    monkeypatch.setattr(socket.socket, "bind", reject_bind)
    async with Client(create_server(lambda: service), mode="legacy") as client:
        result = await client.call_tool("prism_info", {})
        assert result.is_error is False


@pytest.mark.anyio
async def test_p9_tool_list_exactly_matches_contract_read_only_schema(populated_project):
    from mcp import Client
    from prism_agent.mcp_server import MCP_TOOLS, READ_ONLY_TOOLS, create_server

    service, _, _ = populated_project
    async with Client(create_server(lambda: service), mode="legacy") as client:
        result = await client.list_tools()
    tools = {tool.name: tool for tool in result.tools}
    assert tuple(tools) == MCP_TOOLS
    assert set(READ_ONLY_TOOLS) == set(CONTRACT["side_effect_classes"]["read_only"]) | {"project_validate"}
    assert all(tools[name].input_schema == CONTRACT["tools"][name]["request_schema"]
               for name in READ_ONLY_TOOLS)


@pytest.mark.anyio
async def test_every_p9_tool_matches_direct_agent_service(populated_project):
    from mcp import Client
    from prism_agent.mcp_server import create_server

    service, project, module_id = populated_project
    async with Client(create_server(lambda: service), mode="legacy") as client:
        for name, arguments in tool_arguments(project, module_id).items():
            direct = service.call(name, arguments)
            result = await client.call_tool(name, arguments)
            assert direct["ok"] is True, name
            assert result.is_error is False, name
            assert result.structured_content == direct, name
            assert json.loads(result.content[0].text) == direct, name


@pytest.mark.anyio
async def test_business_failures_are_tool_results_and_do_not_end_session(populated_project, tmp_path):
    from mcp import Client
    from prism_agent.mcp_server import create_server

    service, _, _ = populated_project
    corrupt = (tmp_path / "损坏 项目").resolve()
    corrupt.mkdir()
    (corrupt / "基本数据.json").write_text("{", encoding="utf-8")
    cases = [
        ("project_get", {"project": "relative/path"}, "INVALID_ARGUMENT"),
        ("project_get", {"project": str(tmp_path / "missing")}, "PROJECT_NOT_FOUND"),
        ("project_get", {"project": str(corrupt)}, "PROJECT_LOAD_FAILED"),
    ]
    async with Client(create_server(lambda: service), mode="legacy") as client:
        for name, arguments, code in cases:
            direct = service.call(name, arguments)
            result = await client.call_tool(name, arguments)
            assert direct["error"]["code"] == code
            assert result.is_error is True
            assert result.structured_content == direct
            assert json.loads(result.content[0].text) == direct
            healthy = await client.call_tool("model_list", {})
            assert healthy.is_error is False


@pytest.mark.anyio
async def test_unknown_tool_is_protocol_error_not_business_result(populated_project):
    from mcp import Client
    from mcp.shared.exceptions import MCPError
    from prism_agent.mcp_server import create_server

    service, _, _ = populated_project
    async with Client(create_server(lambda: service), mode="legacy") as client:
        with pytest.raises(MCPError):
            await client.call_tool("unknown_tool", {})


@pytest.mark.anyio
async def test_real_stdio_initialize_and_repeated_calls(populated_project):
    from mcp import Client, StdioServerParameters
    from prism_agent.mcp_server import MCP_TOOLS

    service, project, _ = populated_project
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")
    target = StdioServerParameters(
        command=os.environ.get("PRISM_MCP_SERVER_PYTHON", sys.executable),
        args=["-m", "prism_agent.mcp_server"],
        env=environment,
        cwd=ROOT,
    )
    async with Client(target, mode="legacy") as client:
        listed = await client.list_tools()
        assert tuple(tool.name for tool in listed.tools) == MCP_TOOLS
        for _ in range(3):
            result = await client.call_tool("project_get", {"project": str(project)})
            assert result.structured_content == service.call(
                "project_get", {"project": str(project)},
            )
        failure = await client.call_tool("project_get", {"project": "relative/path"})
        assert failure.is_error is True
        assert failure.structured_content["error"]["code"] == "INVALID_ARGUMENT"
        healthy = await client.call_tool("prism_info", {})
        assert healthy.is_error is False
