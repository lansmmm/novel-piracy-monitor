import os
import sys
import re

# ==================== 默认模式 ====================
DEFAULT_HEADLESS = True

# ==================== 路径 ====================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SEEN_ZHIDAO_FILE   = os.path.join(BASE_DIR, "monitor_seen_zhidao.json")
SEEN_ZHIDAO_MOBILE_FILE = os.path.join(BASE_DIR, "monitor_seen_zhidao_mobile.json")
SEEN_TIEBA_FILE    = os.path.join(BASE_DIR, "monitor_seen_tieba.json")
SEEN_BAIDU_FILE    = os.path.join(BASE_DIR, "monitor_seen_baidu.json")
SEEN_BAIDU_MOBILE_FILE = os.path.join(BASE_DIR, "monitor_seen_baidu_mobile.json")
SEEN_BING_FILE     = os.path.join(BASE_DIR, "monitor_seen_bing.json")
SEEN_SO360_MOBILE_FILE = os.path.join(BASE_DIR, "monitor_seen_so360_mobile.json")
SEEN_SO360_PC_FILE     = os.path.join(BASE_DIR, "monitor_seen_so360_pc.json")
SEEN_TOUTIAO_MOBILE_FILE = os.path.join(BASE_DIR, "monitor_seen_toutiao_mobile.json")
SEEN_TOUTIAO_PC_FILE = os.path.join(BASE_DIR, "monitor_seen_toutiao_pc.json")
SEEN_SOGOU_MOBILE_FILE = os.path.join(BASE_DIR, "monitor_seen_sogou_mobile.json")
SEEN_SOGOU_PC_FILE     = os.path.join(BASE_DIR, "monitor_seen_sogou_pc.json")
SEEN_SOGOU_WEIXIN_FILE = os.path.join(BASE_DIR, "monitor_seen_sogou_weixin.json")

# 引擎改名前的旧记录文件（启动时自动改名到上面新文件，见 utils.migrate_old_files）
SEEN_SO360_LEGACY_FILE   = os.path.join(BASE_DIR, "monitor_seen_so360.json")
SEEN_TOUTIAO_LEGACY_FILE = os.path.join(BASE_DIR, "monitor_seen_toutiao.json")
SEEN_SOGOU_LEGACY_FILE   = os.path.join(BASE_DIR, "monitor_seen_sogou.json")
SEEN_QUARK_FILE    = os.path.join(BASE_DIR, "monitor_seen_quark.json")
SEEN_QUARK_CN_FILE = os.path.join(BASE_DIR, "monitor_seen_quark_cn.json")
SEEN_WEIBO_FILE    = os.path.join(BASE_DIR, "monitor_seen_weibo.json")
SEEN_WEIBO_MOBILE_FILE = os.path.join(BASE_DIR, "monitor_seen_weibo_mobile.json")

WHITELIST_COMMON_FILE = os.path.join(BASE_DIR, "monitor_whitelist_common.json")

# ★ 未加白链接：记录文件（内部用）+ 给作者看的本地文档（txt，自动生成）
UNWHITE_RECORD_FILE = os.path.join(BASE_DIR, "monitor_unwhite_links.json")
UNWHITE_DOC_FILE    = os.path.join(BASE_DIR, "未加白链接.txt")
UNWHITE_LEGACY_BOOK = "旧记录"      # 老记录（monitor_seen_*.json）里没有书名，统一放这组

WHITELIST_LEGACY_FILES = [
    os.path.join(BASE_DIR, "monitor_whitelist_zhidao.json"),
    os.path.join(BASE_DIR, "monitor_whitelist_tieba.json"),
    os.path.join(BASE_DIR, "monitor_whitelist_zhinengti.json"),
]

BOOKMARKS_FILE       = os.path.join(BASE_DIR, "bookmarks.json")
DEFAULT_SUFFIX_FILE  = os.path.join(BASE_DIR, "monitor_default_suffixes.json")
ICON_FILE            = os.path.join(BASE_DIR, "app_icon.ico")

INITIAL_SUFFIXES = ["网盘", "链接", "txt", "资源"]

# 主程序用到的监控来源（顺序 = 结果标签页顺序）
WORKING_SRC_ORDER = [
    'quark_cn',
    'baidu_mobile', 'baidu', 'zhidao_mobile', 'zhidao', 'tieba', 'bing',
    'toutiao_pc', 'toutiao_mobile',
    'sogou_mobile', 'sogou_pc',
    'so360_mobile', 'so360_pc',
    'sogou_weixin',
]
SRC_ORDER = WORKING_SRC_ORDER

# 界面上的来源勾选框（顺序 = 勾选框顺序）：一个勾选框 = 一个来源组
VISIBLE_SRC_ORDER = [
    'quark',
    'baidu', 'zhidao', 'tieba', 'bing',
    'toutiao', 'sogou', 'so360', 'sogou_weixin',
]

# 每个勾选框对应哪些引擎：
#   第一个 = 常规模式搜的渠道（百度/头条/搜狗/360/知道 里挑抓得好的那个）
#   其余 = 只在「深度模式」下才一起搜的另一个渠道（pc + 移动 双渠道）
SOURCE_GROUPS = {
    'quark':        ['quark_cn'],
    'baidu':        ['baidu_mobile', 'baidu'],
    'zhidao':       ['zhidao_mobile', 'zhidao'],
    'tieba':        ['tieba'],
    'bing':         ['bing'],
    'toutiao':      ['toutiao_pc', 'toutiao_mobile'],
    'sogou':        ['sogou_pc', 'sogou_mobile'],
    'so360':        ['so360_pc', 'so360_mobile'],
    'sogou_weixin': ['sogou_weixin'],
}

# 待测监控来源（测试专用程序只保留这些）
# 夸克 4 个入口放一起
# （www.quark.cn 主程序里已用，这里一起测作对照）
TEST_SRC_ORDER = [
    'quark', 'quark_m', 'quark_so', 'quark_cn',
    'weibo', 'weibo_mobile',
    'sogou_mobile', 'toutiao_mobile', 'so360_mobile',
]
SRC_LABEL = {
    'zhidao': '知道',
    'zhidao_mobile': '知道移动',
    'tieba': '贴吧',
    'baidu': '百度',
    'baidu_mobile': '百度m',
    'bing': '必应',
    # 来源组名：界面勾选框 / 标签页只显示这三个
    'sogou': '搜狗',
    'toutiao': '头条',
    'so360': '360',
    # 具体来源（日志里用）
    'sogou_mobile': '搜狗移动',
    'sogou_pc': '搜狗PC',
    'toutiao_mobile': '头条移动',
    'toutiao_pc': '头条PC',
    'so360_mobile': '360移动',
    'so360_pc': '360PC',
    'sogou_weixin': '微信',
    # 主程序里的夸克标签页/行内名：界面只显示“夸克”，手动搜索里再带域名
    'quark': '夸克',
    'quark_m': '夸克',
    'quark_so': '夸克',
    'quark_cn': '夸克',
    'weibo': '微博',
    'weibo_mobile': '微博移动',
}

# 手动搜索：搜索引擎按几列摆放（2 列）
MANUAL_SOURCE_COLUMNS = 2

# 手动搜索：搜索引擎的排列顺序（显示成「名字 域名」，域名自动从搜索网址里取）
# 2 列时按「先左后右、先上后下」铺开（左列先排满，多的往右列放）
# 顺序（作者指定）：百度、百度m、知道、知道m、贴吧、必应、夸克、夸克m、头条、头条m、
#                   微博、微博m、搜狗、搜狗m、360、360m、微信
# 同一个引擎有 PC 版和手机版时：PC 版在前、手机版（名字带 m）紧随其后
# 夸克两个入口：夸克 = www.quark.cn（PC），夸克m = quark.sm.cn（手机版）
# 「按搜索引擎」那一栏是从上到下按同一顺序排
# 以后新增的引擎接着往后排
MANUAL_SOURCE_ORDER = [
    'baidu', 'baidu_mobile', 'zhidao', 'zhidao_mobile', 'tieba', 'bing',
    'quark_cn', 'quark',
    'toutiao_pc', 'toutiao_mobile',
    'weibo', 'weibo_mobile',
    'sogou_pc', 'sogou_mobile',
    'so360_pc', 'so360_mobile',
    'sogou_weixin',
]

# 手动搜索里的引擎短名字（显示时会自动拼上域名，如「头条 so.toutiao.com」）
# 头条/搜狗/360/微博 不写 PC、移动，靠域名区分；知道两个入口域名一样，所以手机版标成「知道m」
MANUAL_SOURCE_LABEL = {
    'baidu': '百度',
    'baidu_mobile': '百度m',
    'bing': '必应',
    'so360_pc': '360',
    'so360_mobile': '360',
    'toutiao_pc': '头条',
    'toutiao_mobile': '头条',
    'sogou_pc': '搜狗',
    'sogou_mobile': '搜狗',
    'weibo': '微博',
    'weibo_mobile': '微博',
    'sogou_weixin': '微信',
    'zhidao': '知道',
    'zhidao_mobile': '知道m',
    'tieba': '贴吧',
    'quark': '夸克',
    'quark_cn': '夸克',
}

# 手动搜索里不显示的来源（夸克只留 www.quark.cn + quark.sm.cn 两个入口，
# m.sm.cn / so.m.sm.cn 不显示；想换入口就改 MANUAL_SOURCE_ORDER 和这里）
MANUAL_HIDDEN_SOURCES = ('quark_m', 'quark_so')

DISPLAY_SOURCE_GROUP = {
    'quark_cn': 'quark',
    'baidu_mobile': 'baidu',
    'zhidao_mobile': 'zhidao',
    'sogou_mobile': 'sogou',
    'sogou_pc': 'sogou',
    'toutiao_mobile': 'toutiao',
    'toutiao_pc': 'toutiao',
    'so360_mobile': 'so360',
    'so360_pc': 'so360',
    'sogou_weixin': 'sogou_weixin',
    'weibo_mobile': 'weibo',
}

# 这些落地页即使命中下面的屏蔽词也要保留（例如微信公众号文章正文页）
ALLOWED_URL_PREFIXES = (
    'https://mp.weixin.qq.com/s',
    'http://mp.weixin.qq.com/s',
)

BLOCKED_DOMAINS = (
    'jjwxc.com', 'jjwxc.net',
    'ai.so.com', 'ai.sogou.com',
    'gdt.qq.com', 'c.gdt.qq.com',
    'baike.sogou.com', 'baike.baidu.com', 'baike.so.com',
    'so.com', 'www.so.com', 'sogou.com', 'www.sogou.com',
    'so.toutiao.com', 'www.toutiao.com',
    'www.baidu.com', 'm.baidu.com',
    'www.bing.com', 'cn.bing.com',
    'www.microsoft.com', 'microsoft.com', 'support.microsoft.com',
    'learn.microsoft.com', 'answers.microsoft.com',
    'weixin.sogou.com', 'pic.sogou.com', 'v.sogou.com',
    'zhihu.sogou.com', 'hanyu.sogou.com', 'fanyi.sogou.com',
    'wenwen.sogou.com', 'zhihu.com', 'weixin.qq.com',
    # ★ 微博的正文页必须保留：微博引擎的结果 URL 本身就是 weibo.com / m.weibo.cn，
    #    如果把域名整体屏蔽掉，微博/微博移动会 100% 抓不到东西。
    #    搜索页和话题聚合页改由 BLOCKED_URL_TOKENS 精确拦截。
)

BLOCKED_URL_TOKENS = (
    'baike.', 'dict.', 'dictionary', 'encyclopedia',
    'gdt_click.fcg', 'gdt.qq.com', 'ai.so.com', 'ai.sogou.com',
    'iaimode/search', 'sogou.com/web?query=', 'so.com/s?q=',
    'so.toutiao.com/search', 'baike.baidu.com', 'baike.sogou.com',
    'zhihu.sogou.com', 'weixin.sogou.com', 'pic.sogou.com',
    'v.sogou.com', 'fanyi.sogou.com', 'wenwen.sogou.com',
    'hanyu.sogou.com', 'profile.zjurl.cn', 'www.microsoft.com',
    'microsoft.com', 'support.microsoft.com', 'learn.microsoft.com',
    'answers.microsoft.com', 'weibo.com/weibo', 'm.weibo.cn/search',
    's.weibo.com/weibo', 'm.weibo.cn/p/index'
)

# ==================== 正则与关键词 ====================
DATE_PATTERNS = [
    r'\d{4}年\d{1,2}月\d{1,2}日',
    r'\d{4}-\d{1,2}-\d{1,2}',
    r'\d{4}/\d{1,2}/\d{1,2}',
    r'\d{4}\.\d{1,2}\.\d{1,2}',
]

RELATIVE_DATE_PATTERNS = [
    r'\d+\s*秒\s*之?前',
    r'\d+\s*分钟\s*之?前',
    r'\d+\s*小时\s*之?前',
    r'\d+\s*天\s*之?前',
    r'\d+\s*周\s*之?前',
    r'\d+\s*个?月\s*之?前',
    r'\d+\s*年\s*之?前',
    r'刚刚', r'昨天', r'前天', r'今天',
]

RELDATE_NUM_PATTERNS = {
    'day':    r'(\d+)\s*天\s*之?前',
    'hour':   r'(\d+)\s*小时\s*之?前',
    'minute': r'(\d+)\s*分钟\s*之?前',
    'second': r'(\d+)\s*秒\s*之?前',
    'week':   r'(\d+)\s*周\s*之?前',
    'month':  r'(\d+)\s*个?月\s*之?前',
    'year':   r'(\d+)\s*年\s*之?前',
}

FOOTER_KEYWORDS = [
    '知道商城', '合伙人认证', '投诉建议', '意见反馈',
    '账号申诉', '非法信息举报', '京ICP', '京网文',
    '京公网安备', '违法和不良信息', '举报电话', '举报邮箱',
    '百度知道投诉', '百度知道协议', '品牌合作', '企业推广',
    '网站律师', '使用百度前必读', '©',
]