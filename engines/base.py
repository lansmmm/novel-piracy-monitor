# engines/base.py
import re
from urllib.parse import urlsplit

from utils import filter_summary, normalize_date, strip_date_from_text


class BaseEngine:
    """所有搜索引擎的基类"""
    SOURCE_ID = ""       # 内部标识，如 'baidu'
    SOURCE_LABEL = ""    # 界面显示名，如 '百度搜索'
    SEARCH_URL = ""      # 手动搜索用的 URL 模板，用 {kw} 占位
    EXTRA_URL = ""       # 手动搜索时附带打开的链接（可为空）

    def __init__(self, app):
        self.app = app

    def log(self, msg):
        self.app.log(msg)

    def pause_for_user(self, reason):
        self.app.pause_for_user(reason)

    # 这些页面的定位信息就在查询串里，截断后就打不开了
    KEEP_QUERY_PREFIXES = (
        'https://mp.weixin.qq.com/s',
        'http://mp.weixin.qq.com/s',
    )

    @staticmethod
    def clean_url(url):
        """统一清理链接，只保留真正能用的 URL"""
        if not url:
            return ""
        url = str(url).strip()
        if not url or url == 'about:blank':
            return ""
        if url.startswith('//'):
            url = 'https:' + url
        if not url.startswith(('http://', 'https://')):
            if url.startswith('/'):
                return url
            return url
        head = url.split('#')[0]
        low = head.lower()
        if any(low.startswith(p) for p in BaseEngine.KEEP_QUERY_PREFIXES):
            return head
        return head.split('?')[0]

    @staticmethod
    def clean_content_url(url):
        """源链接专用清理：保留完整地址（含查询串）。

        跳转壳链接（如 so.com/jump?u=... / weixin.sogou.com/link?url=...）
        的价值全在查询串里，截断后就废了，所以这里只做最基本的清洗。
        """
        if not url:
            return ''
        v = str(url).strip()
        if not v or v == 'about:blank':
            return ''
        if v.startswith('//'):
            v = 'https:' + v
        return v

    @staticmethod
    def normalize_title(title):
        if title is None:
            return ""
        return str(title).strip()

    @staticmethod
    def normalize_summary(summary, fallback='（无摘要，双击打开查看）'):
        text = str(summary or '').strip()
        if not text:
            return fallback
        cleaned = strip_date_from_text(text)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if not cleaned:
            return fallback
        return filter_summary(cleaned)

    @staticmethod
    def normalize_date(date_text, fallback='未知'):
        text = str(date_text or '').strip()
        if not text:
            return fallback
        value = normalize_date(text)
        return value if value and value != '未知' else fallback

    def normalize_result(self, source, title, url, summary='', date='未知', author='',
                        content_url='', long_url='', is_new=False, is_white=False,
                        is_zhinengti=False):
        """统一返回结构：以后所有引擎都按这个格式输出，UI 只看这个格式。"""
        clean_url = self.clean_url(url)
        clean_content = self.clean_content_url(content_url or long_url)
        if not clean_url and clean_content:
            clean_url = self.clean_url(clean_content)

        result = {
            'source': source,
            'title': self.normalize_title(title),
            'url': clean_url,
            'summary': self.normalize_summary(summary),
            'date': self.normalize_date(date),
            'author': str(author or '').strip() or '未知来源',
            'content_url': clean_content,
            'long_url': clean_content or clean_url,
            'is_new': bool(is_new),
            'is_white': bool(is_white),
            'is_zhinengti': bool(is_zhinengti),
            'checked': False,
        }

        if not result['title'] and result['url']:
            result['title'] = result['url']

        return result

    def _create_stealth_page(self, context):
        """创建一个带反爬虫伪装的新页面（公共方法）"""
        page = context.new_page()
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        """)
        return page

    def fetch(self, context, term, page_num, book_kw=''):
        """核心抓取方法，子类必须重写"""
        raise NotImplementedError