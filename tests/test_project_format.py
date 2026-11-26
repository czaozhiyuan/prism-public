"""Current v7 project contract checks using isolated projects and real Tk widgets."""
from copy import deepcopy
import json
from pathlib import Path
import queue
from unittest.mock import Mock

import pytest

from prism import functional_app, generation_core, storage
from prism.project import Project, ProjectError
from prism.template_ids import (DEFAULT_DISABLED_TEMPLATE_IDS, default_template_enabled,
                                encode_template_id, parse_template_id, resolve_template_id)
from test_date_trace import ui
from p2_helpers import snapshot


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = "asset:templates/docx/sample.docx"


def batch():
    return {"source_revision": "sha256:" + "a" * 64, "time": "2026-09-15T10:00:00", "results": [
        {"template_id": TEMPLATE, "output": "outputs/sample.docx", "render_status": "success",
         "word_finalize_status": "unavailable", "message": "", "warnings": ["Word unavailable"]},
        {"template_id": "asset:templates/docx/failed.docx", "output": None, "render_status": "failed",
         "word_finalize_status": "not_applicable", "message": "render failed", "warnings": []}]}


@pytest.mark.parametrize("config", [{"mode": "default", "enabled_ids": []},
                                    {"mode": "custom", "enabled_ids": []},
                                    {"mode": "custom", "enabled_ids": [TEMPLATE]}])
def test_v7_roundtrip_and_owned_defaults(tmp_path, config):
    project = Project.create(tmp_path / "project", "容器名")
    assert project.templates == {"mode": "default", "enabled_ids": []}
    assert project.outputs == {} and project.schedule == {}
    assert project.fields["项目名"] == "容器名"
    project.templates = deepcopy(config)
    project.outputs = batch()
    project.fields["项目名"] = "软件名"
    project.save()
    generation_core.save_generation_outputs(project.root, batch())
    loaded = Project.load(project.root)
    assert loaded.templates == config and loaded.outputs == batch()
    assert loaded.name == "容器名" and loaded.fields["项目名"] == "软件名"
    schema = json.loads((ROOT / "assets/schemas/project-basic-v7.schema.json").read_text(encoding="utf-8"))
    import jsonschema
    jsonschema.validate(storage.read_json(project.file_path), schema)
    assert Project(tmp_path / "other").templates == {"mode": "default", "enabled_ids": []}


@pytest.mark.parametrize("version", [6, 5, 4, 3, 2, 1, 8, True, "7", None])
def test_unsupported_versions_are_explicit_and_read_only(tmp_path, version):
    project = Project.create(tmp_path, "test")
    payload = storage.read_json(project.file_path)
    payload["format_version"] = version
    project.file_path.write_text(json.dumps(payload), encoding="utf-8")
    before = (snapshot(tmp_path), {path.name for path in tmp_path.iterdir()})
    with pytest.raises(ProjectError) as error:
        Project.load(tmp_path)
    assert error.value.code == "PROJECT_FORMAT_UNSUPPORTED"
    assert error.value.details == {"detected_format_version": version, "supported_format_version": 7}
    assert (snapshot(tmp_path), {path.name for path in tmp_path.iterdir()}) == before


@pytest.mark.parametrize('operation', ['open', 'import'])
def test_gui_rejects_v6_without_changing_source_or_current_project(ui, tmp_path, monkeypatch, operation):
    source = Project.create(tmp_path / 'old', 'Old')
    payload = storage.read_json(source.file_path)
    payload['format_version'] = 6
    payload.pop('groups')
    payload['fields']['软件负责人'] = '旧负责人'
    source.file_path.write_text(json.dumps(payload), encoding='utf-8')
    ui.project = Project.create(tmp_path / 'current', 'Current')
    ui._load_ui()
    before = (snapshot(source.root), snapshot(ui.project.root), ui._editable_projection())
    current_root, revision = ui.project.root, ui.loaded_revision
    errors = Mock()
    monkeypatch.setattr(functional_app.messagebox, 'showerror', errors)
    if operation == 'open':
        monkeypatch.setattr(functional_app.filedialog, 'askdirectory', lambda **kw: str(source.root))
        ui._open_project()
    else:
        monkeypatch.setattr(functional_app.filedialog, 'askopenfilename', lambda **kw: str(source.file_path))
        ui._import_data()
    errors.assert_called_once()
    assert 'Project Format v7' in errors.call_args.args[1]
    assert ui.project.root == current_root and ui.loaded_revision == revision
    assert (snapshot(source.root), snapshot(ui.project.root), ui._editable_projection()) == before


@pytest.mark.parametrize("field,value", [("templates", []), ("outputs", []),
    ("templates", {"mode": "custom", "enabled_ids": [TEMPLATE, TEMPLATE]}),
    ("templates", {"mode": "custom", "enabled_ids": ["C:/old/template.docx"]}),
    ("templates", {"mode": "default"}), ("outputs", {"results": []})])
def test_no_list_or_historical_shape_fallback(tmp_path, field, value):
    project = Project.create(tmp_path, "test")
    before = project.file_path.read_bytes()
    setattr(project, field, value)
    with pytest.raises(ProjectError):
        project.save()
    assert project.file_path.read_bytes() == before


@pytest.mark.parametrize("field,value", [("output", "outputs/../outside.docx"), ("output", "C:/old.docx"),
    ("output", None), ("render_status", "failed"), ("warnings", {}), ("template_id", "C:/old.docx")])
def test_output_rows_reject_unsafe_paths_and_inconsistent_status(tmp_path, field, value):
    project = Project.create(tmp_path, "test")
    project.outputs = batch()
    project.outputs["results"][0][field] = value
    payload, _ = storage.prepare_project(Project(tmp_path), check_files=False)
    payload["outputs"] = project.outputs
    import jsonschema
    schema = json.loads((ROOT / "assets/schemas/project-basic-v7.schema.json").read_text(encoding="utf-8"))
    with pytest.raises(ProjectError):
        project.save()
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)


@pytest.mark.parametrize("value", ["asset:/templates/docx/a.docx", "asset:templates/docx/../a.docx",
    "asset:templates/docx/C:/a.docx", "asset:templates/docx/a\\b.docx", "asset:templates/docx//a.docx",
    "asset:templates/docx/NUL.docx", "asset:templates/docx/a. /b.docx", "model:x@1:../a.docx"])
def test_template_id_rejects_unsafe_paths(value):
    with pytest.raises(ProjectError):
        parse_template_id(value)


def test_asset_and_model_ids_survive_installation_move(tmp_path):
    from prism.lifecycle import load_model
    model = load_model(ROOT / "assets/models/gjb5000-simplified/model.json")
    for folder in ("install-a", "install-b"):
        assets = tmp_path / folder / "assets"
        template = assets / "templates/docx/sample.docx"
        template.parent.mkdir(parents=True)
        template.write_bytes(b"fixture")
        assert encode_template_id(template, assets) == TEMPLATE
        assert resolve_template_id(TEMPLATE, assets) == template.resolve()
    model_root = tmp_path / "model"
    (model_root / "documents").mkdir(parents=True)
    template = model_root / "documents/sample.docx"
    template.write_bytes(b"fixture")
    from dataclasses import replace
    model = replace(model, source_dir=model_root)
    value = encode_template_id(template, assets, model)
    assert value == "model:gjb5000-simplified@3.0:documents/sample.docx"
    assert resolve_template_id(value, assets, model) == template.resolve()
    with pytest.raises(ProjectError):
        resolve_template_id(value, assets, replace(model, version="2"))


def test_resolver_rejects_escaping_resolved_target(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    target = assets / "templates/docx/sample.docx"
    outside = tmp_path / "outside.docx"
    outside.write_bytes(b"external")
    original = Path.resolve
    # Exercise the post-resolution boundary without requiring symlink privilege.
    monkeypatch.setattr(Path, "resolve", lambda path: outside if path == target else original(path))
    with pytest.raises(ProjectError, match="越出"):
        resolve_template_id(TEMPLATE, assets)


def test_gui_create_open_save_reopen_and_template_modes(ui, tmp_path, monkeypatch):
    monkeypatch.setattr(functional_app.filedialog, "askdirectory", lambda **kw: str(tmp_path))
    monkeypatch.setattr(functional_app.simpledialog, "askstring", lambda *a, **kw: "P1演示")
    monkeypatch.setattr(functional_app.messagebox, "showerror", Mock(side_effect=AssertionError("GUI error")))
    monkeypatch.setattr(ui, "_toast", Mock())
    ui._new_project()
    project_root = tmp_path / "P1演示"
    assert ui.project.root == project_root
    assert ui.schedule_start_var.get() == ""
    ui._save_project()
    assert Project.load(project_root).schedule == {}
    identities = {path: encode_template_id(path, ROOT / "assets", ui.model)
                  for path in ui.template_enabled}
    assert all(enabled == default_template_enabled(identities[path])
               for path, enabled in ui.template_enabled.items())
    assert {identities[path] for path, enabled in ui.template_enabled.items()
            if not enabled} == DEFAULT_DISABLED_TEMPLATE_IDS
    ui.schedule_start_var.set("2026-09-15")
    ui._save_project()
    assert Project.load(project_root).templates["mode"] == "default"
    path = next(iter(ui.template_enabled))
    ui.template_tree.selection_set(path)
    ui._toggle_template(None)
    expected = deepcopy(ui.project.templates)
    ui._save_project()
    monkeypatch.setattr(functional_app.filedialog, "askdirectory", lambda **kw: str(project_root))
    ui._open_project()
    assert ui.project.templates == expected
    assert all(enabled == (encode_template_id(path, ROOT / "assets", ui.model) in expected["enabled_ids"])
               for path, enabled in ui.template_enabled.items())
    external = Project.load(project_root)
    external.templates = {"mode": "custom", "enabled_ids": []}
    external.save()
    generation_core.save_generation_outputs(project_root, batch())
    ui._open_project()
    assert not any(ui.template_enabled.values())
    assert len(ui.result_tree.get_children()) == 2
    ui._save_project()
    assert Project.load(project_root).outputs == batch()
    assert Project.load(project_root).templates == {"mode": "custom", "enabled_ids": []}


def test_session_generation_never_saves_old_business_snapshot(tmp_path, monkeypatch):
    project = Project.create(tmp_path / "project", "test")
    generation_core.save_generation_outputs(project.root, batch())
    project.fields["IDE"] = "newer data"
    project.save()
    before = project.file_path.read_bytes()
    response = {
        "ok": True, "status": "success",
        "data": {"results": [{"template_id": TEMPLATE, "output": str(tmp_path / "external/sample.docx"),
                                "render_status": "success", "word_finalize_status": "success",
                                "message": "", "warnings": []}]},
        "warnings": [], "records_saved": None, "review_checklist": None, "error": None,
    }
    export = Mock(return_value=response)
    monkeypatch.setattr(functional_app, "export_gui_documents", export)
    events = queue.Queue()
    functional_app.generate_in_background(events, ROOT, project.root, tmp_path / "external")
    assert project.file_path.read_bytes() == before
    assert Project.load(project.root).outputs == batch()
    assert events.get()[0] == "saving"
    event, payload = events.get()
    assert event == "done" and payload == response
    export.assert_called_once_with(
        ROOT, project.root.resolve(), (tmp_path / "external").resolve(),
    )


def test_gui_removes_legacy_import_and_keeps_model_package_actions(ui, tmp_path, monkeypatch):
    ui.project = Project.create(tmp_path / "project", "容器")
    ui._load_ui()
    monkeypatch.setattr(ui, "_toast", Mock())
    monkeypatch.setattr(functional_app.messagebox, "showerror", Mock(side_effect=AssertionError("GUI error")))
    assert not hasattr(type(ui), "_import_legacy")
    assert ui.project.context()["项目年"] == ""
    package = tmp_path / "model.prismmodel"
    monkeypatch.setattr(functional_app.filedialog, "asksaveasfilename", lambda **kw: str(package))
    ui._export_model()
    assert package.is_file()
    monkeypatch.setattr(functional_app.filedialog, "askopenfilename", lambda **kw: str(package))
    monkeypatch.setattr(functional_app, "user_model_library", lambda: tmp_path / "library")
    ui._import_model()
    assert ui.model.source_dir.is_dir() and not ui._model_missing
    ui._save_project()
    reopened = Project.load(ui.project.root)
    assert reopened.fields["项目名"] == "容器" and reopened.outputs == {}
