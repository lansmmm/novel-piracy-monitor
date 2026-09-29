import re
import time
from urllib.parse import quote

from engines.base import BaseEngine


class WeiboMobileEngine(BaseEngine):
    SOURCE_ID = 'weibo_mobile'
    SOURCE_LABEL = '微博移动'
    SEARCH_URL = 'https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D{kw}'
    EXTRA_URL = 'https://service.account.weibo.com/rights/movie'   # 微博版权投诉页

    API_URL = 'https://m.weibo.cn/api/container/getIndex'

    # 微博搜索的排序由 containerid 里的 type 决定。
    # 页面上点「综合 / 实时 / 热门」时地址栏 URL 不变，变的只是 XHR 的 containerid：
    #   1 = 综合      61 = 实时（按最新）      60 = 热门
    CONTAINER_TYPE = 61

    @staticmethod
    def _fmt_date(raw):
        """'Sun Sep 20 21:33:53 +0800 2026' -> '2026-09-20 21:33'"""
        if not raw:
            return '未知'
        try:
            from datetime import datetime
            dt = datetime.strptime(str(raw).strip(), '%a %b %d %H:%M:%S %z %Y')
            return dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            return str(raw).strip()

    @staticmethod
    def _clean_html(text):
        if not text:
            return ''
        value = re.sub(r'<br\s*/?>', ' ', str(text))
        value = re.sub(r'<[^>]+>', '', value)
        for a, b in (('&nbsp;', ' '), ('&amp;', '&'), ('&lt;', '<'),
                     ('&gt;', '>'), ('&quot;', '"'), ('&#39;', "'")):
            value = value.replace(a, b)
        return re.sub(r'\s+', ' ', value).strip()

    @staticmethod
    def _first_outer_link(html):
        """正文里第一个外部链接。

        微博的盗文资源往往直接贴在正文里（网盘直链等），这个比微博详情页更有价值，
        所以把它放到「源链接」栏。
        """
        if not html:
            return ''
        for m in re.finditer(r'href="([^"]+)"', str(html)):
            link = m.group(1).strip()
            low = link.lower()
            if not low.startswith('http'):
                continue
            if 'weibo.cn' in low or 'weibo.com' in low or 'sina.com.cn' in low:
                continue
            return link
        return ''

    def _request(self, context, term):
        """调一次接口，返回 (payload, containerid)。"""
        cid = f"100103type={self.CONTAINER_TYPE}&q={term}&t="
        api = (f"{self.API_URL}?containerid={quote(cid, safe='')}"
               f"&page_type=searchall&page=1")
        try:
            resp = context.request.get(
                api,
                headers={
                    'Referer': 'https://m.weibo.cn/',
                    'X-Requested-With': 'XMLHttpRequest',
                    'MWeibo-Pwa': '1',
                },
                timeout=20000,
            )
            payload = resp.json()
        except Exception as e:
            self.log(f"  [微博移动] 接口请求失败：{type(e).__name__}: {e}")
            return None, cid
        return (payload if isinstance(payload, dict) else None), cid

    @staticmethod
    def _mblogs_of(payload):
        cards = ((payload.get('data') or {}).get('cards')) or []
        mblogs = []
        seen = set()
        for card in cards:
            # 微博有三种卡片形态：
            #   card_type=9  直接挂 mblog
            #   card_type=11 把内容放在 card_group[].mblog
            #   card_type=7  是「无结果/提示」卡片，没有 mblog
            for bucket in [card] + list(card.get('card_group') or []):
                mb = bucket.get('mblog')
                if not mb:
                    continue
                mid = str(mb.get('id') or '')
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                mblogs.append(mb)
        return mblogs

    def _search_once(self, context, term):
        """返回 (mblogs, cid)。mblogs 为 None 表示要求登录/被风控。"""
        payload, cid = self._request(context, term)
        if payload is None:
            return None, cid
        if payload.get('ok') != 1:
            self.log(f"  [微博移动] 接口 ok={payload.get('ok')}，"
                     f"跳转={str(payload.get('url') or '')[:90]}")
            return None, cid
        return self._mblogs_of(payload), cid

    def _open_search_page(self, context, cid):
        """打开真实搜索页。

        微博对未登录用户会先跳到访客系统，走完会下发访客 cookie，接口就能用了；
        页面同时留给用户 —— 有头模式下可以自己登录 / 过人机验证。
        """
        page = None
        try:
            page = self._create_stealth_page(context)
            page.goto(f"https://m.weibo.cn/search?containerid={cid}",
                      wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_url(
                    lambda u: 'm.weibo.cn' in (u or '') and 'passport' not in (u or ''),
                    timeout=12000)
            except Exception:
                pass
            time.sleep(2)
            return page
        except Exception as e:
            self.log(f"  [微博移动] 打开搜索页失败：{type(e).__name__}: {e}")
            try:
                if page:
                    page.close()
            except Exception:
                pass
            return None

    def _mblogs_from_page(self, page):
        """兜底：接口不可用时直接从页面 DOM 取（访客页其实也带结果）。"""
        try:
            raw = page.evaluate(r"""
                () => {
                    const out = [];
                    const seen = new Set();
                    const links = document.querySelectorAll('a[href*="/detail/"], a[href*="/status/"]');
                    for (const a of links) {
                        const href = a.getAttribute('href') || '';
                        const m = href.match(/\/(?:detail|status)\/(\d+)/);
                        if (!m) continue;
                        const id = m[1];
                        if (seen.has(id)) continue;
                        seen.add(id);
                        const card = a.closest('article, .card9, .card, .weibo, li, div');
                        const txt = ((card ? card.innerText : a.innerText) || '')
                                        .replace(/\s+/g, ' ').trim();
                        out.push({ id: id, text: txt.slice(0, 600) });
                        if (out.length >= 20) break;
                    }
                    return out;
                }
            """)
        except Exception as e:
            self.log(f"  [微博移动] 页面解析失败：{type(e).__name__}: {e}")
            return []

        return [{'id': it.get('id'), 'text': it.get('text') or '',
                 'created_at': '', 'user': {'screen_name': ''}}
                for it in (raw or [])]

    def fetch(self, context, term, page_num=0, book_kw=''):
        try:
            # 微博搜索接口只给第一页；page>=2 会返回 ok=-100，直接放弃剩余页
            if page_num > 0:
                self.app._last_fetch_raw_count = 0
                return []

            mblogs, cid = self._search_once(context, term)

            # ★ 被要求登录：打开真实页面（建立访客态 / 让用户登录），再重试一次
            if mblogs is None:
                self.log("  [微博移动] 需要登录/被风控，打开微博搜索页处理…")
                page = self._open_search_page(context, cid)
                self.pause_for_user('微博要求登录/人机验证，请在打开的微博页面里处理')

                mblogs, _ = self._search_once(context, term)
                if mblogs is None:
                    mblogs = []
                if not mblogs and page is not None:
                    self.log("  [微博移动] 接口仍不可用，改为直接解析页面内容")
                    mblogs = self._mblogs_from_page(page)
                try:
                    if page:
                        page.close()
                except Exception:
                    pass

            # 长词 0 条 → 用书名重搜（微博实时对「书名+后缀」经常 0 条）
            if (not mblogs) and book_kw and book_kw != term:
                self.log(f"  [微博移动]「{term}」0 条，改用「{book_kw}」重搜")
                retry, _ = self._search_once(context, book_kw)
                mblogs = retry or []

            self.app._last_fetch_raw_count = len(mblogs)
            if not mblogs:
                return []

            normalized = []
            for mb in mblogs:
                mid = str(mb.get('id') or '')
                if not mid:
                    continue
                detail_url = f"https://m.weibo.cn/detail/{mid}"
                raw_html = mb.get('text') or ''
                text = self._clean_html(raw_html)
                outer = self._first_outer_link(raw_html)
                author = (mb.get('user') or {}).get('screen_name') or ''

                normalized.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=text[:60] or detail_url,
                    url=detail_url,
                    summary=text[:500] or '（无摘要）',
                    date=self._fmt_date(mb.get('created_at')),
                    author=author or '微博用户',
                    content_url=outer or detail_url,
                    long_url=outer or detail_url,
                    is_zhinengti=False,
                ))
            return normalized
        except Exception as e:
            self.log(f"  [微博移动搜索错误] {type(e).__name__}: {e}")
            return []
