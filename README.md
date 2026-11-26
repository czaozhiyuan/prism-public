# PRISM 文档生成器 · 演示版

PRISM 用本地项目数据生成 Word 文档和 Excel 进度表。这个版本提供原创教学模板，适合用来体验新建项目、排期、预检和批量生成。模板展示数据绑定方法，不作为正式交付依据。

## 在 Windows 上启动

安装 64 位 CPython 3.8.10。在仓库根目录打开 PowerShell，运行：

```powershell
py -3.8 -m venv .venv-gui
./.venv-gui/Scripts/python.exe -m pip install -r requirements-build.txt
./run_gui.bat
```

选择「新建项目」，建立“温度记录演示”。在「项目基本数据」填入项目和人员信息，在「进度规划」设置开始日期，到「模板与文档」选择需求规格说明，在「批量生成」完成预检和生成。点击左下角「使用手册」可以找到随附的 PDF。

## CLI 与 MCP

Agent 使用独立的 CPython 3.12.13 x64 环境。安装 uv 0.11.7，在仓库根目录依次运行 `setup_agent.bat` 和 `run_cli.bat prism_info`。MCP 客户端可将 `run_mcp.bat` 配置为本地 stdio 服务。

## 演示资源

演示数据使用“温度记录演示”这个虚构项目。项目的单位字段由使用者自行填写。内置资源包含 20 份可选 Word 模板、1 份 FPGA 手册变体、进度表、键值表示例和一页操作指南。MCU 与 FPGA 会自动选用各自的测试文档。

资源的生成源码位于 `scripts/make_demo_assets.py`。需要修改模板的开发者可在单独的工具环境安装 python-docx、openpyxl、reportlab，运行该脚本后再运行 `scripts/build_assets_manifest.py` 更新校验清单。

## 回归与资源

GUI 环境设定 `$env:PYTHONPATH='src'` 后运行 `./.venv-gui/Scripts/python.exe -m pytest tests -q -m "not agent_only and not word_com and not package_smoke"`。Agent 专属测试使用按 `requirements-agent-test.txt` 建立的独立测试环境。演示模板在 `assets/templates`，生成期项目资源清单可用 `python scripts/build_assets_manifest.py --check` 校验。
