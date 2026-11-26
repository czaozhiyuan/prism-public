"""Project-level test environment form bound to ordinary field variables."""
import tkinter as tk
from tkinter import ttk

from .app import COLORS, FONT_FAMILY
from .schema import TEST_ENVIRONMENT_ROWS
from .test_environment import instrument_names


class TestEnvironmentEditor(tk.Frame):
    def __init__(self, parent, field_vars, scroll_class, is_busy):
        super().__init__(parent, bg=COLORS["surface"])
        self.field_vars = field_vars
        self.is_busy = is_busy
        self._writing_instruments = False
        self.tabs = {}
        self.forms = {}
        bar = tk.Frame(self, bg=COLORS["surface"])
        bar.pack(fill="x")
        stack = tk.Frame(self, bg=COLORS["surface"])
        stack.pack(fill="both", expand=True)
        stack.grid_rowconfigure(0, weight=1)
        stack.grid_columnconfigure(0, weight=1)
        for key, label in (("impact", "测试环境的影响"), ("hardware", "硬件准备")):
            button = tk.Button(bar, text=label, relief="flat", bd=0, pady=10,
                               command=lambda value=key: self.select(value))
            button.pack(side="left", fill="x", expand=True)
            self.tabs[key] = button
            form = scroll_class(stack)
            form.grid(row=0, column=0, sticky="nsew")
            self.forms[key] = form
        self._build_impact(self.forms["impact"].body)
        self._build_hardware(self.forms["hardware"].body)
        self.select("impact")

    def select(self, key):
        if self.is_busy():
            return
        self.forms[key].tkraise()
        for name, button in self.tabs.items():
            button.configure(bg=COLORS["primary"] if name == key else COLORS["surface"],
                             fg="white" if name == key else COLORS["text"])

    def _build_impact(self, body):
        body.grid_columnconfigure(0, weight=1)
        ttk.Label(body, text="单元、仿真及配置项九份测试文档共用一套数据；不自动判断差异结论。",
                  style="SurfaceMuted.TLabel").grid(row=0, column=0, sticky="w", padx=18, pady=12)
        table = tk.Frame(body, bg=COLORS["surface"])
        table.grid(row=1, column=0, sticky="ew", padx=18)
        for column, title in enumerate(("分析项", "测试环境", "需求/实际使用环境", "差异结论")):
            table.grid_columnconfigure(column, weight=1 if column else 0)
            ttk.Label(table, text=title, style="SurfaceMuted.TLabel").grid(
                row=0, column=column, sticky="w", padx=4, pady=6)
        self.entries = {}
        for row, (label, fields) in enumerate(TEST_ENVIRONMENT_ROWS, 1):
            ttk.Label(table, text=label, wraplength=165, style="SurfaceMuted.TLabel").grid(
                row=row, column=0, sticky="w", padx=4, pady=7)
            for column, key in enumerate(fields, 1):
                if column == 3:
                    entry = ttk.Combobox(table, width=12, textvariable=self.field_vars[key],
                                         values=("无差异", "有差异", "不适用"))
                    entry.bind("<MouseWheel>", lambda event: self.forms["impact"].scroll_wheel(event.delta))
                else:
                    entry = ttk.Entry(table, width=18, textvariable=self.field_vars[key])
                entry.grid(row=row, column=column, sticky="ew", padx=4, pady=7)
                self.entries[key] = entry
        ttk.Label(body, text="工作环境填写完整描述及单位，例如20～30 ℃；示例不会自动保存。",
                  style="SurfaceMuted.TLabel").grid(row=2, column=0, sticky="w", padx=18, pady=8)
        ttk.Label(body, text="环境差异影响及测试有效性评估", style="SurfaceMuted.TLabel").grid(
            row=3, column=0, sticky="w", padx=18, pady=(12, 6))
        self.assessment = tk.Text(body, height=7, wrap="word", relief="flat", padx=8, pady=6,
                                  bg=COLORS["surface"], fg=COLORS["text"], font=(FONT_FAMILY, 10),
                                  highlightthickness=1, highlightbackground=COLORS["border"], undo=True)
        self.assessment.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 18))
        self.assessment.bind("<<Modified>>", self._assessment_changed)
        self.assessment.bind("<MouseWheel>", lambda event: self.forms["impact"].scroll_wheel(event.delta))
        self.field_vars["测试环境影响及有效性评估"].trace_add("write", self._load_assessment)
        self._load_assessment()

    def _load_assessment(self, *args):
        value = self.field_vars["测试环境影响及有效性评估"].get()
        if self.assessment.get("1.0", "end-1c") != value:
            state = self.assessment.cget("state")
            self.assessment.configure(state="normal")
            self.assessment.delete("1.0", "end")
            self.assessment.insert("1.0", value)
            self.assessment.edit_reset()
            self.assessment.edit_modified(False)
            self.assessment.configure(state=state)

    def _assessment_changed(self, event=None):
        if self.assessment.edit_modified():
            self.flush()

    def flush(self):
        value = self.assessment.get("1.0", "end-1c")
        if self.field_vars["测试环境影响及有效性评估"].get() != value:
            self.field_vars["测试环境影响及有效性评估"].set(value)
        self.assessment.edit_modified(False)

    def _build_hardware(self, body):
        ttk.Label(body, text="只填写仪器名称，可增加、删除和调整顺序。", style="SurfaceMuted.TLabel").pack(
            anchor="w", padx=18, pady=12)
        ttk.Label(body, text="检查内容：经过计量，在有效使用期内；检查结果：一致；符合性：符合。\n"
                  "单元及仿真测试取软件负责人和设计开始日期；配置项测试取测试人员和测试开始日期。\n"
                  "以上为后续模板绑定规则；P0仅维护数据。", style="SurfaceMuted.TLabel",
                  wraplength=740).pack(anchor="w", padx=18, pady=(0, 12))
        self.add_button = ttk.Button(body, text="新增仪器", command=self.add_instrument)
        self.add_button.pack(anchor="e", padx=18, pady=(0, 12))
        self.instrument_body = tk.Frame(body, bg=COLORS["surface"])
        self.instrument_body.pack(fill="x", padx=18, pady=(0, 18))
        self.field_vars["测试仪器名称"].trace_add("write", self._load_instruments)
        self._load_instruments()

    def _load_instruments(self, *args):
        if self._writing_instruments:
            return
        self.instrument_vars = [tk.StringVar(master=self, value=name)
                                for name in instrument_names(self.field_vars["测试仪器名称"].get())]
        for variable in self.instrument_vars:
            variable.trace_add("write", self._instruments_changed)
        self._render_instruments()

    def _render_instruments(self):
        for child in self.instrument_body.winfo_children():
            child.destroy()
        self.instrument_body.grid_columnconfigure(1, weight=1)
        self.instrument_entries = []
        for index, variable in enumerate(self.instrument_vars):
            ttk.Label(self.instrument_body, text=str(index + 1), style="SurfaceMuted.TLabel").grid(
                row=index, column=0, padx=(0, 10), pady=6)
            entry = ttk.Entry(self.instrument_body, textvariable=variable)
            entry.grid(row=index, column=1, sticky="ew", pady=6)
            self.instrument_entries.append(entry)
            for column, (label, action) in enumerate((("上移", -1), ("下移", 1), ("删除", 0)), 2):
                ttk.Button(self.instrument_body, text=label,
                           command=lambda position=index, delta=action: self.change_instrument(position, delta)).grid(
                    row=index, column=column, padx=(6, 0), pady=6)
        if not self.instrument_vars:
            ttk.Label(self.instrument_body, text="尚未填写仪器，点击“新增仪器”添加。",
                      style="SurfaceMuted.TLabel").grid(row=0, column=0, columnspan=5, sticky="w", pady=8)

    def _instruments_changed(self, *args):
        self._writing_instruments = True
        try:
            value = "\n".join(variable.get().strip() for variable in self.instrument_vars if variable.get().strip())
            if self.field_vars["测试仪器名称"].get() != value:
                self.field_vars["测试仪器名称"].set(value)
        finally:
            self._writing_instruments = False

    def add_instrument(self):
        if self.is_busy():
            return
        variable = tk.StringVar(master=self)
        variable.trace_add("write", self._instruments_changed)
        self.instrument_vars.append(variable)
        self._render_instruments()
        self.instrument_entries[-1].focus_set()

    def change_instrument(self, index, delta):
        if self.is_busy():
            return
        if delta == 0:
            self.instrument_vars.pop(index)
        elif 0 <= index + delta < len(self.instrument_vars):
            variable = self.instrument_vars.pop(index)
            self.instrument_vars.insert(index + delta, variable)
        else:
            return
        self._instruments_changed()
        self._render_instruments()
