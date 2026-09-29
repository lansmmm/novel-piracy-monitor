# -*- coding: utf-8 -*-
"""搜索词（后缀）编辑对话框 —— 主程序和手动搜索共用同一个，编辑方式完全一样。

特点：
- 列表里每一项都能勾选（点第一列或按空格切换），不用再手打一串后缀
- 全选 / 全不选、新增、删除选中、导入默认搜索词
- 和主程序一模一样的外观和操作

用法：
    from suffix_editor import open_suffix_editor
    open_suffix_editor(parent, "编辑搜索词 - 书名", "说明文字",
                       suffixes, on_ok, default_suffixes=app.default_suffixes)

    on_ok(新的搜索词列表) 在点「确定」时被调用，窗口自己会关掉。
"""

import re
import tkinter as tk
from tkinter import messagebox, ttk

from utils import normalize_suffixes
from window_helper import fix_show_desktop_return

COLOR_BG       = "#F2F5F7"
COLOR_CARD     = "#FFFFFF"
COLOR_BORDER   = "#E1E5EA"
COLOR_PRIMARY  = "#2A9D8F"
COLOR_TEXT     = "#2F3542"
COLOR_MUTED    = "#7A869A"
COLOR_GHOST    = "#ECEFF1"
COLOR_GHOST_FG = "#455A64"

FONT_TITLE  = ("Microsoft YaHei", 13, "bold")
FONT_NORMAL = ("Microsoft YaHei", 10)
FONT_SMALL  = ("Microsoft YaHei", 9)


def _make_btn(parent, text, command, bg=None, fg="white",
              width=10, bold=False, small=False):
    if bg is None:
        bg, fg = COLOR_GHOST, COLOR_GHOST_FG
    font = ("Microsoft YaHei", 9 if small else 10, "bold" if bold else "normal")
    return tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg,
        activebackground=bg, activeforeground=fg,
        relief="flat", bd=0, cursor="hand2",
        width=width,
        font=font, padx=8, pady=5,
        highlightthickness=0,
    )


def open_suffix_editor(parent, title, hint, suffixes, on_ok, default_suffixes=None):
    """打开搜索词编辑窗口。parent 传主窗口或任意 Toplevel 都行。"""
    suffixes = [dict(s) if isinstance(s, dict)
                else {"text": str(s or ""), "enabled": True}
                for s in (suffixes or [])]
    suffixes = normalize_suffixes(suffixes)
    default_suffixes = default_suffixes or []

    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry("600x660")
    win.configure(bg=COLOR_BG)
    try:
        win.transient(parent.winfo_toplevel())
    except Exception:
        pass
    win.grab_set()
    # ★ 点「显示桌面」把窗口藏起来后，还能从任务栏切回来
    fix_show_desktop_return(win, parent)

    tk.Label(win, text=title, bg=COLOR_BG, fg=COLOR_TEXT,
             font=FONT_TITLE).pack(pady=(16, 2))
    tk.Label(win, text=hint, bg=COLOR_BG, fg=COLOR_MUTED,
             font=FONT_SMALL, wraplength=540, justify="left").pack(padx=20)

    outer = tk.Frame(win, bg=COLOR_BORDER)
    outer.pack(fill="both", expand=True, padx=20, pady=12)
    body = tk.Frame(outer, bg=COLOR_CARD)
    body.pack(fill="both", expand=True, padx=1, pady=1)

    ctrl = tk.Frame(body, bg=COLOR_CARD)
    ctrl.pack(fill="x", padx=14, pady=(12, 6))
    _make_btn(ctrl, "✅ 全选", lambda: _select_all(), bg="#DCEDC8", fg="#33691E",
              width=8, small=True).pack(side="left", padx=(0, 6))
    _make_btn(ctrl, "⬜ 全不选", lambda: _select_none(), bg="#ECEFF1", fg="#455A64",
              width=8, small=True).pack(side="left")
    tk.Label(ctrl, text="点第一列或按空格切换；『无后缀』可取消勾选，但不可删除",
             bg=COLOR_CARD, fg=COLOR_MUTED,
             font=("Microsoft YaHei", 8)).pack(side="left", padx=10)

    tf = tk.Frame(body, bg=COLOR_CARD)
    tf.pack(fill="both", expand=True, padx=14, pady=(0, 6))
    tree = ttk.Treeview(tf, columns=("enabled", "text"),
                        show="headings", height=14, selectmode="browse")
    tree.heading("enabled", text="✔")
    tree.heading("text", text="后缀 / 搜索词")
    tree.column("enabled", width=60, anchor="center", stretch=False)
    tree.column("text", width=440, anchor="w")
    tree.pack(side="left", fill="both", expand=True)
    sb = ttk.Scrollbar(tf, orient="vertical", command=tree.yview)
    sb.pack(side="right", fill="y")
    tree.config(yscrollcommand=sb.set)
    tree.tag_configure("no_suffix", foreground="#1976D2")
    tree.tag_configure("disabled", foreground="#9AA5B1")

    def refresh_tree():
        for i in tree.get_children():
            tree.delete(i)
        for idx, s in enumerate(suffixes):
            is_no = (s["text"] == "")
            display = "（无后缀 - 直接用书名）" if is_no else s["text"]
            mark = "☑" if s["enabled"] else "☐"
            tag = "no_suffix" if is_no else ("disabled" if not s["enabled"] else "")
            tree.insert("", "end", iid=str(idx), values=(mark, display),
                        tags=(tag,) if tag else ())

    def toggle_row(iid):
        try:
            idx = int(iid)
        except Exception:
            return
        if 0 <= idx < len(suffixes):
            suffixes[idx]["enabled"] = not suffixes[idx]["enabled"]
            refresh_tree()

    def _select_all():
        for s in suffixes:
            s["enabled"] = True
        refresh_tree()

    def _select_none():
        for s in suffixes:
            s["enabled"] = False
        refresh_tree()

    def on_click(event):
        if tree.identify_column(event.x) != "#1":
            return
        row = tree.identify_row(event.y)
        if row:
            toggle_row(row)

    def on_space(event):
        sel = tree.selection()
        if sel:
            toggle_row(sel[0])

    tree.bind("<Button-1>", on_click)
    tree.bind("<space>", on_space)

    row = tk.Frame(body, bg=COLOR_CARD)
    row.pack(fill="x", padx=14, pady=(0, 6))
    ent = tk.Entry(row, font=FONT_NORMAL,
                   bd=1, relief="solid", highlightthickness=0,
                   bg="#FAFBFC", fg=COLOR_TEXT, insertbackground=COLOR_TEXT)
    ent.pack(side="left", fill="x", expand=True, ipady=4)

    def add():
        v = ent.get().strip()
        if not v:
            return
        exists = set(s["text"] for s in suffixes)
        for part in re.split(r'[,，、;；\s]+', v):
            part = part.strip()
            if part and part not in exists:
                suffixes.append({"text": part, "enabled": True})
                exists.add(part)
        ent.delete(0, tk.END)
        refresh_tree()

    ent.bind("<Return>", lambda e: add())
    _make_btn(row, "➕ 新增", add, bg=COLOR_PRIMARY,
              width=8, small=True).pack(side="left", padx=(6, 0))

    btns = tk.Frame(body, bg=COLOR_CARD)
    btns.pack(fill="x", padx=14, pady=(4, 12))

    def del_sel():
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中要删除的后缀。", parent=win)
            return
        idx = int(sel[0])
        if 0 <= idx < len(suffixes):
            s = suffixes[idx]
            if s["text"] == "":
                messagebox.showinfo("提示", "『无后缀』条目不可删除。", parent=win)
                return
            suffixes.pop(idx)
            refresh_tree()

    def import_default():
        exists = set(s["text"] for s in suffixes)
        for s in default_suffixes:
            t = s.get("text", "") if isinstance(s, dict) else str(s or "")
            if t and t not in exists:
                suffixes.append({"text": t, "enabled": True})
                exists.add(t)
        refresh_tree()

    _make_btn(btns, "🗑️ 删除选中", del_sel, bg="#EF5350",
              width=11, small=True).pack(side="left")
    _make_btn(btns, "📥 导入默认搜索词", import_default,
              bg="#7E57C2", width=16, small=True).pack(side="left", padx=8)

    def ok():
        result = [dict(s) for s in suffixes]
        on_ok(result)
        win.destroy()

    _make_btn(btns, "取 消", win.destroy, bg="#B0BEC5",
              fg="#263238", width=8, small=True).pack(side="right")
    _make_btn(btns, "✅ 确 定", ok, bg=COLOR_PRIMARY,
              width=10, small=True, bold=True).pack(side="right", padx=8)

    refresh_tree()
    ent.focus_set()
    return win
