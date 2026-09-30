# -*- coding: utf-8 -*-
"""单开保护：程序（exe）已经在跑的时候，再双击一次不会开出第二个程序。

说人话的原理：
- 启动时先跟 Windows 要一个「带名字的锁」，谁先拿到谁是第一个；
- 第二次启动拿不到锁，就去「叫醒」已经在跑的那个窗口（把它显示到最前面），
  自己马上退出，所以不会同时开两个。

用法（放在程序入口 __main__）：
    guard = SingleInstance("打盗全家捅监控")
    if not guard.acquire():
        if not guard.wake_existing():
            messagebox.showinfo("提示", "程序已经在运行了（可能在右下角托盘里）。")
        sys.exit(0)
    root = tk.Tk()
    app = MonitorApp(root)
    guard.on_wake(lambda: root.after(0, app._restore_window))
    root.mainloop()
"""

import ctypes
import sys
import threading
import time

ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0x00000000


class SingleInstance:
    def __init__(self, name):
        self._name = str(name or "app")
        self._mutex_name = f"Local\\{self._name}_single_instance"
        self._event_name = f"Local\\{self._name}_wake_up"
        self._mutex = None      # 句柄要一直留着，不能关，否则锁就没了
        self._event = None
        self._stop = False
        self._thread = None

    @property
    def available(self):
        return sys.platform == 'win32'

    def acquire(self):
        """第一个启动的返回 True；已经有程序在跑返回 False"""
        if not self.available:
            return True
        try:
            k32 = ctypes.windll.kernel32
            k32.SetLastError(0)
            self._mutex = k32.CreateMutexW(None, False, self._mutex_name)
            first = (k32.GetLastError() != ERROR_ALREADY_EXISTS)
            if first:
                # 顺便把「叫醒」用的信号也建好，第二个程序马上就能叫到我们
                self._event = k32.CreateEventW(None, False, False, self._event_name)
            return first
        except Exception:
            return True          # 判断不了就照常启动，别把程序卡住

    def wake_existing(self):
        """叫醒已经在跑的那个程序（把它的窗口显示到最前面）"""
        if not self.available:
            return False
        try:
            k32 = ctypes.windll.kernel32
        except Exception:
            return False
        for _ in range(15):
            h = k32.OpenEventW(EVENT_MODIFY_STATE, False, self._event_name)
            if h:
                try:
                    k32.SetEvent(h)
                finally:
                    k32.CloseHandle(h)
                return True
            time.sleep(0.2)      # 刚启动的程序可能还没把信号建好，等一小会儿再试
        return False

    def on_wake(self, callback):
        """callback：收到「叫醒」信号时做什么（一般是把主窗口显示出来）"""
        if not self.available or not self._event:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop = False

        def _wait():
            k32 = ctypes.windll.kernel32
            while not self._stop:
                try:
                    rc = k32.WaitForSingleObject(self._event, 500)
                except Exception:
                    return
                if rc == WAIT_OBJECT_0:
                    try:
                        callback()
                    except Exception:
                        pass

        self._thread = threading.Thread(target=_wait, daemon=True)
        self._thread.start()
