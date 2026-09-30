# -*- coding: utf-8 -*-
"""手动搜索（demo.py 风格）：一键用浏览器打开搜索结果页。

特点：
- 书签直接读写主程序的 bookmarks.json（通过传入的 app 对象），保持关联
- ★ 一个书名一个窗口：同一个书名的所有标签页都开在同一个窗口里（不管有没有间隔时间）
- ★ 打开间隔只对夸克生效（夸克开太快会被挡），其他引擎几乎不等待
- ★ 编辑书签/搜索词与主程序完全一样（弹窗里勾选，不用手打一串后缀）
- 可选附带打开引擎的「额外链接」（如版权投诉页）
- 支持按书名 / 按搜索引擎两种模式，底部实时统计组合数量
"""

import json
import os
import re
import subprocess
import time
import uuid
import webbrowser
from urllib.parse import quote, urlsplit

import tkinter as tk
from tkinter import messagebox, simpledialog

from config import (BASE_DIR, MANUAL_SOURCE_ORDER, MANUAL_SOURCE_COLUMNS,
                    MANUAL_SOURCE_LABEL)
from engines import MANUAL_SEARCH_ENGINE_MAP
from suffix_editor import open_suffix_editor
from utils import enabled_suffix_texts, fmt_suffixes
from window_helper import fix_show_desktop_return

ENGINE_OVERRIDE_FILE = os.path.join(BASE_DIR, "manual_search_engines.json")

# 老的「腾讯邮箱」只是占位用的投诉页；遇到这个值就自动换成引擎现在的默认投诉页
# （微信 → mp.weixin.qq.com，微博 → 微博版权投诉页）
LEGACY_EXTRA_URLS = ("https://mail.qq.com/", "https://mail.qq.com")

# ==================== 打开间隔 ====================
# 只有夸克需要慢慢开（开太快会被风控），其他引擎只留一点点缓冲
# 夸克的 4 个入口都算上，以后在 config 里换哪个入口都不用改这里
QUARK_ENGINE_IDS = ("quark", "quark_m", "quark_so", "quark_cn")
QUARK_TAB_GAP  = 8.0     # 夸克：两个标签页之间等 5 秒
NORMAL_TAB_GAP = 0.25    # 其他引擎：0.25 秒


def _engine_host(url_tpl):
    """从搜索网址里取域名，如 https://quark.sm.cn/s?q={kw} → quark.sm.cn"""
    try:
        host = urlsplit(str(url_tpl or "")).netloc.lower()
    except Exception:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _engine_display_name(short_name, url_tpl):
    """引擎显示名 = 短名字 + 域名（如「头条 so.toutiao.com」）"""
    short = str(short_name or "").strip()
    host = _engine_host(url_tpl)
    if not host or host in short:
        return short
    return f"{short} {host}"

COLOR_BG        = "#F2F5F7"
COLOR_TEXT      = "#2F3542"
COLOR_MUTED     = "#7A869A"
COLOR_PRIMARY   = "#2A9D8F"
COLOR_PRIMARY_D = "#21867A"
COLOR_SEL_BG    = "#C8E6C9"
COLOR_SEL_FG    = "#2E7D32"
COLOR_IDLE_BG   = "#ECEFF1"
COLOR_IDLE_FG   = "#455A64"

FONT_NORMAL = ("Microsoft YaHei", 10)
FONT_BOLD   = ("Microsoft YaHei", 11, "bold")
FONT_SMALL  = ("Microsoft YaHei", 9)


# ==================== 后缀工具 ====================
def _book_texts(sufs):
    out = []
    for s in sufs or []:
        if isinstance(s, dict):
            out.append(str(s.get("text", "") or ""))
        else:
            out.append(str(s or ""))
    return out


# ==================== 引擎配置 ====================
def _load_engine_overrides():
    try:
        with open(ENGINE_OVERRIDE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_engine_overrides(data):
    try:
        with open(ENGINE_OVERRIDE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _build_engine_list():
    """把引擎类 + 用户覆盖配置，合成 demo 风格的引擎字典列表。"""
    overrides = _load_engine_overrides()
    order = [sid for sid in MANUAL_SOURCE_ORDER if sid in MANUAL_SEARCH_ENGINE_MAP]
    for sid in MANUAL_SEARCH_ENGINE_MAP:
        if sid not in order:
            order.append(sid)

    engines = []
    seen = set()
    for sid in order:
        cls = MANUAL_SEARCH_ENGINE_MAP[sid]
        ov = overrides.get(sid)
        ov = ov if isinstance(ov, dict) else {}
        url_tpl = ov.get("url_tpl") or getattr(cls, "SEARCH_URL", "") or ""
        short = ov.get("name") or MANUAL_SOURCE_LABEL.get(sid) or cls.SOURCE_LABEL
        extra_url = (ov.get("extra_url", getattr(cls, "EXTRA_URL", "") or "") or "").strip()
        if extra_url in LEGACY_EXTRA_URLS:
            # 老的占位投诉页，换成这个引擎现在的默认投诉页
            extra_url = getattr(cls, "EXTRA_URL", "") or ""
        engines.append({
            "id": sid,
            # ★ 名字后面自动带上域名，一眼能看出是 PC 还是移动（头条 so.toutiao.com / 头条 m.toutiao.com）
            "name": _engine_display_name(short, url_tpl),
            "url_tpl": url_tpl,
            "extra_url": extra_url,
        })
        seen.add(sid)

    # ★ 用户自己新增的引擎（写在 manual_search_engines.json 里、代码里没有的）也显示，
    #    名字一样自动带上域名
    for sid, ov in overrides.items():
        if sid in seen or not isinstance(ov, dict):
            continue
        url_tpl = ov.get("url_tpl") or ""
        if not url_tpl:
            continue
        engines.append({
            "id": sid,
            "name": _engine_display_name(ov.get("name") or sid, url_tpl),
            "url_tpl": url_tpl,
            "extra_url": ov.get("extra_url", "") or "",
        })
        seen.add(sid)
    return engines


def _tab_gap(engine):
    """两个标签页之间等多久：只有夸克要等，其他引擎几乎不等"""
    if engine and engine.get("id") in QUARK_ENGINE_IDS:
        return QUARK_TAB_GAP
    return NORMAL_TAB_GAP


def _find_browser():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


# ==================== 主对话框 ====================
class ManualSearchDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.engines = _build_engine_list()

        self.title("手动搜索 - 一键打开")
        self.geometry("940x720")
        self.minsize(860, 620)
        self.configure(bg=COLOR_BG)
        # 主窗口如果缩在托盘里（withdrawn），就不做子窗口 —— 否则任务栏上没有按钮，切不回来
        try:
            if app.root.state() != "withdrawn":
                self.transient(app.root)
        except Exception:
            pass
        self.grab_set()
        # ★ 点「显示桌面」把窗口藏起来后，还能从任务栏切回来
        fix_show_desktop_return(self, app.root)

        self.mode = tk.StringVar(value="book")
        self.selected_book_id = tk.StringVar()
        self.selected_engine_id = tk.StringVar()
        self.engine_vars = {}
        self.book_vars = {}
        self.all_check = tk.BooleanVar(value=True)

        self._init_selection()
        self._build_ui()
        self._rebuild_all()

    # ---------- 数据 ----------
    def _bookmarks(self):
        return [b for b in getattr(self.app, "bookmarks", []) if b.get("name")]

    def _book_label(self, b):
        texts = _book_texts(b.get("suffixes"))
        enabled = enabled_suffix_texts(b.get("suffixes"))
        n = len(enabled)
        has_no = any(t == "" for t in enabled)
        if has_no and n > 1:
            return f"{b['name']} [{n-1}+无]"
        if has_no and n == 1:
            return f"{b['name']} [无后缀]"
        if n == 0:
            return f"{b['name']} [无可用]"
        return f"{b['name']} [{n}档]"

    def _init_selection(self):
        bms = self._bookmarks()
        if bms:
            self.selected_book_id.set(bms[0].get("id", ""))
        if self.engines:
            self.selected_engine_id.set(self.engines[0]["id"])

    def _persist_bookmarks(self):
        try:
            self.app.save_bookmarks()
        except Exception:
            pass
        try:
            self.app.refresh_bookmarks()
        except Exception:
            pass

    # ---------- UI ----------
    def _build_ui(self):
        top = tk.Frame(self, bg=COLOR_BG, pady=8)
        top.pack(fill="x", padx=12)

        tk.Label(top, text="手动搜索", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Microsoft YaHei", 13, "bold")).pack(side="left", padx=(0, 16))

        tk.Button(top, text="📝 编辑书签", command=self._edit_bookmarks,
                  bg="#FFE082", fg="#5D4037", relief="flat", padx=14, pady=5,
                  font=FONT_NORMAL, cursor="hand2").pack(side="left", padx=4)
        tk.Button(top, text="⚙️ 编辑搜索引擎", command=self._edit_engines,
                  bg="#E6EE9C", fg="#33691E", relief="flat", padx=14, pady=5,
                  font=FONT_NORMAL, cursor="hand2").pack(side="left", padx=4)

        mode_frame = tk.Frame(self, bg=COLOR_BG)
        mode_frame.pack(fill="x", padx=12, pady=(0, 8))

        self.mode_btn_book = tk.Button(
            mode_frame, text="按书名", font=FONT_BOLD,
            bg=COLOR_SEL_BG, fg=COLOR_SEL_FG, relief="flat", padx=22, pady=8,
            cursor="hand2", command=lambda: self._set_mode("book"))
        self.mode_btn_book.pack(side="left", padx=(0, 8))

        self.mode_btn_engine = tk.Button(
            mode_frame, text="按搜索引擎", font=FONT_BOLD,
            bg=COLOR_IDLE_BG, fg=COLOR_IDLE_FG, relief="flat", padx=22, pady=8,
            cursor="hand2", command=lambda: self._set_mode("engine"))
        self.mode_btn_engine.pack(side="left")

        body = tk.Frame(self, bg=COLOR_BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        left = tk.Frame(body, bg="#FFFFFF", bd=1, relief="solid")
        left.pack(side="left", fill="y", padx=(0, 6))
        left.configure(width=230)
        left.pack_propagate(False)
        self.left_label = tk.Label(left, text="书名", bg="#FFFFFF",
                                   font=FONT_BOLD, anchor="w")
        self.left_label.pack(fill="x", padx=10, pady=(10, 6))

        # ★ 列表长了能滚（「按搜索引擎」里引擎多，底下的不会被挡住）
        left_wrap = tk.Frame(left, bg="#FFFFFF")
        left_wrap.pack(fill="both", expand=True, padx=(8, 2), pady=(0, 8))
        left_wrap.rowconfigure(0, weight=1)
        left_wrap.columnconfigure(0, weight=1)
        self.left_canvas = tk.Canvas(left_wrap, bg="#FFFFFF", bd=0,
                                     highlightthickness=0)
        self.left_bar = tk.Scrollbar(left_wrap, orient="vertical",
                                     command=self.left_canvas.yview)
        self.left_canvas.configure(yscrollcommand=self._on_left_scroll)
        self.left_canvas.grid(row=0, column=0, sticky="nsew")
        self.left_bar.grid(row=0, column=1, sticky="ns")
        self.left_bar.grid_remove()      # 不需要滚动时先不占位置
        self.left_inner = tk.Frame(self.left_canvas, bg="#FFFFFF")
        self._left_win = self.left_canvas.create_window((0, 0), window=self.left_inner,
                                                        anchor="nw")
        self.left_inner.bind("<Configure>", self._on_left_inner_config)
        self.left_canvas.bind("<Configure>", self._on_left_canvas_config)
        for w in (self.left_canvas, self.left_inner):
            w.bind("<MouseWheel>", self._on_left_mousewheel)

        mid = tk.Frame(body, bg="#FFFFFF", bd=1, relief="solid")
        mid.pack(side="left", fill="both", expand=True)
        self.mid_head = tk.Frame(mid, bg="#FFFFFF")
        self.mid_head.pack(fill="x", padx=10, pady=(10, 6))
        self.mid_label = tk.Label(self.mid_head, text="搜索引擎", bg="#FFFFFF",
                                  font=FONT_BOLD, anchor="w")
        self.mid_label.pack(side="left")
        # ★ 一键搜索：把勾选的所有搜索引擎都搜一遍（只在「按书名」板块显示）
        self.btn_search_all = tk.Button(
            self.mid_head, text="🔍 一键搜索（所有勾选）", command=self._open_all,
            bg=COLOR_PRIMARY, fg="white", relief="flat", padx=12, pady=3,
            font=FONT_SMALL, cursor="hand2",
            activebackground=COLOR_PRIMARY_D, activeforeground="white")
        self.mid_inner = tk.Frame(mid, bg="#FFFFFF")
        self.mid_inner.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        bottom = tk.Frame(self, bg=COLOR_BG, pady=8)
        bottom.pack(fill="x", padx=12, pady=(0, 12))

        self.count_label = tk.Label(bottom, text="", bg=COLOR_BG, fg="#455A64",
                                    font=("Microsoft YaHei", 11))
        self.count_label.pack(side="left", padx=(4, 0))

        tk.Button(bottom, text="🚀 一键打开全部", command=self._open_all,
                  bg=COLOR_PRIMARY, fg="white", relief="flat", padx=28, pady=10,
                  font=("Microsoft YaHei", 12, "bold"), cursor="hand2",
                  activebackground=COLOR_PRIMARY_D, activeforeground="white"
                  ).pack(side="right")

        tk.Checkbutton(bottom, text="全选/全不选", variable=self.all_check,
                       bg=COLOR_BG, fg=COLOR_IDLE_FG, font=FONT_NORMAL,
                       command=self._toggle_all).pack(side="right", padx=12)

    # ---------- 左侧列表滚动（引擎/书名多了能滚） ----------
    def _on_left_scroll(self, first, last):
        try:
            if float(first) <= 0.0 and float(last) >= 1.0:
                self.left_bar.grid_remove()
            else:
                self.left_bar.grid()
        except Exception:
            pass
        try:
            self.left_bar.set(first, last)
        except Exception:
            pass

    def _on_left_inner_config(self, _event=None):
        try:
            self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all"))
        except Exception:
            pass

    def _on_left_canvas_config(self, event):
        try:
            self.left_canvas.itemconfigure(self._left_win, width=event.width)
        except Exception:
            pass
        # 窗口变大/变小后，重新判断要不要显示滚动条
        try:
            self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all"))
            first, last = self.left_canvas.yview()
            self._on_left_scroll(first, last)
        except Exception:
            pass

    def _on_left_mousewheel(self, event):
        try:
            self.left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    # ---------- 重建列表 ----------
    def _rebuild_all(self):
        self._rebuild_left()
        self._rebuild_mid()
        self._update_count()

    def _rebuild_left(self):
        for w in self.left_inner.winfo_children():
            w.destroy()
        if self.mode.get() == "book":
            self.left_label.config(text="书名")
            items = [(b.get("id"), self._book_label(b)) for b in self._bookmarks()]
            selected = self.selected_book_id.get()
        else:
            self.left_label.config(text="搜索引擎")
            items = [(e["id"], e["name"]) for e in self.engines]
            selected = self.selected_engine_id.get()

        if not items:
            tk.Label(self.left_inner, text="（空）", bg="#FFFFFF", fg=COLOR_MUTED,
                     font=FONT_SMALL).pack(pady=6)

        for item_id, label in items:
            is_sel = (item_id == selected)
            bg = COLOR_SEL_BG if is_sel else COLOR_IDLE_BG
            fg = COLOR_SEL_FG if is_sel else COLOR_IDLE_FG
            btn = tk.Button(self.left_inner, text=label, bg=bg, fg=fg,
                            relief="flat", anchor="w", padx=10, pady=6,
                            font=FONT_NORMAL, cursor="hand2",
                            activebackground=bg, activeforeground=fg,
                            command=lambda i=item_id: self._select_left(i))
            btn.pack(fill="x", pady=2)
            btn.bind("<MouseWheel>", self._on_left_mousewheel)
            # ★ 与主程序一样：在书名上点右键，就能改搜索词/改名/删除
            if self.mode.get() == "book":
                btn.bind("<Button-3>",
                         lambda ev, bid=item_id: self._book_menu(ev, bid, self.left_inner))

        # 重建后保持原来的滚动位置，别跳回顶部
        try:
            keep = self.left_canvas.yview()[0]
            self.left_canvas.update_idletasks()
            self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all"))
            self.left_canvas.yview_moveto(keep)
        except Exception:
            pass

    def _rebuild_mid(self):
        for w in self.mid_inner.winfo_children():
            w.destroy()
        self.engine_vars = {}
        self.book_vars = {}

        if self.mode.get() == "book":
            # 按书名：搜索引擎分两列摆（先左后右、先上后下），右上角一个「一键搜索」
            self.mid_label.config(text="搜索引擎")
            self.btn_search_all.pack(side="right")
            self._build_engine_grid()
        else:
            self.btn_search_all.pack_forget()
            self.mid_label.config(text="书名（勾选后参与一键打开；点右侧按钮单独打开）")
            for b in self._bookmarks():
                var = tk.BooleanVar(value=True)
                self.book_vars[b.get("id")] = var
                row = tk.Frame(self.mid_inner, bg="#FFFFFF")
                row.pack(fill="x", pady=2)
                chk = tk.Checkbutton(row, text=self._book_label(b), variable=var,
                                     bg="#FFFFFF", fg=COLOR_TEXT, font=FONT_NORMAL,
                                     anchor="w", activebackground="#FFFFFF",
                                     command=self._update_count)
                chk.pack(side="left")
                # ★ 与主程序一样：在书名上点右键＝改搜索词 / 改名 / 删除
                for w in (row, chk):
                    w.bind("<Button-3>",
                           lambda ev, bid=b.get("id"): self._book_menu(ev, bid, self.mid_inner))
                tk.Button(row, text="搜索", bg=COLOR_PRIMARY, fg="white",
                          relief="flat", padx=14, pady=2, cursor="hand2",
                          font=FONT_SMALL,
                          activebackground=COLOR_PRIMARY_D, activeforeground="white",
                          command=lambda bid=b.get("id"): self._search_one(bid)
                          ).pack(side="right", padx=4)

    def _build_engine_grid(self):
        """★ 搜索引擎排两列，按中文阅读习惯排：先左到右、再换下一行；新增的排在最后"""
        cols = max(1, int(MANUAL_SOURCE_COLUMNS))
        items = list(self.engines)
        if not items:
            tk.Label(self.mid_inner, text="（空）", bg="#FFFFFF", fg=COLOR_MUTED,
                     font=FONT_SMALL).pack(pady=6)
            return

        grid = tk.Frame(self.mid_inner, bg="#FFFFFF")
        grid.pack(fill="both", expand=True)
        for c in range(cols):
            grid.columnconfigure(c, weight=1, uniform="engine_col")

        for idx, e in enumerate(items):
            r, c = divmod(idx, cols)      # 先左后右、再下一行（中文阅读习惯）
            cell = tk.Frame(grid, bg="#FFFFFF")
            cell.grid(row=r, column=c, sticky="ew", padx=(0, 10), pady=2)
            var = tk.BooleanVar(value=True)
            self.engine_vars[e["id"]] = var
            tk.Checkbutton(cell, text=e["name"], variable=var,
                           bg="#FFFFFF", fg=COLOR_TEXT, font=FONT_NORMAL,
                           anchor="w", activebackground="#FFFFFF",
                           command=self._update_count).pack(side="left")
            tk.Button(cell, text="搜索", bg=COLOR_PRIMARY, fg="white",
                      relief="flat", padx=10, pady=2, cursor="hand2",
                      font=FONT_SMALL,
                      activebackground=COLOR_PRIMARY_D, activeforeground="white",
                      command=lambda eid=e["id"]: self._search_one(eid)
                      ).pack(side="right", padx=4)

    # ---------- 书签右键菜单（改成与主程序一样：直接改搜索词/改名/删除） ----------
    def _find_book(self, book_id):
        return next((b for b in self._bookmarks() if b.get("id") == book_id), None)

    def _find_engine(self, engine_id):
        return next((e for e in self.engines if e["id"] == engine_id), None)

    def _book_menu(self, event, book_id, parent):
        book = self._find_book(book_id)
        if not book:
            return
        m = tk.Menu(parent, tearoff=0)
        m.add_command(label="🏷️ 编辑搜索词…",
                      command=lambda: self._edit_book_suffixes(book, parent))
        m.add_command(label="✏️ 改书名…",
                      command=lambda: self._rename_book(book, parent))
        m.add_separator()
        m.add_command(label="🗑️ 删除书签",
                      command=lambda: self._delete_book(book, parent))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _edit_book_suffixes(self, book, parent):
        """编辑搜索词：用主程序那个「带勾选的列表」窗口，不用手打一串后缀"""
        def on_ok(sufs):
            book["suffixes"] = sufs
            self._persist_bookmarks()
            self._rebuild_all()

        open_suffix_editor(
            parent,
            f"编辑搜索词 - {book['name']}",
            "勾选的搜索词会与书名拼成搜索关键词（如「书名 网盘」）；未勾选则跳过",
            book.get("suffixes") or [],
            on_ok,
            default_suffixes=getattr(self.app, "default_suffixes", []) or [])

    def _rename_book(self, book, parent):
        name = simpledialog.askstring("改书名", "书名：",
                                      initialvalue=book["name"], parent=parent)
        if not name or not name.strip():
            return
        book["name"] = name.strip()
        self._persist_bookmarks()
        self._rebuild_all()

    def _delete_book(self, book, parent):
        if not messagebox.askyesno("确认", f"删除书签「{book['name']}」？", parent=parent):
            return
        try:
            self.app.bookmarks.remove(book)
        except ValueError:
            pass
        self._persist_bookmarks()
        self._rebuild_all()

    # ---------- 交互 ----------
    def _set_mode(self, m):
        self.mode.set(m)
        if m == "book":
            self.mode_btn_book.config(bg=COLOR_SEL_BG, fg=COLOR_SEL_FG)
            self.mode_btn_engine.config(bg=COLOR_IDLE_BG, fg=COLOR_IDLE_FG)
        else:
            self.mode_btn_book.config(bg=COLOR_IDLE_BG, fg=COLOR_IDLE_FG)
            self.mode_btn_engine.config(bg=COLOR_SEL_BG, fg=COLOR_SEL_FG)
        self._rebuild_all()

    def _select_left(self, item_id):
        if self.mode.get() == "book":
            self.selected_book_id.set(item_id)
        else:
            self.selected_engine_id.set(item_id)
        self._rebuild_left()

    def _toggle_all(self):
        val = self.all_check.get()
        for v in self.engine_vars.values():
            v.set(val)
        for v in self.book_vars.values():
            v.set(val)
        self._update_count()

    # ---------- URL 构造 ----------
    def _build_urls(self, engine_id, book_id):
        engine = self._find_engine(engine_id)
        book = self._find_book(book_id)
        if not engine or not book:
            return []
        tpl = engine.get("url_tpl") or ""
        if not tpl:
            return []
        urls = []
        for suf in enabled_suffix_texts(book.get("suffixes")):
            term = f"{book['name']}{suf}".strip()
            try:
                urls.append(tpl.format(kw=quote(term)))
            except Exception:
                continue
        return urls

    def _collect_all(self):
        """收集本次要打开的全部组合：[(引擎, 书签, url), ...]"""
        combos = []
        if self.mode.get() == "book":
            book = self._find_book(self.selected_book_id.get())
            if not book:
                return combos
            for e in self.engines:
                var = self.engine_vars.get(e["id"])
                if var and var.get():
                    for u in self._build_urls(e["id"], book.get("id")):
                        combos.append((e, book, u))
        else:
            engine = self._find_engine(self.selected_engine_id.get())
            if not engine:
                return combos
            for b in self._bookmarks():
                var = self.book_vars.get(b.get("id"))
                if var and var.get():
                    for u in self._build_urls(engine["id"], b.get("id")):
                        combos.append((engine, b, u))
        return combos

    def _group_by_book(self, combos):
        """★ 同一个书名的标签页放进同一个窗口（按书名第一次出现的先后排窗口）"""
        groups = {}
        order = []
        for engine, book, url in combos:
            key = book.get("id") or book.get("name")
            if key not in groups:
                groups[key] = {"book": book, "items": []}
                order.append(key)
            groups[key]["items"].append((engine, url))
        return [groups[k] for k in order]


    def _update_count(self):
        n = len(self._collect_all())
        self.count_label.config(text=f"将打开 {n} 个标签页")

    # ---------- 打开逻辑 ----------
    def _search_one(self, other_id):
        if self.mode.get() == "book":
            book_id = self.selected_book_id.get()
            engine_id = other_id
        else:
            engine_id = self.selected_engine_id.get()
            book_id = other_id
        if not book_id or not engine_id:
            messagebox.showwarning("提示", "请先选择书名和搜索引擎", parent=self)
            return
        urls = self._build_urls(engine_id, book_id)
        engine = self._find_engine(engine_id)
        book = self._find_book(book_id)
        if not urls or not engine or not book:
            messagebox.showinfo("提示", "该组合没有可打开的链接（请检查搜索词是否勾选）。", parent=self)
            return
        combos = [(engine, book, u) for u in urls]
        self._do_open_groups(self._group_by_book(combos))

    def _open_all(self):
        """一键搜索 / 一键打开全部：把勾选的搜索引擎都搜一遍"""
        combos = self._collect_all()
        if not combos:
            messagebox.showinfo("提示", "没有可打开的组合", parent=self)
            return

        groups = self._group_by_book(combos)
        total = len(combos)
        if total > 50:
            messagebox.showwarning("太多了",
                                   f"共 {total} 个标签页，太多，请分批打开（取消部分勾选）。",
                                   parent=self)
            return
        if total >= 20:
            if not messagebox.askyesno("确认",
                                       f"即将打开 {total} 个标签页，分 {len(groups)} 个窗口"
                                       f"（同一个书名在同一个窗口）。\n\n确定吗？",
                                       parent=self):
                return
        self._do_open_groups(groups)

    def _do_open_groups(self, groups):
        """★ 一个书名一个窗口：窗口内依次开标签页。
           打开间隔只对夸克生效（夸克开太快会被挡），其他引擎几乎不等待。"""
        browser = _find_browser()
        for g in groups:
            items = g["items"]
            if not items:
                continue

            extras = []
            for engine, _u in items:
                extra = (engine.get("extra_url") or "").strip()
                if extra and extra not in extras:
                    extras.append(extra)

            opened = False
            if browser:
                try:
                    subprocess.Popen([browser, "--new-window", items[0][1]])
                    time.sleep(0.9)
                    for engine, u in items[1:]:
                        # 只有夸克等得久一点
                        time.sleep(_tab_gap(engine))
                        subprocess.Popen([browser, u])
                    opened = True
                except Exception:
                    opened = False
            if not opened:
                for _e, u in items:
                    webbrowser.open(u, new=1)

            # 额外链接（版权投诉页等）也开在同一个窗口里
            for extra in extras:
                time.sleep(0.3)
                if browser:
                    try:
                        subprocess.Popen([browser, extra])
                        continue
                    except Exception:
                        pass
                webbrowser.open(extra, new=1)

    # ---------- 编辑书签（与主程序一样的编辑方式） ----------
    def _edit_bookmarks(self):
        """书签管理窗口：书签列表 + 与主程序同款的「搜索词编辑」窗口"""
        win = tk.Toplevel(self)
        win.title("编辑书签")
        win.geometry("700x600")
        win.configure(bg=COLOR_BG)
        win.transient(self)
        win.grab_set()
        fix_show_desktop_return(win, self)

        tk.Label(win, text="书签（双击＝编辑搜索词，右键＝菜单）", bg=COLOR_BG,
                 fg=COLOR_TEXT, font=FONT_BOLD).pack(pady=(14, 2))
        tk.Label(win, text="搜索词的编辑方式和主程序一样：弹窗里勾选，不用手打一串后缀",
                 bg=COLOR_BG, fg=COLOR_MUTED, font=FONT_SMALL).pack()

        outer = tk.Frame(win, bg="#E1E5EA")
        outer.pack(fill="both", expand=True, padx=16, pady=10)
        inner = tk.Frame(outer, bg="#FFFFFF")
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        lb = tk.Listbox(inner, font=FONT_NORMAL, activestyle="none", bd=0,
                        highlightthickness=0, selectbackground=COLOR_SEL_BG,
                        selectforeground=COLOR_SEL_FG)
        lb.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb = tk.Scrollbar(inner, orient="vertical", command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.config(yscrollcommand=sb.set)

        def refresh():
            lb.delete(0, tk.END)
            for b in self._bookmarks():
                lb.insert(tk.END, f"{b['name']}    {fmt_suffixes(b.get('suffixes'))}")

        def sel_book():
            sel = lb.curselection()
            if not sel:
                return None
            bms = self._bookmarks()
            return bms[sel[0]] if sel[0] < len(bms) else None

        def after_change():
            self._persist_bookmarks()
            refresh()
            self._rebuild_all()

        def edit_suffixes(b=None):
            b = b or sel_book()
            if not b:
                messagebox.showinfo("提示", "请先选中一个书签。", parent=win)
                return

            def on_ok(sufs):
                b["suffixes"] = sufs
                after_change()

            open_suffix_editor(
                win,
                f"编辑搜索词 - {b['name']}",
                "勾选的搜索词会与书名拼成搜索关键词（如「书名 网盘」）；未勾选则跳过",
                b.get("suffixes") or [], on_ok,
                default_suffixes=getattr(self.app, "default_suffixes", []) or [])

        def rename(b=None):
            b = b or sel_book()
            if not b:
                messagebox.showinfo("提示", "请先选中一个书签。", parent=win)
                return
            name = simpledialog.askstring("改书名", "书名：",
                                          initialvalue=b["name"], parent=win)
            if not name or not name.strip():
                return
            b["name"] = name.strip()
            after_change()

        def del_book(b=None):
            b = b or sel_book()
            if not b:
                messagebox.showinfo("提示", "请先选中一个书签。", parent=win)
                return
            if not messagebox.askyesno("确认", f"删除书签「{b['name']}」？", parent=win):
                return
            try:
                self.app.bookmarks.remove(b)
            except ValueError:
                pass
            after_change()

        def menu(event):
            idx = lb.nearest(event.y)
            bms = self._bookmarks()
            if idx < 0 or idx >= len(bms):
                return
            lb.selection_clear(0, tk.END)
            lb.selection_set(idx)
            b = bms[idx]
            m = tk.Menu(win, tearoff=0)
            m.add_command(label="🏷️ 编辑搜索词…", command=lambda: edit_suffixes(b))
            m.add_command(label="✏️ 改书名…", command=lambda: rename(b))
            m.add_separator()
            m.add_command(label="🗑️ 删除书签", command=lambda: del_book(b))
            try:
                m.tk_popup(event.x_root, event.y_root)
            finally:
                m.grab_release()

        lb.bind("<Double-Button-1>", lambda e: edit_suffixes())
        lb.bind("<Button-3>", menu)

        # ★ 新增书签和主程序一样：输入书名 → 保存（搜索词自动用主程序的默认搜索词）
        row = tk.Frame(win, bg=COLOR_BG)
        row.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(row, text="书名", bg=COLOR_BG, fg=COLOR_MUTED,
                 font=FONT_SMALL).pack(side="left", padx=(0, 6))
        ent = tk.Entry(row, font=FONT_NORMAL)
        ent.pack(side="left", fill="x", expand=True, ipady=4)

        def add_book():
            name = ent.get().strip()
            if not name:
                messagebox.showinfo("提示", "请先输入书名。", parent=win)
                return
            sufs = [dict(s) for s in (getattr(self.app, "default_suffixes", []) or [])]
            if not sufs:
                sufs = [{"text": "", "enabled": True}]
            self.app.bookmarks.append({"id": uuid.uuid4().hex[:8],
                                       "name": name, "suffixes": sufs})
            ent.delete(0, tk.END)
            after_change()
            bms = self._bookmarks()
            if bms:
                lb.selection_clear(0, tk.END)
                lb.selection_set(len(bms) - 1)

        ent.bind("<Return>", lambda e: add_book())
        tk.Button(row, text="➕ 新增书签", command=add_book, bg=COLOR_PRIMARY, fg="white",
                  relief="flat", padx=12, pady=4, cursor="hand2",
                  font=FONT_NORMAL).pack(side="left", padx=6)

        btn_frame = tk.Frame(win, bg=COLOR_BG)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="🏷️ 编辑搜索词", command=lambda: edit_suffixes(),
                  bg="#FFE082", fg="#5D4037", relief="flat", padx=12, pady=5,
                  font=FONT_NORMAL, cursor="hand2").pack(side="left", padx=4)
        tk.Button(btn_frame, text="✏️ 改书名", command=lambda: rename(),
                  bg="#ECEFF1", fg="#455A64", relief="flat", padx=12, pady=5,
                  font=FONT_NORMAL, cursor="hand2").pack(side="left", padx=4)
        tk.Button(btn_frame, text="🗑️ 删除书签", command=lambda: del_book(),
                  bg="#EF5350", fg="white", relief="flat", padx=12, pady=5,
                  font=FONT_NORMAL, cursor="hand2").pack(side="left", padx=4)
        tk.Button(btn_frame, text="✅ 完成",
                  command=lambda: (self._rebuild_all(), win.destroy()),
                  bg=COLOR_PRIMARY, fg="white", relief="flat", padx=12, pady=5,
                  font=FONT_NORMAL, cursor="hand2").pack(side="left", padx=4)

        refresh()

    # ---------- 编辑搜索引擎 ----------
    def _edit_engines(self):
        win = tk.Toplevel(self)
        win.title("编辑搜索引擎")
        win.geometry("760x620")
        win.transient(self)
        win.grab_set()
        fix_show_desktop_return(win, self)

        tk.Label(win, text="搜索引擎（双击＝编辑；➕ 新增的自动排在最后）",
                 font=FONT_BOLD).pack(pady=(10, 0))
        tk.Label(win, text="名字后面的域名是自动加的；额外链接留空就不额外打开",
                 fg="#888", font=FONT_SMALL).pack(pady=(0, 6))

        lb = tk.Listbox(win, font=FONT_SMALL, height=15)
        lb.pack(fill="both", expand=True, padx=12)

        def persist():
            overrides = _load_engine_overrides()
            for e in self.engines:
                overrides[e["id"]] = {
                    "name": e["name"],
                    "url_tpl": e["url_tpl"],
                    "extra_url": e["extra_url"],
                }
            _save_engine_overrides(overrides)

        def refresh():
            lb.delete(0, tk.END)
            for e in self.engines:
                extra = ("  [+额外]" if (e.get("extra_url") or "").strip() else "")
                lb.insert(tk.END, f"{e['name']}{extra}  →  {e['url_tpl'][:70]}")

        def engine_form(engine, parent_win, is_new=False):
            """编辑 / 新增一个搜索引擎"""
            dlg = tk.Toplevel(parent_win)
            dlg.title("新增搜索引擎" if is_new else f"编辑：{engine['name']}")
            dlg.geometry("680x340")
            dlg.transient(parent_win)
            dlg.grab_set()
            fix_show_desktop_return(dlg, parent_win)

            tk.Label(dlg, text="名称：").grid(row=0, column=0, padx=10, pady=8, sticky="e")
            ent_name = tk.Entry(dlg, width=54)
            ent_name.insert(0, "" if is_new else engine["name"])
            ent_name.grid(row=0, column=1, padx=10, pady=8, sticky="we")

            tk.Label(dlg, text="URL 模板：").grid(row=1, column=0, padx=10, pady=8, sticky="e")
            ent_url = tk.Entry(dlg, width=54)
            ent_url.insert(0, "" if is_new else engine["url_tpl"])
            ent_url.grid(row=1, column=1, padx=10, pady=8, sticky="we")

            tk.Label(dlg, text="（用 {kw} 代表关键词）", fg="#888",
                     font=FONT_SMALL).grid(row=2, column=1, padx=10, sticky="w")

            tk.Label(dlg, text="额外打开链接：").grid(row=3, column=0, padx=10, pady=8, sticky="e")
            ent_extra = tk.Entry(dlg, width=54)
            ent_extra.insert(0, "" if is_new else engine.get("extra_url", ""))
            ent_extra.grid(row=3, column=1, padx=10, pady=8, sticky="we")

            tk.Label(dlg, text="（留空就不额外打开；域名会自动拼在名字后面）", fg="#888",
                     font=FONT_SMALL).grid(row=4, column=1, padx=10, sticky="w")

            def on_ok():
                name = ent_name.get().strip()
                url_tpl = ent_url.get().strip()
                extra = ent_extra.get().strip()
                if not name or not url_tpl:
                    messagebox.showinfo("提示", "名称和 URL 模板都要填。", parent=dlg)
                    return
                if "{kw}" not in url_tpl:
                    if not messagebox.askyesno(
                            "确认",
                            "URL 模板里没有 {kw}，搜索词加不进去。\n\n还确定要这样吗？",
                            parent=dlg):
                        return
                if is_new:
                    # ★ 新增的引擎排在最后（按中文阅读习惯，就是列表最末尾）
                    self.engines.append({
                        "id": "custom_" + uuid.uuid4().hex[:6],
                        "name": _engine_display_name(name, url_tpl),
                        "url_tpl": url_tpl,
                        "extra_url": extra,
                    })
                else:
                    engine["name"] = _engine_display_name(name, url_tpl)
                    engine["url_tpl"] = url_tpl
                    engine["extra_url"] = extra
                persist()
                refresh()
                self._rebuild_all()
                if is_new:
                    lb.selection_clear(0, tk.END)
                    lb.selection_set(len(self.engines) - 1)
                    lb.see(len(self.engines) - 1)
                dlg.destroy()

            tk.Button(dlg, text="确定", command=on_ok, bg=COLOR_PRIMARY, fg="white",
                      relief="flat", padx=20, pady=6).grid(row=5, column=1, pady=12, sticky="e")
            tk.Button(dlg, text="取消", command=dlg.destroy, padx=20, pady=6
                      ).grid(row=5, column=1, pady=12, sticky="w")

            dlg.columnconfigure(1, weight=1)

        def edit_sel():
            sel = lb.curselection()
            if not sel:
                return
            engine_form(self.engines[sel[0]], win)

        def add_engine():
            engine_form(None, win, is_new=True)

        def reset_all():
            if not messagebox.askyesno("确认", "恢复所有搜索引擎的默认名称/网址？", parent=win):
                return
            _save_engine_overrides({})
            self.engines = _build_engine_list()
            refresh()
            self._rebuild_all()

        lb.bind("<Double-Button-1>", lambda e: edit_sel())
        lb.bind("<Button-3>", lambda e: edit_sel())

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="➕ 新增", command=add_engine, bg=COLOR_PRIMARY, fg="white",
                  relief="flat", padx=14, pady=5, cursor="hand2",
                  activebackground=COLOR_PRIMARY_D, activeforeground="white"
                  ).pack(side="left", padx=4)
        tk.Button(btn_frame, text="✏️ 编辑", command=edit_sel, padx=12).pack(side="left", padx=4)
        tk.Button(btn_frame, text="↩️ 恢复默认", command=reset_all, padx=12).pack(side="left", padx=4)
        tk.Button(btn_frame, text="✅ 完成", padx=12,
                  command=lambda: (self._rebuild_all(), win.destroy())).pack(side="left", padx=4)

        refresh()


def open_manual_search(app):
    """入口：由主程序/测试程序的手动搜索按钮调用。"""
    try:
        ManualSearchDialog(app)
    except Exception as e:
        messagebox.showerror("手动搜索", f"打开失败：{type(e).__name__}: {e}")
