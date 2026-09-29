# -*- coding: utf-8 -*-
"""窗口小助手：按屏幕右下角「显示桌面」把窗口全藏起来后，还能从任务栏切回来。

为什么需要它：
「手动搜索」这类小窗口是模态窗（transient + 抢焦点/grab）。点「显示桌面」把所有
窗口藏起来之后，这种小窗口会连着 grab 一起躲在后台 —— 主窗口点不动、小窗口也回不来，
看起来就像程序卡死、切不回去。

这里做三件事：
1. 小窗口被藏起来（Unmap）时立刻松手（放掉 grab），免得主窗口点不动；
2. 主窗口重新显示 / 重新获得焦点（点任务栏切回来）时，把小窗口一起叫出来；
3. 定时兜底：有的系统藏窗口、切回来不发 Map / FocusIn 事件，每 0.7 秒查一次。
"""


def fix_show_desktop_return(win, root=None):
    """给小窗口（Toplevel）加「显示桌面后还能切回来」的修复。

    win  ：小窗口（Toplevel）
    root ：主窗口；不传就自动用 win 的父窗口
    """
    if root is None:
        root = getattr(win, "master", None)
    try:
        followed = bool(win.transient())      # 是不是挂在主窗口下面（子窗口）
    except Exception:
        followed = False

    def _root_visible():
        if root is None:
            return True
        try:
            if not root.winfo_exists():
                return False
            return root.state() == "normal"
        except Exception:
            return True

    def _is_our_child(w):
        """w 是不是我们下面开出来的窗口（子窗口/孙子窗口）"""
        try:
            m = getattr(w, "master", None)
            depth = 0
            while m is not None and depth < 10:
                if m is win:
                    return True
                m = getattr(m, "master", None)
                depth += 1
        except Exception:
            pass
        return False

    def _sync(_event=None):
        """重新显示小窗口（我们上面开的子窗口正在抓取时不要抢）"""
        try:
            if not win.winfo_exists():
                return
            if followed and not _root_visible():
                return          # 主窗口自己还藏着，小窗口也先跟着藏
            other = win.grab_current()
            if other is not None and other is not win and _is_our_child(other):
                return
            win.deiconify()
            win.lift()
            if win.grab_current() is None:
                win.grab_set()
        except Exception:
            pass

    def _on_hide(_event=None):
        """被藏起来时先松手，免得主窗口点不动（Tk 一般也会自动放）"""
        try:
            if win.winfo_exists() and win.grab_current() is win:
                win.grab_release()
        except Exception:
            pass

    def _sync_later(_event=None):
        # 等窗口管理器处理完「重新显示」再叫小窗口，免得被压回去
        try:
            win.after(80, _sync)
        except Exception:
            _sync()

    # 定时兜底：有些系统藏窗口/切回来不发 Map、FocusIn 事件，这里定期查一次
    def _tick():
        try:
            if not win.winfo_exists():
                return
            if win.state() == "withdrawn":
                _sync()
        except Exception:
            pass
        try:
            win.after(700, _tick)
        except Exception:
            pass

    try:
        win.bind("<Unmap>", _on_hide, add="+")
        win.bind("<Map>", _sync, add="+")
    except Exception:
        pass
    try:
        if root is not None and root is not win:
            root.bind("<Map>", _sync_later, add="+")
            root.bind("<FocusIn>", _sync_later, add="+")
    except Exception:
        pass
    try:
        win.after(700, _tick)
    except Exception:
        pass
    return win
