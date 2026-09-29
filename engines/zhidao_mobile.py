# engines/zhidao_mobile.py
"""百度知道 · 手机版（移动端入口：https://zhidao.baidu.com/index/?fr=&word=书名）

常规模式搜这个入口；「深度模式」再叠加 PC 版（engines/zhidao.py）。

手机版的页面逻辑和 PC 版完全不同，这里是按手机版的 DOM 重写的：
1. 结果卡在 #new-search-list 里，一条一个 .w-solved-list-li；
2. ★ 标题不是 <a href>，而是 .search-title[data-url="/question/xxx.html"]，
   所以不能像 PC 版那样只扫 a[href*="/question/"]，要认 data-url 和卡片 id；
3. 摘要 = .s-con .content，时间 + 回答者 = .explain（如「2025-11-26 赛玖久生活日记」）；
4. 手机版是滚动加载的，翻页除了带 pn= 参数，还会顺手滚到底把后面内容滚出来；
5. 没结果时页面给「未找到相关问题」（.new-noresult），属于正常情况，不当报错。
"""
import time
import random
from urllib.parse import quote

from engines.base import BaseEngine


class ZhidaoMobileEngine(BaseEngine):
    SOURCE_ID = 'zhidao_mobile'
    SOURCE_LABEL = '知道手机版'
    SEARCH_URL = 'https://zhidao.baidu.com/index/?fr=&word={kw}'
    EXTRA_URL = 'https://newcopyright.baidu.com/'

    # 手机版结果区：认 data-url / 卡片 id，再兜底扫一遍普通链接
    EXTRACT_JS = r"""
        () => {
            const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const text = (el) => el ? clean(el.innerText || el.textContent || '') : '';

            const normUrl = (raw) => {
                let u = (raw || '').trim();
                if (!u) return '';
                if (u.startsWith('//')) u = 'https:' + u;
                else if (u.startsWith('/')) u = 'https://zhidao.baidu.com' + u;
                else if (!u.startsWith('http')) return '';
                return u.split('#')[0];
            };

            const pickUrl = (card) => {
                const holders = [card].concat(Array.from(card.querySelectorAll(
                    '.search-title, [data-url], a[href]')));
                for (const h of holders) {
                    let cand = '';
                    try { cand = h.getAttribute('data-url') || h.getAttribute('href') || ''; } catch (e) {}
                    if (cand && cand.indexOf('/question/') >= 0) return normUrl(cand);
                }
                const id = (card.getAttribute('id') || '').trim();
                if (/^\d{6,}$/.test(id)) {
                    return 'https://zhidao.baidu.com/question/' + id + '.html';
                }
                return '';
            };

            const parseDate = (info) => {
                if (!info) return '未知';
                const abs = info.match(/\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?/);
                if (abs) return abs[0];
                const rel = info.match(/\d+\s*(?:秒|分钟|小时|天|周|个月|年)\s*之?前|刚刚|昨天|前天|今天/);
                if (rel) return rel[0];
                return '未知';
            };

            const out = [];
            const seen = new Set();

            // ① 手机版结果卡
            const cards = Array.from(document.querySelectorAll(
                '#new-search-list .w-solved-list-li, .w-solved-list-li, .new-search-list .search-item'));
            for (const card of cards) {
                const url = pickUrl(card);
                if (!url) continue;
                const key = url.split('?')[0];
                if (seen.has(key)) continue;
                const titleEl = card.querySelector('.search-title .head')
                    || card.querySelector('.search-title')
                    || card.querySelector('.head');
                const title = text(titleEl);
                if (!title || title.length < 4) continue;
                seen.add(key);
                const info = text(card.querySelector('.explain, .qmsg, .info, .time'));
                const authorEl = card.querySelector('.explain .author, .author');
                out.push({
                    title: title,
                    url: url,
                    summary: text(card.querySelector('.s-con .content, .s-con .content-item, .s-con')) || '（无摘要）',
                    date: parseDate(info),
                    author: text(authorEl) || '百度知道',
                });
            }
            if (out.length) return { items: out, kind: 'mobile' };

            // ② 兜底：手机版改版 / 直接落到 PC 版页面时，扫普通链接
            document.querySelectorAll('a[href*="/question/"]').forEach(a => {
                let href = a.getAttribute('href') || '';
                if (!href || href.indexOf('/question/') < 0) return;
                if (!/\/question\/\d+/.test(href)) return;
                const url = normUrl(href);
                const key = url.split('?')[0];
                if (!url || seen.has(key)) return;
                const title = text(a);
                if (!title || title.length < 5) return;
                seen.add(key);
                let box = a;
                for (let i = 0; i < 6; i++) {
                    if (!box.parentElement) break;
                    box = box.parentElement;
                    if (text(box).length > title.length + 30) break;
                }
                const summary = clean(text(box).replace(title, '')) || '（无摘要）';
                out.push({
                    title: title,
                    url: url,
                    summary: summary.slice(0, 500),
                    date: parseDate(summary),
                    author: '百度知道',
                });
            });
            if (out.length) return { items: out, kind: 'fallback' };

            const empty = !!document.querySelector('.new-noresult, .wgt-no-res, .no-result-box');
            return { items: [], kind: empty ? 'empty' : 'unknown' };
        }
    """

    @staticmethod
    def _wait_items(page, retries=3, gap=1.6):
        """手机版结果是 JS 渲染的，第一遍可能还没出来，多试几次"""
        data = {'items': [], 'kind': 'unknown'}
        for i in range(max(1, retries)):
            try:
                data = page.evaluate(ZhidaoMobileEngine.EXTRACT_JS) or data
            except Exception:
                data = {'items': [], 'kind': 'unknown'}
            if data.get('items') or data.get('kind') == 'empty':
                return data
            if i < retries - 1:
                time.sleep(gap)
        return data

    def fetch(self, context, term, page_num=0, book_kw=''):
        results = []
        page = None
        try:
            url = self.SEARCH_URL.format(kw=quote(term))
            if page_num:
                url += f"&pn={page_num * 10}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2.5, 4.5))

            cur = (page.url or "").lower()
            if "wappass" in cur or "verify" in cur or "captcha" in cur or "passport" in cur:
                try:
                    page.close()
                except Exception:
                    pass
                page = None
                self.pause_for_user("百度知道手机版要求验证码/登录")
                return results

            # 手机版是滚动加载：翻页时顺手滚到底（有的词要滚动才会渲染出后面的卡片）
            if page_num:
                try:
                    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(0.8)
                except Exception:
                    pass

            data = self._wait_items(page)
            items = data.get('items') or []
            self.app._last_fetch_raw_count = len(items) if items else 0

            if not items:
                if data.get('kind') == 'empty':
                    self.log(f"  [知道手机版] 没有搜到「{term}」的相关结果")
                else:
                    self.log("  [知道手机版] 页面没解析出结果（可能改版或需要登录）")
                return results

            normalized = []
            seen = set()
            for it in items:
                item_url = (it.get('url') or '').strip()
                if not item_url or item_url in seen:
                    continue
                seen.add(item_url)
                normalized.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=item_url,
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author=it.get('author', '百度知道'),
                    content_url=item_url,
                    long_url=item_url,
                    is_zhinengti=False,
                ))
            return normalized
        except Exception as e:
            self.log(f"  [知道手机版错误] {type(e).__name__}: {e}")
            return results
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass
