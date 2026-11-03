"""Tk GUI: edit RCS login, robots / routes, and search across them."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from config_io import (
    CallRouteRow,
    EditableConfig,
    LeaveRouteRow,
    RcsSettings,
    RobotRow,
    SearchHit,
    load_editable,
    resolve_default_config,
    save_editable,
    search_config,
    validate_rows,
)


def project_root() -> Path:
    """Repo root in source tree; directory of the .exe when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


class RowDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        title: str,
        fields: list[tuple[str, str, int]],
        initial: dict[str, str] | None = None,
        show_password_keys: set[str] | None = None,
    ):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.result: dict[str, str] | None = None
        self.transient(master)
        self.grab_set()

        initial = initial or {}
        show_password_keys = show_password_keys or set()
        self._vars: dict[str, tk.StringVar] = {}
        frm = ttk.Frame(self, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")

        for i, (key, label, width) in enumerate(fields):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=4, padx=(0, 8))
            var = tk.StringVar(value=initial.get(key, ""))
            self._vars[key] = var
            show = "*" if key in show_password_keys else ""
            ttk.Entry(frm, textvariable=var, width=width, show=show).grid(
                row=i, column=1, sticky="ew", pady=4
            )

        btns = ttk.Frame(frm)
        btns.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(btns, text="确定", command=self._ok).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(50, self._focus_first)
        self.wait_window(self)

    def _focus_first(self) -> None:
        for child in self.winfo_children():
            for sub in child.winfo_children():
                if isinstance(sub, ttk.Entry):
                    sub.focus_set()
                    return

    def _ok(self) -> None:
        self.result = {k: v.get().strip() for k, v in self._vars.items()}
        self.destroy()


class TableTab(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        columns: list[tuple[str, str, int]],
        on_change: Callable[[], None],
    ):
        super().__init__(master)
        self.on_change = on_change
        self.columns = columns
        self._rows: list[Any] = []

        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(toolbar, text="新增", command=self.add_row).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="编辑", command=self.edit_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="删除", command=self.delete_selected).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="上移", command=lambda: self.move_selected(-1)).pack(
            side=tk.LEFT, padx=(12, 4)
        )
        ttk.Button(toolbar, text="下移", command=lambda: self.move_selected(1)).pack(side=tk.LEFT)

        col_ids = [c[0] for c in columns]
        self.tree = ttk.Treeview(self, columns=col_ids, show="headings", height=12, selectmode="browse")
        for key, heading, width in columns:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor=tk.W)
        scroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", lambda _e: self.edit_selected())

    def set_rows(self, rows: list[Any]) -> None:
        self._rows = list(rows)
        self._refresh_tree()

    def get_rows(self) -> list[Any]:
        return list(self._rows)

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for idx, row in enumerate(self._rows):
            self.tree.insert("", tk.END, iid=str(idx), values=self.row_values(row))

    def selected_index(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def focus_index(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._rows):
            return
        iid = str(idx)
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)

    def add_row(self) -> None:
        data = self.prompt_row("新增", None)
        if data is None:
            return
        self._rows.append(self.dict_to_row(data))
        self._refresh_tree()
        self.on_change()

    def edit_selected(self) -> None:
        idx = self.selected_index()
        if idx is None:
            messagebox.showinfo("提示", "请先选中一行")
            return
        data = self.prompt_row("编辑", self.row_to_dict(self._rows[idx]))
        if data is None:
            return
        self._rows[idx] = self.dict_to_row(data)
        self._refresh_tree()
        self.tree.selection_set(str(idx))
        self.on_change()

    def delete_selected(self) -> None:
        idx = self.selected_index()
        if idx is None:
            messagebox.showinfo("提示", "请先选中一行")
            return
        if not messagebox.askyesno("确认", "删除选中行？"):
            return
        self._rows.pop(idx)
        self._refresh_tree()
        self.on_change()

    def move_selected(self, delta: int) -> None:
        idx = self.selected_index()
        if idx is None:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self._rows):
            return
        self._rows[idx], self._rows[new_idx] = self._rows[new_idx], self._rows[idx]
        self._refresh_tree()
        self.tree.selection_set(str(new_idx))
        self.on_change()

    def prompt_row(self, title: str, initial: dict[str, str] | None) -> dict[str, str] | None:
        raise NotImplementedError

    def row_values(self, row: Any) -> tuple[str, ...]:
        raise NotImplementedError

    def row_to_dict(self, row: Any) -> dict[str, str]:
        raise NotImplementedError

    def dict_to_row(self, data: dict[str, str]) -> Any:
        raise NotImplementedError


class RobotsTab(TableTab):
    def __init__(self, master: tk.Misc, on_change: Callable[[], None]):
        super().__init__(
            master,
            [
                ("code", "短码", 80),
                ("robot_id", "robotId", 80),
                ("name", "名称", 120),
                ("home", "停靠点 home", 100),
                ("map_id", "地图 mapId", 100),
            ],
            on_change,
        )

    def prompt_row(self, title: str, initial: dict[str, str] | None) -> dict[str, str] | None:
        dlg = RowDialog(
            self,
            title,
            [
                ("code", "短码（TCP robot）", 24),
                ("robot_id", "robotId", 24),
                ("name", "名称", 24),
                ("home_point_id", "停靠点 home_point_id", 24),
                ("map_id", "地图 map_id", 24),
            ],
            initial,
        )
        return dlg.result

    def row_values(self, row: RobotRow) -> tuple[str, ...]:
        return (row.code, row.robot_id, row.name, row.home_point_id, row.map_id)

    def row_to_dict(self, row: RobotRow) -> dict[str, str]:
        return {
            "code": row.code,
            "robot_id": row.robot_id,
            "name": row.name,
            "home_point_id": row.home_point_id,
            "map_id": row.map_id,
        }

    def dict_to_row(self, data: dict[str, str]) -> RobotRow:
        return RobotRow(
            code=data.get("code", ""),
            robot_id=data.get("robot_id", ""),
            name=data.get("name", ""),
            home_point_id=data.get("home_point_id", ""),
            map_id=data.get("map_id", ""),
        )


class CallRoutesTab(TableTab):
    def __init__(self, master: tk.Misc, on_change: Callable[[], None]):
        super().__init__(
            master,
            [
                ("robot", "短码", 80),
                ("start", "起点 start", 100),
                ("template_id", "模板 ID", 100),
                ("task_name", "任务名", 180),
            ],
            on_change,
        )

    def prompt_row(self, title: str, initial: dict[str, str] | None) -> dict[str, str] | None:
        dlg = RowDialog(
            self,
            title,
            [
                ("robot", "短码 robot", 24),
                ("start_point_id", "起点 start_point_id", 24),
                ("template_id", "模板 template_id", 24),
                ("task_name", "任务名 task_name", 24),
            ],
            initial,
        )
        return dlg.result

    def row_values(self, row: CallRouteRow) -> tuple[str, ...]:
        return (row.robot, row.start_point_id, row.template_id, row.task_name)

    def row_to_dict(self, row: CallRouteRow) -> dict[str, str]:
        return {
            "robot": row.robot,
            "start_point_id": row.start_point_id,
            "template_id": row.template_id,
            "task_name": row.task_name,
        }

    def dict_to_row(self, data: dict[str, str]) -> CallRouteRow:
        return CallRouteRow(
            robot=data.get("robot", ""),
            start_point_id=data.get("start_point_id", ""),
            template_id=data.get("template_id", ""),
            task_name=data.get("task_name", ""),
        )


class LeaveRoutesTab(TableTab):
    def __init__(self, master: tk.Misc, on_change: Callable[[], None]):
        super().__init__(
            master,
            [
                ("robot", "短码(可空)", 90),
                ("start", "起点 start", 100),
                ("end", "终点 end", 100),
                ("template_id", "模板 ID", 100),
                ("task_name", "任务名", 160),
            ],
            on_change,
        )

    def prompt_row(self, title: str, initial: dict[str, str] | None) -> dict[str, str] | None:
        dlg = RowDialog(
            self,
            title,
            [
                ("robot", "短码 robot（可空）", 24),
                ("start_point_id", "起点 start_point_id", 24),
                ("end_point_id", "终点 end_point_id", 24),
                ("template_id", "模板 template_id", 24),
                ("task_name", "任务名 task_name", 24),
            ],
            initial,
        )
        return dlg.result

    def row_values(self, row: LeaveRouteRow) -> tuple[str, ...]:
        return (row.robot, row.start_point_id, row.end_point_id, row.template_id, row.task_name)

    def row_to_dict(self, row: LeaveRouteRow) -> dict[str, str]:
        return {
            "robot": row.robot,
            "start_point_id": row.start_point_id,
            "end_point_id": row.end_point_id,
            "template_id": row.template_id,
            "task_name": row.task_name,
        }

    def dict_to_row(self, data: dict[str, str]) -> LeaveRouteRow:
        return LeaveRouteRow(
            robot=data.get("robot", ""),
            start_point_id=data.get("start_point_id", ""),
            end_point_id=data.get("end_point_id", ""),
            template_id=data.get("template_id", ""),
            task_name=data.get("task_name", ""),
        )


class RcsTab(ttk.Frame):
    """Edit [rcs] base_url / username / password."""

    def __init__(self, master: tk.Misc, on_change: Callable[[], None]):
        super().__init__(master, padding=12)
        self.on_change = on_change
        self._env = {"base_url": None, "username": None, "password": None}
        self._vars = {
            "base_url": tk.StringVar(),
            "username": tk.StringVar(),
            "password": tk.StringVar(),
        }
        self._show_pwd = tk.BooleanVar(value=False)

        hint = ttk.Label(
            self,
            text="对应 config 中 [rcs] 的登录地址与账号。若原值是 ${ENV:默认值}，保存时仍保留环境变量包装。",
            wraplength=720,
            foreground="#555",
        )
        hint.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))

        fields = [
            ("base_url", "base_url", 56),
            ("username", "username", 32),
            ("password", "password", 32),
        ]
        for i, (key, label, width) in enumerate(fields, start=1):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="w", pady=6, padx=(0, 8))
            show = "*" if key == "password" and not self._show_pwd.get() else ""
            entry = ttk.Entry(self, textvariable=self._vars[key], width=width, show=show)
            entry.grid(row=i, column=1, sticky="ew", pady=6)
            if key == "password":
                self._pwd_entry = entry
            self._vars[key].trace_add("write", lambda *_a: self.on_change())

        ttk.Checkbutton(
            self,
            text="显示密码",
            variable=self._show_pwd,
            command=self._toggle_password,
        ).grid(row=3, column=2, sticky="w", padx=(8, 0))

        self._env_label = ttk.Label(self, text="", foreground="#666")
        self._env_label.grid(row=4, column=0, columnspan=3, sticky="w", pady=(16, 0))
        self.columnconfigure(1, weight=1)

    def _toggle_password(self) -> None:
        self._pwd_entry.configure(show="" if self._show_pwd.get() else "*")

    def set_settings(self, rcs: RcsSettings) -> None:
        self._env = {
            "base_url": rcs.base_url_env,
            "username": rcs.username_env,
            "password": rcs.password_env,
        }
        # avoid mark_dirty while loading
        for key in ("base_url", "username", "password"):
            self._vars[key].set(getattr(rcs, key) or "")
        parts = []
        for key, env in self._env.items():
            if env:
                parts.append(f"{key}←${{{env}:…}}")
        self._env_label.configure(
            text=("环境变量包装：" + "，".join(parts)) if parts else "当前为明文写入（无 ${ENV} 包装）"
        )

    def get_settings(self) -> RcsSettings:
        return RcsSettings(
            base_url=self._vars["base_url"].get().strip(),
            username=self._vars["username"].get().strip(),
            password=self._vars["password"].get().strip(),
            base_url_env=self._env.get("base_url"),
            username_env=self._env.get("username"),
            password_env=self._env.get("password"),
        )


class SearchTab(ttk.Frame):
    """Search robots / CALL / LEAVE / RCS fields in a dedicated sub-tab."""

    def __init__(
        self,
        master: tk.Misc,
        get_cfg: Callable[[], EditableConfig | None],
        on_jump: Callable[[SearchHit], None],
    ):
        super().__init__(master, padding=8)
        self.get_cfg = get_cfg
        self.on_jump = on_jump
        self._hits: list[SearchHit] = []

        top = ttk.Frame(self)
        top.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(top, text="关键词").pack(side=tk.LEFT)
        self.query_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.query_var, width=36)
        entry.pack(side=tk.LEFT, padx=8)
        entry.bind("<Return>", lambda _e: self.run_search())
        ttk.Button(top, text="搜索", command=self.run_search).pack(side=tk.LEFT)
        ttk.Button(top, text="清空", command=self.clear_results).pack(side=tk.LEFT, padx=4)

        scopes = ttk.Frame(self)
        scopes.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(scopes, text="范围").pack(side=tk.LEFT)
        self.scope_vars = {
            "robots": tk.BooleanVar(value=True),
            "call_routes": tk.BooleanVar(value=True),
            "leave_routes": tk.BooleanVar(value=True),
            "rcs": tk.BooleanVar(value=True),
        }
        for key, label in (
            ("robots", "小车"),
            ("call_routes", "CALL"),
            ("leave_routes", "LEAVE"),
            ("rcs", "RCS登录"),
        ):
            ttk.Checkbutton(scopes, text=label, variable=self.scope_vars[key]).pack(
                side=tk.LEFT, padx=6
            )

        self.result_var = tk.StringVar(value="输入关键词后搜索小车 / 路由 / 登录配置")
        ttk.Label(self, textvariable=self.result_var, foreground="#555").pack(anchor=tk.W)

        cols = ("kind", "title", "detail")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=14, selectmode="browse")
        self.tree.heading("kind", text="类型")
        self.tree.heading("title", text="匹配项")
        self.tree.heading("detail", text="详情")
        self.tree.column("kind", width=90, anchor=tk.W)
        self.tree.column("title", width=180, anchor=tk.W)
        self.tree.column("detail", width=420, anchor=tk.W)
        scroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(6, 0))
        scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=(6, 0))
        self.tree.bind("<Double-1>", lambda _e: self.jump_selected())
        ttk.Button(self, text="跳转到该项", command=self.jump_selected).pack(anchor=tk.E, pady=6)

    def run_search(self) -> None:
        cfg = self.get_cfg()
        if cfg is None:
            messagebox.showinfo("提示", "请先打开配置文件")
            return
        scopes = [k for k, v in self.scope_vars.items() if v.get()]
        self._hits = search_config(cfg, self.query_var.get(), scopes)
        self.tree.delete(*self.tree.get_children())
        kind_label = {
            "robots": "小车",
            "call_routes": "CALL",
            "leave_routes": "LEAVE",
            "rcs": "RCS",
        }
        for i, hit in enumerate(self._hits):
            self.tree.insert(
                "",
                tk.END,
                iid=str(i),
                values=(kind_label.get(hit.kind, hit.kind), hit.title, hit.detail),
            )
        q = self.query_var.get().strip()
        self.result_var.set(f"“{q}” 共 {len(self._hits)} 条" if q else "请输入关键词")

    def clear_results(self) -> None:
        self.query_var.set("")
        self._hits = []
        self.tree.delete(*self.tree.get_children())
        self.result_var.set("输入关键词后搜索小车 / 路由 / 登录配置")

    def jump_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一条搜索结果")
            return
        hit = self._hits[int(sel[0])]
        self.on_jump(hit)


class ConfigEditorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("RCS 路由配置编辑器")
        self.geometry("920x560")
        self.minsize(760, 460)

        self.cfg: EditableConfig | None = None
        self.dirty = False
        self._loading = False

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_status()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        default = resolve_default_config(project_root())
        if default:
            self.load_path(default)
        else:
            self.set_status("未找到默认 config，请点击「打开」")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开…", command=self.open_file, accelerator="Ctrl+O")
        file_menu.add_command(label="保存", command=self.save_file, accelerator="Ctrl+S")
        file_menu.add_command(label="另存为…", command=self.save_file_as)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_close)
        menubar.add_cascade(label="文件", menu=file_menu)
        self.config(menu=menubar)
        self.bind_all("<Control-o>", lambda _e: self.open_file())
        self.bind_all("<Control-s>", lambda _e: self.save_file())
        self.bind_all("<Control-f>", lambda _e: self.focus_search())

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 8, 8, 0))
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="打开", command=self.open_file).pack(side=tk.LEFT)
        ttk.Button(bar, text="保存", command=self.save_file).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="另存为", command=self.save_file_as).pack(side=tk.LEFT)
        ttk.Button(bar, text="重新加载", command=self.reload_file).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(bar, text="搜索", command=self.focus_search).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(
            bar,
            text="可改 RCS 登录 / 小车 / CALL·LEAVE；Ctrl+F 打开搜索",
            foreground="#555",
        ).pack(side=tk.RIGHT)

    def _build_body(self) -> None:
        self.nb = ttk.Notebook(self, padding=8)
        self.nb.pack(fill=tk.BOTH, expand=True)
        self.rcs_tab = RcsTab(self.nb, self.mark_dirty)
        self.robots_tab = RobotsTab(self.nb, self.mark_dirty)
        self.call_tab = CallRoutesTab(self.nb, self.mark_dirty)
        self.leave_tab = LeaveRoutesTab(self.nb, self.mark_dirty)
        self.search_tab = SearchTab(self.nb, self._cfg_snapshot_for_search, self.jump_to_hit)
        self.nb.add(self.rcs_tab, text="RCS 登录")
        self.nb.add(self.robots_tab, text="小车绑定")
        self.nb.add(self.call_tab, text="CALL 路由（去站台）")
        self.nb.add(self.leave_tab, text="LEAVE 路由（去终点）")
        self.nb.add(self.search_tab, text="搜索")

    def _build_status(self) -> None:
        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, anchor=tk.W, padding=(8, 4)).pack(
            fill=tk.X, side=tk.BOTTOM
        )

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def mark_dirty(self) -> None:
        if self._loading:
            return
        self.dirty = True
        if self.cfg:
            self.set_status(f"* 未保存  |  {self.cfg.path}")

    def focus_search(self) -> None:
        self.nb.select(self.search_tab)
        self.search_tab.query_var.set(self.search_tab.query_var.get())
        for child in self.search_tab.winfo_children():
            if isinstance(child, ttk.Frame):
                for sub in child.winfo_children():
                    if isinstance(sub, ttk.Entry):
                        sub.focus_set()
                        sub.selection_range(0, tk.END)
                        return

    def _cfg_snapshot_for_search(self) -> EditableConfig | None:
        if not self.cfg:
            return None
        # include unsaved edits from tabs
        snap = EditableConfig(
            path=self.cfg.path,
            robots=self.robots_tab.get_rows(),
            call_routes=self.call_tab.get_rows(),
            leave_routes=self.leave_tab.get_rows(),
            rcs=self.rcs_tab.get_settings(),
            raw_text=self.cfg.raw_text,
        )
        return snap

    def jump_to_hit(self, hit: SearchHit) -> None:
        mapping = {
            "rcs": self.rcs_tab,
            "robots": self.robots_tab,
            "call_routes": self.call_tab,
            "leave_routes": self.leave_tab,
        }
        tab = mapping.get(hit.kind)
        if tab is None:
            return
        self.nb.select(tab)
        if hit.kind != "rcs" and hasattr(tab, "focus_index"):
            tab.focus_index(hit.index)

    def _sync_from_tabs(self) -> None:
        assert self.cfg is not None
        self.cfg.robots = self.robots_tab.get_rows()
        self.cfg.call_routes = self.call_tab.get_rows()
        self.cfg.leave_routes = self.leave_tab.get_rows()
        self.cfg.rcs = self.rcs_tab.get_settings()

    def _apply_to_tabs(self) -> None:
        assert self.cfg is not None
        self._loading = True
        try:
            self.rcs_tab.set_settings(self.cfg.rcs)
            self.robots_tab.set_rows(self.cfg.robots)
            self.call_tab.set_rows(self.cfg.call_routes)
            self.leave_tab.set_rows(self.cfg.leave_routes)
            self.search_tab.clear_results()
        finally:
            self._loading = False

    def ensure_can_discard(self) -> bool:
        if not self.dirty:
            return True
        return messagebox.askyesno("未保存", "有未保存的修改，继续将丢弃更改，是否继续？")

    def load_path(self, path: Path) -> None:
        try:
            self.cfg = load_editable(path)
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc))
            return
        self._apply_to_tabs()
        self.dirty = False
        self.set_status(f"已加载  |  {path}")

    def open_file(self) -> None:
        if not self.ensure_can_discard():
            return
        initial = project_root() / "config"
        path = filedialog.askopenfilename(
            title="打开配置文件",
            initialdir=str(initial if initial.is_dir() else project_root()),
            filetypes=[("Config", "*.config"), ("All", "*.*")],
        )
        if path:
            self.load_path(Path(path))

    def reload_file(self) -> None:
        if not self.cfg:
            messagebox.showinfo("提示", "当前没有已打开的文件")
            return
        if not self.ensure_can_discard():
            return
        self.load_path(self.cfg.path)

    def _collect_and_validate(self) -> bool:
        if not self.cfg:
            messagebox.showinfo("提示", "请先打开配置文件")
            return False
        self._sync_from_tabs()
        errors = validate_rows(
            self.cfg.robots,
            self.cfg.call_routes,
            self.cfg.leave_routes,
            self.cfg.rcs,
        )
        if errors:
            messagebox.showerror("校验失败", "\n".join(errors[:12]))
            return False
        return True

    def save_file(self) -> None:
        if not self.cfg:
            messagebox.showinfo("提示", "请先打开配置文件")
            return
        if not self._collect_and_validate():
            return
        try:
            path = save_editable(self.cfg)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.dirty = False
        self.set_status(f"已保存  |  {path}")
        messagebox.showinfo("保存成功", f"已写入：\n{path}")

    def save_file_as(self) -> None:
        if not self.cfg:
            messagebox.showinfo("提示", "请先打开配置文件")
            return
        if not self._collect_and_validate():
            return
        initial = self.cfg.path
        path = filedialog.asksaveasfilename(
            title="另存为",
            initialdir=str(initial.parent),
            initialfile=initial.name if initial.name != "config.example.config" else "config.config",
            defaultextension=".config",
            filetypes=[("Config", "*.config"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            saved = save_editable(self.cfg, Path(path))
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.dirty = False
        self.set_status(f"已保存  |  {saved}")
        messagebox.showinfo("保存成功", f"已写入：\n{saved}")

    def on_close(self) -> None:
        if not self.ensure_can_discard():
            return
        self.destroy()


def run() -> None:
    app = ConfigEditorApp()
    app.mainloop()
