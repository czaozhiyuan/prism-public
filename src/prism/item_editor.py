"""Collapsible editor for dynamic project objects and nested tests."""

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .app import COLORS, FONT_FAMILY
from .items import ITEM_TYPES, ITEM_FIELDS, TEST_TYPES, new_item, new_test, test_fields, EXECUTION_STATUSES
from .storage import name_key, module_filename, remove_module


class TestSummary(tk.Frame):
    """Read-only badges for the two explicit test-record categories."""

    def __init__(self, parent, background):
        super().__init__(parent, bg=background)
        self.badges = {}
        self.counts = {}
        for category, label in TEST_TYPES:
            badge = tk.Frame(self, highlightthickness=1, padx=7, pady=5)
            badge.pack(side="left", padx=(6, 0))
            tk.Label(badge, text=label, fg=COLORS["muted"],
                     font=(FONT_FAMILY, 9)).pack(side="left")
            count = tk.Label(badge, font=(FONT_FAMILY, 9, "bold"))
            count.pack(side="left", padx=4)
            tk.Label(badge, text="通过", fg=COLORS["muted"],
                     font=(FONT_FAMILY, 9)).pack(side="left")
            self.badges[category] = badge
            self.counts[category] = count
        self.update_counts(())

    def update_counts(self, items):
        items = tuple(items)
        for category, _label in TEST_TYPES:
            tests = [test for item in items for test in item[category]]
            total = len(tests)
            passed = sum(test.get("execution_status") == "passed" for test in tests)
            complete = total > 0 and passed == total
            background = "#ECFDF3" if complete else "#EFF6FF"
            border = "#B7E7CC" if complete else "#C7DBFF"
            badge = self.badges[category]
            badge.configure(bg=background, highlightbackground=border)
            for child in badge.winfo_children():
                child.configure(bg=background)
            value = "{}/{}".format(passed, total)
            self.counts[category].configure(text=value, width=max(5, len(value)),
                fg=COLORS["success"] if complete else COLORS["primary"])


class ItemEditor(tk.Frame):
    def __init__(self, parent, project, scroll_class, on_change=None):
        super().__init__(parent, bg=COLORS["canvas"])
        self.project = project
        self.on_remove = None
        self.on_change = on_change
        self.kind = "features"
        self.expanded = set()
        self.bindings = []
        bar = tk.Frame(self, bg=COLORS["surface"])
        bar.pack(fill="x")
        self.tabs = {}
        for kind, label in ITEM_TYPES:
            button = tk.Button(bar, text=label, relief="flat", bd=0, pady=10,
                               command=lambda key=kind: self.select(key))
            button.pack(side="left", fill="x", expand=True)
            self.tabs[kind] = button
        toolbar = tk.Frame(self, bg=COLORS["canvas"])
        toolbar.pack(fill="x", pady=10)
        self.count = ttk.Label(toolbar, style="Muted.TLabel")
        self.count.pack(side="left")
        self.add_button = ttk.Button(toolbar, style="Primary.TButton", command=self.add)
        self.add_button.pack(side="right")
        self.scroll = scroll_class(self)
        self.scroll.pack(fill="both", expand=True)
        self.rebuild()

    def set_project(self, project, preserve_expanded=False):
        expanded = set()
        if preserve_expanded:
            # A save replaces the snapshot without reordering modules or tests.
            for kind, _label in ITEM_TYPES:
                for previous, current in zip(getattr(self.project, kind), getattr(project, kind)):
                    if id(previous) in self.expanded:
                        expanded.add(id(current))
                    for category, _test_label in TEST_TYPES:
                        for old_test, new_test in zip(previous[category], current[category]):
                            if id(old_test) in self.expanded:
                                expanded.add(id(new_test))
        self.project = project
        self.expanded = expanded
        self.bindings = []
        self.rebuild()

    def flush(self):
        for data, key, get in self.bindings:
            data[key] = get()

    def select(self, kind):
        changed = kind != self.kind
        self.flush()
        self.kind = kind
        self.rebuild()
        if changed:
            self.scroll.scroll_to_top()

    def add(self):
        self.flush()
        name = simpledialog.askstring('新增' + dict(ITEM_TYPES)[self.kind], '请输入真实名称：', parent=self)
        if name is None:
            return
        try:
            filename = module_filename(self.kind, name)
            if any(name_key(value['name']) == name_key(name) for value in getattr(self.project, self.kind)):
                raise ValueError('同类别已有该名称，请使用导入数据明确替换，或输入其他名称。')
            if (self.project.root / filename).exists():
                raise ValueError('本地已有同名数据文件，请使用导入数据。')
        except ValueError as error:
            messagebox.showerror('新增模块', str(error), parent=self)
            return
        item = new_item(self.kind)
        item['name'] = name
        getattr(self.project, self.kind).append(item)
        self.expanded.add(id(item))
        self.rebuild()
        self.after_idle(lambda: self.scroll.canvas.yview_moveto(1))

    def change(self, items, index, action, label):
        self.flush()
        if action == "delete":
            item = items[index]
            detail = ""
            if "unit_tests" in item:
                detail = "该{}包含 {} 个单元测试项和 {} 个配置项测试项。\n相关数据将一并删除。\n".format(
                    label, len(item["unit_tests"]), len(item["config_tests"]))
                entry = self.project._module_files.get(getattr(item, 'module_id', ''))
                if entry:
                    answer = messagebox.askyesnocancel('删除' + label,
                        detail + '是否同步删除本地数据文件 {}？\n是：移除并删除文件\n否：仅移除，保留文件\n取消：不作修改'.format(entry['file']), parent=self)
                    if answer is None:
                        return
                    try:
                        if self.on_remove:
                            self.on_remove(item.module_id, answer)
                        else:
                            remove_module(self.project, item.module_id, answer)
                            self.set_project(self.project)
                    except (OSError, ValueError) as error:
                        messagebox.showerror('删除模块', str(error), parent=self)
                    return
            if not messagebox.askyesno("删除" + label, detail + "确认删除此{}？".format(label), parent=self):
                return
            self.expanded.discard(id(item))
            del items[index]
        else:
            target = index + (-1 if action == "up" else 1)
            if 0 <= target < len(items):
                items[index], items[target] = items[target], items[index]
        self.rebuild()

    def add_test(self, parent, category):
        self.flush()
        test = new_test(category)
        parent[category].append(test)
        self.expanded.add(id(test))
        self.rebuild()

    def toggle(self, data):
        self.flush()
        key = id(data)
        if key in self.expanded:
            self.expanded.remove(key)
        else:
            self.expanded.add(key)
        self.rebuild()

    def group(self, parent, items, index, label):
        data = items[index]
        outer = tk.Frame(parent, bg=COLORS["surface"], highlightthickness=1,
                         highlightbackground=COLORS["border"])
        outer.pack(fill="x", padx=10, pady=5)
        header = tk.Frame(outer, bg=COLORS["soft"])
        header.pack(fill="x")
        for text, action in (("删除", "delete"), ("下移", "down"), ("上移", "up")):
            button = ttk.Button(header, text=text, width=5,
                                command=lambda value=action: self.change(items, index, value, label))
            button.pack(side="right", padx=2, pady=5)
            if (action == "up" and index == 0) or (action == "down" and index == len(items)-1):
                button.configure(state="disabled")
        if "unit_tests" in data:
            summary = TestSummary(header, COLORS["soft"])
            summary.pack(side="right", padx=(0, 12), pady=5)
            self.item_summaries[id(data)] = summary
        else:
            badge = tk.Label(header, width=6, padx=8, pady=5, highlightthickness=1,
                             font=(FONT_FAMILY, 9, "bold"))
            badge.pack(side="right", padx=(0, 12), pady=5)
            self.test_status_badges[id(data)] = badge
        title = tk.StringVar(master=self)
        def refresh_title():
            title.set("{} {} {}：{}".format("▼" if id(data) in self.expanded else "▶",
                                           label, index+1, data["name"] or "未命名"))
        refresh_title()
        button = tk.Button(header, textvariable=title, anchor="w", relief="flat", bd=0,
                           bg=COLORS["soft"], fg=COLORS["text"], padx=12, pady=10,
                           font=(FONT_FAMILY, 10, "bold"), command=lambda: self.toggle(data))
        button.pack(side="left", fill="x", expand=True)
        button.bind("<Configure>", lambda event: event.widget.configure(
            wraplength=max(1, event.width - 24), justify="left"))
        if id(data) not in self.expanded:
            return None, refresh_title
        body = tk.Frame(outer, bg=COLORS["surface"], padx=12, pady=10)
        body.pack(fill="x")
        return body, refresh_title

    def fields(self, body, data, fields, refresh_title):
        for key, label in fields:
            ttk.Label(body, text=label, style="SurfaceMuted.TLabel").pack(anchor="w", pady=(6, 3))
            if key == "execution_status":
                variable = tk.StringVar(master=self, value=EXECUTION_STATUSES[data[key]])
                choices = {label: value for value, label in EXECUTION_STATUSES.items()}
                entry = ttk.Combobox(body, textvariable=variable,
                                     values=list(choices), state="readonly")
                entry.bind("<MouseWheel>", lambda event: self.scroll.scroll_wheel(event.delta))
                entry.pack(fill="x")
                get = lambda value=variable, mapping=choices: mapping[value.get()]
                def changed(*args, obj=data, field=key, read=get):
                    obj[field] = read()
                    self.refresh_summaries()
                variable.trace_add("write", changed)
                self.bindings.append((data, key, get))
            elif key == "result_image":
                variable = tk.StringVar(master=self, value=data.get(key, ""))
                line = tk.Frame(body, bg=COLORS["surface"])
                line.pack(fill="x")
                ttk.Entry(line, textvariable=variable).pack(side="left", fill="x", expand=True)
                def choose(value=variable):
                    path = filedialog.askopenfilename(title="选择测试结果图片", parent=self,
                                                     filetypes=(("图片", "*.png *.jpg *.jpeg"),))
                    if path:
                        value.set(path)
                ttk.Button(line, text="选择图片", command=choose).pack(side="left", padx=4)
                ttk.Button(line, text="清除", command=lambda value=variable: value.set("")).pack(side="left")
                variable.trace_add("write", lambda *args, obj=data, field=key, value=variable: obj.__setitem__(field, value.get()))
                self.bindings.append((data, key, variable.get))
            elif key in ("name", "code", "direction", "actual_date"):
                variable = tk.StringVar(master=self, value=data[key])
                entry = ttk.Entry(body, textvariable=variable)
                entry.pack(fill="x")
                def changed(*args, obj=data, field=key, value=variable):
                    obj[field] = value.get()
                    if field == "name":
                        refresh_title()
                variable.trace_add("write", changed)
                self.bindings.append((data, key, variable.get))
            else:
                text = tk.Text(body, height=3, wrap="word", relief="flat", padx=8, pady=6,
                               bg=COLORS["surface"], fg=COLORS["text"], font=(FONT_FAMILY, 10),
                               highlightthickness=1, highlightbackground=COLORS["border"], undo=True)
                text.insert("1.0", data[key])
                text.pack(fill="x")
                get = lambda widget=text: widget.get("1.0", "end-1c")
                self.bindings.append((data, key, get))
                text.edit_modified(False)
                def changed(event, obj=data, field=key, widget=text):
                    if widget.edit_modified():
                        obj[field] = widget.get("1.0", "end-1c")
                        widget.edit_modified(False)
                text.bind("<<Modified>>", changed)
                text.bind("<MouseWheel>", lambda event: self.scroll.scroll_wheel(event.delta))

    def _refresh_test_badge(self, test):
        status = test["execution_status"]
        if status == "passed":
            background, border, foreground = "#ECFDF3", "#B7E7CC", COLORS["success"]
        elif status == "failed":
            background, border, foreground = "#FEF3F2", "#FECDCA", COLORS["danger"]
        else:
            background, border, foreground = COLORS["canvas"], COLORS["border"], COLORS["muted"]
        self.test_status_badges[id(test)].configure(text=EXECUTION_STATUSES[status],
            bg=background, fg=foreground, highlightbackground=border)

    def refresh_summaries(self):
        for item in getattr(self.project, self.kind):
            self.item_summaries[id(item)].update_counts((item,))
            for category, _label in TEST_TYPES:
                for test in item[category]:
                    if id(test) in self.test_status_badges:
                        self._refresh_test_badge(test)
        if self.on_change:
            self.on_change()

    def rebuild(self):
        position = self.scroll.canvas.canvasy(0)
        self.bindings = []
        self.item_summaries = {}
        self.test_status_badges = {}
        for child in self.scroll.body.winfo_children():
            child.destroy()
        label = dict(ITEM_TYPES)[self.kind]
        items = getattr(self.project, self.kind)
        self.count.configure(text="共 {} 项".format(len(items)))
        self.add_button.configure(text="+ 新增" + label)
        for kind, button in self.tabs.items():
            button.configure(bg=COLORS["primary"] if kind == self.kind else COLORS["surface"],
                             fg="white" if kind == self.kind else COLORS["text"])
        if not items:
            ttk.Label(self.scroll.body, text="尚无{}，点击上方按钮新增。".format(label),
                      style="SurfaceMuted.TLabel").pack(anchor="w", padx=18, pady=20)
        for index, item in enumerate(items):
            body, title = self.group(self.scroll.body, items, index, label)
            if body is None:
                continue
            self.fields(body, item, ITEM_FIELDS[self.kind], title)
            for category, test_label in TEST_TYPES:
                ttk.Label(body, text=test_label, style="Section.TLabel").pack(anchor="w", pady=(18, 5))
                for number, test in enumerate(item[category]):
                    test_body, test_title = self.group(body, item[category], number, "测试项")
                    if test_body is not None:
                        self.fields(test_body, test, test_fields(category), test_title)
                ttk.Button(body, text="+ 新增" + test_label + "项", style="Secondary.TButton",
                           command=lambda parent=item, key=category: self.add_test(parent, key)).pack(anchor="w", pady=8)
        self.refresh_summaries()
        # Settle nested geometry before restoring a view clamped to the new content.
        self.scroll.body.update_idletasks()
        self.scroll.update_scrollregion()
        self.scroll.canvas.yview_moveto(position / self.scroll.body.winfo_reqheight())
