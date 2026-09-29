# -*- mode: python ; coding: utf-8 -*-
"""打包配置：把 monitor.py 及其相关文件打成单个 exe。

- playwright 只带驱动，浏览器用对方电脑上装的 Edge（代码里 channel="msedge"）
- pystray / PIL / winotify 用于托盘图标和 Windows 通知
"""
from PyInstaller.utils.hooks import collect_all

datas = [('app_icon.ico', '.'), ('monitor_default_suffixes.json', '.')]
binaries = []
hiddenimports = ['pystray', 'PIL', 'winotify', 'manual_search']

for _pkg in ('pystray', 'playwright'):
    _tmp = collect_all(_pkg)
    datas += _tmp[0]
    binaries += _tmp[1]
    hiddenimports += _tmp[2]


a = Analysis(
    ['monitor.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='打盗全家捅监控',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
)
