# 打盗全家捅监控

一个用于监控网文盗版链接的 Python 工具。

## 功能

- 十余个搜索引擎的盗版链接抓取（百度 / 搜狗 / 360 / 头条 / 夸克 / 微博 / 微信 / 贴吧 / 知道 等 PC + 移动端）
- 结果去重、白名单
- 一键投诉多个平台（百度 / 晋江 / 夸克 / UC / 必应 / 微信 / 头条 / 360 / 搜狗）
- 自动搜索、批量监控、前台模式、后台模式、深度模式
- 手动搜索

## 安装

pip install playwright pystray Pillow winotify
playwright install msedge

## 运行

python monitor.py

## 打包

pyinstaller --noconfirm 打盗全家捅监控.spec

## 依赖

- Python 3.9+
- Microsoft Edge（Playwright 用 channel="msedge"）

## 许可

GNU General Public License v3.0