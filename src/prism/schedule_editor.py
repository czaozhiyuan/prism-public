"""Collapsible work schedule controls; calculation remains in the shared model."""
import tkinter as tk
from tkinter import ttk

from .app import COLORS, FONT_FAMILY


def work_label(name, device):
    """Keep device-specific display names separate from stable work identities."""
    return name.replace('单元／仿真', '仿真' if str(device).strip().upper() == 'FPGA' else '单元')


class WorkScheduleEditor(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=COLORS['surface'])
        self.app = app
        self.groups, self.entries, self.work_labels = {}, {}, {}
        self.errors, self.error_labels = {}, {}
        self.phase_durations, self.work_starts, self.work_finishes, self.work_days = {}, {}, {}, {}
        self.columnconfigure(0, weight=1)
        controls = tk.Frame(self, bg=COLORS['surface'])
        controls.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        ttk.Label(controls, text='项目开始日期', style='SurfaceMuted.TLabel').pack(side='left')
        app.schedule_start_var = tk.StringVar(value=app.project.schedule.get('project_start', ''))
        self.start_entry = ttk.Entry(controls, width=14, textvariable=app.schedule_start_var)
        self.start_entry.pack(side='left', padx=(8, 12))
        ttk.Label(controls, text='YYYY-MM-DD', style='SurfaceMuted.TLabel').pack(side='left')
        ttk.Button(controls, text='导出进度表', style='Primary.TButton', command=app._export_schedule).pack(side='right')
        ttk.Button(controls, text='重新计算', style='Secondary.TButton', command=app._recalculate_schedule).pack(side='right', padx=8)
        ttk.Button(controls, text='恢复模型默认', style='Secondary.TButton', command=self.restore_defaults).pack(side='right')
        self.bind_input(self.start_entry)
        self.add_error('project_start', self, 1, 0)
        ttk.Label(self, text='展开阶段可修改单人计划工时（正整数小时）；工作日按每人每天8小时向上取整，后续工作和文档日期自动顺延。',
                  style='SurfaceMuted.TLabel').grid(row=2, column=0, sticky='w', pady=(0, 10))
        table = tk.Frame(self, bg=COLORS['surface'], highlightthickness=1, highlightbackground=COLORS['border'])
        table.grid(row=3, column=0, sticky='ew')
        heading = tk.Frame(table, bg=COLORS['soft'])
        heading.pack(fill='x')
        self.columns(heading)
        for column, title in enumerate(('阶段／工作内容', '单人计划工时（小时）', '工作日', '开始日期', '结束日期')):
            tk.Label(heading, text=title, bg=COLORS['soft'], fg=COLORS['muted'], pady=10,
                     font=(FONT_FAMILY, 9, 'bold')).grid(row=0, column=column, sticky='ew')
        stored = app.project.schedule.get('work_hours', {})
        app.schedule_hour_vars = {work.id: tk.StringVar(value=str(stored.get(work.id, work.default_hours)))
                                      for work in app.model.works}
        app.schedule_start_labels, app.schedule_finish_labels = {}, {}
        for phase in app.model.phases:
            group = tk.Frame(table, bg=COLORS['surface'])
            group.pack(fill='x')
            header = tk.Frame(group, bg=COLORS['soft'])
            header.pack(fill='x')
            self.columns(header)
            button = tk.Button(header, anchor='w', bg=COLORS['soft'], fg=COLORS['text'], relief='flat', bd=0,
                               padx=12, pady=10, font=(FONT_FAMILY, 10, 'bold'),
                               command=lambda key=phase.id: self.toggle(key))
            button.grid(row=0, column=0, sticky='ew')
            self.phase_durations[phase.id] = tk.StringVar()
            tk.Label(header, text='—', bg=COLORS['soft'], fg=COLORS['muted']).grid(row=0,column=1,sticky='ew')
            tk.Label(header, textvariable=self.phase_durations[phase.id], bg=COLORS['soft'], fg=COLORS['text'],
                     font=(FONT_FAMILY, 10, 'bold')).grid(row=0, column=2, sticky='ew')
            start, finish = tk.StringVar(value='未排期'), tk.StringVar(value='未排期')
            app.schedule_start_labels[phase.id], app.schedule_finish_labels[phase.id] = start, finish
            for column, var in ((3, start), (4, finish)):
                tk.Label(header, textvariable=var, bg=COLORS['soft'], fg=COLORS['text']).grid(row=0, column=column, sticky='ew')
            detail = tk.Frame(group, bg=COLORS['surface'])
            self.columns(detail)
            members = [work for work in app.model.works if work.phase_id == phase.id]
            for row, work in enumerate(members):
                label = tk.Label(detail, anchor='w', bg=COLORS['surface'], fg=COLORS['text'], padx=24, pady=8,
                                 wraplength=400, justify='left')
                label.grid(row=row, column=0, sticky='ew')
                self.work_labels[work.id] = label
                cell = tk.Frame(detail, bg=COLORS['surface'])
                cell.grid(row=row, column=1, sticky='ew', padx=10, pady=3)
                cell.columnconfigure(0, weight=1)
                entry = ttk.Entry(cell, width=7, justify='center', textvariable=app.schedule_hour_vars[work.id])
                entry.grid(row=0, column=0, sticky='ew')
                self.entries[work.id] = entry
                self.bind_input(entry)
                self.add_error(work.id, cell, 1, 0)
                self.work_days[work.id] = tk.StringVar()
                tk.Label(detail, textvariable=self.work_days[work.id], bg=COLORS['surface'],
                         fg=COLORS['text']).grid(row=row,column=2,sticky='ew')
                start, finish = tk.StringVar(value='未排期'), tk.StringVar(value='未排期')
                self.work_starts[work.id], self.work_finishes[work.id] = start, finish
                for column, var in ((3, start), (4, finish)):
                    tk.Label(detail, textvariable=var, bg=COLORS['surface'], fg=COLORS['text']).grid(row=row, column=column, sticky='ew')
            self.groups[phase.id] = dict(button=button, detail=detail, open=False, phase=phase)
        app.schedule_status_var = tk.StringVar()
        ttk.Label(self, textvariable=app.schedule_status_var, style='SurfaceMuted.TLabel', wraplength=900).grid(
            row=4, column=0, sticky='w', pady=(12, 0))
        self.refresh_names()
        self.render(app.project.schedule)

    @staticmethod
    def columns(frame):
        for column, weight in enumerate((7, 3, 2, 3, 3)):
            frame.columnconfigure(column, weight=weight, uniform='schedule', minsize=25)

    def bind_input(self, entry):
        entry.bind('<FocusOut>', self.commit_input)
        entry.bind('<Return>', self.commit_input)

    def commit_input(self, _event=None):
        if not getattr(self.app, '_generation_busy', False):
            self.app._recalculate_schedule()

    def add_error(self, key, parent, row, column):
        self.errors[key] = tk.StringVar()
        label = tk.Label(parent, textvariable=self.errors[key], bg=COLORS['surface'], fg=COLORS['danger'],
                         font=(FONT_FAMILY, 8))
        label.grid(row=row, column=column, sticky='w')
        label.grid_remove()
        self.error_labels[key] = label

    def show_errors(self, errors):
        for key, variable in self.errors.items():
            variable.set(errors.get(key, ''))
            entry = self.start_entry if key == 'project_start' else self.entries[key]
            entry.state(['invalid'] if key in errors else ['!invalid'])
            if key in errors:
                self.error_labels[key].grid()
            else:
                self.error_labels[key].grid_remove()

    def toggle(self, phase_id):
        group = self.groups[phase_id]
        group['open'] = not group['open']
        if group['open']:
            group['detail'].pack(fill='x')
        else:
            group['detail'].pack_forget()
        self.refresh_names()
        self.update_idletasks()
        self.app.schedule_scroll.update_scrollregion()

    def refresh_names(self):
        device = self.app.field_vars['器件类型'].get()
        for work in self.app.model.works:
            self.work_labels[work.id].configure(text=work_label(work.name, device))
        for group in self.groups.values():
            name = group['phase'].name.replace('单元测试', '仿真测试') if str(device).strip().upper() == 'FPGA' else group['phase'].name
            group['button'].configure(text=('▼  ' if group['open'] else '▶  ') + name)

    def restore_defaults(self):
        if getattr(self.app, '_generation_busy', False):
            return
        for work in self.app.model.works:
            self.app.schedule_hour_vars[work.id].set(str(work.default_hours))
        self.app._recalculate_schedule()

    def render(self, schedule):
        phases = {row['id']: row for row in schedule.get('phases', [])}
        works = {row['id']: row for row in schedule.get('work_items', [])}
        overrides = schedule.get('work_hours', {})
        for phase in self.app.model.phases:
            days = sum((overrides.get(w.id, w.default_hours)+7)//8 for w in self.app.model.works if w.phase_id == phase.id)
            self.phase_durations[phase.id].set('{} 合计'.format(days))
            self.app.schedule_start_labels[phase.id].set(phases.get(phase.id, {}).get('start', '未排期'))
            self.app.schedule_finish_labels[phase.id].set(phases.get(phase.id, {}).get('finish', '未排期'))
        for work in self.app.model.works:
            self.work_days[work.id].set(str((overrides.get(work.id,work.default_hours)+7)//8))
            self.work_starts[work.id].set(works.get(work.id, {}).get('start', '未排期'))
            self.work_finishes[work.id].set(works.get(work.id, {}).get('finish', '未排期'))
