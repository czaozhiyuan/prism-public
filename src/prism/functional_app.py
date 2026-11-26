"""Functional Tkinter desktop interface for PRISM."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import tkinter as tk
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional, Tuple

from .app import COLORS, FONT_FAMILY, find_project_root, infer_stage
from .agent_service import AgentService
from .generation import inspect_template
from .gui_generation import export_documents as export_gui_documents
from . import gui_writes
from .lifecycle import LifecycleModel, ModelError, export_model_package, import_model_package, load_model
from .project import Project, ProjectError, PROJECT_FILENAME, sanitize_filename
from .storage import DataConflict, KINDS, GROUP_FILES, read_json, validate_group
from .schedule import load_work_calendar
from .schedule_state import require_current_schedule
from .schedule_editor import WorkScheduleEditor
from .schedule_template import export_schedule_template
from .schema import ALL_FIELDS, DATE_FIELDS, FIELD_LABELS, FIELD_SECTIONS
from .items import inspect_items
from .template_ids import encode_template_id, selected_template_ids, device_test_template_id, discover_template_paths, TEST_TEMPLATE_PAIRS
from .document_versions import DEFAULT_DOCUMENT_VERSION, context_for_template, normalize_template_config, normalize_version


TEMPLATE_STAGE_ORDER = ("项目启动", "需求与策划", "软件设计", "开发与测试", "软件测试", "交付与验收", "其他")


def default_model_path(root: Path) -> Path:
    return root / "assets" / "models" / "gjb5000-simplified" / "model.json"


def user_model_library() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "PRISM" / "models"


def discover_all_templates(root: Path, model: LifecycleModel) -> List[Tuple[str, str, str]]:
    """Discover bundled and model-specific DOCX/XLSX templates."""
    paths = discover_template_paths(root / "assets", model)
    templates = [(path.suffix.upper().lstrip("."), infer_stage(path.name), str(path)) for path in paths]
    return sorted(templates, key=lambda item: TEMPLATE_STAGE_ORDER.index(item[1]))


def date_trace_rows(model: LifecycleModel, dates: Dict[str, Any]) -> Dict[str, List[Tuple[str, str, str, str]]]:
    """Describe model nodes using already calculated values, in model order."""
    groups = {phase.id: [] for phase in model.phases}
    works = {work.id: work for work in model.works}
    for node in model.date_nodes:
        work = works[node.work_id]
        anchor = "工作开始" if node.anchor == "start" else "工作完成"
        for key in node.formats or (node.key,):
            groups[work.phase_id].append((key, str(dates.get(key, "")), work.name, anchor))
    return groups


def generate_in_background(events: queue.Queue, application_root: Path,
                           project_root: Path, output: Path) -> None:
    """Generate from a disk snapshot; communicate without calling Tk."""
    try:
        events.put(("saving", None))
        formal_output = (Path(project_root).resolve() / "outputs").resolve()
        if Path(output).resolve() == formal_output:
            service = AgentService(application_root=application_root)
            response = service.call(
                "project_generate",
                {"project": str(Path(project_root).resolve()), "mode": "enabled"},
            )
        else:
            response = export_gui_documents(
                application_root, project_root, Path(output).resolve(),
            )
    except Exception as exc:
        response = {"ok": False, "status": "failed", "data": {"results": []},
                    "warnings": [], "records_saved": None, "review_checklist": None,
                    "error": {"message": str(exc)}}
    events.put(("done", response))


class ScrollableForm(tk.Frame):
    """A minimal vertically scrollable form."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, bg=COLORS["surface"])
        self.canvas = canvas = tk.Canvas(self, bg=COLORS["surface"], highlightthickness=0, yscrollincrement=24)
        self._wheel_delta = 0
        scroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview, style="Prism.Vertical.TScrollbar")
        self.body = tk.Frame(canvas, bg=COLORS["surface"])
        window = canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self.update_scrollregion)
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def update_scrollregion(self, _event: Optional[tk.Event] = None) -> None:
        # An offscreen canvas window may not receive Configure after its content shrinks.
        height = self.body.winfo_reqheight()
        self.canvas.configure(scrollregion=(0, 0, self.body.winfo_width(), height))
        if height <= self.canvas.winfo_height():
            self.scroll_to_top()

    def scroll_to_top(self) -> None:
        self._wheel_delta = 0
        self.canvas.yview_moveto(0)

    def scroll_wheel(self, delta: int) -> str:
        if self.canvas.yview() == (0.0, 1.0):
            self._wheel_delta = 0
            return "break"
        self._wheel_delta += delta
        steps = int(self._wheel_delta / 120)
        self._wheel_delta -= steps * 120
        if steps:
            self.canvas.yview_scroll(-steps * 3, "units")
        return "break"


class SaveBeforeExitDialog(simpledialog.Dialog):
    """Ask whether to save using explicit action labels."""

    def body(self, master):
        self.resizable(False, False)
        tk.Label(master, text="当前存在未保存修改。", padx=16, pady=14).pack()

    def buttonbox(self):
        box = tk.Frame(self)
        for text, answer in (("保存并退出", True), ("不保存并退出", False), ("取消", None)):
            ttk.Button(box, text=text, width=16, command=lambda value=answer: self._choose(value)).pack(
                side="left", padx=5)
        box.pack(padx=12, pady=(0, 12))
        self.bind("<Return>", lambda event: self._choose(True))
        self.bind("<Escape>", self.cancel)

    def _choose(self, answer):
        self.result = answer
        self.cancel()


class PrismApp(tk.Tk):
    """PRISM main window backed by real project and generation services."""

    def __init__(self) -> None:
        super().__init__()
        self.title("PRISM V2 - 文档批量生成")
        self.geometry("1280x800")
        self.minsize(1080, 680)
        self.configure(bg=COLORS["canvas"])
        self.application_root = find_project_root()
        self.model = load_model(default_model_path(self.application_root))
        self.calendar = load_work_calendar(self.application_root / "assets" / "calendars" / "cn_workdays_2026_2035.json")
        self.project = Project(Path.home() / "Documents" / "PRISM项目" / "未命名项目")
        self.field_vars = {key: tk.StringVar(value=str(self.project.fields.get(key, ""))) for key in ALL_FIELDS}
        self.project_name_var = tk.StringVar(value="未保存项目")
        self.output_dir_var = tk.StringVar(value=str(self.project.output_dir))
        self.templates: List[Tuple[str, str, str]] = []
        self.template_enabled: Dict[str, bool] = {}
        self.generated_results: List[Dict[str, Any]] = []
        self.pages: Dict[str, tk.Frame] = {}
        self.nav_buttons: Dict[str, tk.Button] = {}
        self._generation_busy = False
        self.loaded_revision = self.project.loaded_revision
        self._clean_projection = None
        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self._configure_styles()
        self._build_shell()
        self.bind("<MouseWheel>", self._scroll_form_under_pointer, add="+")
        self._refresh_templates()
        self.field_vars['器件类型'].trace_add('write', self._device_type_changed)
        self._mark_clean()
        self.show_page("overview")

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.option_add("*Font", (FONT_FAMILY, 10))
        for name, background, foreground in (
            ("TLabel", COLORS["canvas"], COLORS["text"]), ("Surface.TLabel", COLORS["surface"], COLORS["text"]),
            ("Muted.TLabel", COLORS["canvas"], COLORS["muted"]), ("SurfaceMuted.TLabel", COLORS["surface"], COLORS["muted"]),
        ):
            style.configure(name, background=background, foreground=foreground)
        style.configure("Title.TLabel", background=COLORS["canvas"], foreground=COLORS["text"], font=(FONT_FAMILY, 20, "bold"))
        style.configure("Section.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=(FONT_FAMILY, 12, "bold"))
        style.configure("Primary.TButton", background=COLORS["primary"], foreground="#FFFFFF", borderwidth=0, padding=(18, 10), font=(FONT_FAMILY, 10, "bold"))
        style.map("Primary.TButton", background=[("active", COLORS["primary_dark"])])
        style.configure("Secondary.TButton", background=COLORS["surface"], foreground=COLORS["text"], bordercolor=COLORS["border"], padding=(14, 9))
        style.configure("Selection.TCheckbutton", background=COLORS["surface"], foreground=COLORS["text"])
        style.configure("TEntry", fieldbackground="#FFFFFF", foreground=COLORS["text"], bordercolor=COLORS["border"], padding=8)
        style.map("TEntry", bordercolor=[("invalid", COLORS["danger"])])
        style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=COLORS["text"], rowheight=32, borderwidth=0)
        style.configure("Treeview.Heading", background=COLORS["soft"], foreground=COLORS["muted"], relief="flat", font=(FONT_FAMILY, 9, "bold"), padding=(8, 8))
        style.map("Treeview", background=[("selected", "#DCE8FF")], foreground=[("selected", COLORS["text"])])
        style.layout("Prism.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        style.configure("Prism.Vertical.TScrollbar", background="#B8C5D9", troughcolor=COLORS["canvas"],
                        bordercolor=COLORS["canvas"], lightcolor="#B8C5D9", darkcolor="#B8C5D9",
                        borderwidth=0, width=12, arrowsize=12, gripcount=0)
        for option in ("background", "lightcolor", "darkcolor"):
            style.map("Prism.Vertical.TScrollbar", **{option: [
                ("pressed", COLORS["primary"]), ("active", "#8DA5CA"),
            ]})

    def _scroll_form_under_pointer(self, event: tk.Event) -> Optional[str]:
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if isinstance(widget, ScrollableForm):
                return widget.scroll_wheel(event.delta)
            widget = widget.master
        return None

    def _build_shell(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        sidebar = tk.Frame(self, bg=COLORS["nav"], width=224)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        tk.Label(sidebar, text="PRISM", bg=COLORS["nav"], fg="#FFFFFF", font=(FONT_FAMILY, 22, "bold")).pack(anchor="w", padx=24, pady=(22, 1))
        tk.Label(sidebar, text="文档批量生成工具", bg=COLORS["nav"], fg="#9EACC1", font=(FONT_FAMILY, 9)).pack(anchor="w", padx=25, pady=(0, 22))
        nav = tk.Frame(sidebar, bg=COLORS["nav"])
        nav.pack(fill="x", padx=12)
        for key, label in (("overview", "01  项目概览"), ("data", "02  项目基本数据"), ("items", "03  功能性能接口"), ("test_environment", "04  测试环境"), ("schedule", "05  进度规划"), ("templates", "06  模板与文档"), ("generate", "07  批量生成"), ("results", "08  生成结果")):
            button = tk.Button(nav, text=label, anchor="w", bg=COLORS["nav"], fg="#C9D3E1", activebackground=COLORS["nav_hover"], activeforeground="#FFFFFF", relief="flat", bd=0, padx=16, pady=12, command=lambda page=key: self.show_page(page))
            button.pack(fill="x", pady=2)
            self.nav_buttons[key] = button
        status = tk.Frame(sidebar, bg="#111D31", height=74)
        status.pack(side="bottom", fill="x")
        status.pack_propagate(False)
        tk.Button(status, text="使用手册", command=self._reveal_user_manual, cursor="hand2",
                  font=(FONT_FAMILY, 9, "underline"), relief="flat", bd=0,
                  bg="#111D31", fg="#CBDCF4", activebackground="#111D31",
                  activeforeground="white").place(relx=1, x=-12, y=10, anchor="ne")
        tk.Label(status, text="本地模式", bg="#111D31", fg="#FFFFFF", font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", padx=24, pady=(15, 1))
        tk.Label(status, text="仅处理 DOCX / XLSX", bg="#111D31", fg="#8FA0B7", font=(FONT_FAMILY, 8)).pack(anchor="w", padx=24)

        main = tk.Frame(self, bg=COLORS["canvas"])
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)
        top = tk.Frame(main, bg=COLORS["surface"], height=68, highlightthickness=1, highlightbackground=COLORS["border"])
        top.grid(row=0, column=0, sticky="ew")
        top.grid_propagate(False)
        tk.Label(top, text="当前项目", bg=COLORS["surface"], fg=COLORS["muted"]).pack(side="left", padx=(28, 8))
        tk.Label(top, textvariable=self.project_name_var, bg=COLORS["surface"], fg=COLORS["text"], font=(FONT_FAMILY, 10, "bold")).pack(side="left")
        ttk.Button(top, text="保存", style="Primary.TButton", command=self._save_project).pack(side="right", padx=(8, 24), pady=13)
        ttk.Button(top, text="打开项目", style="Secondary.TButton", command=self._open_project).pack(side="right", pady=13)
        ttk.Button(top, text="刷新", style="Secondary.TButton", command=self._refresh_project).pack(side="right", padx=(0, 8), pady=13)
        ttk.Button(top, text="新建项目", style="Secondary.TButton", command=self._new_project).pack(side="right", padx=(0, 8), pady=13)
        ttk.Button(top, text="导入数据", style="Secondary.TButton", command=self._import_data).pack(side="right", padx=(0, 8), pady=13)
        self.content = tk.Frame(main, bg=COLORS["canvas"])
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        for key, builder in (("overview", self._build_overview), ("data", self._build_data), ("items", self._build_items), ("test_environment", self._build_test_environment), ("schedule", self._build_schedule), ("templates", self._build_templates), ("generate", self._build_generate), ("results", self._build_results)):
            page = builder(self.content)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = page

    def _page(self, parent: tk.Frame, title: str, subtitle: str) -> Tuple[tk.Frame, tk.Frame]:
        page = tk.Frame(parent, bg=COLORS["canvas"])
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        header = tk.Frame(page, bg=COLORS["canvas"])
        header.grid(row=0, column=0, sticky="ew", padx=30, pady=(24, 18))
        ttk.Label(header, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text=subtitle, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        body = tk.Frame(page, bg=COLORS["canvas"])
        body.grid(row=1, column=0, sticky="nsew", padx=30, pady=(0, 28))
        return page, body

    def _card(self, parent: tk.Widget, padding: int = 18) -> tk.Frame:
        return tk.Frame(parent, bg=COLORS["surface"], highlightthickness=1, highlightbackground=COLORS["border"], padx=padding, pady=padding)

    def show_page(self, key: str) -> None:
        if getattr(self, "_generation_busy", False) and getattr(self, "_current_page", key) != key:
            return
        self._current_page = key
        self.pages[key].tkraise()
        for page_key, button in self.nav_buttons.items():
            active = page_key == key
            button.configure(bg=COLORS["nav_active"] if active else COLORS["nav"], fg="#FFFFFF" if active else "#C9D3E1", font=(FONT_FAMILY, 10, "bold" if active else "normal"))
        if key == "overview":
            self._refresh_overview()
        elif key == "generate":
            self.generate_summary_var.set("已选择 {} 个模板".format(sum(self.template_enabled.values())))

    def _build_overview(self, parent: tk.Frame) -> tk.Frame:
        page, body = self._page(parent, "项目概览", "项目数据、进度和输出状态")
        body.grid_columnconfigure((0, 1, 2), weight=1, uniform="metrics")
        self.metric_vars = [tk.StringVar() for _ in range(3)]
        for column, (label, variable, accent) in enumerate(zip(("已填写字段", "已选模板", "最近生成"), self.metric_vars, (COLORS["primary"], COLORS["success"], COLORS["warning"]))):
            card = self._card(body)
            card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 7, 0 if column == 2 else 7))
            tk.Frame(card, bg=accent, width=4, height=48).pack(side="left", padx=(0, 14))
            group = tk.Frame(card, bg=COLORS["surface"])
            group.pack(side="left")
            ttk.Label(group, text=label, style="SurfaceMuted.TLabel").pack(anchor="w")
            tk.Label(group, textvariable=variable, bg=COLORS["surface"], fg=COLORS["text"], font=(FONT_FAMILY, 20, "bold")).pack(anchor="w")
        card = self._card(body, 24)
        card.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(16, 0))
        ttk.Label(card, text="当前项目", style="Section.TLabel").pack(anchor="w", pady=(0, 16))
        self.overview_text = tk.Label(card, justify="left", anchor="nw", bg=COLORS["surface"], fg=COLORS["text"], font=(FONT_FAMILY, 11))
        self.overview_text.pack(fill="both", expand=True)
        ttk.Button(card, text="重命名项目", style="Secondary.TButton", command=self._rename_project).pack(anchor="w", pady=(16, 0))
        ttk.Button(card, text="继续填写项目数据", style="Primary.TButton", command=lambda: self.show_page("data")).pack(anchor="e", pady=(22, 0))
        return page

    def _refresh_overview(self) -> None:
        values = self._collect_fields()
        editable_fields = [field for field in ALL_FIELDS if field not in DATE_FIELDS]
        filled = sum(1 for field in editable_fields if str(values.get(field, "")).strip())
        self.metric_vars[0].set("{} / {}".format(filled, len(editable_fields)))
        self.metric_vars[1].set(str(sum(self.template_enabled.values())))
        self.metric_vars[2].set(
            str(sum(result.get("render_status") == "success"
                    for result in self.generated_results))
            if self.generated_results else "尚未生成"
        )
        phases = self.project.schedule.get("phases", [])
        finish = phases[-1]["finish"] if phases else "尚未计算"
        self.overview_text.configure(text="项目名称：{}\n生命周期模型：{} {}\n项目目录：{}\n计划结束：{}\n输出目录：{}".format(self.project.name, self.model.name, self.model.version, self.project.root, finish, self.output_dir_var.get()))

    def _build_data(self, parent: tk.Frame) -> tk.Frame:
        page, body = self._page(parent, "项目基本数据", "一次录入，多份 DOCX 和 XLSX 文档共同使用")
        body.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)
        bar = tk.Frame(body, bg=COLORS["surface"], highlightthickness=1, highlightbackground=COLORS["border"])
        bar.grid(row=0, column=0, sticky="ew")
        stack = tk.Frame(body, bg=COLORS["surface"], highlightthickness=1, highlightbackground=COLORS["border"])
        stack.grid(row=1, column=0, sticky="nsew")
        stack.grid_rowconfigure(0, weight=1)
        stack.grid_columnconfigure(0, weight=1)
        self.data_tab_buttons: Dict[str, tk.Button] = {}
        self.data_tab_lines: Dict[str, tk.Frame] = {}
        self.data_tab_pages: Dict[str, ScrollableForm] = {}
        for column, (key, title, fields) in enumerate(FIELD_SECTIONS):
            bar.grid_columnconfigure(column, weight=1, uniform="data_tabs")
            tab = tk.Frame(bar, bg=COLORS["surface"], height=46)
            tab.grid(row=0, column=column, sticky="nsew")
            tab.grid_propagate(False)
            button = tk.Button(tab, text=title, bg=COLORS["surface"], fg=COLORS["muted"], activebackground=COLORS["surface"], relief="flat", bd=0, command=lambda value=key: self._show_data_tab(value))
            button.pack(fill="both", expand=True)
            line = tk.Frame(tab, bg=COLORS["surface"], height=3)
            line.pack(side="bottom", fill="x")
            self.data_tab_buttons[key], self.data_tab_lines[key] = button, line
            form = ScrollableForm(stack)
            form.grid(row=0, column=0, sticky="nsew")
            self.data_tab_pages[key] = form
            form.body.grid_columnconfigure(1, weight=1)
            for row, field_name in enumerate(fields):
                ttk.Label(form.body, text=FIELD_LABELS[field_name], wraplength=170, style="SurfaceMuted.TLabel").grid(row=row, column=0, sticky="w", padx=(18, 16), pady=7)
                entry = ttk.Entry(form.body, textvariable=self.field_vars[field_name])
                entry.grid(row=row, column=1, sticky="ew", padx=(0, 22), pady=7)
                if key == "pictures":
                    ttk.Button(form.body, text="选择图片", command=lambda name=field_name: self._choose_picture(name)).grid(row=row, column=2, padx=4)
                    ttk.Button(form.body, text="清除", command=lambda name=field_name: self.field_vars[name].set("")).grid(row=row, column=3, padx=(4, 18))
        footer = tk.Frame(body, bg=COLORS["canvas"])
        footer.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(footer, text="保存项目数据", style="Primary.TButton", command=self._save_project).pack(side="right")
        self._show_data_tab("basic")
        return page

    def _choose_picture(self, field_name: str) -> None:
        path = filedialog.askopenfilename(title="选择" + field_name, parent=self,
                                         filetypes=(("图片", "*.png *.jpg *.jpeg"),))
        if path:
            self.field_vars[field_name].set(path)

    def _show_data_tab(self, key: str) -> None:
        self.data_tab_pages[key].tkraise()
        for tab_key, button in self.data_tab_buttons.items():
            active = tab_key == key
            button.configure(fg=COLORS["primary"] if active else COLORS["muted"], font=(FONT_FAMILY, 9, "bold" if active else "normal"))
            self.data_tab_lines[tab_key].configure(bg=COLORS["primary"] if active else COLORS["surface"])

    def _build_test_environment(self, parent: tk.Frame) -> tk.Frame:
        from .test_environment_editor import TestEnvironmentEditor
        page, body = self._page(parent, "测试环境", "集中维护环境差异、影响评估及仪器名称")
        self.test_environment_editor = TestEnvironmentEditor(
            body, self.field_vars, ScrollableForm, lambda: self._generation_busy)
        self.test_environment_editor.pack(fill="both", expand=True)
        ttk.Button(body, text="保存项目数据", style="Primary.TButton", command=self._save_project).pack(anchor="e", pady=(12, 0))
        return page

    def _build_items(self, parent: tk.Frame) -> tk.Frame:
        from .item_editor import ItemEditor, TestSummary
        page = tk.Frame(parent, bg=COLORS["canvas"])
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        header = tk.Frame(page, bg=COLORS["canvas"])
        header.grid(row=0, column=0, sticky="ew", padx=30, pady=(24, 18))
        titles = tk.Frame(header, bg=COLORS["canvas"])
        titles.pack(side="left")
        ttk.Label(titles, text="功能性能接口", style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="统一管理需求、设计及对应的多项测试",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        self.item_test_summary = TestSummary(header, COLORS["canvas"])
        self.item_test_summary.pack(side="right")
        ttk.Label(header, text="全项目测试汇总", style="Muted.TLabel").pack(side="right", padx=(12, 4))
        body = tk.Frame(page, bg=COLORS["canvas"])
        body.grid(row=1, column=0, sticky="nsew", padx=30, pady=(0, 28))
        self.items_editor = ItemEditor(body, self.project, ScrollableForm,
                                      on_change=self._refresh_item_test_summary)
        self.items_editor.on_remove = self._remove_module
        self.items_editor.pack(fill="both", expand=True)
        return page

    def _refresh_item_test_summary(self) -> None:
        from .items import ITEM_TYPES
        self.item_test_summary.update_counts(
            item for kind, _label in ITEM_TYPES for item in getattr(self.project, kind))

    def _build_schedule(self, parent: tk.Frame) -> tk.Frame:
        page, body = self._page(parent, "进度规划", "设置单人计划工时，自动计算工作日并同步文档日期")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.schedule_scroll = ScrollableForm(body)
        self.schedule_scroll.grid(row=0, column=0, sticky="nsew")
        self.schedule_card = self._card(self.schedule_scroll.body, 20)
        self.schedule_card.pack(fill="x")
        self._rebuild_schedule()
        return page

    def _rebuild_schedule(self, recalculate=True) -> None:
        for child in self.schedule_card.winfo_children():
            child.destroy()
        self.schedule_card.grid_columnconfigure(0, weight=1)
        self.schedule_editor = WorkScheduleEditor(self.schedule_card, self)
        self.schedule_editor.grid(row=0, column=0, sticky="ew")
        self._build_date_trace_section(self.schedule_card)
        if recalculate:
            self._recalculate_schedule(allow_empty=True)
        else:
            phases = self.project.schedule.get('phases', [])
            self.schedule_status_var.set('已保存排期：总计 {} 个工作日，计划结束 {}；修改后自动计算。'.format(
                sum(p['duration'] for p in phases), phases[-1]['finish']) if phases
                else '未排期：可以保存项目，生成前需设置开始日期。')
        self.schedule_scroll.scroll_to_top()

    def _build_date_trace_section(self, parent: tk.Frame) -> None:
        section = tk.Frame(parent, bg=COLORS["surface"])
        section.grid(row=3, column=0, sticky="ew", pady=(24, 0))
        ttk.Label(section, text="文档日期明细", style="Section.TLabel").pack(anchor="w")
        ttk.Label(section, text="日期由生命周期模型和中国工作日历自动计算，不支持手工修改。\n"
                  "日期绑定对应工作的开始或完成，不使用固定阶段偏移。",
                  style="SurfaceMuted.TLabel").pack(anchor="w", pady=(6, 12))
        self.date_trace_hint = ttk.Label(section, text="请先设置项目开始日期。", style="SurfaceMuted.TLabel")
        self.date_trace_hint.pack(anchor="w", pady=(0, 8))
        self.date_trace_vars: List[Tuple[str, tk.StringVar]] = []
        self.date_trace_groups = {}
        rows = date_trace_rows(self.model, self.project.schedule.get("dates", {}))
        for index, phase in enumerate(self.model.phases):
            group = tk.Frame(section, bg=COLORS["surface"])
            group.pack(fill="x", pady=(0, 8))
            button = tk.Button(group, anchor="w", bg=COLORS["soft"], fg=COLORS["text"],
                               activebackground=COLORS["soft"], activeforeground=COLORS["primary"],
                               relief="flat", bd=0, padx=12, pady=10, font=(FONT_FAMILY, 10, "bold"),
                               command=lambda key=phase.id: self._toggle_date_trace(key))
            button.pack(fill="x")
            table = tk.Frame(group, bg=COLORS["surface"], highlightthickness=1, highlightbackground=COLORS["border"])
            for column, weight in enumerate((3, 2, 1, 1)):
                table.grid_columnconfigure(column, weight=weight)
            for column, title in enumerate(("日期字段", "当前值", "基准节点", "计算规则")):
                tk.Label(table, text=title, anchor="w", bg=COLORS["soft"], fg=COLORS["muted"],
                         padx=10, pady=8, font=(FONT_FAMILY, 9, "bold")).grid(row=0, column=column, sticky="nsew")
            for row, (key, value, anchor, rule) in enumerate(rows[phase.id], start=1):
                background = COLORS["surface"] if row % 2 else "#FAFBFD"
                variable = tk.StringVar(master=self, value=value)
                self.date_trace_vars.append((key, variable))
                for column, text in enumerate((key, value, anchor, rule)):
                    label = tk.Label(table, anchor="w", bg=background, fg=COLORS["text"], padx=10, pady=8)
                    label.configure(**({"textvariable": variable} if column == 1 else {"text": text}))
                    label.grid(row=row, column=column, sticky="nsew")
            self.date_trace_groups[phase.id] = {"name": phase.name, "button": button, "table": table, "open": False}
            button.configure(text="▶ " + phase.name)
            if index == 0:
                self._toggle_date_trace(phase.id)
        self._refresh_date_trace()

    def _toggle_date_trace(self, phase_id: str) -> None:
        group = self.date_trace_groups[phase_id]
        group["open"] = not group["open"]
        group["button"].configure(text=("▼ " if group["open"] else "▶ ") + group["name"])
        if group["open"]:
            group["table"].pack(fill="x")
        else:
            group["table"].pack_forget()

    def _refresh_date_trace(self) -> None:
        dates = self.project.schedule.get("dates", {})
        for key, variable in self.date_trace_vars:
            variable.set(str(dates.get(key, "")))
        if dates:
            self.date_trace_hint.pack_forget()
        elif not self.date_trace_hint.winfo_manager():
            self.date_trace_hint.pack(anchor="w", pady=(0, 8), before=next(iter(self.date_trace_groups.values()))["button"].master)

    def _recalculate_schedule(self, allow_empty: bool = False) -> bool:
        if getattr(self, '_model_missing', False):
            self.schedule_status_var.set('项目模型尚未安装，请先导入对应生命周期模型。')
            return False
        if getattr(self, '_generation_busy', False):
            return False
        editor = getattr(self, 'schedule_editor', None)
        errors, hours = {}, {}
        for key, variable in self.schedule_hour_vars.items():
            raw = variable.get().strip()
            if not re.fullmatch(r'[0-9]+', raw) or int(raw) < 1:
                errors[key] = '请输入正整数小时'
            else:
                hours[key] = int(raw)
        start = self.schedule_start_var.get().strip()
        try:
            parsed = date.fromisoformat(start) if start else None
        except ValueError:
            errors['project_start'] = '请输入有效日期 YYYY-MM-DD'
        if editor:
            editor.show_errors(errors)
        if errors:
            self.schedule_status_var.set('无法计算：当前输入无效，显示上次有效结果；保存和导出前请修正。')
            return False
        try:
            self.project.schedule = self.model.calculate(parsed, hours, self.calendar)
        except (ValueError, IndexError) as error:
            self.schedule_status_var.set("无法计算：{}".format(error))
            if editor:
                editor.show_errors({'project_start': str(error)})
            return False
        for key in DATE_FIELDS:
            if key in self.field_vars:
                self.field_vars[key].set(str(self.project.schedule.get("dates", {}).get(key, "")))
        for variable in list(self.schedule_start_labels.values()) + list(self.schedule_finish_labels.values()):
            variable.set("")
        for phase in self.project.schedule.get("phases", []):
            self.schedule_start_labels[phase["id"]].set(phase["start"])
            self.schedule_finish_labels[phase["id"]].set(phase["finish"])
        if start:
            self.schedule_status_var.set("总计 {} 个工作日，计划结束 {}".format(
                sum(p["duration"] for p in self.project.schedule["phases"]), self.project.schedule["phases"][-1]["finish"]))
        else:
            self.schedule_status_var.set("未排期：可以保存项目，生成前需设置开始日期。")
        if editor:
            editor.render(self.project.schedule)
        self._refresh_date_trace()
        return True

    def _build_templates(self, parent: tk.Frame) -> tk.Frame:
        page, body = self._page(parent, "模板与文档", "选择模板，管理只读生命周期模型")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        toolbar = tk.Frame(body, bg=COLORS["canvas"])
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.model_label_var = tk.StringVar(value="{} {}".format(self.model.name, self.model.version))
        ttk.Label(toolbar, textvariable=self.model_label_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(toolbar, text="导出模型", style="Secondary.TButton", command=self._export_model).pack(side="right")
        ttk.Button(toolbar, text="导入模型", style="Secondary.TButton", command=self._import_model).pack(side="right", padx=(0, 8))
        ttk.Button(toolbar, text="检查模板", style="Primary.TButton", command=lambda: self._run_preflight(True)).pack(side="right", padx=(0, 8))
        card = self._card(body, 0)
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_rowconfigure(1, weight=1)
        card.grid_columnconfigure(0, weight=1)
        self.select_all_var = tk.IntVar(value=0)
        self.select_all_checkbox = ttk.Checkbutton(card, text="全选 / 全不选", variable=self.select_all_var,
                                                  command=self._select_all, style="Selection.TCheckbutton")
        self.select_all_checkbox.grid(row=0, column=0, sticky="w", padx=16, pady=10)
        self.template_checkbox_images = {}
        for checked in (False, True):
            image = tk.PhotoImage(master=self, width=16, height=16)
            image.put(COLORS['primary'] if checked else COLORS['border'], to=(0, 0, 16, 16))
            image.put(COLORS['primary'] if checked else '#FFFFFF', to=(1, 1, 15, 15))
            if checked:
                for x, y in ((3, 7), (4, 8), (5, 9), (6, 10), (7, 9), (8, 8), (9, 7), (10, 6), (11, 5), (12, 4)):
                    image.put('#FFFFFF', to=(x, y, x + 1, y + 2))
            self.template_checkbox_images[checked] = image
        columns = ("选择", "类型", "阶段", "模板", "版本", "状态")
        self.template_tree = ttk.Treeview(card, columns=columns, displaycolumns=columns[1:],
                                          show="tree headings", selectmode="browse")
        self.template_tree.heading('#0', text='生成')
        self.template_tree.column('#0', width=70, minwidth=70, stretch=False)
        for column, width in zip(columns, (70, 70, 130, 430, 100, 130)):
            self.template_tree.heading(column, text=column)
            self.template_tree.column(column, width=width, stretch=column == "模板")
        self.template_tree.bind("<ButtonRelease-1>", self._toggle_template)
        self.template_tree.bind("<space>", lambda _event: self._toggle_template())
        self.template_tree.bind("<Double-1>", self._template_double_click)
        self.template_tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.template_tree.yview)
        self.template_tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=1, sticky="ns")
        footer = tk.Frame(card, bg=COLORS["surface"], padx=16, pady=12)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(footer, text="勾选生成，取消勾选跳过；双击版本编辑。\n单元测试：FPGA生成仿真文档，其余生成单元文档；版本对应实际生成的文档。",
                  style="SurfaceMuted.TLabel").pack(side="left")
        return page

    def _refresh_templates(self) -> None:
        self._cancel_version_edit()
        self.templates = discover_all_templates(self.application_root, self.model)
        config = self.project.templates
        identities = {path: encode_template_id(path, self.application_root / "assets", self.model)
                      for _kind, _stage, path in self.templates}
        device = self.field_vars['器件类型'].get()
        enabled = set(selected_template_ids(config, identities.values(), device))
        self.template_enabled = {path: identities[path] in enabled for _kind, _stage, path in self.templates}
        if not hasattr(self, "template_tree"):
            return
        self.template_tree.delete(*self.template_tree.get_children())
        for kind, stage, path in self.templates:
            if device_test_template_id(identities[path], device) != identities[path]:
                continue
            version = config.get('document_versions', {}).get(identities[path], DEFAULT_DOCUMENT_VERSION) if kind == 'DOCX' else '—'
            selection = '生成' if self.template_enabled[path] else '跳过'
            name = Path(path).stem
            if identities[path].startswith('asset:templates/docx/'):
                name = re.sub(r"^\d+(?:\.\d+)*[.、\s_-]*", "", name)
                name = re.sub(r"_模板(?:[（(].*[）)])?$", "", name).strip()
            if identities[path] == 'asset:reference/workbooks/进度计划制定模板v1.2(简化模型).xlsx':
                name = '进度计划'
            for pair, label in zip(TEST_TEMPLATE_PAIRS, ('单元测试计划', '单元测试说明', '单元测试报告')):
                if identities[path] in pair:
                    name = label
                    break
            self.template_tree.insert("", "end", iid=path, image=self.template_checkbox_images[self.template_enabled[path]],
                                      values=(selection, kind, stage, name, version, "未检查"))
        self._sync_select_all()

    def _device_type_changed(self, *_args):
        if not getattr(self, '_generation_busy', False):
            self._refresh_templates()
            if hasattr(self, 'schedule_editor'):
                self.schedule_editor.refresh_names()

    def _template_double_click(self, event):
        if getattr(self, '_generation_busy', False):
            return 'break'
        row = self.template_tree.identify_row(event.y)
        column = self.template_tree.identify_column(event.x)
        if row and column and self.template_tree.column(column, 'id') == '版本':
            self._edit_template_version(row)
        return 'break'

    def _toggle_template(self, _event: Any = None) -> Optional[str]:
        if getattr(self, "_generation_busy", False):
            return
        if _event is not None and hasattr(_event, 'x') and hasattr(_event, 'y'):
            if self.template_tree.identify_column(_event.x) != '#0':
                return
            row = self.template_tree.identify_row(_event.y)
            if not row:
                return
            self.template_tree.selection_set(row)
            self.template_tree.focus(row)
            self.template_tree.focus_set()
        selected = self.template_tree.selection()
        if selected:
            path = selected[0]
            template_id = encode_template_id(path, self.application_root / 'assets', self.model)
            if device_test_template_id(template_id, self.field_vars['器件类型'].get()) != template_id:
                return
            self.template_enabled[path] = not self.template_enabled[path]
            values = list(self.template_tree.item(path, "values"))
            values[0], values[5] = ("生成" if self.template_enabled[path] else "跳过"), "未检查"
            self.template_tree.item(path, values=values, image=self.template_checkbox_images[self.template_enabled[path]])
            self._store_template_selection()
            self._sync_select_all()
            return 'break'

    def _sync_select_all(self):
        enabled = [self.template_enabled[path] for path in self.template_tree.get_children()]
        self.select_all_var.set(int(bool(enabled) and all(enabled)))
        self.select_all_checkbox.state(['alternate' if any(enabled) and not all(enabled) else '!alternate'])

    def _store_template_selection(self) -> None:
        # Explicit edits switch to custom; simply opening/saving keeps default.
        self.project.templates = dict(self.project.templates, mode="custom", enabled_ids=[
            encode_template_id(path, self.application_root / "assets", self.model)
            for path, enabled in self.template_enabled.items() if enabled])

    def _cancel_version_edit(self, _event=None):
        editor = getattr(self, '_version_editor', None)
        self._version_editor = None
        if editor is not None and editor.winfo_exists():
            editor.destroy()

    def _set_template_version(self, path, value):
        if getattr(self, '_generation_busy', False):
            return False
        if Path(path).suffix.lower() != '.docx':
            raise ProjectError('XLSX 不支持文档版本', 'INVALID_ARGUMENT')
        version = normalize_version(value)
        template_id = encode_template_id(path, self.application_root / 'assets', self.model)
        config = deepcopy(self.project.templates)
        versions = config.setdefault('document_versions', {})
        old = versions.get(template_id, DEFAULT_DOCUMENT_VERSION)
        if version == old:
            return False
        versions[template_id] = version
        self.project.templates = normalize_template_config(config)
        self.template_tree.set(path, '版本', version)
        for row in self.template_tree.get_children():
            if self.template_tree.set(row, '类型') == 'DOCX':
                self.template_tree.set(row, '状态', '未检查')
        return True

    def _edit_template_version(self, path):
        self._cancel_version_edit()
        if getattr(self, '_generation_busy', False) or Path(path).suffix.lower() != '.docx':
            return
        box = self.template_tree.bbox(path, '版本')
        if not box:
            return
        editor = ttk.Entry(self.template_tree)
        self._version_editor = editor
        editor.insert(0, self.template_tree.set(path, '版本'))
        editor.place(x=box[0], y=box[1], width=box[2], height=box[3])
        editor.focus_set()
        editor.selection_range(0, 'end')

        def commit(_event):
            value = editor.get()
            self._cancel_version_edit()
            try:
                self._set_template_version(path, value)
            except ProjectError as error:
                messagebox.showerror('文档版本无效', str(error), parent=self)
            return 'break'

        editor.bind('<Return>', commit)
        editor.bind('<Escape>', lambda event: self._cancel_version_edit())
        editor.bind('<FocusOut>', self._cancel_version_edit)

    def _select_all(self) -> None:
        if getattr(self, "_generation_busy", False):
            return
        enabled = bool(self.select_all_var.get())
        for path in self.template_enabled:
            template_id = encode_template_id(path, self.application_root / 'assets', self.model)
            self.template_enabled[path] = enabled and device_test_template_id(template_id, self.field_vars['器件类型'].get()) == template_id
            if self.template_tree.exists(path):
                values = list(self.template_tree.item(path, "values"))
                values[0], values[5] = ("生成" if self.template_enabled[path] else "跳过"), "未检查"
                self.template_tree.item(path, values=values, image=self.template_checkbox_images[self.template_enabled[path]])
        self._store_template_selection()
        self._sync_select_all()

    def _build_generate(self, parent: tk.Frame) -> tk.Frame:
        page, body = self._page(parent, "批量生成", "检查输入后生成 DOCX 和 XLSX")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)
        settings = self._card(body, 22)
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(settings, text="生成设置", style="Section.TLabel").pack(anchor="w", pady=(0, 18))
        ttk.Label(settings, text="输出目录", style="SurfaceMuted.TLabel").pack(anchor="w")
        row = tk.Frame(settings, bg=COLORS["surface"])
        row.pack(fill="x", pady=(6, 18))
        ttk.Entry(row, textvariable=self.output_dir_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览", style="Secondary.TButton", command=self._choose_output).pack(side="left", padx=(8, 0))
        self.generate_summary_var = tk.StringVar()
        tk.Label(settings, textvariable=self.generate_summary_var, bg=COLORS["soft"], fg=COLORS["text"], padx=16, pady=14, anchor="w", font=(FONT_FAMILY, 12, "bold")).pack(fill="x")
        ttk.Button(settings, text="开始批量生成", style="Primary.TButton", command=self._generate).pack(fill="x", pady=(22, 0))
        ttk.Style(self).configure("Prism.Horizontal.TProgressbar", background=COLORS["primary"],
                                  troughcolor=COLORS["soft"], borderwidth=0)
        self.generate_progress = ttk.Progressbar(settings, mode="determinate", maximum=100,
                                                style="Prism.Horizontal.TProgressbar")
        self.generate_progress.pack(fill="x", pady=(16, 6))
        self.generate_progress_var = tk.StringVar(value="尚未开始生成")
        progress_label = ttk.Label(settings, textvariable=self.generate_progress_var, style="SurfaceMuted.TLabel")
        progress_label.pack(fill="x")
        progress_label.bind("<Configure>", lambda event: progress_label.configure(wraplength=max(1, event.width)))
        checks = self._card(body, 22)
        checks.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ttk.Label(checks, text="生成前检查", style="Section.TLabel").pack(anchor="w", pady=(0, 18))
        self.preflight_text = tk.Text(checks, wrap="word", relief="flat", bg=COLORS["surface"], fg=COLORS["text"], state="disabled", font=(FONT_FAMILY, 9))
        self.preflight_text.pack(fill="both", expand=True)
        ttk.Button(checks, text="检查", style="Secondary.TButton", command=lambda: self._run_preflight(True)).pack(fill="x", pady=(12, 0))
        return page

    def _run_preflight(self, show_dialog: bool) -> bool:
        self._collect_fields()
        if not self._recalculate_schedule():
            if show_dialog:
                messagebox.showerror('检查模板', self.schedule_status_var.get(), parent=self)
            return False
        context = self.project.context()
        selected = [Path(path) for path, enabled in self.template_enabled.items() if enabled]
        identities = {path: encode_template_id(path, self.application_root / 'assets', self.model)
                      for _kind, _stage, path in self.templates}
        checks = [inspect_template(path, context_for_template(
            path, context, identities[str(path)], self.project.templates.get('document_versions', {}),
            identities.values())) for path in selected]
        structure_issues = inspect_items(self.project)
        errors = [check for check in checks if check.error]
        missing = sorted({key for check in checks for key in check.missing})
        lines = ["已选择 {} 个模板".format(len(selected)), "模板读取错误：{}".format(len(errors)), "未提供字段：{}".format(len(missing))]
        if structure_issues:
            lines.append("\n结构化数据待补充（{} 项）：\n".format(len(structure_issues)) + "\n".join(structure_issues))
        if missing:
            lines.append("\n前 12 项：\n" + "、".join(missing[:12]))
        if errors:
            lines.append("\n错误：\n" + "\n".join("{}：{}".format(check.path.name, check.error) for check in errors[:5]))
        self.preflight_text.configure(state="normal")
        self.preflight_text.delete("1.0", "end")
        self.preflight_text.insert("1.0", "\n".join(lines))
        self.preflight_text.configure(state="disabled")
        self.generate_summary_var.set("将生成 {} 个文件；{} 个字段尚未提供".format(len(selected), len(missing)))
        by_path = {str(check.path): check for check in checks}
        for path in self.template_tree.get_children():
            values = list(self.template_tree.item(path, "values"))
            check = by_path.get(path)
            values[5] = "跳过" if not self.template_enabled[path] else ("错误" if check and check.error else ("缺少字段" if check and check.missing else "可用"))
            self.template_tree.item(path, values=values)
        if show_dialog:
            messagebox.showinfo("模板检查", "\n".join(lines[:3]))
        return bool(selected) and not errors

    def _build_results(self, parent: tk.Frame) -> tk.Frame:
        page, body = self._page(parent, "生成结果", "查看每个输出文件的状态")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        toolbar = tk.Frame(body, bg=COLORS["canvas"])
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.result_summary_var = tk.StringVar(value="尚未执行生成")
        ttk.Label(toolbar, textvariable=self.result_summary_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(toolbar, text="打开输出目录", style="Primary.TButton", command=self._open_output).pack(side="right")
        card = self._card(body, 0)
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)
        columns = ("状态", "类型", "模板", "输出文件", "说明")
        self.result_tree = ttk.Treeview(card, columns=columns, show="headings")
        for column, width in zip(columns, (70, 70, 270, 330, 250)):
            self.result_tree.heading(column, text=column)
            self.result_tree.column(column, width=width, stretch=column in ("模板", "输出文件", "说明"))
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        return page

    def _collect_fields(self) -> Dict[str, Any]:
        self.test_environment_editor.flush()
        self.items_editor.flush()
        for key, variable in self.field_vars.items():
            if key in DATE_FIELDS:
                continue
            value = variable.get()
            if value != "" or key in self.project.fields:
                self.project.fields[key] = value
        return self.project.fields

    def _editable_projection(self) -> Dict[str, Any]:
        self.test_environment_editor.flush()
        self.items_editor.flush()
        fields = deepcopy(self.project.fields)
        for key, variable in self.field_vars.items():
            if key not in DATE_FIELDS:
                value = variable.get()
                if value != "" or key in fields:
                    fields[key] = value
        modules = {}
        for kind in ("features", "performances", "interfaces", "reliabilities"):
            modules[kind] = [(getattr(item, "module_id", None), deepcopy(dict(item)))
                             for item in getattr(self.project, kind)]
        return {
            "name": self.project.name,
            "model": (self.project.model_id, self.project.model_version),
            "fields": fields,
            "schedule_input": (
                self.schedule_start_var.get(),
                tuple((key, value.get()) for key, value in self.schedule_hour_vars.items()),
            ),
            "templates": deepcopy(self.project.templates),
            "modules": modules,
        }

    @property
    def dirty(self) -> bool:
        return self._clean_projection is not None and self._editable_projection() != self._clean_projection

    def _mark_clean(self) -> None:
        self.loaded_revision = self.project.loaded_revision
        self._clean_projection = self._editable_projection()

    def _load_ui(self, mark_clean: bool = True) -> None:
        for key, variable in self.field_vars.items():
            value = self.project.schedule.get("dates", {}).get(key, self.project.fields.get(key, ""))
            variable.set("" if value is None else str(value))
        self.project_name_var.set(self.project.name)
        self.output_dir_var.set(str(self.project.output_dir))
        found = self._find_model(self.project.model_id, self.project.model_version)
        self._model_missing = not bool(found)
        if found:
            self.model = load_model(found)
        self.model_label_var.set("{} {}".format(self.model.name, self.model.version) if found else
                                 '{} {}（模型尚未安装）'.format(self.project.model_id, self.project.model_version))
        self._rebuild_schedule(recalculate=False)
        self.items_editor.set_project(self.project)
        self._refresh_templates()
        self._refresh_overview()
        self.generated_results = []
        self._refresh_results()
        if mark_clean:
            self._mark_clean()

    def _confirm_discard_changes(self, action: str) -> bool:
        if not self.dirty:
            return True
        return messagebox.askyesno(
            "未保存修改",
            "当前存在未保存修改，{}将丢弃这些修改。\n\n是否丢弃并继续？".format(action),
            parent=self,
        )

    def _run_gui_write(self, writer, title: str):
        expected_revision = self.loaded_revision
        while True:
            try:
                return True, writer(expected_revision)
            except ProjectError as error:
                if error.code != "REVISION_CONFLICT":
                    raise
                actual_revision = getattr(error, "details", {}).get("actual_revision")
                if not actual_revision:
                    raise
                if not messagebox.askyesno(
                        title,
                        "项目已被外部修改。\n继续操作将覆盖外部修改。\n\n是否覆盖？",
                        parent=self):
                    return False, None
                expected_revision = actual_revision

    def _new_project(self) -> None:
        if getattr(self, "_generation_busy", False):
            return
        if not self._confirm_discard_changes("新建项目"):
            return
        base = filedialog.askdirectory(title="选择项目保存位置")
        if not base:
            return
        name = simpledialog.askstring("新建项目", "项目名称：", parent=self)
        if not name:
            return
        root = Path(base) / sanitize_filename(name)
        if (root / PROJECT_FILENAME).exists() or (root / 'project.json').exists():
            if not messagebox.askyesno("新建项目", "该项目已经存在，是否打开？"):
                return
            try:
                self.project = Project.load(root)
            except (OSError, ValueError) as error:
                messagebox.showerror('新建项目', str(error))
                return
        else:
            try:
                self.project = Project.create(root, name.strip())
            except (OSError, ValueError) as error:
                messagebox.showerror('新建项目', str(error))
                return
        self._load_ui()

    def _open_project(self) -> None:
        if getattr(self, "_generation_busy", False):
            return
        if not self._confirm_discard_changes("打开其他项目"):
            return
        path = filedialog.askdirectory(title="选择包含 基本数据.json 的项目目录")
        if path:
            try:
                self.project = Project.load(Path(path))
                self._load_ui()
            except (ProjectError, OSError, ValueError) as error:
                messagebox.showerror("打开项目", str(error))

    def _rename_project(self) -> None:
        if getattr(self, "_generation_busy", False):
            return
        name = simpledialog.askstring(
            "重命名项目", "项目显示名称（不修改文档项目名或目录）：",
            initialvalue=self.project.name, parent=self)
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("重命名项目", "项目名称不能为空或只包含空白。", parent=self)
            return
        if self.project.loaded_revision is None or self.dirty:
            if not self._save_project():
                return
        response = AgentService(application_root=self.application_root).call("project_rename", {
            "project": str(self.project.root.resolve()), "name": name,
            "expected_revision": self.loaded_revision,
        })
        if not response["ok"]:
            messagebox.showerror("重命名项目", response["error"]["message"], parent=self)
            return
        try:
            self.project = Project.load(self.project.root)
            self._load_ui()
        except (ProjectError, OSError, ValueError) as error:
            messagebox.showerror("重命名项目", "名称已保存，但重新读取项目失败：{}".format(error), parent=self)
            return
        self._toast("项目已重命名")

    def _refresh_project(self) -> None:
        if getattr(self, "_generation_busy", False):
            return
        if self.project.loaded_revision is None:
            messagebox.showinfo("刷新项目", "当前项目尚未保存。", parent=self)
            return
        if not self._confirm_discard_changes("刷新"):
            return
        try:
            self.project = Project.load(self.project.root)
            self._load_ui()
            self._toast("项目已刷新")
        except (ProjectError, OSError, ValueError) as error:
            messagebox.showerror("刷新项目", str(error), parent=self)

    def _save_project(self) -> bool:
        if getattr(self, "_generation_busy", False):
            return False
        try:
            self._collect_fields()
            if not self._recalculate_schedule(allow_empty=True):
                messagebox.showerror("保存项目", "进度规划填写有误，请修正后保存。已填写的项目数据仍保留在界面中。")
                return False
            target_root = self.project.root
            target_name = self.project.name
            if self.project.loaded_revision is None:
                base = filedialog.askdirectory(title="选择项目保存位置")
                if not base:
                    return False
                name = simpledialog.askstring("保存项目", "项目名称：", initialvalue=target_name, parent=self)
                if not name or not name.strip():
                    return False
                target_name = sanitize_filename(name.strip())
                target_root = Path(base) / target_name
                if (target_root / 'project.json').exists() and not (target_root / PROJECT_FILENAME).exists():
                    raise ProjectError('此目录为旧版项目；不支持旧项目格式迁移，请选择新目录。')
                if (target_root / PROJECT_FILENAME).exists():
                    raise ProjectError('新项目不能覆盖已有项目，请选择空目录或先打开目标项目。')
            pending = replace(
                self.project,
                root=target_root,
                name=target_name,
                fields=dict(self.project.fields),
                templates=deepcopy(self.project.templates),
                model_id=self.model.id,
                model_version=self.model.version,
            )
            if pending.loaded_revision is None:
                pending.save()
            else:
                written, result = self._run_gui_write(
                    lambda revision: gui_writes.save(pending, revision), "保存项目")
                if not written:
                    return False
                pending = result.project
            if self.output_dir_var.get() == str(self.project.output_dir):
                self.output_dir_var.set(str(pending.output_dir))
            self.project = pending
            self.items_editor.set_project(pending, preserve_expanded=True)
            self.project_name_var.set(self.project.name)
            self._mark_clean()
            self._toast("项目已保存")
            if self.project._storage_warnings:
                messagebox.showwarning('保存项目', '\n'.join(self.project._storage_warnings), parent=self)
            return True
        except (OSError, ValueError) as error:
            messagebox.showerror("保存项目", str(error))
            return False

    def _import_data(self) -> None:
        if getattr(self, "_generation_busy", False):
            return
        if not self.project.file_path.exists():
            messagebox.showinfo('导入数据', '请先保存项目，再导入 JSON 数据。', parent=self)
            return
        path = filedialog.askopenfilename(title='导入数据', filetypes=(('JSON 数据', '*.json'),), parent=self)
        if not path:
            return
        try:
            payload = read_json(Path(path))
            if isinstance(payload, dict) and isinstance(payload.get('data_type'), str) and payload['data_type'] in GROUP_FILES:
                self._import_group_data(payload)
                return
        except (OSError, ValueError) as error:
            messagebox.showerror('导入数据', str(error), parent=self)
            return
        # Flush widget contents into a candidate so cancellation does not change
        # the current project or files.
        self._collect_fields()
        if not self._recalculate_schedule(allow_empty=True):
            messagebox.showerror("导入数据", "进度规划填写有误，请修正后再导入。已填写的项目数据仍保留在界面中。")
            return
        candidate = deepcopy(self.project)
        overwrite_basic = replace_existing = False
        while True:
            try:
                written, outcome = self._run_gui_write(
                    lambda revision: gui_writes.import_data(
                        candidate, Path(path), overwrite_basic, replace_existing, revision, self.application_root),
                    "导入数据",
                )
                if not written:
                    return
                result = outcome.value
                self.project = outcome.project
                self._load_ui()
                if result['data_type'] in KINDS:
                    self.items_editor.select(KINDS[result['data_type']])
                    self.show_page('items')
                self._toast('数据已导入并保存')
                if result['warnings']:
                    messagebox.showwarning('导入数据', '\n'.join(result['warnings']), parent=self)
                return
            except DataConflict as error:
                if (error.code == 'BASIC_CONFLICT' and overwrite_basic) or (error.code != 'BASIC_CONFLICT' and replace_existing):
                    messagebox.showerror('导入数据', str(error), parent=self)
                    return
                if not messagebox.askyesno('导入数据', str(error), parent=self):
                    return
                if error.code == 'BASIC_CONFLICT':
                    overwrite_basic = True
                else:
                    replace_existing = True
            except (OSError, ValueError) as error:
                messagebox.showerror('导入数据', str(error), parent=self)
                return

    def _import_group_data(self, payload) -> None:
        validate_group(payload)
        if self.dirty:
            messagebox.showinfo('导入数据', '请先保存当前修改，再导入人员职责或测试环境。', parent=self)
            return
        revision = self.loaded_revision
        preview = gui_writes.import_group(self.project, payload, revision, dry_run=True)
        changes = preview.value['changes']
        if not changes:
            self._toast('数据相同，无需更新')
            return
        message = '将更新以下字段（未提供的字段保持不变）：\n\n' + '\n'.join(
            '{}\n  原值：{}\n  新值：{}'.format(row['field'],
                '（未设置）' if row['before'] is None else repr(row['before']), repr(row['after']))
            for row in changes)
        if not messagebox.askyesno('导入数据', message + '\n\n是否导入？', parent=self):
            return
        # Keep the validated payload frozen across confirmation. A changed
        # project must be reloaded and previewed, never silently overwritten.
        outcome = gui_writes.import_group(self.project, payload, revision)
        self.project = outcome.project
        self._load_ui()
        if payload['data_type'] == 'test_environment':
            self.show_page('test_environment')
        else:
            self.show_page('data')
        self._toast('组数据已导入并保存')

    def _remove_module(self, module_id, delete_file):
        if getattr(self, "_generation_busy", False):
            return
        self._collect_fields()
        if not self._recalculate_schedule(allow_empty=True):
            messagebox.showerror("删除模块", "进度规划填写有误，请修正后再删除。已填写的项目数据仍保留在界面中。")
            return
        written, outcome = self._run_gui_write(
            lambda revision: gui_writes.remove_module(self.project, module_id, delete_file, revision),
            "删除模块",
        )
        if not written:
            return
        result = outcome.value
        self.project = outcome.project
        self._load_ui()
        if result['warnings']:
            messagebox.showwarning('删除模块', '\n'.join(result['warnings']), parent=self)

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_dir_var.set(path)

    def _generate(self) -> None:
        if getattr(self, "_generation_busy", False):
            return
        if self.project.loaded_revision is None or self.dirty:
            if not self._save_project():
                return
        else:
            try:
                output_dir = self.output_dir_var.get()
                self.project = Project.load(self.project.root)
                self._load_ui()
                self.output_dir_var.set(output_dir)
            except (ProjectError, OSError, ValueError) as error:
                messagebox.showerror("批量生成", str(error), parent=self)
                return
        selected = [Path(path) for path, enabled in self.template_enabled.items() if enabled]
        if not selected:
            messagebox.showerror("批量生成", "请至少选择一个模板。")
            return
        output_value = self.output_dir_var.get().strip()
        if not output_value:
            messagebox.showerror("批量生成", "请选择输出目录。")
            return
        output = Path(output_value).resolve()
        if output.exists() and any(output.iterdir()) and not messagebox.askyesno("批量生成", "输出目录不是空目录。同名文件将被覆盖，是否继续？"):
            return
        self._generation_project_root = self.project.root.resolve()
        self._generation_output = output
        self._generation_events = queue.Queue()
        self.generate_progress.configure(value=0)
        self.generate_progress_var.set("准备生成 0/{} 个文件".format(len(selected)))
        self._set_generation_busy(True)
        try:
            worker = threading.Thread(target=generate_in_background,
                                      args=(self._generation_events, self.application_root,
                                            self._generation_project_root, output))
            worker.start()
        except Exception as error:
            self._set_generation_busy(False)
            self.generate_progress_var.set("无法启动生成")
            messagebox.showerror("批量生成", str(error))
            return
        self.after(100, self._poll_generation)

    def _set_generation_busy(self, busy: bool) -> None:
        if busy:
            self._cancel_version_edit()
        self._generation_busy = busy
        if busy:
            self._generation_controls = []
            pending = list(self.winfo_children())
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if isinstance(widget, ttk.Treeview):
                    self._generation_controls.append((widget, "tree"))
                    widget.state(["disabled"])
                elif isinstance(widget, (tk.Button, ttk.Button, ttk.Checkbutton, tk.Entry, ttk.Entry,
                                         ttk.Combobox, tk.Text, tk.Listbox)):
                    self._generation_controls.append((widget, widget.cget("state")))
                    widget.configure(state="disabled")
        else:
            for widget, state in self._generation_controls:
                if state == "tree":
                    widget.state(["!disabled"])
                else:
                    widget.configure(state=state)
            self._generation_controls = []

    def _poll_generation(self) -> None:
        while True:
            try:
                kind, payload = self._generation_events.get_nowait()
            except queue.Empty:
                break
            if kind == "file":
                completed, total, name = payload
                self.generate_progress_var.set("已处理 {}/{} 个文件，正在生成：{}".format(completed, total, name))
            elif kind == "progress":
                completed, total = payload
                self.generate_progress.configure(value=completed * 100 / total)
            elif kind == "saving":
                self.generate_progress_var.set("文件处理完成，正在整理结果……")
            elif kind == "done":
                self._set_generation_busy(False)
                response = payload
                output_dir = str(self._generation_output)
                try:
                    latest = Project.load(self._generation_project_root)
                    if self.project.root.resolve() == self._generation_project_root:
                        self.project = latest
                        self._load_ui()
                        self.output_dir_var.set(output_dir)
                except (ProjectError, OSError, ValueError) as error:
                    messagebox.showerror("批量生成", "生成完成，但无法刷新项目：{}".format(error))
                self.generated_results = list((response.get("data") or {}).get("results", []))
                success = sum(item.get("render_status") == "success"
                              for item in self.generated_results)
                self.generate_progress.configure(value=100)
                self.generate_progress_var.set("{}：{} 个成功，{} 个失败".format(
                    "完成" if response.get("ok") else "生成中断",
                    success, len(self.generated_results) - success))
                self._refresh_results()
                if not response.get("ok"):
                    error = response.get("error") or {}
                    messagebox.showerror("批量生成", error.get("message", "生成失败"))
                elif response.get("warnings"):
                    messagebox.showwarning(
                        "批量生成",
                        "\n".join(item.get("message", str(item))
                                  for item in response["warnings"]),
                        parent=self,
                    )
                self.show_page("results")
                return
        self.after(100, self._poll_generation)

    def _request_close(self) -> None:
        if getattr(self, "_generation_busy", False):
            messagebox.showinfo("正在生成", "请等待本批文件生成完成后再关闭程序。", parent=self)
            return
        if self.dirty:
            answer = SaveBeforeExitDialog(self, title="退出 PRISM").result
            if answer is None:
                return
            if answer and not self._save_project():
                return
        self.destroy()

    def _refresh_results(self) -> None:
        self.result_tree.delete(*self.result_tree.get_children())
        if not self.generated_results:
            for result in self.project.outputs.get("results", []):
                template = Path(result["template_id"].split(":")[-1])
                self.result_tree.insert("", "end", values=(result["render_status"], template.suffix.upper().lstrip("."),
                    template.name, result["output"] or "", "；".join(filter(None, [result["message"],
                        "Word: " + result["word_finalize_status"]] + result["warnings"]))))
            self.result_summary_var.set("最近正式批次：" + self.project.outputs["time"] if self.project.outputs else "尚未执行生成")
            return
        for result in self.generated_results:
            template = Path(result["template_id"].split(":")[-1])
            self.result_tree.insert("", "end", values=(
                result["render_status"], template.suffix.upper().lstrip("."),
                template.name, result.get("output") or "",
                "；".join(filter(None, [result.get("message", ""),
                    "Word: " + result.get("word_finalize_status", "not_applicable")] +
                    [item.get("message", str(item)) if isinstance(item, dict) else str(item)
                     for item in result.get("warnings", [])])),
            ))
        success = sum(result["render_status"] == "success" for result in self.generated_results)
        self.result_summary_var.set("完成：{} 个成功，{} 个失败".format(success, len(self.generated_results) - success))

    def _export_schedule(self) -> None:
        if self.project.loaded_revision is None or self.dirty:
            if not self._save_project():
                return
        else:
            try:
                self.project = Project.load(self.project.root)
                self._load_ui()
            except (ProjectError, OSError, ValueError) as error:
                messagebox.showerror("导出进度表", str(error), parent=self)
                return
        if "project_start" not in self.project.schedule:
            messagebox.showerror("导出进度表", "PROJECT_UNSCHEDULED：请先设置项目开始日期。", parent=self)
            return
        try:
            require_current_schedule(self.project, self.model, self.calendar)
        except (ProjectError, OSError, ValueError) as error:
            messagebox.showerror("导出进度表", str(error), parent=self)
            return
        template = self.application_root / "assets" / "reference" / "workbooks" / "进度计划制定模板v1.2(简化模型).xlsx"
        path = filedialog.asksaveasfilename(title="导出进度表", defaultextension=".xlsx", initialfile=template.name, filetypes=(("Excel 工作簿", "*.xlsx"),))
        if path:
            try:
                export_schedule_template(template, Path(path), self.project.context(), self.project.schedule, self.calendar)
                self._toast("进度表已导出")
            except Exception as error:
                messagebox.showerror("导出进度表", str(error))

    def _import_model(self) -> None:
        if getattr(self, "_generation_busy", False):
            return
        path = filedialog.askopenfilename(title="导入生命周期模型", filetypes=(("PRISM 模型", "*.prismmodel"),))
        if path:
            try:
                installed = import_model_package(Path(path), user_model_library())
                self.model = load_model(installed / "model.json")
                self.project.model_id = self.model.id
                self.project.model_version = self.model.version
                self._model_missing = False
                self.model_label_var.set("{} {}".format(self.model.name, self.model.version))
                self._rebuild_schedule()
                self._refresh_templates()
                self._toast("生命周期模型已导入")
            except (ModelError, OSError, ValueError) as error:
                messagebox.showerror("导入模型", str(error))

    def _export_model(self) -> None:
        path = filedialog.asksaveasfilename(title="导出生命周期模型", defaultextension=".prismmodel", initialfile="{}-{}.prismmodel".format(self.model.id, self.model.version), filetypes=(("PRISM 模型", "*.prismmodel"),))
        if path:
            try:
                export_model_package(self.model.source_dir, Path(path))
                self._toast("生命周期模型已导出")
            except (ModelError, OSError, ValueError) as error:
                messagebox.showerror("导出模型", str(error))

    def _find_model(self, model_id: str, version: str) -> Optional[Path]:
        candidates = (self.application_root / "assets" / "models" / model_id / "model.json", self.application_root / "assets" / "models" / "{}-{}".format(model_id, version) / "model.json", user_model_library() / "{}-{}".format(model_id, version) / "model.json")
        return next((path for path in candidates if path.is_file()), None)

    def _reveal_user_manual(self) -> None:
        path = self.application_root / "assets" / "guides" / "PRISM使用手册.pdf"
        if not path.is_file():
            messagebox.showerror("使用手册", "缺少手册文件，请检查软件目录是否完整：\n{}".format(path), parent=self)
            return
        explorer = Path(os.environ["SystemRoot"]) / "explorer.exe"
        try:
            subprocess.Popen('"{}" /select,"{}"'.format(explorer, path), executable=str(explorer))
        except OSError as error:
            messagebox.showerror("使用手册", "无法打开资源管理器。手册位置：\n{}\n{}".format(path, error), parent=self)

    def _open_output(self) -> None:
        path = Path(self.output_dir_var.get())
        if path.exists() and hasattr(os, "startfile"):
            os.startfile(str(path))

    def _toast(self, message: str) -> None:
        toast = tk.Toplevel(self)
        toast.overrideredirect(True)
        tk.Label(toast, text=message, bg=COLORS["nav"], fg="#FFFFFF", padx=18, pady=10).pack()
        self.update_idletasks()
        toast.geometry("+{}+{}".format(self.winfo_rootx() + self.winfo_width() - toast.winfo_reqwidth() - 28, self.winfo_rooty() + self.winfo_height() - toast.winfo_reqheight() - 28))
        toast.after(1800, toast.destroy)


def main() -> None:
    """Start PRISM."""
    PrismApp().mainloop()
