import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import time
import random
import re
import uuid
import os
import sys
import webbrowser
from datetime import datetime
from config import *
from utils import *
from window_helper import fix_show_desktop_return
from single_instance import SingleInstance
import unwhite_log
from engines import ENGINE_MAP, MANUAL_SEARCH_ENGINE_MAP

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

try:
    from winotify import Notification, audio
    HAS_WINOTIFY = True
except ImportError:
    HAS_WINOTIFY = False

from playwright.sync_api import sync_playwright

class MonitorApp:
    COLOR_BG           = "#F2F5F7"
    COLOR_CARD         = "#FFFFFF"
    COLOR_BORDER       = "#E1E5EA"
    COLOR_PRIMARY      = "#2A9D8F"
    COLOR_TEXT         = "#2F3542"
    COLOR_MUTED        = "#7A869A"
    COLOR_DANGER       = "#E63946"
    COLOR_WARNING      = "#F4A261"
    COLOR_INFO         = "#457B9D"
    COLOR_GHOST        = "#ECEFF1"
    COLOR_GHOST_FG     = "#455A64"

    # True：把 360移动/搜狗移动/微博移动 等合并到主来源标签页
    # False：每个来源单独一个标签页（测试程序用）
    MERGE_DISPLAY_GROUPS = True

    # 是否显示「文心」标签页（只有百度搜索会产生文心内容；测试程序不需要）
    SHOW_AI_TAB = True

    # 这些来源即使第一页书名全不匹配，也继续翻后续页（当前为空，所有来源遵循同一规则）
    NO_BREAK_ON_MISMATCH_SOURCES = set()

    # 这些来源的最小翻页数（当前为空，所有来源都按用户设的页数走）
    MIN_PAGES_BY_SOURCE = {}

    # 「监控页数 = 自动」时，单个搜索词最多翻多少页（兜底，防止无限翻页）
    AUTO_MAX_PAGES = 10

    def __init__(self, root):
        migrate_old_files()

        self.root = root
        self.root.title("打盗全家捅监控 v2.0.0")
        self.root.geometry("1280x840")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.COLOR_BG)

        self.is_monitoring = False
        self._stop_done = False      # 停止流程是否已执行过，避免日志重复
        self.headless = bool(DEFAULT_HEADLESS)
        self.deep_mode = False      # 深度模式：百度/头条/搜狗/360/知道 同时搜 pc + 移动 双渠道

        # ---- 去重库 ----
        self.seen_zhidao = set(load_json(SEEN_ZHIDAO_FILE, []))
        self.seen_zhidao_mobile = set(load_json(SEEN_ZHIDAO_MOBILE_FILE, []))
        self.seen_tieba  = set(load_json(SEEN_TIEBA_FILE, []))
        self.seen_baidu  = set(load_json(SEEN_BAIDU_FILE, []))
        self.seen_baidu_mobile = set(load_json(SEEN_BAIDU_MOBILE_FILE, []))
        self.seen_bing   = set(load_json(SEEN_BING_FILE, []))
        self.seen_so360_mobile = set(load_json(SEEN_SO360_MOBILE_FILE, []))
        self.seen_so360_pc = set(load_json(SEEN_SO360_PC_FILE, []))
        self.seen_toutiao_mobile = set(load_json(SEEN_TOUTIAO_MOBILE_FILE, []))
        self.seen_toutiao_pc = set(load_json(SEEN_TOUTIAO_PC_FILE, []))
        self.seen_sogou_mobile = set(load_json(SEEN_SOGOU_MOBILE_FILE, []))
        self.seen_sogou_pc = set(load_json(SEEN_SOGOU_PC_FILE, []))
        self.seen_sogou_weixin = set(load_json(SEEN_SOGOU_WEIXIN_FILE, []))
        self.seen_quark  = set(load_json(SEEN_QUARK_FILE, []))
        self.seen_quark_cn = set(load_json(SEEN_QUARK_CN_FILE, []))
        self.seen_weibo = set(load_json(SEEN_WEIBO_FILE, []))
        self.seen_weibo_mobile = set(load_json(SEEN_WEIBO_MOBILE_FILE, []))
        # ---- 统一白名单（合并 common + 三个旧文件） ----
        self.whitelist_common = set(load_json(WHITELIST_COMMON_FILE, []))
        for f in WHITELIST_LEGACY_FILES:
            try:
                legacy = load_json(f, [])
                if legacy:
                    self.whitelist_common |= set(legacy)
            except Exception:
                pass
        # 立刻回写到 common，避免每次都要读旧文件
        save_json(WHITELIST_COMMON_FILE, list(self.whitelist_common))
                # ★ 白名单词：标题/摘要/url 命中就自动加白
        self.whitelist_words = set(
            load_json(os.path.join(BASE_DIR, "whitelist_words.json"), []) or [])
        self._auto_whitelist_dirty = False

        # ★ 未加白链接文档：把老的 monitor_seen_*.json 补进来，再生成「未加白链接.txt」
        try:
            unwhite_log.init_once(self._seen_map(), self.whitelist_common)
        except Exception:
            pass

        # ---- 默认搜索词 ----
        raw_suf = load_json(DEFAULT_SUFFIX_FILE, None)
        if raw_suf is None:
            self.default_suffixes = [{"text": "", "enabled": True}]
            for _s in INITIAL_SUFFIXES:
                self.default_suffixes.append({"text": _s, "enabled": False})
            save_json(DEFAULT_SUFFIX_FILE, self.default_suffixes)
        else:
            self.default_suffixes = normalize_suffixes(raw_suf)

        self.bookmarks = []
        self._load_bookmarks()

        self._rows = {}
        self._refresh_pending = False
        self._sum_author_seen = set()

        self.trigger_now = False
        self._suppress_select_event = False
        self._filter_keyword = ""
        self._last_fetch_raw_count = 0
        # 本轮遇到的验证码事件（轮末标红复述一次后清空）
        self.captcha_events = []
        # 本次运行累计的验证码事件（跨轮保留，停止时汇报）
        self.captcha_session_events = []
        self.captcha_warned = False
        self.last_round_elapsed = 0.0
        self.browser_context = None
        # ★ 后台模式：本轮撞过验证码的来源（后续所有词跳过）
        self._skip_sources_this_round = set()
        # ★ 当前正在抓的来源（供 pause_for_user 记录）
        self._current_source = None
        # ★ 本次 fetch 里 pause_for_user 被触发的次数（用于验证码后重试）
        self._pause_count = 0
        # ★ 搜索统计：每个来源搜了几次、遇到几次验证码
        self.stats_file = os.path.join(BASE_DIR, "搜索统计.json")
        self.stats_doc  = os.path.join(BASE_DIR, "搜索统计.txt")
        self.stats = self._load_stats()
        self.stats_session_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.tray_icon = None
        self.tray_thread = None
        self.tray_visible = False
        self.icon_img = generate_icon()
        self.bookmark_buttons = []
        self.bookmark_checks = {}      # 书签前面的小方格：勾上的才批量监控
        self.bookmark_select_all_var = tk.IntVar(value=0)   # 0=未选，1=全选，2=半选

        self.font_normal = ("Microsoft YaHei", 10)
        self.font_title = ("Microsoft YaHei", 11, "bold")
        self.font_small = ("Microsoft YaHei", 9)
        self.root.option_add("*Font", self.font_normal)

        self._init_style()
        self.setup_ui()
        self.refresh_bookmarks()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ==================== 主题 ====================
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure("TNotebook",
                        background=self.COLOR_BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
        style.configure("TNotebook.Tab",
                        background=self.COLOR_BG,
                        foreground=self.COLOR_MUTED,
                        padding=[18, 8],
                        font=("Microsoft YaHei", 10))
        # ★ 未选中的标签页窄一点（选中的保持宽一点，看起来更醒目）
        style.map("TNotebook.Tab",
                  background=[("selected", self.COLOR_CARD)],
                  foreground=[("selected", self.COLOR_PRIMARY)],
                  padding=[("selected", (18, 8)), ("!selected", (11, 6))])
        style.configure("Treeview",
                        background=self.COLOR_CARD,
                        foreground=self.COLOR_TEXT,
                        rowheight=28,
                        fieldbackground=self.COLOR_CARD,
                        font=("Microsoft YaHei", 9),
                        borderwidth=0)
        style.configure("Treeview.Heading",
                        background="#EEF2F5",
                        foreground=self.COLOR_TEXT,
                        font=("Microsoft YaHei", 9, "bold"),
                        padding=6,
                        relief="flat")
        style.map("Treeview.Heading",
                  background=[("active", "#E3E9EE")])
        style.map("Treeview",
                  background=[("selected", self.COLOR_PRIMARY)],
                  foreground=[("selected", "white")])
        style.configure("Vertical.TScrollbar",
                        background="#D5DBE1",
                        troughcolor=self.COLOR_BG,
                        borderwidth=0,
                        arrowcolor=self.COLOR_MUTED)

    # ==================== 组件工厂 ====================
    def _make_btn(self, parent, text, command, bg=None, fg="white",
                  width=10, bold=False, small=False, height=1):
        if bg is None:
            bg, fg = self.COLOR_GHOST, self.COLOR_GHOST_FG
        font = ("Microsoft YaHei", 9 if small else 10, "bold" if bold else "normal")
        return tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg,
            activebackground=bg, activeforeground=fg,
            relief="flat", bd=0, cursor="hand2",
            width=width, height=height,
            font=font, padx=8, pady=5,
            highlightthickness=0,
        )

    def _card(self, parent, title=None):
        outer = tk.Frame(parent, bg=self.COLOR_BORDER)
        inner = tk.Frame(outer, bg=self.COLOR_CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        if title:
            tk.Label(inner, text=title, bg=self.COLOR_CARD, fg=self.COLOR_TEXT,
                     font=self.font_title, anchor="w").pack(
                fill="x", padx=14, pady=(10, 4))
        return outer, inner

    # ==================== 书签读写 ====================
    def _load_bookmarks(self):
        raw = load_json(BOOKMARKS_FILE, [])
        migrated = False
        self.bookmarks = []
        if not isinstance(raw, list):
            return
        for it in raw:
            if isinstance(it, str):
                name = it.strip()
                if not name:
                    continue
                self.bookmarks.append({
                    "id": uuid.uuid4().hex[:8],
                    "name": name,
                    "suffixes": [dict(s) for s in self.default_suffixes],
                })
                migrated = True
            elif isinstance(it, dict) and it.get("name"):
                sufs = it.get("suffixes")
                book_id = it.get("id")
                if not book_id:
                    book_id = uuid.uuid4().hex[:8]
                    migrated = True
                self.bookmarks.append({
                    "id": book_id,
                    "name": str(it["name"]).strip(),
                    "suffixes": normalize_suffixes(sufs),
                })
                if sufs and not all(isinstance(x, dict) for x in sufs):
                    migrated = True
        if migrated:
            self.save_bookmarks()

    def save_bookmarks(self):
        save_json(BOOKMARKS_FILE, self.bookmarks)

    def save_default_suffixes(self):
        save_json(DEFAULT_SUFFIX_FILE, self.default_suffixes)

    def _find_bookmark_by_id(self, book_id):
        for bm in self.bookmarks:
            if bm.get("id") == book_id:
                return bm
        return None

    @staticmethod
    def _enabled_suffixes_display(sufs):
        """只显示已勾选的后缀，用于日志输出"""
        parts = []
        for s in sufs or []:
            if isinstance(s, dict):
                if not s.get("enabled", True):
                    continue
                text = str(s.get("text", "") or "").strip()
            else:
                text = str(s or "").strip()
            parts.append(text if text else "无后缀")
        return "、".join(parts) if parts else "（无）"    

    def save_seen(self):
        save_json(SEEN_ZHIDAO_FILE, list(self.seen_zhidao))
        save_json(SEEN_ZHIDAO_MOBILE_FILE, list(self.seen_zhidao_mobile))
        save_json(SEEN_TIEBA_FILE, list(self.seen_tieba))
        save_json(SEEN_BAIDU_FILE, list(self.seen_baidu))
        save_json(SEEN_BING_FILE, list(self.seen_bing))
        save_json(SEEN_SO360_MOBILE_FILE, list(self.seen_so360_mobile))
        save_json(SEEN_SO360_PC_FILE, list(self.seen_so360_pc))
        save_json(SEEN_TOUTIAO_MOBILE_FILE, list(self.seen_toutiao_mobile))
        save_json(SEEN_TOUTIAO_PC_FILE, list(self.seen_toutiao_pc))
        save_json(SEEN_SOGOU_MOBILE_FILE, list(self.seen_sogou_mobile))
        save_json(SEEN_SOGOU_PC_FILE, list(self.seen_sogou_pc))
        save_json(SEEN_SOGOU_WEIXIN_FILE, list(self.seen_sogou_weixin))
        save_json(SEEN_QUARK_FILE, list(self.seen_quark))
        save_json(SEEN_QUARK_CN_FILE, list(self.seen_quark_cn))
        save_json(SEEN_WEIBO_FILE, list(self.seen_weibo))
        save_json(SEEN_WEIBO_MOBILE_FILE, list(self.seen_weibo_mobile))
    def save_whitelist(self):
        save_json(WHITELIST_COMMON_FILE, list(self.whitelist_common))
        # ★ 白名单变了，「未加白链接.txt」跟着刷新（加白的会自动消失）
        try:
            unwhite_log.build_doc(self.whitelist_common)
        except Exception:
            pass

    def log(self, msg):
        ts = datetime.now().strftime("%m-%d %H:%M:%S")
        full = f"[{ts}] {msg}"
        def _do():
            try:
                self.log_text.config(state='normal')
                self.log_text.insert(tk.END, full + "\n")
                self.log_text.see(tk.END)
                self.log_text.config(state='disabled')
            except Exception:
                pass
        self.root.after(0, _do)

    def log_alert(self, msg):
        """标红底的重要提醒（用于验证码复述）"""
        ts = datetime.now().strftime("%m-%d %H:%M:%S")
        full = f"[{ts}] {msg}"
        def _do():
            try:
                self.log_text.tag_config("alert", background="#000000", foreground="#FFFFFF")
                self.log_text.config(state='normal')
                self.log_text.insert(tk.END, full + "\n", "alert")
                self.log_text.see(tk.END)
                self.log_text.config(state='disabled')
            except Exception:
                pass
        self.root.after(0, _do)

    def _flush_captcha_reminder(self):
        """一轮结束 / 监控停止时，只汇总本轮遇到的验证码次数和来源，再标红复述一次"""
        events = list(self.captcha_events)
        if not events:
            return
        self.captcha_events = []
        counter = {}
        for r in events:
            if not r:
                continue
            counter[r] = counter.get(r, 0) + 1
        if not counter:
            return
        parts = []
        for reason, count in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0]))):
            parts.append(f"{reason} {count} 次")
        self.log_alert(f"⚠️ 本次抓取共遇到验证码/登录 {len(events)} 次：" + "；".join(parts))
        # ★ 只要其中包含 360，就额外建议 360 单独用前台模式
        if any('360' in str(k) for k in counter.keys()):
            self.log_alert("💡 360 搜索容易撞验证码，建议 360 以前台模式搜索，随时手动过验证。")

    def _report_captcha_session(self):
        """停止监控（手动或自动）时，汇报本次运行整体遇到的登录/验证码情况。"""
        events = list(getattr(self, 'captcha_session_events', None) or [])
        self.captcha_session_events = []
        if not events:
            self.log("✅ 本次运行未遇到验证码/登录")
            return
        counter = {}
        for r in events:
            if not r:
                continue
            counter[r] = counter.get(r, 0) + 1
        parts = [f"{reason} {count} 次"
                 for reason, count in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))]
        self.log_alert(f"🔔 本次运行共遇到验证码/登录 {len(events)} 次：" + "；".join(parts))
        # ★ 只要其中包含 360，就额外建议切前台模式
        if any('360' in str(k) for k in counter.keys()):
            self.log_alert("💡 360 搜索容易撞验证码，建议单独把 360 切到「前台模式」手动过一次验证，"
                           "验证通过后再切回后台模式。")

    # ==================== UI ====================
    def setup_ui(self):
        # ---------- 顶部卡片 ----------
        card_outer, top = self._card(self.root)
        card_outer.pack(fill="x", padx=12, pady=(12, 6))

        # ★ 从左到右：监控设置，前台模式按钮，前台模式说明，深度模式按钮，深度模式说明
        header = tk.Frame(top, bg=self.COLOR_CARD)
        header.grid(row=0, column=0, columnspan=12, sticky="ew", padx=14, pady=(10, 6))

        tk.Label(header, text="监控设置", bg=self.COLOR_CARD, fg=self.COLOR_TEXT,
                 font=self.font_title, anchor="w").pack(side="left")

        # 前台模式按钮
        self.var_headless = tk.BooleanVar(value=not self.headless)
        self.chk_headless = tk.Checkbutton(
            header, text="前台模式", variable=self.var_headless,
            command=self._on_headless_toggle,
            bg=self.COLOR_CARD, fg=self.COLOR_TEXT,
            activebackground=self.COLOR_CARD, activeforeground=self.COLOR_TEXT,
            selectcolor=self.COLOR_CARD,
            font=("Microsoft YaHei", 10), bd=0, highlightthickness=0,
            anchor="w")
        self.chk_headless.pack(side="left", anchor="w", padx=(14, 4))

        # 前台模式说明
        self.tip_label = tk.Label(header, anchor="w", font=self.font_small,
                                  bg="#FFF8E1", fg="#6D4C41",
                                  padx=8, pady=2)
        self.tip_label.pack(side="left", anchor="w")
        self._refresh_headless_tip()                        # ← 立刻显示初始文字

        # 深度模式按钮
        self.var_deep = tk.BooleanVar(value=self.deep_mode)
        self.chk_deep = tk.Checkbutton(
            header, text="深度模式",
            variable=self.var_deep, command=self._on_deep_toggle,
            bg=self.COLOR_CARD, fg=self.COLOR_INFO,
            activebackground=self.COLOR_CARD, activeforeground=self.COLOR_INFO,
            selectcolor=self.COLOR_CARD,
            font=("Microsoft YaHei", 9), bd=0, highlightthickness=0,
            anchor="w")
        self.chk_deep.pack(side="left", anchor="w", padx=(14, 4))

        # 深度模式说明
        self.deep_tip_label = tk.Label(header, anchor="w", font=self.font_small,
                                       bg="#E3F2FD", fg="#0D47A1",
                                       padx=8, pady=2)
        self.deep_tip_label.pack(side="left", anchor="w")
        self._refresh_deep_tip()                            # ← 立刻显示初始文字

        r = 1
        # ★ 第 1 行所有按钮左对齐；只给第 12 列配权重，让第 2 行（监控来源）
        #   的容器横向铺满，但不影响第 1 行按钮的位置
        top.columnconfigure(12, weight=1)

        tk.Label(top, text="书名", bg=self.COLOR_CARD, fg=self.COLOR_MUTED,
                 font=self.font_small).grid(row=r, column=0, padx=(14, 4), pady=4, sticky="e")
        self.entry_keyword = tk.Entry(top, width=20, font=self.font_normal,
                                      bd=1, relief="solid", highlightthickness=0,
                                      bg="#FAFBFC", fg=self.COLOR_TEXT,
                                      insertbackground=self.COLOR_TEXT)
        self.entry_keyword.grid(row=r, column=1, padx=4, pady=4, ipady=5)

        self._make_btn(top, "💾 保存为书签", self.add_bookmark,
                       bg="#FFF3C4", fg="#8D6E00", width=12).grid(row=r, column=4, padx=(14, 4))

        self.btn_patrol = self._make_btn(top, "🔍 搜索本书", self.start_monitor_once,
                                         bg="#FFE0B2", fg="#9E5A00", width=12, bold=True)
        self.btn_patrol.grid(row=r, column=3, padx=4)

        self.btn_stop = self._make_btn(top, "⛔ 停止", self.stop_monitor,
                                       bg="#FFCDD2", fg="#B71C1C", width=8, bold=True)
        self.btn_stop.config(state="disabled", bg="#ECEFF1", activebackground="#ECEFF1", fg="#9AA5B1")
        self.btn_stop.grid(row=r, column=5, padx=4)

        self.btn_clear_cookies = self._make_btn(top, "🧼 清cookies", self.clear_cookies_now,
                                                bg="#FDE2E2", fg="#B71C1C",
                                                width=9, bold=True, small=True)
        self.btn_clear_cookies.grid(row=r, column=6, padx=4)

        self.btn_manual_search = self._make_btn(top, "🧭 手动搜索", self.open_manual_search_dialog,
                                               bg="#E1BEE7", fg="#6A1B9A", width=11, bold=True)
        self.btn_manual_search.grid(row=r, column=7, padx=4)

        r = 3
        # ★ 整行一个容器：右边两个按钮先 pack（先占位置），窗口拖窄时也不会被挤出窗口；
        #   左边的来源勾选框放在会自动换行的框里，窄了自动排到第二行
        src_row = tk.Frame(top, bg=self.COLOR_CARD)
        src_row.grid(row=r, column=0, columnspan=13, sticky="we", padx=(14, 12), pady=(4, 6))

        tk.Label(src_row, text="搜索来源", bg=self.COLOR_CARD, fg=self.COLOR_MUTED,
                 font=self.font_small).pack(side="left", padx=(0, 6))

        self.btn_source_toggle = tk.Checkbutton(src_row, text="全选",
                                                command=self.toggle_all_source_checks,
                                                bg=self.COLOR_CARD, activebackground=self.COLOR_CARD,
                                                fg="#0D47A1", font=("Microsoft YaHei", 9),
                                                bd=0, highlightthickness=0, cursor="hand2")
        self.btn_source_toggle.pack(side="left", padx=(0, 8), pady=3)

        src_frame = tk.Frame(src_row, bg=self.COLOR_CARD)
        src_frame.pack(side="left", fill="x", expand=True)
        self.src_flow = src_frame

        # 主界面只留这 8 个来源（顺序见 config.VISIBLE_SRC_ORDER）
        # 一个勾选框 = 一个来源组：常规模式只搜移动版，深度模式连 PC 版一起搜
        self.source_vars = {}
        self._source_checks = []
        for src_id in VISIBLE_SRC_ORDER:
            var = tk.BooleanVar(value=False)
            self.source_vars[src_id] = var
            cb = tk.Checkbutton(
                src_frame, text=self._source_display_name(src_id), variable=var,
                bg=self.COLOR_CARD, fg=self.COLOR_TEXT,
                activebackground=self.COLOR_CARD,
                activeforeground=self.COLOR_TEXT,
                selectcolor=self.COLOR_CARD,
                font=self.font_normal,
                bd=0, highlightthickness=0,
                anchor="w",
                padx=2, pady=0
            )
            self._source_checks.append(cb)

        self.src_flow.bind("<Configure>", lambda e: self._reflow_source_checks())
        self._schedule_reflow(self.src_flow, self._reflow_source_checks, (120, 400, 900))

        # ---------- 书签卡片 ----------
        card_outer, bk = self._card(top, "书签  ·  左键填入 / 双击搜索 / 右键菜单")
        card_outer.grid(row=2, column=0, columnspan=13, sticky="we", padx=(14, 12), pady=(4, 6))

        # 横向容器：左列（批量搜索 + 搜索页数）/ 右列（全选 + 书签流）
        bookmark_body = tk.Frame(bk, bg=self.COLOR_CARD)
        bookmark_body.pack(fill="x", padx=12, pady=(6, 6))

        # 左列
        left_col = tk.Frame(bookmark_body, bg=self.COLOR_CARD)
        left_col.pack(side="left", anchor="n", padx=(0, 14))

        self.btn_batch = self._make_btn(left_col, "📚 批量搜索",
                                        self.start_batch_monitor,
                                        bg=self.COLOR_INFO, width=12, bold=True)
        self.btn_batch.pack(anchor="w", pady=(0, 4))

        pages_row = tk.Frame(left_col, bg=self.COLOR_CARD)
        pages_row.pack(anchor="w")
        tk.Label(pages_row, text="搜索页数",
                 bg=self.COLOR_CARD, fg=self.COLOR_MUTED,
                 font=self.font_small).pack(side="left", padx=(0, 4))
        self.entry_pages = tk.Entry(pages_row, width=6, font=self.font_normal,
                                    bd=1, relief="solid", highlightthickness=0,
                                    bg="#FAFBFC", fg=self.COLOR_TEXT,
                                    insertbackground=self.COLOR_TEXT)
        self.entry_pages.pack(side="left", ipady=3)
        self.entry_pages.insert(0, "自动")

        # 右列：全选 + 书签流（都在 bookmark_inner 里）
        right_col = tk.Frame(bookmark_body, bg=self.COLOR_CARD)
        right_col.pack(side="left", fill="x", expand=True)

        self.bookmark_inner = tk.Frame(right_col, bg=self.COLOR_CARD)
        self.bookmark_inner.pack(fill="x")
        self._bookmark_cells = []
        self.bookmark_inner.bind("<Configure>", lambda e: self._reflow_bookmark_cells())

        # ---------- 工具栏 ----------
        tool = tk.Frame(self.root, bg=self.COLOR_BG)
        tool.pack(fill="x", padx=12, pady=(0, 6))

        tool_items = [
            ("✅ 全选/取消", None, self.toggle_current_selection, 9),
            ("🌐 批量打开", self.COLOR_INFO, self.open_selected_src, 9),
            ("📋 批量复制", "#FB8C00", self.copy_selected_src, 9),
            ("🛡 加白名单", "#8E24AA", self.add_selected_to_whitelist, 9),
            ("🏷 白名单词", "#6A1B9A", self.open_whitelist_words_dialog, None),
            ("🗑 清空", "#546E7A", self.clear_current_tab, 9),
            # ★ 托盘按钮和清空按钮中间：把「未加白链接」记录里的历史链接导入结果列表
            #   width=None → 按钮按文字自己撑开，文字不会被截断（中文/emoji 都够宽）
            ("📥 载入历史数据", "#00897B", self.load_unwhite_history, None),
            ("📤 托盘", "#607D8B", self.hide_to_tray, 9),
        ]
        for text, bg, cmd, w in tool_items:
            self._make_btn(tool, text, cmd, bg=bg, width=w, small=True).pack(
                side="left", padx=(0, 6))

        self.filter_frame = tk.Frame(tool, bg=self.COLOR_BG)
        self.filter_frame.pack(side="right", padx=(0, 4))
        tk.Label(self.filter_frame, text="🔍", bg=self.COLOR_BG,
                 fg=self.COLOR_MUTED, font=self.font_small).pack(side="left")
        self.entry_filter = tk.Entry(self.filter_frame, width=16, font=self.font_small,
                                     bd=1, relief="solid", highlightthickness=0,
                                     bg="#FAFBFC", fg=self.COLOR_TEXT,
                                     insertbackground=self.COLOR_TEXT)
        self.entry_filter.pack(side="left", padx=(4, 4), ipady=2)
        self.entry_filter.bind("<KeyRelease>", lambda e: self._on_filter_change())
        self._make_btn(self.filter_frame, "清空", self._clear_filter, bg="#B0BEC5",
                       fg="#263238", width=5, small=True).pack(side="left")

        # ---------- 主内容 PanedWindow ----------
        self.main_paned = tk.PanedWindow(self.root, orient="vertical",
                                         sashwidth=6, sashrelief="flat",
                                         bg=self.COLOR_BG, bd=0)
        self.main_paned.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        top_container = tk.Frame(self.main_paned, bg=self.COLOR_CARD)
        self.main_paned.add(top_container, stretch="always", minsize=200)

        self.notebook = ttk.Notebook(top_container)
        self.notebook.pack(fill="both", expand=True)

        # Tab: 全部
        self.tab_all = tk.Frame(self.notebook, bg=self.COLOR_CARD)
        self.notebook.add(self.tab_all, text="  全部  ")
        self.tree_all = self._make_tree(
            self.tab_all,
            columns=("sel", "source", "title", "date", "summary", "url", "content_url"),
            headings=("☑", "来源", "标题", "日期", "摘要", "最终url", "原始url"),
            widths=(30, 60, 220, 110, 380, 220, 200),
        )

        # 按显示分组生成 Tab 页：把 360移动 / 搜狗移动 / 微博移动 等归并到对应主来源
        self.engine_tabs = {}
        self.source_tab_group = {}
        for src_id in SRC_ORDER:
            group_key = self._display_group_for(src_id)
            self.source_tab_group[src_id] = group_key
            if group_key in self.engine_tabs:
                self.engine_tabs[group_key]["sources"].append(src_id)
                continue

            label = self._source_display_name(group_key)
            tab = tk.Frame(self.notebook, bg=self.COLOR_CARD)
            self.notebook.add(tab, text=f"  {label}  ")
            tree = self._make_tree(
                tab,
                columns=("sel", "title", "date", "summary", "url", "content_url"),
                headings=("☑", "标题", "日期", "摘要", "最终url", "原始url"),
                widths=(30, 220, 110, 380, 220, 200),
            )
            self.engine_tabs[group_key] = {"tab": tab, "tree": tree, "sources": [src_id], "label": label}

        # 文心 Tab（百度搜索里的文心内容单独显示）；测试程序不需要
        self.tab_ai = None
        self.tree_ai = None
        if self.SHOW_AI_TAB:
            self.tab_ai = tk.Frame(self.notebook, bg=self.COLOR_CARD)
            self.notebook.add(self.tab_ai, text="  文心  ")
            self.tree_ai = self._make_tree(
                self.tab_ai,
                columns=("sel", "title", "date", "summary", "url", "content_url"),
                headings=("☑", "标题", "日期", "摘要", "最终url", "原始url"),
                widths=(30, 220, 110, 380, 220, 200),
            )

        # ---------- 状态栏 ----------
        self.status_var = tk.StringVar(value="未开始")
        status_bar = tk.Label(self.root, textvariable=self.status_var, anchor="w",
                              bg="#E3E9EE", fg=self.COLOR_TEXT,
                              font=self.font_small, padx=14, pady=4)
        status_bar.pack(fill="x", side="bottom")

        # ---------- 日志卡片 ----------
        log_outer, log_card = self._card(self.main_paned)
        self.main_paned.add(log_outer, stretch="never", minsize=60, height=170)

        log_head = tk.Frame(log_card, bg=self.COLOR_CARD)
        log_head.pack(fill="x", padx=12, pady=(8, 4))
        tk.Label(log_head, text="运行日志", bg=self.COLOR_CARD, fg=self.COLOR_TEXT,
                 font=self.font_title).pack(side="left")
        tk.Label(log_head, text="  >>>>>>>>> 四十米大剑捅死盗文 🔪",
                 bg=self.COLOR_CARD, fg=self.COLOR_MUTED,
                 font=self.font_small).pack(side="left")

        self.log_text = scrolledtext.ScrolledText(
            log_card, height=6, state='disabled',
            bg="#FFFFFF", fg="#2F3542",
            insertbackground="#2F3542",
            font=("Cascadia Mono", 9), bd=0, relief="flat",
            padx=10, pady=6)
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    # ==================== 事件回调 ====================
    def _on_headless_toggle(self):
        self.headless = not bool(self.var_headless.get())
        self._refresh_headless_tip()

    def _on_deep_toggle(self):
        """深度模式开关：决定「百度/头条/搜狗/360/知道」要不要 pc + 移动两个渠道一起搜"""
        self._refresh_deep_tip()
        if self.var_deep.get():
            self.log("🕳️ 深度模式已开：百度/头条/搜狗/360/知道 同时搜 pc 和移动两个渠道"
                     "（更慢、更容易触发验证码）")
        else:
            self.log("🕳️ 深度模式已关：百度/头条/搜狗/360/知道 只搜一个渠道")

    def _on_filter_change(self):
        try:
            self._filter_keyword = self.entry_filter.get().strip()
        except Exception:
            self._filter_keyword = ""
        self.refresh_all_trees()

    def _clear_filter(self):
        try:
            self.entry_filter.delete(0, tk.END)
        except Exception:
            pass
        self._filter_keyword = ""
        self.refresh_all_trees()

    def open_manual_search_dialog(self):
        """手动搜索：demo.py 风格（实现在 manual_search.py，书签与 bookmarks.json 关联）"""
        from manual_search import open_manual_search
        open_manual_search(self)

    def _refresh_headless_tip(self):
        try:
            if self.var_headless.get():
                self.tip_label.config(
                    text="当前：前台模式（可手动过验证码/登录）",
                    bg="#E8F5E9", fg="#1B5E20")
            else:
                self.tip_label.config(
                    text="当前：后台模式（大量 0 结果时建议切入前台模式）",
                    bg="#FFF8E1", fg="#6D4C41")
        except Exception:
            pass

    def _refresh_deep_tip(self):
        """深度模式的小说明：位于前台模式说明的右边"""
        try:
            if self.var_deep.get():
                self.deep_tip_label.config(
                    text="当前：深度模式（pc+移动双渠道）",
                    bg="#E3F2FD", fg="#0D47A1")
            else:
                self.deep_tip_label.config(
                    text="当前：常规模式（每个来源只搜一个渠道）",
                    bg="#ECEFF1", fg="#455A64")
        except Exception:
            pass
    @staticmethod

    @staticmethod
    def _normalize_url_for_dedupe(url):
        if not url:
            return ""
        u = str(url).strip().lower()
        u = re.sub(r'^https?://(?=https?://)', '', u)
        u = re.sub(r'^https?://', '', u)
        u = re.sub(r'^www\.', '', u)
        u = u.split('#')[0].rstrip('/')
        return u

    @classmethod
    def _row_key(cls, group_source, url):
        return f"{group_source}|{cls._normalize_url_for_dedupe(url)}"

    def _make_tree(self, parent, columns, headings, widths):
        wrapper = tk.Frame(parent, bg=self.COLOR_CARD)
        wrapper.pack(fill="both", expand=True, padx=8, pady=8)

        frame = tk.Frame(wrapper, bg=self.COLOR_CARD)
        frame.pack(fill="both", expand=True)

        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for col, head, w in zip(columns, headings, widths):
            tree.heading(col, text=head)
            if col == "sel":
                anchor = "center"
            elif col in ("date", "source"):
                anchor = "center"
            else:
                anchor = "w"
            tree.column(col, width=w, anchor=anchor, stretch=(col != "sel"))
        tree.tag_configure("whitelisted", foreground="#9AA5B1")
        tree.tag_configure("new_bold", font=("Microsoft YaHei", 10, "bold"))
        tree.tag_configure("redirected", foreground="#E63946")  # 红色显示重定向结果
        tree.pack(side="left", fill="both", expand=True)

        sy = ttk.Scrollbar(frame, orient="vertical", command=tree.yview,
                           style="Vertical.TScrollbar")
        sy.pack(side="right", fill="y")
        tree.config(yscrollcommand=sy.set)

        sx = ttk.Scrollbar(wrapper, orient="horizontal", command=tree.xview)
        sx.pack(fill="x")
        tree.config(xscrollcommand=sx.set)

        tree.bind("<<TreeviewSelect>>", lambda e, t=tree: self._on_tree_select(t))
        tree.bind("<Double-1>", lambda e, t=tree: self.open_problem_url_from(t))
        tree.bind("<Button-3>", lambda e, t=tree: self.show_context_menu(e, t))
        return tree

    def _on_tree_select(self, tree):
        if self._suppress_select_event:
            return
        try:
            selected = set(tree.selection())
            for item in tree.get_children():
                if item not in self._rows:
                    continue
                is_sel = item in selected
                self._rows[item]['checked'] = is_sel
                vals = tree.item(item, "values")
                mark = "☑" if is_sel else "☐"
                if str(vals[0]) != mark:
                    new_vals = list(vals)
                    new_vals[0] = mark
                    tree.item(item, values=new_vals)
        except Exception:
            pass

    def _display_group_for(self, source):
        """按当前程序是否启用来源合并，返回该来源所属的标签页 key。"""
        if not self.MERGE_DISPLAY_GROUPS:
            return source
        return DISPLAY_SOURCE_GROUP.get(source, source)

    def _tab_group_for_source(self, source):
        return self.source_tab_group.get(source, self._display_group_for(source))

    def current_tree(self):
        idx = self.notebook.index(self.notebook.select())
        # 第一个是"全部"Tab
        if idx == 0:
            return self.tree_all
        # 最后一个是"文心"Tab（可用 SHOW_AI_TAB 关闭）
        if self.tree_ai is not None and idx == len(self.engine_tabs) + 1:
            return self.tree_ai
        # 中间的是合并后的各个来源 Tab
        tab_keys = list(self.engine_tabs.keys())
        return self.engine_tabs[tab_keys[idx - 1]]["tree"]

    @staticmethod
    def _is_all_tree(tree):
        return len(tree["columns"]) == 7

    def get_row_urls(self, tree, item):
        """item 是 tree 里的 iid，即复合 key（来源|url）"""
        row = self._rows.get(item)
        if row:
            url = row.get('url') or ''
            if url and 'baidu.com/link?' in url:
                url = "打开超时"
            return url, row.get('long_url') or row.get('content_url') or ''
        # 兜底（旧数据/未在 _rows 中的情况）
        vals = tree.item(item, "values")
        if self._is_all_tree(tree):
            return vals[5], vals[6]
        return vals[4], vals[5]

    # ==================== 搜索词编辑对话框 ====================
    def _suffix_dialog(self, title, hint, suffixes, on_ok):
        suffixes = [dict(s) if isinstance(s, dict)
                    else {"text": str(s or ""), "enabled": True}
                    for s in (suffixes or [])]
        suffixes = normalize_suffixes(suffixes)

        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("600x660")
        win.configure(bg=self.COLOR_BG)
        win.transient(self.root)
        win.grab_set()
        # ★ 点「显示桌面」把窗口藏起来后，还能从任务栏切回来
        fix_show_desktop_return(win, self.root)

        tk.Label(win, text=title, bg=self.COLOR_BG, fg=self.COLOR_TEXT,
                 font=("Microsoft YaHei", 13, "bold")).pack(pady=(16, 2))
        tk.Label(win, text=hint, bg=self.COLOR_BG, fg=self.COLOR_MUTED,
                 font=self.font_small, wraplength=540, justify="left").pack(padx=20)

        outer = tk.Frame(win, bg=self.COLOR_BORDER)
        outer.pack(fill="both", expand=True, padx=20, pady=12)
        body = tk.Frame(outer, bg=self.COLOR_CARD)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        ctrl = tk.Frame(body, bg=self.COLOR_CARD)
        ctrl.pack(fill="x", padx=14, pady=(12, 6))
        self._make_btn(ctrl, "✅ 全选", lambda: _select_all(), bg="#DCEDC8", fg="#33691E",
                       width=8, small=True).pack(side="left", padx=(0, 6))
        self._make_btn(ctrl, "⬜ 全不选", lambda: _select_none(), bg="#ECEFF1", fg="#455A64",
                       width=8, small=True).pack(side="left")
        tk.Label(ctrl, text="点第一列或按空格切换；『无后缀』可取消勾选，但不可删除",
                 bg=self.COLOR_CARD, fg=self.COLOR_MUTED,
                 font=("Microsoft YaHei", 8)).pack(side="left", padx=10)

        tf = tk.Frame(body, bg=self.COLOR_CARD)
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

        row = tk.Frame(body, bg=self.COLOR_CARD)
        row.pack(fill="x", padx=14, pady=(0, 6))
        ent = tk.Entry(row, font=self.font_normal,
                       bd=1, relief="solid", highlightthickness=0,
                       bg="#FAFBFC", fg=self.COLOR_TEXT, insertbackground=self.COLOR_TEXT)
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
        self._make_btn(row, "➕ 新增", add, bg=self.COLOR_PRIMARY,
                       width=8, small=True).pack(side="left", padx=(6, 0))

        btns = tk.Frame(body, bg=self.COLOR_CARD)
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
            for s in self.default_suffixes:
                t = s.get("text", "") if isinstance(s, dict) else str(s or "")
                if t and t not in exists:
                    suffixes.append({"text": t, "enabled": True})
                    exists.add(t)
            refresh_tree()

        self._make_btn(btns, "🗑️ 删除选中", del_sel, bg="#EF5350", width=11, small=True).pack(side="left")
        self._make_btn(btns, "📥 导入默认搜索词", import_default,
                       bg="#7E57C2", width=16, small=True).pack(side="left", padx=8)

        def ok():
            result = [dict(s) for s in suffixes]
            on_ok(result)
            win.destroy()

        self._make_btn(btns, "取 消", win.destroy, bg="#B0BEC5",
                       fg="#263238", width=8, small=True).pack(side="right")
        self._make_btn(btns, "✅ 确 定", ok, bg=self.COLOR_PRIMARY,
                       width=10, small=True, bold=True).pack(side="right", padx=8)

        refresh_tree()
        ent.focus_set()

    def edit_suffixes_by_id(self, book_id):
        bm = self._find_bookmark_by_id(book_id)
        if not bm:
            return

        def on_ok(sufs):
            bm["suffixes"] = sufs
            self.save_bookmarks()
            self.refresh_bookmarks()
            self.log(f"🏷️ 「{bm['name']}」搜索词已更新：{fmt_suffixes(sufs)}")

        self._suffix_dialog(
            f"编辑搜索词 - {bm['name']}",
            "勾选的搜索词会与书名拼成搜索关键词（如「书名 网盘」）；未勾选则跳过",
            bm["suffixes"], on_ok)

    def edit_default_suffixes(self):
        def on_ok(sufs):
            self.default_suffixes = sufs
            self.save_default_suffixes()
            self.log(f"🔍 搜索词已更新：{fmt_suffixes(sufs)}")

        self._suffix_dialog(
            "搜索词（巡逻一次时使用）",
            "点「巡逻一次」时，会用搜索框里的书名 + 这里的搜索词来搜索",
            self.default_suffixes, on_ok)

    # ==================== 书签 ====================
    def _update_select_all_state(self):
        """根据所有书签的勾选状态，刷新三态全选框"""
        if not hasattr(self, "bookmark_select_all_var"):
            return
        values = [v.get() for v in self.bookmark_checks.values()]
        if not values:
            state = 0
        elif all(values):
            state = 1
        elif any(values):
            state = 2   # 半选
        else:
            state = 0
        try:
            self.bookmark_select_all_var.set(state)
        except Exception:
            pass

    def _bookmark_var(self, book_id):
        """书签前面那个小方格（勾上的才会被「📚 批量搜索」带上）"""
        if not hasattr(self, "bookmark_checks"):
            self.bookmark_checks = {}
        var = self.bookmark_checks.get(book_id)
        if var is None:
            var = tk.BooleanVar(value=False)
            var.trace_add("write", lambda *a: self._update_select_all_state())
            self.bookmark_checks[book_id] = var
        return var

    def _checked_bookmarks(self):
        return [b for b in self.bookmarks
                if self._bookmark_var(b.get("id")).get()]

    # ==================== 会自动换行的排版 ====================
    def _flow_place(self, holder, cells, key, width, gap=6, row_gap=2):
        """把控件按可用宽度一行行排开：够宽就一行，窄了自动换到下一行（不重叠、不溢出窗口）"""
        if not cells or width <= 1:
            return
        # ★ 先把待处理的界面布局跑掉，免得拿到还没算准的控件宽度（那样会误判成「一行放得下」）
        try:
            holder.update_idletasks()
        except Exception:
            pass
        cache = getattr(self, "_flow_cache", None)
        if cache is None:
            cache = self._flow_cache = {}
        limit = max(int(width) - 2, 80)
        plan = []
        row = 0
        col = 0
        used = 0
        for cell in cells:
            try:
                w = cell.winfo_reqwidth()
            except Exception:
                w = 0
            if col and (used + w) > limit:
                row += 1
                col = 0
                used = 0
            plan.append((cell, row, col, int(w)))
            used += w + gap
            col += 1
        # 把量到的宽度也写进签名：宽度变了（字体/文字变了）就重新排一次
        signature = tuple((str(cell), r, c, w) for cell, r, c, w in plan)
        if cache.get(key) == signature:
            return
        cache[key] = signature
        for cell, r, c, _w in plan:
            try:
                cell.grid(row=r, column=c, sticky="w", padx=(0, gap), pady=row_gap)
            except Exception:
                pass

    def _reflow_source_checks(self, tries=0):
        """监控来源的勾选框：窗口窄了自动排到第二行"""
        holder = getattr(self, "src_flow", None)
        cells = list(getattr(self, "_source_checks", []) or [])
        if holder is None or not cells:
            return
        try:
            holder.update_idletasks()
        except Exception:
            pass
        width = holder.winfo_width()
        if width <= 1:
            if tries < 40:
                holder.after(80, lambda: self._reflow_source_checks(tries + 1))
            return
        self._flow_place(holder, cells, "sources", width)

    def _reflow_bookmark_cells(self, tries=0):
        """书签：多了/窗口窄了自动换行，不会重叠也不会跑出窗口"""
        holder = getattr(self, "bookmark_inner", None)
        cells = list(getattr(self, "_bookmark_cells", []) or [])
        if holder is None or not cells:
            return
        try:
            holder.update_idletasks()
        except Exception:
            pass
        width = holder.winfo_width()
        if width <= 1:
            if tries < 40:
                holder.after(80, lambda: self._reflow_bookmark_cells(tries + 1))
            return
        self._flow_place(holder, cells, "bookmarks", width)

    def _schedule_reflow(self, holder, func, delays=(150, 400, 900)):
        """界面刚建好/刚加书签时宽度可能还没算准，过一会儿再排几次，保证立刻就是多行"""
        for d in delays:
            try:
                holder.after(d, func)
            except Exception:
                pass

    def _reposition_suffix_popup(self):
        popup = getattr(self, "_suffix_popup", None)
        owner_bid = getattr(self, "_suffix_popup_owner_bid", None)
        if not popup or not owner_bid:
            return
        try:
            if not popup.winfo_exists():
                return
        except Exception:
            return
        target_cell = None
        for i, bm in enumerate(self.bookmarks):
            if bm.get("id") == owner_bid and i < len(self._bookmark_cells):
                target_cell = self._bookmark_cells[i]
                break
        if not target_cell:
            return
        try:
            target_cell.update_idletasks()
            x = target_cell.winfo_rootx() - self.root.winfo_rootx()
            y = target_cell.winfo_rooty() - self.root.winfo_rooty() + target_cell.winfo_height() + 2
            popup.place(x=x, y=y)
            popup.lift()
        except Exception:
            pass

    def _open_suffix_popup(self, book_id, anchor_cell):
        # 关掉已有浮层
        if getattr(self, "_suffix_popup", None):
            try:
                self._suffix_popup.destroy()
            except Exception:
                pass
            self._suffix_popup = None

        # ★ 解绑旧的 root 监听（否则会累积，导致关不掉）
        try:
            _old_bid = getattr(self, "_suffix_popup_close_bind", None)
            if _old_bid:
                self.root.unbind("<Button-1>", _old_bid)
        except Exception:
            pass
        try:
            _old_bid = getattr(self, "_suffix_popup_esc_bind", None)
            if _old_bid:
                self.root.unbind("<Escape>", _old_bid)
        except Exception:
            pass
        self._suffix_popup_close_bind = None
        self._suffix_popup_esc_bind = None

        self._suffix_popup_owner_bid = book_id

        bm = next((b for b in self.bookmarks if b.get("id") == book_id), None)
        if not bm:
            return

        popup = tk.Frame(self.root, bg="#FFFFFF",
                         relief="solid", bd=1, highlightthickness=0)
        self._suffix_popup = popup

        tk.Label(popup, text=f"「{bm['name']}」的搜索词",
                 bg="#FFFFFF", fg="#2F3542",
                 font=("Microsoft YaHei", 9, "bold")).pack(anchor="w", padx=8, pady=(6, 4))

        list_frame = tk.Frame(popup, bg="#FFFFFF")
        list_frame.pack(fill="x", padx=8, pady=(0, 4))

        def _render():
            for w in list_frame.winfo_children():
                w.destroy()
            for i, s in enumerate(bm["suffixes"]):
                row = tk.Frame(list_frame, bg="#FFFFFF")
                row.pack(fill="x", pady=1)
                var = tk.BooleanVar(value=bool(s.get("enabled", True)))

                def _toggle(idx=i, v=var):
                    bm["suffixes"][idx]["enabled"] = bool(v.get())
                    self.save_bookmarks()

                tk.Checkbutton(row, variable=var, command=_toggle,
                               bg="#FFFFFF", activebackground="#FFFFFF",
                               bd=0, highlightthickness=0).pack(side="left")
                label = s.get("text", "") or "（无后缀）"
                tk.Label(row, text=label, bg="#FFFFFF", fg="#2F3542",
                         font=("Microsoft YaHei", 9)).pack(side="left", padx=(2, 8))

                x_btn = tk.Label(row, text="✕", bg="#FFFFFF", fg="#E63946",
                                 font=("Microsoft YaHei", 9, "bold"), cursor="hand2")
                x_btn.pack(side="right", padx=(4, 2))

                def _delete(e=None, idx=i):
                    bm["suffixes"].pop(idx)
                    self.save_bookmarks()
                    self.refresh_bookmarks()
                    _render()
                x_btn.bind("<Button-1>", _delete)

        _render()

        add_row = tk.Frame(popup, bg="#FFFFFF")
        add_row.pack(fill="x", padx=8, pady=(2, 6))
        entry = tk.Entry(add_row, font=("Microsoft YaHei", 9),
                         bd=1, relief="solid", highlightthickness=0)
        entry.pack(side="left", fill="x", expand=True, ipady=2)

        def _add(e=None):
            t = entry.get().strip()
            if not t:
                return
            bm["suffixes"].append({"text": t, "enabled": True})
            self.save_bookmarks()
            self.refresh_bookmarks()
            entry.delete(0, "end")
            _render()
        entry.bind("<Return>", _add)

        tk.Button(add_row, text="＋ 添加", command=_add,
                  bg="#5DADE2", fg="#FFFFFF", relief="flat",
                  font=("Microsoft YaHei", 9), padx=6).pack(side="left", padx=(4, 0))

        # 定位到书签下方
        anchor_cell.update_idletasks()
        x = anchor_cell.winfo_rootx() - self.root.winfo_rootx()
        y = anchor_cell.winfo_rooty() - self.root.winfo_rooty() + anchor_cell.winfo_height() + 2
        popup.place(x=x, y=y)
        popup.lift()

        # 点别处 / Esc 关闭
        def _do_close():
            try:
                popup.destroy()
            except Exception:
                pass
            self._suffix_popup = None
            self._suffix_popup_owner_bid = None
            try:
                bid = getattr(self, "_suffix_popup_close_bind", None)
                if bid:
                    self.root.unbind("<Button-1>", bid)
            except Exception:
                pass
            try:
                bid = getattr(self, "_suffix_popup_esc_bind", None)
                if bid:
                    self.root.unbind("<Escape>", bid)
            except Exception:
                pass
            self._suffix_popup_close_bind = None
            self._suffix_popup_esc_bind = None

        def _close_by_click(e):
            # ★ 判断点击位置是否在浮层内部，在的话不关闭
            try:
                w = self.root.winfo_containing(e.x_root, e.y_root)
                p = w
                while p is not None:
                    if p == popup:
                        return
                    p = getattr(p, "master", None)
            except Exception:
                pass
            _do_close()

        def _close_by_esc(e=None):
            _do_close()

        # ★ 延迟绑定：让当前这次"点 ✏"的事件先冒泡完，再挂关闭监听
        def _bind_close():
            if not getattr(self, "_suffix_popup", None):
                return
            try:
                self._suffix_popup_close_bind = self.root.bind(
                    "<Button-1>", _close_by_click, add="+")
                self._suffix_popup_esc_bind = self.root.bind(
                    "<Escape>", _close_by_esc, add="+")
            except Exception:
                pass
        self.root.after(150, _bind_close)
        entry.focus_set()

    def refresh_bookmarks(self):
        for w in self.bookmark_buttons:
            try:
                w.destroy()
            except Exception:
                pass
        self.bookmark_buttons = []
        self._bookmark_cells = []

        # ★ 全选 cell（第一个）
        all_cell = tk.Frame(self.bookmark_inner, bg=self.COLOR_CARD)
        self.btn_select_all = tk.Checkbutton(all_cell, text="全选",
                                             variable=self.bookmark_select_all_var,
                                             onvalue=1, offvalue=0, tristatevalue=2,
                                             command=self.toggle_all_bookmarks,
                                             bg=self.COLOR_CARD, activebackground=self.COLOR_CARD,
                                             fg="#0D47A1", font=("Microsoft YaHei", 9),
                                             bd=0, highlightthickness=0, cursor="hand2",
                                             takefocus=0)
        self.btn_select_all.pack(side="left", padx=(0, 8))
        self.bookmark_buttons.append(all_cell)
        self._bookmark_cells.append(all_cell)
        try:
            self._flow_cache.pop("bookmarks", None)
        except Exception:
            pass

        # 清掉已删除书签的勾选状态
        valid_ids = set(b.get("id") for b in self.bookmarks)
        for bid in list(getattr(self, "bookmark_checks", {}) or {}):
            if bid not in valid_ids:
                self.bookmark_checks.pop(bid, None)

        if not self.bookmarks:
            lbl = tk.Label(self.bookmark_inner,
                           text="（还没有书签，输入书名后点「💾 保存为书签」）",
                           bg=self.COLOR_CARD, fg=self.COLOR_MUTED,
                           font=self.font_small)
            self.bookmark_buttons.append(lbl)
            self._bookmark_cells.append(lbl)
            self._reflow_bookmark_cells()
            self._schedule_reflow(self.bookmark_inner, self._reflow_bookmark_cells)
            # ★ 浮层跟随：若后缀浮层开着，重排后重新定位到 owner 书签下方
            self.root.after(20, self._reposition_suffix_popup)
            return

        for bm in self.bookmarks:
            book_id = bm.get("id")
            name = bm["name"]
            sufs = bm.get("suffixes") or []
            enabled = [s for s in sufs if s.get("enabled", True)]
            has_no = any(s.get("text", "") == "" for s in enabled)
            n = len(enabled)
            if has_no and n > 1:
                text = f"{name}  [{n-1}+无]"
            elif has_no and n == 1:
                text = f"{name}  [无后缀]"
            else:
                text = f"{name}  [{n}档]"

            # ★ 一个书签 = 一个「小方格 + 按钮」的卡片，整体作为一格参与自动换行
            cell = tk.Frame(self.bookmark_inner, bg=self.COLOR_CARD)
            chk = tk.Checkbutton(cell, variable=self._bookmark_var(book_id),
                                 bg=self.COLOR_CARD, activebackground=self.COLOR_CARD,
                                 bd=0, highlightthickness=0, cursor="hand2",
                                 takefocus=0)
            chk.pack(side="left", padx=(0, 1))

            btn = tk.Button(cell, text=text,
                            font=("Microsoft YaHei", 10, "bold"),
                            bg=self.COLOR_CARD,
                            fg="#00695C" if n else "#B71C1C",
                            activebackground="#F5F7FA",
                            activeforeground="#004D40" if n else "#B71C1C",
                            relief="flat", bd=0, cursor="hand2",
                            padx=4, pady=2, highlightthickness=0,
                            command=lambda bid=book_id: self.load_bookmark(bid))
            btn.pack(side="left")

            edit_btn = tk.Label(cell, text="✏", bg=self.COLOR_CARD,
                                fg="#5DADE2", font=("Microsoft YaHei", 9),
                                cursor="hand2")
            edit_btn.pack(side="left", padx=(3, 0))
            edit_btn.bind("<Button-1>",
                          lambda e, bid=book_id, c=cell: self._open_suffix_popup(bid, c))
            btn.bind("<Double-Button-1>", lambda e, bid=book_id: self.start_bookmark_monitor(bid))
            btn.bind("<Button-3>", lambda e, bid=book_id: self.bookmark_menu(e, bid))
            self.bookmark_buttons.append(cell)
            self._bookmark_cells.append(cell)

        self._reflow_bookmark_cells()
        # ★ 刚加完书签时控件宽度可能还没算准，稍后再排两次，保证马上就分成多行
        self._schedule_reflow(self.bookmark_inner, self._reflow_bookmark_cells)
        # ★ 浮层跟随：若后缀浮层开着，重排后重新定位到 owner 书签下方
        self.root.after(20, self._reposition_suffix_popup)

    def bookmark_menu(self, event, book_id):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="监控此书签",
                      command=lambda: self.start_bookmark_monitor(book_id))
        m.add_separator()
        m.add_command(label="删除书签",
                      command=lambda: self.delete_bookmark_by_id(book_id))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def add_suffix_from_entry_by_id(self, book_id):
        bm = self._find_bookmark_by_id(book_id)
        if not bm:
            return
        v = self.entry_keyword.get().strip()
        if not v:
            messagebox.showinfo("提示", "请先在书名输入框里输入要作为搜索词的词。")
            return
        exists = set(s.get("text", "") for s in bm["suffixes"])
        if v in exists:
            messagebox.showinfo("提示", f"「{v}」已在该书签的搜索词列表里。")
            return
        bm["suffixes"].append({"text": v, "enabled": True})
        self.save_bookmarks()
        self.refresh_bookmarks()
        self.log(f"🏷️ 「{bm['name']}」新增搜索词：{v}")

    def add_bookmark(self):
        name = self.entry_keyword.get().strip()
        if not name:
            messagebox.showinfo("提示", "请先在输入框里输入书名！")
            return
        sufs = [
            {"text": "", "enabled": True},
            {"text": "链接", "enabled": True},
            {"text": "网盘", "enabled": True},
            {"text": "免费", "enabled": True},
        ]
        book_id = uuid.uuid4().hex[:8]
        self.bookmarks.append({"id": book_id, "name": name, "suffixes": sufs})
        self.save_bookmarks()
        self.refresh_bookmarks()
        self.log(f"💾 已保存书签: {name}（4 个默认搜索词）")

    def load_bookmark(self, book_id):
        bm = self._find_bookmark_by_id(book_id)
        if not bm:
            return
        self.entry_keyword.delete(0, tk.END)
        self.entry_keyword.insert(0, bm["name"])

    def delete_bookmark_by_id(self, book_id):
        bm = self._find_bookmark_by_id(book_id)
        if not bm:
            return
        if not messagebox.askyesno("确认删除", f"确定要删除书签「{bm['name']}」吗？"):
            return
        self.bookmarks.remove(bm)
        self.save_bookmarks()
        self.refresh_bookmarks()
        self.log(f"🗑️ 已删除书签: {bm['name']}")

    # ==================== 勾选/全选 ====================
    def toggle_all_source_checks(self):
        values = list(self.source_vars.values())
        if not values:
            return
        should_select = not all(var.get() for var in values)
        for var in values:
            var.set(should_select)

    def toggle_all_bookmarks(self):
        """书签区三态全选：勾选 → 全选；取消 → 全不选"""
        if not hasattr(self, "bookmark_checks") or not self.bookmark_checks:
            return
        want = (self.bookmark_select_all_var.get() == 1)
        for var in self.bookmark_checks.values():
            var.set(want)

    def clear_cookies_now(self):
        if self.is_monitoring:
            messagebox.showinfo("提示", "监控进行中，建议等本轮结束后再清 cookies。")
            return
        cleared = False
        if self.browser_context is not None:
            try:
                self.browser_context.clear_cookies()
                cleared = True
            except Exception:
                pass
        profile_dir = os.path.join(BASE_DIR, "baidu_engine_data")
        for candidate in [
            os.path.join(profile_dir, "Default", "Cookies"),
            os.path.join(profile_dir, "Default", "Network", "Cookies"),
            os.path.join(profile_dir, "Network", "Cookies"),
        ]:
            try:
                if os.path.exists(candidate):
                    os.remove(candidate)
                    cleared = True
            except Exception:
                pass
        if cleared:
            self.log("🧼 已清理浏览器 cookies，下一次搜索会从干净状态重试。")
        else:
            self.log("🧼 没有可清理的 cookies 文件，当前浏览器状态已是干净的。")

    def select_all_current(self):
        tree = self.current_tree()
        url_idx = 5 if self._is_all_tree(tree) else 4
        items = []
        for item in tree.get_children():
            vals = tree.item(item, "values")
            if url_idx >= len(vals):
                continue
            url = vals[url_idx]
            row = self._rows.get(url)
            if not row:
                continue
            if row.get('is_white'):
                continue
            items.append(item)
        if items:
            tree.selection_add(items)

    def toggle_current_selection(self):
        tree = self.current_tree()
        if not tree.get_children():
            return
        selected = set(tree.selection())
        total = list(tree.get_children())
        if len(selected) >= len(total):
            tree.selection_remove(total)
        else:
            self.select_all_current()

    def clear_current_tab(self):
        tree = self.current_tree()
        if not messagebox.askyesno("确认", "清空当前 Tab 的显示？（不影响历史记录、白名单）"):
            return
        if tree is self.tree_all:
            self._rows.clear()
        elif self.tree_ai is not None and tree is self.tree_ai:
            self._rows = {u: r for u, r in self._rows.items()
                        if not (r['source'] == 'baidu' and r.get('is_zhinengti'))}
        else:
            # 动态判断是哪个引擎的 Tab（移动版 / PC 版算同一个 Tab）
            for tab_key, tab_info in self.engine_tabs.items():
                if tree is tab_info["tree"]:
                    self._rows = {u: r for u, r in self._rows.items()
                                  if self._tab_group_for_source(r['source']) != tab_key}
                    break
        self._sum_author_seen = set((r['summary'], r['author']) for r in self._rows.values())
        self.refresh_all_trees()
        self.log("🗑️ 已清空当前 Tab")

    def load_unwhite_history(self):
        """载入历史数据：把「未加白链接」记录里的链接倒进结果列表（不联网、不改记录）"""
        try:
            records = unwhite_log.all_records()
        except Exception as e:
            self.log(f"📥 读取历史数据失败：{type(e).__name__}: {e}")
            return
        if not records:
            messagebox.showinfo("载入历史数据",
                                "还没有历史数据。\n\n"
                                "（监控抓到「没加白」的链接后，这里就能导入了）")
            return

        loaded = 0
        skipped = 0
        for it in records:
            url = str(it.get('url') or '').strip()
            if not url.lower().startswith(('http://', 'https://')):
                continue
            src = str(it.get('source') or '').strip() or 'unknown'
            group_for_key = self._display_group_for(src)
            key = self._row_key(group_for_key, url)
            if key in self._rows:
                skipped += 1
                continue
            book = str(it.get('book') or '').strip() or '（未知书名）'
            label = self._source_display_name(src)
            self._rows[key] = {
                'source': src,
                'title': book,
                'date': '历史',
                'summary': "历史搜索结果（已加白名单）" if url in self.whitelist_common else "历史搜索结果（未加白名单）",
                'author': label,
                'url': url,
                'content_url': url,
                'is_new': False,
                'is_white': url in self.whitelist_common,
                'is_zhinengti': False,
                'checked': False,
                'long_url': url,
            }
            loaded += 1
        self._sum_author_seen = set((r['source'], r['summary'], r['author'])
                                    for r in self._rows.values())
        self.refresh_all_trees()
        self.log(f"📥 已载入历史数据 {loaded} 条"
                 + (f"（{skipped} 条已经在列表里，跳过）" if skipped else ""))

    # ==================== 打开/复制 ====================
    def open_problem_url_from(self, tree):
        sel = tree.selection()
        if not sel:
            return
        url, _ = self.get_row_urls(tree, sel[0])
        if url:
            webbrowser.open(url)
            self.log(f"🌐 已打开: {url}")

    def open_selected_src(self):
        tree = self.current_tree()
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中要打开的行！")
            return
        urls = [self.get_row_urls(tree, i)[0] for i in sel if self.get_row_urls(tree, i)[0]]
        if not urls:
            return
        self._open_urls(urls)

    def copy_selected_src(self):
        tree = self.current_tree()
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中要复制的行！")
            return
        urls = [self.get_row_urls(tree, i)[0] for i in sel if self.get_row_urls(tree, i)[0]]
        if not urls:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(urls))
        messagebox.showinfo("复制成功", f"已复制 {len(urls)} 条链接到剪贴板。")

    def _open_urls(self, urls):
        if not urls:
            return
        if len(urls) > 10:
            if not messagebox.askyesno("确认", f"即将打开 {len(urls)} 个标签页，是否继续？"):
                return
        count = 0
        for u in urls:
            try:
                webbrowser.open(u)
                count += 1
                time.sleep(0.15)
            except Exception as e:
                self.log(f"打开失败 {u}: {e}")
        self.log(f"🌐 批量打开完成: {count}/{len(urls)}")

    # ==================== 右键菜单 ====================
    def show_context_menu(self, event, tree):
        item = tree.identify_row(event.y)
        if not item:
            return
        if item not in tree.selection():
            tree.selection_set(item)
        selected = list(tree.selection())
        selected_urls = []
        selected_content_urls = []
        base_row_data = None
        for row_id in selected:
            url, content_url = self.get_row_urls(tree, row_id)
            if url:
                selected_urls.append(url)
            if content_url:
                selected_content_urls.append(content_url)
            if row_id == item:
                base_row_data = self._rows.get(item)   # ★ iid = 复合 key
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="🌐 打开所选链接",
                      command=lambda: self._open_urls(selected_urls))
        m.add_command(label="📋 复制所选链接",
                      command=lambda: self._copy_urls(selected_urls))
        m.add_separator()
        if selected_content_urls:
            m.add_command(label="🔗 打开所选原始url",
                          command=lambda: self._open_urls(selected_content_urls))
            m.add_command(label="📋 复制所选原始url",
                          command=lambda: self._copy_urls(selected_content_urls))
        else:
            m.add_command(label="🔗 打开所选原始url（无）", state="disabled")
            m.add_command(label="📋 复制所选原始url（无）", state="disabled")
        m.add_separator()
        row_data = base_row_data or {}
        if row_data.get('source'):
            engine_class = MANUAL_SEARCH_ENGINE_MAP.get(row_data['source'])
            if engine_class and hasattr(engine_class, 'SEARCH_URL'):
                search_url = engine_class.SEARCH_URL.format(kw=self._extract_search_keyword(row_data))
                if search_url:
                    m.add_command(label="🌐 打开源链接",
                                  command=lambda url=search_url: webbrowser.open(url))
        if row_data.get('redirects'):
            redirects = row_data.get('redirects', [])
            if redirects:
                m.add_separator()
                m.add_command(label="📋 复制重定向url",
                              command=lambda r=redirects: self._copy_redirects(r))
        m.add_separator()
        m.add_command(label="🔒 加入白名单", command=self.add_selected_to_whitelist)
        m.add_command(label="🔄 从白名单移除",
                      command=self.remove_selected_from_whitelist)
        m.add_separator()
        m.add_command(label="🧹 清空全部白名单…",
                      command=self.clear_all_whitelist)
        m.post(event.x_root, event.y_root)

    @staticmethod
    def _norm_cmp_url(u):
        if not u:
            return ""
        u = str(u).strip().split('?')[0].split('#')[0].rstrip('/')
        u = re.sub(r'\.html?$', '', u, flags=re.IGNORECASE)
        return u.lower()

    def _dedupe_baidu_against_others(self):
        """百度搜索里指向知道/贴吧的链接：已存在则去重，不存在则归类过去。
        返回 (删除数, 归类数)"""
        zhidao_set = set()
        tieba_set = set()
        for url, row in list(self._rows.items()):
            if row['source'] == 'zhidao':
                zhidao_set.add(self._norm_cmp_url(url))
            elif row['source'] == 'tieba':
                tieba_set.add(self._norm_cmp_url(url))

        to_remove = []
        reclassified = 0
        for url, row in list(self._rows.items()):
            if row['source'] not in ('baidu', 'baidu_mobile'):
                continue
            low = (url or '').lower()
            n = self._norm_cmp_url(url)
            target = None
            if 'zhidao.baidu.com/question/' in low:
                if n in zhidao_set:
                    to_remove.append(url)
                else:
                    target = 'zhidao'
            elif 'tieba.baidu.com/p/' in low:
                if n in tieba_set:
                    to_remove.append(url)
                else:
                    target = 'tieba'
            if target:
                row['source'] = target
                row['is_zhinengti'] = False
                row['author'] = '百度知道' if target == 'zhidao' else '百度贴吧'
                if target == 'zhidao':
                    self.seen_zhidao.add(url)
                    zhidao_set.add(n)
                else:
                    self.seen_tieba.add(url)
                    tieba_set.add(n)
                reclassified += 1

        for u in to_remove:
            self._rows.pop(u, None)

        return len(to_remove), reclassified

    def _open_single_content(self, tree):
        sel = tree.selection()
        if not sel:
            return
        _, cu = self.get_row_urls(tree, sel[0])
        if cu:
            webbrowser.open(cu)
            self.log(f"🔗 已打开: {cu}")

    def _copy_single(self, tree, which):
        sel = tree.selection()
        if not sel:
            return
        url, cu = self.get_row_urls(tree, sel[0])
        val = url if which == 'src' else cu
        if val:
            self.root.clipboard_clear()
            self.root.clipboard_append(val)
            self.log(f"📋 已复制: {val}")

    def _copy_urls(self, urls):
        urls = [u for u in (urls or []) if u]
        if not urls:
            return
        text = "\n".join(urls)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log(f"📋 已复制 {len(urls)} 个链接到剪贴板")

    def _extract_search_keyword(self, row_data):
        """从搜索结果中提取搜索关键词"""
        # 如果有标题，尝试从标题中提取可能的搜索关键词
        title = row_data.get('title', '')
        if title:
            # 简单处理：取标题的前几个字作为可能的搜索关键词
            # 实际应用中可能需要更复杂的逻辑
            return title[:20]  # 取前20个字符
        
        # 如果没有标题，返回空字符串
        return ""

    def _copy_redirects(self, redirects):
        """复制重定向URL"""
        if redirects:
            # 将多个重定向URL用回车连接
            redirects_text = '\n'.join(redirects)
            self.root.clipboard_clear()
            self.root.clipboard_append(redirects_text)
            self.log(f"📋 已复制重定向URL: {len(redirects)} 个")

    def _check_redirects_summary(self):
        """检查是否有重定向链接并提示"""
        redirect_count = 0
        redirect_urls = []
        
        for url, row in self._rows.items():
            if row.get('redirects'):
                redirect_count += 1
                redirect_urls.extend(row.get('redirects', []))
        
        if redirect_count > 0:
            # 标红底提示有重定向链接
            self.log(f"⚠️ 发现 {redirect_count} 个结果包含重定向链接（标红显示）")
            if redirect_urls:
                # 记录前几个重定向URL作为示例
                sample_urls = redirect_urls[:3]
                sample_text = ', '.join(sample_urls)
                self.log(f"   示例: {sample_text}")
                if len(redirect_urls) > 3:
                    self.log(f"   共 {len(redirect_urls)} 个重定向URL")

    # ==================== 白名单 ====================
    def add_selected_to_whitelist(self):
        tree = self.current_tree()
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中要加入白名单的行！")
            return
        added = 0
        for item in sel:
            row = self._rows.get(item)
            if not row:
                continue
            if row['is_white']:
                continue
            row['is_white'] = True
            candidates = []
            for v in (row.get('url'), row.get('content_url'), row.get('long_url')):
                if v and v not in candidates:
                    candidates.append(v)
            for candidate in candidates:
                self.whitelist_common.add(candidate)
            added += 1
        self.save_whitelist()
        self.log(f"🔒 已将 {added} 条加入白名单")
        self.refresh_all_trees()

    def remove_selected_from_whitelist(self):
        tree = self.current_tree()
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中要从白名单移除的行！")
            return
        removed = 0
        for item in sel:
            row = self._rows.get(item)
            if not row:
                continue
            if not row['is_white']:
                continue
            row['is_white'] = False
            candidates = []
            for v in (row.get('url'), row.get('content_url'), row.get('long_url')):
                if v and v not in candidates:
                    candidates.append(v)
            for candidate in candidates:
                self.whitelist_common.discard(candidate)
            removed += 1
        self.save_whitelist()
        self.log(f"🔄 已将 {removed} 条从白名单移除")
        self.refresh_all_trees()

    def clear_all_whitelist(self):
        total = len(self.whitelist_common)
        if total == 0:
            messagebox.showinfo("提示", "白名单已经是空的。")
            return
        if not messagebox.askyesno("⚠️ 确认清空",
                f"即将清空全部白名单（{total} 条），清空后这些链接会恢复为未加白状态。\n\n"
                "确定继续？"):
            return
        self.whitelist_common.clear()
        self.save_whitelist()
        for row in self._rows.values():
            row['is_white'] = False
        self.log(f"🧹 已清空全部白名单（共 {total} 条）")
        self.refresh_all_trees()

    # ==================== 树刷新 ====================
    def insert_result(self, source, title=None, date=None, summary=None, author="",
                      url="", content_url="", is_new=False, is_white=False,
                      is_zhinengti=False, long_url=""):
        if isinstance(source, dict):
            row = source
            url = row.get('url') or url
            if not url:
                return
        else:
            row = {
                'source': source,
                'title': title or '',
                'date': date or '未知',
                'summary': summary or '（无摘要，双击打开查看）',
                'author': author or '未知来源',
                'url': url or '',
                'content_url': content_url or '',
                'is_new': bool(is_new),
                'is_white': bool(is_white),
                'is_zhinengti': bool(is_zhinengti),
                'checked': False,
                'long_url': long_url or content_url or '',
            }

        url = row.get('url') or ""
        if not url:
            return

        # ★ 白名单词：标题/摘要/url 命中就自动加白
        auto_word = self._match_whitelist_word(row)
        if auto_word:
            added_any = False
            for v in (row.get('url'), row.get('content_url'), row.get('long_url')):
                if v and v not in self.whitelist_common:
                    self.whitelist_common.add(v)
                    added_any = True
            row['is_white'] = True
            if added_any:
                self._auto_whitelist_dirty = True
                self.log(f"🏷️ 命中白名单词「{auto_word}」，已自动加白: {url[:80]}")

        eff_source = row.get('source', 'unknown')
        # ★ 按「显示组」去重：baidu 和 baidu_mobile 归到同一组，同 URL 合并
        group_for_key = self._display_group_for(eff_source)
        key = self._row_key(group_for_key, url)

        # ① 已经存在同一个 key：只更新字段，不新增
        if key in self._rows:
            current = self._rows[key]
            current.update({
                'source': eff_source,
                'title': row.get('title', current.get('title', '')),
                'date': row.get('date', current.get('date', '未知')),
                'summary': row.get('summary', current.get('summary', '（无摘要，双击打开查看）')),
                'author': row.get('author', current.get('author', '未知来源')),
                'content_url': row.get('content_url', current.get('content_url', '')),
                'long_url': row.get('long_url', current.get('long_url', row.get('content_url', ''))),
                'is_zhinengti': bool(row.get('is_zhinengti', current.get('is_zhinengti', False))),
                'is_white': bool(row.get('is_white', current.get('is_white', False))),
            })
            if not self._refresh_pending:
                self._refresh_pending = True
                self.root.after(200, self._do_pending_refresh)
            return

        # ② 摘要+作者重复（同一显示组内）也算重复
        sa_key = (group_for_key, row.get('summary', ''), row.get('author', ''))
        if sa_key in self._sum_author_seen:
            return
        self._sum_author_seen.add(sa_key)

        # ③ 新条目：只存一次，key 固定用 "显示组|url"
        self._rows[key] = {
            'source': eff_source,
            'title': row.get('title', ''),
            'date': row.get('date', '未知'),
            'summary': row.get('summary', '（无摘要，双击打开查看）'),
            'author': row.get('author', '未知来源'),
            'url': url,
            'content_url': row.get('content_url', ''),
            'is_new': bool(row.get('is_new', False)),
            'is_white': bool(row.get('is_white', False)),
            'is_zhinengti': bool(row.get('is_zhinengti', False)),
            'checked': False,
            'long_url': row.get('long_url') or row.get('content_url') or '',
        }
        if not self._refresh_pending:
            self._refresh_pending = True
            self.root.after(200, self._do_pending_refresh)
        return

        # ==================== 白名单词 ====================
    def _match_whitelist_word(self, row):
        """标题 / 摘要 / url 任一包含白名单词 → 返回命中的那个词，否则 None"""
        words = getattr(self, "whitelist_words", None)
        if not words:
            return None
        hay = " ".join(str(x or "") for x in (
            row.get('title', ''),
            row.get('summary', ''),
            row.get('url', ''),
            row.get('content_url', ''),
            row.get('long_url', ''),
        )).lower()
        for w in words:
            if w and str(w).lower() in hay:
                return w
        return None

    def _save_whitelist_words(self):
        try:
            save_json(os.path.join(BASE_DIR, "whitelist_words.json"),
                      sorted(self.whitelist_words))
        except Exception:
            pass

    def _apply_whitelist_words_to_rows(self):
        """把白名单词立刻套用到当前已抓到的结果上（加白 + 刷新）"""
        if not self.whitelist_words or not self._rows:
            return 0
        changed = 0
        for row in self._rows.values():
            if row.get('is_white'):
                continue
            w = self._match_whitelist_word(row)
            if not w:
                continue
            row['is_white'] = True
            for v in (row.get('url'), row.get('content_url'), row.get('long_url')):
                if v:
                    self.whitelist_common.add(v)
            changed += 1
        if changed:
            self.save_whitelist()
            self.refresh_all_trees()
        return changed

    def open_whitelist_words_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("白名单词")
        win.geometry("560x640")
        win.configure(bg=self.COLOR_BG)
        win.transient(self.root)
        win.grab_set()
        fix_show_desktop_return(win, self.root)

        tk.Label(win, text="白名单词", bg=self.COLOR_BG, fg=self.COLOR_TEXT,
                 font=("Microsoft YaHei", 13, "bold")).pack(pady=(16, 2))
        tk.Label(win,
                 text="抓到的结果里，只要【标题 / 摘要 / url】包含下面任意一个词，"
                      "就自动加白（以后不再提示为新盗文）。",
                 bg=self.COLOR_BG, fg=self.COLOR_MUTED,
                 font=self.font_small, wraplength=500,
                 justify="left").pack(padx=20)

        outer = tk.Frame(win, bg=self.COLOR_BORDER)
        outer.pack(fill="both", expand=True, padx=20, pady=12)
        body = tk.Frame(outer, bg=self.COLOR_CARD)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        tf = tk.Frame(body, bg=self.COLOR_CARD)
        tf.pack(fill="both", expand=True, padx=14, pady=(12, 6))
        tree = ttk.Treeview(tf, columns=("word",), show="headings",
                            height=12, selectmode="extended")
        tree.heading("word", text="白名单词")
        tree.column("word", width=460, anchor="w")
        tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tf, orient="vertical", command=tree.yview)
        sb.pack(side="right", fill="y")
        tree.config(yscrollcommand=sb.set)

        def refresh_tree():
            for i in tree.get_children():
                tree.delete(i)
            for w in sorted(self.whitelist_words):
                tree.insert("", "end", values=(w,))

        def _delete_one(iid):
            """删掉单个词（双击/Delete 键用）"""
            w = tree.item(iid, "values")[0]
            self.whitelist_words.discard(w)
            self._save_whitelist_words()
            refresh_tree()
            self.log(f"🏷️ 已删除白名单词：{w}")

        def _on_double_click(event):
            iid = tree.identify_row(event.y)
            if iid:
                _delete_one(iid)

        def _on_delete_key(event):
            for iid in tree.selection():
                _delete_one(iid)

        tree.bind("<Double-1>", _on_double_click)
        tree.bind("<Delete>", _on_delete_key)
        tree.bind("<BackSpace>", _on_delete_key)

        row_frame = tk.Frame(body, bg=self.COLOR_CARD)
        row_frame.pack(fill="x", padx=14, pady=(0, 6))
        ent = tk.Entry(row_frame, font=self.font_normal,
                       bd=1, relief="solid", highlightthickness=0,
                       bg="#FAFBFC", fg=self.COLOR_TEXT,
                       insertbackground=self.COLOR_TEXT)
        ent.pack(side="left", fill="x", expand=True, ipady=4)

        def add():
            v = ent.get().strip()
            if not v:
                return
            added = 0
            for part in re.split(r'[,，、;；\s]+', v):
                part = part.strip()
                if part and part not in self.whitelist_words:
                    self.whitelist_words.add(part)
                    added += 1
            ent.delete(0, tk.END)
            if added:
                self._save_whitelist_words()
                refresh_tree()
                n = self._apply_whitelist_words_to_rows()
                self.log(f"🏷️ 已新增 {added} 个白名单词"
                         + (f"，当前结果里自动加白 {n} 条" if n else ""))

        ent.bind("<Return>", lambda e: add())
        self._make_btn(row_frame, "➕ 新增", add, bg=self.COLOR_PRIMARY,
                       width=8, small=True).pack(side="left", padx=(6, 0))

        btns = tk.Frame(body, bg=self.COLOR_CARD)
        btns.pack(fill="x", padx=14, pady=(4, 12))

        def del_sel():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("提示", "请先选中要删除的词。", parent=win)
                return
            for iid in sel:
                w = tree.item(iid, "values")[0]
                self.whitelist_words.discard(w)
            self._save_whitelist_words()
            refresh_tree()
            self.log("🏷️ 已删除选中的白名单词")

        def clear_all():
            if not self.whitelist_words:
                return
            if not messagebox.askyesno("确认", "清空全部白名单词？", parent=win):
                return
            self.whitelist_words.clear()
            self._save_whitelist_words()
            refresh_tree()
            self.log("🏷️ 已清空全部白名单词")

        self._make_btn(btns, "🗑️ 删除选中", del_sel, bg="#EF5350",
                       width=11, small=True).pack(side="left")
        self._make_btn(btns, "🧹 清空全部", clear_all, bg="#546E7A",
                       width=11, small=True).pack(side="left", padx=8)
        self._make_btn(btns, "关 闭", win.destroy, bg="#B0BEC5",
                       fg="#263238", width=8, small=True).pack(side="right")

        refresh_tree()
        ent.focus_set()

    def _do_pending_refresh(self):
        self._refresh_pending = False
        try:
            self.refresh_all_trees()
        except Exception as e:
            import traceback
            self.log(f"❌ refresh_all_trees 出错: {type(e).__name__}: {e}")
            self.log(traceback.format_exc())
        # ★ 白名单词命中后，统一在这里落盘（避免频繁写文件）
        if getattr(self, "_auto_whitelist_dirty", False):
            self._auto_whitelist_dirty = False
            try:
                self.save_whitelist()
            except Exception:
                pass

    # ==================== 搜索统计 ====================
    def _load_stats(self):
        """从 搜索统计.json 读历史统计；读不到就返回初始结构"""
        raw = load_json(self.stats_file, None)
        if not isinstance(raw, dict):
            raw = {}
        raw.setdefault("total_searches", 0)
        raw.setdefault("total_captchas", 0)
        raw.setdefault("sources", {})       # {src_id: {"searches": N, "captchas": N}}
        raw.setdefault("first_started", "")
        raw.setdefault("last_updated", "")
        if not raw["first_started"]:
            raw["first_started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return raw

    def _stats_add_search(self, source):
        if not source:
            return
        s = self.stats["sources"].setdefault(source, {"searches": 0, "captchas": 0})
        s["searches"] += 1
        self.stats["total_searches"] += 1

    def _stats_add_captcha(self, source):
        if not source:
            return
        s = self.stats["sources"].setdefault(source, {"searches": 0, "captchas": 0})
        s["captchas"] += 1
        self.stats["total_captchas"] += 1

    def _save_stats(self):
        """写 搜索统计.json（累计数据）和 搜索统计.txt（人看的表格）"""
        try:
            self.stats["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_json(self.stats_file, self.stats)
        except Exception:
            pass
        try:
            lines = []
            lines.append("=" * 60)
            lines.append("  打盗全家捅监控 · 搜索统计")
            lines.append("=" * 60)
            lines.append(f"首次运行：{self.stats.get('first_started', '')}")
            lines.append(f"最后更新：{self.stats.get('last_updated', '')}")
            lines.append(f"本次启动：{self.stats_session_start}")
            lines.append("")
            lines.append(f"累计搜索次数：{self.stats.get('total_searches', 0)}")
            lines.append(f"累计验证码/登录：{self.stats.get('total_captchas', 0)}")
            lines.append("")
            lines.append("-" * 60)
            lines.append(f"{'平台':<22}{'搜索次数':>12}{'验证码/登录':>16}{'验证码率':>12}")
            lines.append("-" * 60)
            for src, s in sorted(self.stats.get("sources", {}).items(),
                                 key=lambda kv: -kv[1].get("searches", 0)):
                name = self._source_display_name(src)
                sr = s.get('searches', 0)
                cp = s.get('captchas', 0)
                rate = f"{cp / sr * 100:.1f}%" if sr else "—"
                lines.append(f"{name:<22}{sr:>12}{cp:>16}{rate:>12}")
            lines.append("-" * 60)
            text = "\n".join(lines) + "\n"
            with open(self.stats_doc, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def _format_row_for_display(self, row):
        title_show = row.get('title', '')
        is_zn = bool(row.get('is_zhinengti'))
        tags = []
        if row.get('is_new'):
            tags.append('new_bold')
            title_show = '【新发现！】' + title_show      # ★ 新发现的排在标题最前面
        if row.get('is_white'):
            tags.append('whitelisted')
            title_show = '【已加白】' + title_show
        # 检查是否有重定向URL
        if row.get('redirects'):
            tags.append('redirected')
            title_show = '【重定向】' + title_show
        tags = tuple(tags)

        sel_mark = "☑" if row.get('checked') else "☐"
        src = row.get('source', 'unknown')
        if is_zn:
            source_label = "文心"
        else:
            group_key = self._display_group_for(src)
            source_label = SRC_LABEL.get(group_key, SRC_LABEL.get(src, src))

        date_show = row.get('date', '未知')
        if is_zn and row.get('author'):
            date_show = f"{row.get('date', '未知')}  {row.get('author')}"

        long_show = row.get('long_url') or row.get('content_url') or ''
        url_show = row.get('url') or ''
        if url_show and 'baidu.com/link?' in url_show:
            url_show = "打开超时"

        return {
            'sel_mark': sel_mark,
            'source_label': source_label,
            'title_show': title_show,
            'date_show': date_show,
            'summary_show': row.get('summary', '（无摘要，双击打开查看）'),
            'url_show': url_show,
            'long_show': long_show,
            'tags': tags,
            'is_zhinengti': is_zn,
            'source': src,
        }

    def refresh_all_trees(self):
        self._suppress_select_event = True
        try:
            trees_to_clear = ([self.tree_all] + [t["tree"] for t in self.engine_tabs.values()]
                              + ([self.tree_ai] if self.tree_ai is not None else []))
            for tree in trees_to_clear:
                for item in tree.get_children():
                    tree.delete(item)

            # ★ 用 (key, row) 对
            rows = list(self._rows.items())
            if self._filter_keyword:
                kw = self._filter_keyword
                rows = [(k, r) for k, r in rows if (
                    title_or_summary_matches(
                        kw,
                        r.get('title', ''),
                        r.get('summary', ''),
                        r.get('url', ''),
                        r.get('content_url', ''),
                        r.get('long_url', ''),
                    )
                )]
            rows.sort(key=lambda kr: (
                1 if kr[1].get('is_white') else 0,
                -date_sort_key(kr[1].get('date', '未知'))[0],
                -date_sort_key(kr[1].get('date', '未知'))[1],
                -date_sort_key(kr[1].get('date', '未知'))[2],
            ))

            for key, row in rows:
                display = self._format_row_for_display(row)
                self.tree_all.insert("", "end", iid=key, tags=display['tags'],
                                     values=(display['sel_mark'], display['source_label'],
                                             display['title_show'], display['date_show'],
                                             display['summary_show'], display['url_show'],
                                             display['long_show']))

                sub_values = (display['sel_mark'], display['title_show'], display['date_show'],
                              display['summary_show'], display['url_show'], display['long_show'])

                if display['is_zhinengti'] and self.tree_ai is not None:
                    self.tree_ai.insert("", "end", iid=key, tags=display['tags'], values=sub_values)
                else:
                    tab_info = self.engine_tabs.get(self._tab_group_for_source(display['source']))
                    if tab_info is not None:
                        tab_info['tree'].insert("", "end", iid=key, tags=display['tags'], values=sub_values)
                    elif self.engine_tabs:
                        first_tree = list(self.engine_tabs.values())[0]['tree']
                        first_tree.insert("", "end", iid=key, tags=display['tags'], values=sub_values)

            trees_to_check = ([self.tree_all] + [t["tree"] for t in self.engine_tabs.values()]
                              + ([self.tree_ai] if self.tree_ai is not None else []))
            for tree in trees_to_check:
                to_sel = []
                for item in tree.get_children():
                    if self._rows.get(item, {}).get('checked'):
                        to_sel.append(item)
                if to_sel:
                    tree.selection_add(to_sel)
        finally:
            self._suppress_select_event = False

    # ==================== 托盘 ====================
    def hide_to_tray(self):
        if not HAS_TRAY:
            messagebox.showwarning("提示", "未安装 pystray 或 pillow。")
            return
        self.root.withdraw()
        if not self.tray_visible:
            self._start_tray()
        self.tray_visible = True

    def _start_tray(self):
        def on_show(icon, item): self.root.after(0, self._restore_window)
        def on_search(icon, item):
            self.trigger_now = True
            self.root.after(0, lambda: self.log("⚡ 托盘菜单：立即搜一次"))
        def on_quit(icon, item):
            self.is_monitoring = False
            try:
                icon.stop()
            except Exception:
                pass
            self.root.after(0, self._real_quit)
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", on_show, default=True),
            pystray.MenuItem("立即搜一次", on_search),
            pystray.MenuItem("退出程序", on_quit),
        )
        self.tray_icon = pystray.Icon("monitor", self.icon_img, "盗文监控 - 后台运行中", menu)
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()
        self.log("📌 已隐藏到系统托盘")

    def _restore_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    def _real_quit(self):
        self.is_monitoring = False
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def on_close(self):
        if self.is_monitoring:
            self.hide_to_tray()
            try:
                self.windows_toast("盗文监控仍在后台运行",
                                   "程序已最小化到系统托盘。")
            except Exception:
                pass
        else:
            self._real_quit()

    # ==================== Windows 通知 ====================
    def windows_toast(self, title, msg):
        if HAS_WINOTIFY:
            try:
                icon_path = ensure_ico()
                kwargs = {"app_id": "盗文监控", "title": title, "msg": msg, "duration": "long"}
                if icon_path:
                    kwargs["icon"] = icon_path
                toast = Notification(**kwargs)
                try:
                    toast.set_audio(audio.Default, loop=False)
                except Exception:
                    pass
                toast.show()
                return True
            except Exception as e:
                self.log(f"winotify 弹窗失败: {e}")
        try:
            self.root.after(0, lambda: messagebox.showwarning(title, msg))
        except Exception:
            pass
        return False

    # ==================== 监控启动 ====================
    def _source_group_sources(self, group):
        """一个来源勾选框对应哪些引擎：第一个是常规模式搜的版本，其余只在深度模式搜"""
        versions = SOURCE_GROUPS.get(group, [group])
        if self.deep_mode:
            return list(versions)
        return versions[:1]

    def _collect_enabled_sources(self):
        """勾选的来源组 → 这轮真正要搜的来源列表（按界面顺序，去重）"""
        enabled = []
        for group in VISIBLE_SRC_ORDER:
            var = self.source_vars.get(group)
            if not (var and var.get()):
                continue
            for src_id in self._source_group_sources(group):
                if src_id not in enabled:
                    enabled.append(src_id)
        return enabled

    def _fetch_by_source(self, context, source, term, page_num, book_kw=''):
        engine_cls = ENGINE_MAP.get(source)
        if engine_cls is None:
            self.log(f"❌ 未找到引擎：{source}")
            return []
        # ★ 告诉 pause_for_user 当前是哪个来源
        self._current_source = source
        # ★ 统计：每调用一次算一次搜索
        self._stats_add_search(source)

        # ★ 前台模式下遇到验证码：用户处理完后，重试当前页
        MAX_PAUSE_RETRY = 5
        for _try in range(MAX_PAUSE_RETRY):
            self._pause_count = 0
            try:
                engine = engine_cls(self)
                result = engine.fetch(context, term, page_num, book_kw)
            except Exception as e:
                self.log(f"  [{source}] 引擎调用失败：{type(e).__name__}: {e}")
                return []

            # 本次 fetch 没触发验证码暂停 → 正常返回
            if self._pause_count == 0:
                return result

            # 后台模式：pause_for_user 只是记了日志，不重试
            if self.headless:
                return result

            # 前台模式：用户已经手动处理了（或者点了停止）
            if not self.is_monitoring:
                return result

            # 用户点了「是，继续」→ 重试当前页
            self.log(f"  🔁 验证码已处理，重试 [{self._source_display_name(source)}] "
                     f"第 {page_num+1} 页（第 {_try+1}/{MAX_PAUSE_RETRY} 次）")
            time.sleep(random.uniform(2, 4))
        self.log(f"  ⚠️ {self._source_display_name(source)} 第 {page_num+1} 页"
                 f"重试 {MAX_PAUSE_RETRY} 次仍未成功，放弃")
        return []

    def _is_source_skipped(self, source):
        """后台模式下，本轮该来源撞过验证码 → 后续所有词跳过它"""
        return source in self._skip_sources_this_round

    @staticmethod
    def _source_display_name(src_id):
        """通知和日志里显示的来源名：优先用引擎自己的名字，没登记的退回配置简称"""
        cls = MANUAL_SEARCH_ENGINE_MAP.get(src_id) or ENGINE_MAP.get(src_id)
        label = getattr(cls, 'SOURCE_LABEL', '') if cls else ''
        return label or SRC_LABEL.get(src_id, src_id)

    def _resolve_result_source(self, source, row):
        url = (row.get('url') or row.get('content_url') or row.get('long_url') or '').lower()
        if not url:
            return source
        if source not in ('zhidao', 'zhidao_mobile') and (
                'zhidao.baidu.com/' in url or 'zhidao.baidu.com/question/' in url):
            # 别的引擎搜到的知道问答页，归到知道组（知道手机版/PC 版本身保持各自来源，方便分开统计）
            return 'zhidao'
        if 'tieba.baidu.com/' in url or 'tieba.baidu.com/p/' in url:
            return 'tieba'
        if 'mp.weixin.qq.com/' in url or 'weixin.qq.com/' in url or 'weixin.sogou.com/' in url:
            return 'sogou_weixin'
        return source

    def _seen_map(self):
        """各来源的「已见过」集合（去重库）；以后加新引擎只要在这里加一行"""
        return {
            'zhidao': self.seen_zhidao,
            'zhidao_mobile': self.seen_zhidao_mobile,
            'tieba': self.seen_tieba,
            'baidu': self.seen_baidu,
            'baidu_mobile': self.seen_baidu_mobile,
            'bing': self.seen_bing,
            'so360_mobile': self.seen_so360_mobile,
            'so360_pc': self.seen_so360_pc,
            'toutiao_mobile': self.seen_toutiao_mobile,
            'toutiao_pc': self.seen_toutiao_pc,
            'sogou_mobile': self.seen_sogou_mobile,
            'sogou_pc': self.seen_sogou_pc,
            'sogou_weixin': self.seen_sogou_weixin,
            'quark': self.seen_quark,
            'quark_cn': self.seen_quark_cn,
            'weibo': self.seen_weibo,
            'weibo_mobile': self.seen_weibo_mobile,
        }

    def _mark_result_state(self, source, url, is_white):
        if not url:
            return False, False
        low = (url or '').lower()
        dedupe_source = source
        if 'mp.weixin.qq.com/' in low or 'weixin.qq.com/' in low or 'weixin.sogou.com/' in low:
            dedupe_source = 'sogou_weixin'
        elif source in ('baidu', 'baidu_mobile') and 'zhidao.baidu.com/' in low:
            dedupe_source = 'zhidao'
        elif source in ('baidu', 'baidu_mobile') and 'tieba.baidu.com/' in low:
            dedupe_source = 'tieba'

        seen = self._seen_map().get(dedupe_source)
        if seen is None:
            return False, False

        existed = url in seen
        if not existed:
            seen.add(url)
        return (not existed), (not existed and not is_white)

    def _build_tasks(self, bookmarks):
        tasks = []
        seen_terms = set()
        for bm in bookmarks:
            name = bm["name"]
            enabled = enabled_suffix_texts(bm.get("suffixes"))
            if not enabled:
                continue
            for text in enabled:
                text = (text or "").strip()
                term = f"{name}{text}".strip()
                if term in seen_terms:
                    continue
                seen_terms.add(term)
                tasks.append({"term": term, "kw": name, "suffix": text})
        return tasks

    def start_monitor_once(self):
        if self.is_monitoring:
            messagebox.showinfo("提示", "正在监控中，请先点『⛔ 停止』。")
            return
        kw = self.entry_keyword.get().strip()
        if not kw:
            messagebox.showwarning("提示", "请输入书名或点击下方书签！")
            return
        bookmarks = [{"name": kw, "suffixes": [{"text": "", "enabled": True}]}]
        self._start_monitor_with(bookmarks, once=True)

    def start_bookmark_monitor(self, book_id):
        if self.is_monitoring:
            messagebox.showinfo("提示", "正在监控中，请先点『⛔ 停止』。")
            return
        bm = self._find_bookmark_by_id(book_id)
        if not bm:
            return
        bookmarks = [{"name": bm["name"],
                      "suffixes": [dict(s) for s in bm["suffixes"]]}]
        self._start_monitor_with(bookmarks, once=True)

    def start_batch_monitor(self):
        if self.is_monitoring:
            return
        if not self.bookmarks:
            messagebox.showinfo("提示", "还没有书签。")
            return
        # ★ 只监控前面小方格勾上的书签
        picked = self._checked_bookmarks()
        if not picked:
            messagebox.showinfo("提示", "请先在书签前面的小方格里勾选要监控的书签。")
            return

        def _enabled_suffixes_only(sufs):
            """只显示已勾选的后缀，未勾选的不显示"""
            parts = []
            for s in sufs or []:
                if isinstance(s, dict):
                    if not s.get("enabled", True):
                        continue
                    text = str(s.get("text", "") or "").strip()
                else:
                    text = str(s or "").strip()
                parts.append(text if text else "无后缀")
            return "、".join(parts) if parts else "（无）"

        # ★ 预估来源数（把「深度模式」当成已生效，与真正开跑时一致）
        deep_now = bool(self.var_deep.get())
        enabled_srcs = []
        for group in VISIBLE_SRC_ORDER:
            var = self.source_vars.get(group)
            if not (var and var.get()):
                continue
            versions = SOURCE_GROUPS.get(group, [group])
            if not deep_now:
                versions = versions[:1]
            for src_id in versions:
                if src_id not in enabled_srcs:
                    enabled_srcs.append(src_id)
        source_count = len(enabled_srcs)

        # ★ 每个书签的启用搜索词数 + 只显示已选后缀
        total_terms = 0
        lines = []
        for b in picked:
            enabled_sufs = enabled_suffix_texts(b.get("suffixes"))
            n = len(enabled_sufs)
            total_terms += n
            lines.append(
                f"· {b['name']}（{n} 个搜索词：{_enabled_suffixes_only(b.get('suffixes'))}）"
            )

        # ★ 排列组合的总搜索次数 = 搜索词总数 × 来源数
        total_queries = total_terms * source_count

        msg = (
            f"即将同时监控 {len(picked)} 个书签（勾选的）：\n\n"
            + "\n".join(lines) + "\n\n"
            + f"📊 合计 {total_terms} 个搜索词 × {source_count} 个来源 = "
              f"约 {total_queries} 次搜索\n\n"
            + "是否继续？"
        )

        if not messagebox.askyesno("确认", msg):
            return

        self._start_monitor_with(
            [{"name": b["name"], "suffixes": [dict(s) for s in b["suffixes"]]}
             for b in picked],
            once=True)

    def _start_monitor_with(self, bookmarks, once=False):
        hours = 0   # 间隔已废弃：本工具改为一次性搜索，不再周期监控

        pages_raw = str(self.entry_pages.get()).strip()
        if pages_raw in ("", "自动", "auto", "Auto", "AUTO", "0"):
            pages_per_term = 0          # ★ 0 = 自动翻页（有结果就继续，0 匹配即停）
        else:
            try:
                pages_per_term = int(pages_raw)
                if pages_per_term < 1:
                    raise ValueError
            except Exception:
                messagebox.showwarning("提示", "搜索页数请填「自动」，或大于等于 1 的整数。")
                return

        self.deep_mode = bool(self.var_deep.get())
        enabled = self._collect_enabled_sources()
        if not enabled:
            messagebox.showwarning("提示", "请至少勾选一个搜索来源！")
            return

        tasks = self._build_tasks(bookmarks)
        if not tasks:
            messagebox.showwarning("提示", "没有任何搜索词被勾选，无法生成搜索关键词。")
            return

        total_terms = len(tasks) * len(enabled)
        self.headless = not bool(self.var_headless.get())

        if self.headless:
            self.log("后台模式：浏览器不会弹窗。大量 0 结果时建议切回有头登录一次。")
        src_names = "、".join(self._source_display_name(s) for s in enabled)
        self.log(f"🎯 本轮实际搜索来源（{len(enabled)} 个）：{src_names}"
                 + ("　※ 深度模式：pc+移动 双渠道一起搜" if self.deep_mode else ""))

        self.is_monitoring = True
        self._stop_done = False
        self.captcha_events = []
        self.captcha_session_events = []
        self.captcha_warned = False
        # ★ 0 结果搜索词快照（本轮跑起来后由 monitor_worker 持续更新）
        self._last_term_status = None
        self._last_enabled_sources = []
        for b in (self.btn_patrol, self.btn_batch):
            b.config(state="disabled", bg="#B0BEC5", activebackground="#B0BEC5", fg="#607D8B")
        self.btn_stop.config(state="normal", bg=self.COLOR_DANGER,
                             activebackground=self.COLOR_DANGER, fg="white")
        for chk in (self.chk_headless, self.chk_deep):
            try:
                chk.config(state="disabled")
            except Exception:
                pass

        mode = "搜索本书" if once else "批量搜索"
        pages_desc = ("自动翻页（有结果就继续翻，0 匹配即停）" if pages_per_term <= 0
                      else f"每词 {pages_per_term} 页")
        self.log(f"====== [{mode}] {len(bookmarks)} 个书名"
                 f"（{len(tasks)} 个搜索词 × {len(enabled)} 来源 = {total_terms} 次查询，"
                 f"{pages_desc}"
                 + "） ======")
        for b in bookmarks:
            self.log(f"   📖 {b['name']} → {self._enabled_suffixes_display(b.get('suffixes'))}")

        threading.Thread(target=self.monitor_worker,
                         args=(bookmarks, hours, enabled, once, pages_per_term),
                         daemon=True).start()

    def stop_monitor(self):
        # 按「⛔停止」/「巡逻结束」/「出错」都可能触发，只让第一次真正执行，避免日志重复
        if getattr(self, "_stop_done", False):
            return
        self._stop_done = True
        self.is_monitoring = False

        # 停止时：汇报本次运行全程的验证码/登录情况
        self._report_captcha_session()
        # ★ 停止时：落盘搜索统计
        try:
            self._save_stats()
        except Exception:
            pass

        # ★ 停止时：汇报 0 结果的搜索词（低相关搜索词）
        try:
            self._report_zero_result_terms(
                getattr(self, '_last_term_status', None),
                getattr(self, '_last_enabled_sources', None) or [],
                phase="停止时")
            self._last_term_status = None
            self._last_enabled_sources = []
        except Exception:
            pass

        try:
            dup_removed, reclassified = self._dedupe_baidu_against_others()
            if dup_removed:
                self.log(f"🧹 百度搜索与知道/贴吧重合，已移除 {dup_removed} 条")
            if reclassified:
                self.log(f"📂 百度搜索的知道/贴吧链接已归类 {reclassified} 条")
            if dup_removed or reclassified:
                self._do_pending_refresh()
        except Exception:
            pass

        # 检查是否有重定向链接
        self._check_redirects_summary()

        self.btn_patrol.config(state="normal", bg=self.COLOR_WARNING,
                               activebackground=self.COLOR_WARNING, fg="white")
        self.btn_batch.config(state="normal", bg=self.COLOR_INFO,
                              activebackground=self.COLOR_INFO, fg="white")
        self.btn_stop.config(state="disabled", bg="#B0BEC5", activebackground="#B0BEC5", fg="#607D8B")
        for chk in (self.chk_headless, self.chk_deep):
            try:
                chk.config(state="normal")
            except Exception:
                pass
        self.status_var.set("已停止")
        if getattr(self, 'last_round_elapsed', 0):
            self.log(f"⏱️ 停止时本轮用时 {fmt_hms(self.last_round_elapsed)}")
        self.log("⛔ 监控已停止")

    def resume_from_user(self):
        """（弹窗版）直接继续，不再检查按钮状态"""
        self.log("✅ 用户已处理，继续监控")
        self.root.after(0, lambda: self.status_var.set("正在抓取..."))

    def pause_for_user(self, reason=""):
        reason = (reason or '验证码/登录').strip()
        # ★ 通知 _fetch_by_source：本次 fetch 触发了暂停，需要重试
        self._pause_count = getattr(self, "_pause_count", 0) + 1
        # 记录验证码事件（轮末复述 + 停止时汇总）
        self.captcha_events.append(reason)
        self.captcha_session_events.append(reason)
        # ★ 统计：按当前来源记一次验证码/登录
        self._stats_add_captcha(self._current_source)
        if self.headless:
            src = self._current_source or ""
            if src and src not in self._skip_sources_this_round:
                self._skip_sources_this_round.add(src)
                self.log(f"⚠️ [后台] 「{self._source_display_name(src)}」遇到验证码/登录（{reason}），"
                         f"本轮剩余搜索词将跳过该来源。若持续失败，请切回前台模式登录一次。")
            else:
                self.log(f"⚠️ [后台] 遇到验证码/登录（{reason}），跳过当前页面。")
            return
        if not self.captcha_warned:
            self.captcha_warned = True
            self.log_alert("⚠️ 前台模式：浏览器里出现验证码时，请在页面上完成验证。")
        self.log(f"⏸️ 暂停，等待用户处理：{reason}（浏览器页面已保留）")
        self.root.after(0, lambda: self.status_var.set(
            "⚠️ 请在浏览器处理验证码/登录，完成后点击「确定」"))
        try:
            self.windows_toast("⚠️ 需要你手动处理",
                               "浏览器弹出了验证码或登录页，请在上面完成验证。")
        except Exception:
            pass
        # ★ 主线程弹窗等待用户确认（不阻塞 worker 线程的事件处理）
        import threading as _th
        _dlg_event = _th.Event()
        _user_wants_continue = [True]

        def _show_captcha_dialog():
            _user_wants_continue[0] = messagebox.askyesno(
                "验证码/登录提示",
                f"浏览器弹出了验证码或登录页：{reason}\n\n"
                "请在浏览器页面上完成验证/登录操作，\n"
                "完成后点「是」继续监控，点「否」停止。")
            _dlg_event.set()

        self.root.after(0, _show_captcha_dialog)
        _dlg_event.wait()  # 等待用户关闭弹窗

        if _user_wants_continue[0]:
            self.resume_from_user()
        else:
            self.stop_monitor()

    # ==================== 抓取核心 ====================
    def _max_pages_for_source(self, source, pages_per_term):
        """某个来源这一轮要翻多少页。

        pages_per_term = 0 → 「自动」模式：一直往后翻，直到某页 0 条匹配就停
                             （最多 AUTO_MAX_PAGES 页兜底，防止无限翻页）
        百度知道移动版：每页结果太少（通常 2~3 条），用户设了页数时自动翻倍；
                        百度知道 PC 版不翻倍。
        """
        # ★ 夸克是滚动加载、每页结果一样；盗版会置顶且只放 3~5 个，只搜第 1 页
        if source in ('quark', 'quark_cn'):
            return 1
        try:
            pages = int(pages_per_term)
        except Exception:
            pages = 0
        if pages <= 0:
            return self.AUTO_MAX_PAGES          # ★ 自动翻页
        base = max(pages, self.MIN_PAGES_BY_SOURCE.get(source, 1))
        if source == 'zhidao_mobile':
            return base * 2                     # ★ 只有百度知道移动版翻倍
        return base

    def _report_zero_result_terms(self, term_status, enabled_sources, phase="本轮结束"):
        """汇报 0 结果的搜索词（低相关搜索词）。

        日志长这样：
            📋 本轮结束：2 个搜索词 0 结果（低相关搜索词，建议调整搜索词/后缀或换来源）：
                · 书名+百度网盘，在百度知道共有0个结果
                · 书名+网盘，在夸克共有0个结果
        """
        if not term_status:
            return
        enabled_sources = list(enabled_sources or [])
        if not enabled_sources:
            return
        zero_items = []          # [(搜索词, 来源显示名), ...]
        for _term, _src_stat in term_status.items():
            for _src in enabled_sources:
                if _src not in _src_stat:
                    continue                 # 这个词还没搜到这个来源，不算 0 结果
                _hits = _src_stat[_src][0]
                if _hits <= 0:
                    zero_items.append((_term, self._source_display_name(_src)))
        if not zero_items:
            return
        self.log(f"📋 {phase}：{len(zero_items)} 个组合 0 结果"
                 f"（低相关搜索词，建议调整搜索词/后缀或换来源）：")
        for _term, _src_name in zero_items:
            self.log(f"    · {_term}，在{_src_name}共有0个结果")

    def monitor_worker(self, bookmarks, interval_hours, enabled_sources, once=False, pages_per_term=3):
        base_terms = []
        for bm in bookmarks:
            name = bm["name"]
            enabled_sufs = enabled_suffix_texts(bm.get("suffixes"))
            if not enabled_sufs:
                continue
            for text in enabled_sufs:
                text = (text or "").strip()
                term = f"{name}{text}".strip()
                for src in enabled_sources:
                    base_terms.append({"term": term, "source": src, "kw": name})

        if not base_terms:
            self.log("⚠️ 没有生成任何搜索词，请检查搜索词和监控来源。")
            self.root.after(0, self.stop_monitor)
            return

        MAX_RETRY = 3
        PAGES_PER_TERM = pages_per_term
        headless_now = self.headless

        try:
            with sync_playwright() as p:
                baidu_data_dir = os.path.join(BASE_DIR, "baidu_engine_data")
                os.makedirs(baidu_data_dir, exist_ok=True)

                if headless_now:
                    launch_args = [
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-software-rasterizer",
                        "--disable-extensions",
                        "--window-size=1920,1080",
                    ]
                else:
                    launch_args = ["--disable-blink-features=AutomationControlled"]

                baidu_context = p.chromium.launch_persistent_context(
                    user_data_dir=baidu_data_dir,
                    headless=headless_now,
                    channel="msedge",
                    args=launch_args,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                )
                self.browser_context = baidu_context
                try:
                    _first = baidu_context.pages[0] if baidu_context.pages else baidu_context.new_page()
                    _first.goto("https://www.baidu.com/", timeout=20000)
                except Exception:
                    pass

                if headless_now:
                    self.log("后台模式已启动；如遇验证码，请切回前台模式登录一次。")
                else:
                    self.log("前台模式：遇到验证码/登录时会自动暂停并提醒处理")

                while self.is_monitoring:
                    round_start = datetime.now()
                    # ★ 每轮开始：清空上一轮被跳过的来源（新一轮重新尝试）
                    self._skip_sources_this_round.clear()
                    self.log(f"▶️ 开始本轮抓取 ({round_start.strftime('%H:%M:%S')})")
                    self.root.after(0, lambda: self.status_var.set(
                        f"正在抓取... 开始于 {round_start.strftime('%H:%M:%S')}"))

                    pending = list(base_terms)
                    random.shuffle(pending)

                    term_status = {}

                    # 本轮新增结果：{来源id: [命中链接, ...]}，以后加新引擎这里不用改
                    new_by_source = {}
                    total_hits = 0
                    term_index = 0

                    while pending and self.is_monitoring:
                        # ★ 撞过验证码的来源：把剩下所有词一次性剔掉，不再逐个等换词间隔
                        if self.headless and self._skip_sources_this_round:
                            before = len(pending)
                            pending = [t for t in pending
                                       if t['source'] not in self._skip_sources_this_round]
                            removed = before - len(pending)
                            if removed:
                                names = "、".join(self._source_display_name(s)
                                                  for s in self._skip_sources_this_round)
                                self.log(f"⏭️ 本轮「{names}」已撞验证码，"
                                         f"剩余 {removed} 个词一次性全部跳过，不再逐个等待")

                        if not pending:
                            break

                        task = pending.pop(0)
                        term = task['term']
                        source = task['source']
                        book_kw = task.get('kw', '')
                        task_key = term + '|' + source

                        # 双重保险（极少走到）：pop 出来后如果是被跳过来源，直接丢
                        if self.headless and self._is_source_skipped(source):
                            continue

                        term_index += 1

                        if term_index > 1:
                            gap = random.uniform(8, 15)
                            self.log(f"⏸️ 换词，等待 {gap:.0f} 秒...")
                            for _ in range(int(gap)):
                                if not self.is_monitoring:
                                    break
                                if self.trigger_now:
                                    break
                                time.sleep(1)
                            if not self.is_monitoring:
                                break

                        src_label = SRC_LABEL.get(source, source)
                        self.log(f"🔎 [{term_index}][{src_label}] {term}  （剩 {len(pending)}）")                        
                        term_hits = 0
                        term_raw_hits = 0

                        try:
                            max_pages = self._max_pages_for_source(source, PAGES_PER_TERM)
                            if (source == 'zhidao_mobile' and PAGES_PER_TERM > 0
                                    and max_pages > PAGES_PER_TERM):
                                self.log(f"📈 百度知道移动版结果较少，页数已自动翻倍至 {max_pages} 页"
                                         f"（原 {PAGES_PER_TERM} 页）")
                            for page_num in range(0, max_pages):
                                # ★ 用户点了停止就立刻收手，不再抓本词剩余页
                                if not self.is_monitoring:
                                    self.log("⛔ 已停止，中断本词剩余页面")
                                    break
                                items = self._fetch_by_source(baidu_context, source, term, page_num, book_kw)
                                if not isinstance(items, list):
                                    items = []

                                raw_cnt = self._last_fetch_raw_count
                                self.log(f"  [第{page_num+1}页] 命中 {len(items)} 条（原始 {raw_cnt} 条）")
                                total_hits += len(items)
                                term_hits += len(items)
                                term_raw_hits += raw_cnt

                                if raw_cnt == 0:
                                    self.log(f"  ⏭️ 第{page_num+1}页 0 条，停止该词后续页并列入排队")
                                    break

                                page_matched = 0

                                for item in items:
                                    if not self.is_monitoring:
                                        break
                                    if not isinstance(item, dict):
                                        continue

                                    row = dict(item)
                                    row.setdefault('source', source)
                                    row.setdefault('title', '')
                                    row.setdefault('summary', '（无摘要，双击打开查看）')
                                    row.setdefault('date', '未知')
                                    row.setdefault('author', SRC_LABEL.get(source, source))
                                    row.setdefault('url', '')
                                    row.setdefault('content_url', row.get('url', ''))
                                    row.setdefault('long_url', row.get('content_url') or row.get('url', ''))
                                    row.setdefault('is_zhinengti', False)

                                    eff_source = self._resolve_result_source(source, row)
                                    row['source'] = eff_source

                                    blocked_url = (row.get('url') or row.get('content_url') or row.get('long_url') or '').lower()
                                    is_allowed_page = any(blocked_url.startswith(p) for p in ALLOWED_URL_PREFIXES)
                                    if (not is_allowed_page) and (any(d in blocked_url for d in BLOCKED_DOMAINS) or any(tok in blocked_url for tok in BLOCKED_URL_TOKENS)):
                                        self.log(f"    🚫 屏蔽搜索引擎/百科/AI/广告点击链接，丢弃: {blocked_url[:80]}")
                                        continue

                                    title_text = row.get('title', '')
                                    summary_text = row.get('summary', '')
                                    match = title_or_summary_matches(book_kw, title_text, summary_text)
                                    if not match:
                                        self.log(f"    ✗ 标题和摘要都不含书名，丢弃: {str(title_text)[:40]}")
                                        continue

                                    # ★ 临时诊断：把"书名匹配但可能静默"的条目打出来
                                    _tmp_white = any(v and v in self.whitelist_common
                                                     for v in (row.get('url'), row.get('content_url'), row.get('long_url')))
                                    _seen = self._seen_map().get(eff_source) or set()
                                    _tmp_seen = any(v and v in _seen
                                                    for v in (row.get('url'), row.get('content_url'), row.get('long_url')))
                                    self.log(f"    ⚠️ 匹配：white={_tmp_white} seen={_tmp_seen} | {str(title_text)[:50]} | {str(row.get('url'))[:80]}")

                                    page_matched += 1
                                    clean_url = row.get('url') or row.get('content_url') or row.get('long_url') or f"{eff_source}:{row.get('title', '')}"
                                    content_url = row.get('content_url') or row.get('url') or clean_url
                                    long_url = row.get('long_url') or row.get('content_url') or row.get('url') or clean_url
                                    if 'ai.so.com' in (long_url or '').lower():
                                        self.log(f"    🚫 屏蔽 AI 主页，丢弃: {long_url[:80]}")
                                        continue

                                    match_candidates = []
                                    for key in (row.get('url'), row.get('content_url'), row.get('long_url')):
                                        if key and key not in match_candidates:
                                            match_candidates.append(key)
                                    match_key = next((k for k in match_candidates if k in self.whitelist_common), clean_url)
                                    is_white = any(k in self.whitelist_common for k in match_candidates) or clean_url in self.whitelist_common
                                    is_new, should_notify = self._mark_result_state(eff_source, match_key, is_white)

                                    if is_new and not is_white:
                                        # ★ 记进「未加白链接.txt」（最终链接；书名 = 当前搜的书名）
                                        _final = next(
                                            (str(v) for v in (row.get('url'), row.get('content_url'),
                                                              row.get('long_url'))
                                             if str(v or '').lower().startswith(('http://', 'https://'))),
                                            '')
                                        unwhite_log.record(_final, book_kw, eff_source)

                                    if should_notify:
                                        new_by_source.setdefault(eff_source, []).append(match_key)

                                    self.root.after(0, lambda r=dict(row), s=eff_source, n=is_new, w=is_white:
                                                    self.insert_result({
                                                        'source': s,
                                                        'title': r.get('title', ''),
                                                        'date': r.get('date', '未知'),
                                                        'summary': r.get('summary', '（无摘要，双击打开查看）'),
                                                        'author': r.get('author', SRC_LABEL.get(s, s)),
                                                        'url': r.get('url') or r.get('content_url') or r.get('long_url') or '',
                                                        'content_url': r.get('content_url') or r.get('url') or '',
                                                        'long_url': r.get('long_url') or r.get('content_url') or r.get('url') or '',
                                                        'is_new': n,
                                                        'is_white': w,
                                                        'is_zhinengti': bool(r.get('is_zhinengti', False)),
                                                    }))

                                if page_matched == 0:
                                    if source in self.NO_BREAK_ON_MISMATCH_SOURCES:
                                        self.log(f"  ⏭️ 第{page_num+1}页有 {raw_cnt} 条书名全不匹配，"
                                                 f"{SRC_LABEL.get(source, source)} 例外，继续翻下一页")
                                    else:
                                        self.log(f"  ⏭️ 第{page_num+1}页有 {raw_cnt} 条但书名全不匹配，"
                                                 f"停止该词后续页并列入排队")
                                        break

                                # 可中断的翻页间隔（点停止时立即退出）
                                gap_end = time.time() + random.uniform(1.5, 3)
                                while time.time() < gap_end:
                                    if not self.is_monitoring:
                                        break
                                    time.sleep(0.2)
                                if not self.is_monitoring:
                                    break
                        except Exception as e:
                            self.log(f"  [抓取错误] {type(e).__name__}: {e}")

                        if not self.is_monitoring:
                            break

                        term_status.setdefault(term, {})[source] = (term_hits, term_raw_hits)
                        # ★ 实时留一份快照：用户中途点「停止」也能汇报 0 结果的搜索词
                        self._last_term_status = {k: dict(v) for k, v in term_status.items()}
                        self._last_enabled_sources = list(enabled_sources)

                        if term_hits > 0:
                            self.log(f"  ✅ 该词有结果（{term_hits} 条）")
                        elif term_raw_hits > 0:
                            self.log(f"  ⏭️ 该词页面有 {term_raw_hits} 条结果但全部不匹配书名")
                        else:
                            self.log("  ⏭️ 该词 0 结果")

                    dup_removed, reclassified = self._dedupe_baidu_against_others()
                    if dup_removed:
                        self.log(f"🧹 百度搜索与知道/贴吧重合，已移除 {dup_removed} 条")
                    if reclassified:
                        self.log(f"📂 百度搜索的知道/贴吧链接已归类 {reclassified} 条")

                    self.save_seen()
                    self.save_whitelist()
                    self.root.after(0, self._do_pending_refresh)

                    # 按来源汇总本轮新增（顺序固定，新来源自动排在后面）
                    new_src_list = [s for s in SRC_ORDER if s in new_by_source]
                    new_src_list += [s for s in new_by_source if s not in new_src_list]
                    total_new = sum(len(new_by_source[s]) for s in new_src_list)
                    if total_new:
                        lines = [f"· {self._source_display_name(s)} {len(new_by_source[s])} 条"
                                 for s in new_src_list]

                        if self.tray_visible:
                            msg = (f"发现 {total_new} 条新的盗文线索！\n\n" +
                                   "\n".join(lines) +
                                   "\n\n双击托盘图标查看详情。")
                            self.windows_toast("⚠️ 发现新增盗文线索", msg)
                        else:
                            try:
                                self.root.after(0, lambda: self.root.bell())
                            except Exception:
                                pass

                            _parts = [f"{self._source_display_name(s)} {len(new_by_source[s])}"
                                      for s in new_src_list]
                            self.log(f"⚠️ 本轮新增：{'，'.join(_parts)}（共 {total_new} 条）")
                    else:
                        self.log(f"✅ 本轮结束：无新增（共检查 {total_hits} 条）")

                    # 本轮结束：把验证码情况标红复述一次
                    self._flush_captcha_reminder()

                    # ★ 本轮结束：汇报 0 结果的搜索词（低相关搜索词）
                    if self.is_monitoring:
                        self._report_zero_result_terms(term_status, enabled_sources, phase="本轮结束")
                        self._last_term_status = None
                        self._last_enabled_sources = []

                    elapsed = (datetime.now() - round_start).total_seconds()
                    self.last_round_elapsed = elapsed
                    self.log(f"⏱️ 本轮用时 {fmt_hms(elapsed)}")
                    # ★ 每轮结束：把统计写进 搜索统计.json / 搜索统计.txt
                    try:
                        self._save_stats()
                    except Exception:
                        pass

                    if once:
                        self.log("🔍 巡逻完成，自动停止")
                        self.root.after(0, self.stop_monitor)
                        break

                    if not self.is_monitoring:
                        break

                # ★ 优雅关闭：先关所有 page，等它消化，再关 context
                try:
                    # 1. 主动关掉所有 page（让浏览器知道"该撤了"）
                    try:
                        for _pg in baidu_context.pages:
                            try:
                                _pg.close()
                            except Exception:
                                pass
                    except Exception:
                        pass

                    time.sleep(0.5)

                    # 2. 关 context
                    baidu_context.close()

                    time.sleep(0.3)
                except Exception as _e:
                    self.log(f"关闭浏览器失败: {type(_e).__name__}: {_e}")
        except Exception as e:
            self.log(f"[致命错误] {type(e).__name__}: {e}")
            self.root.after(0, self.stop_monitor)


if __name__ == "__main__":
    # ★ 单开保护：程序已经在跑时，再双击一次只会把原来的窗口叫到前面，不会再开一个
    guard = SingleInstance("打盗全家捅监控")
    if not guard.acquire():
        if not guard.wake_existing():
            try:
                tk.Tk().withdraw()
                messagebox.showinfo("打盗全家捅监控",
                                    "程序已经在运行了（可能缩在右下角托盘里）。\n"
                                    "请在任务栏/托盘点一下原来的窗口，不要重复打开。")
            except Exception:
                pass
        sys.exit(0)

    root = tk.Tk()
    app = MonitorApp(root)
    try:
        guard.on_wake(lambda: root.after(0, app._restore_window))
    except Exception:
        pass
    root.mainloop()