import random
import time
from urllib.parse import quote, urlparse, parse_qs, unquote

from engines.base import BaseEngine


class ToutiaoMobileEngine(BaseEngine):
    SOURCE_ID = 'toutiao_mobile'
    SOURCE_LABEL = '头条移动'
    SEARCH_URL = 'https://m.toutiao.com/search?keyword={kw}'
    EXTRA_URL = 'https://mail.qq.com/'

    @staticmethod
    def _extract_real_url(href):
        # 头条跳转链接的 url= 参数里往往是个中间页；需要优先解析真实落地页 h5_url
        if not href:
            return ''
        href = href.strip()
        if href.startswith('//'):
            href = 'https:' + href
        if not href.startswith('http'):
            return ''
        try:
            u = urlparse(href)
            if '/search/jump' in u.path:
                qs = parse_qs(u.query)
                target = (qs.get('url') or [''])[0]
                if target:
                    target = unquote(target)
                    # 先看是否是中间页 url 参数里嵌套了真正落地页：...&h5_url=... 
                    try:
                        nested = urlparse(target)
                        nested_qs = parse_qs(nested.query)
                        h5_url = (nested_qs.get('h5_url') or [''])[0]
                        if h5_url:
                            return unquote(h5_url).split('?')[0].split('#')[0]
                    except Exception:
                        pass
                    return target.split('?')[0].split('#')[0]
                return ''
            return href.split('?')[0].split('#')[0]
        except Exception:
            return ''

    def fetch(self, context, term, page_num=0, book_kw=''):
        page = None
        try:
            # ★ 头条分页参数是 offset（每页 10 条），不是 page/page_num；
            #   不带 offset 时服务端永远返回第 1 页，这就是"翻页全是第一页"的根因。
            offset = max(0, int(page_num or 0)) * 10
            url = f"https://so.toutiao.com/search?keyword={quote(term)}&offset={offset}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2, 4))

            cur = (page.url or '').lower()
            body_text = (page.text_content('body') or '').lower()
            if 'captcha' in cur or 'verify' in cur or 'security' in cur or '人机验证' in body_text:
                # ★ 不关页面：有头模式下把验证页留给用户手动处理
                self.pause_for_user('头条搜索要求验证码/登录')
                return []

            items = page.evaluate(r"""
                () => {
                    const selectorSet = [
                        '.result-content',
                        '.cs-view-item',
                        '.cs-view-vertical-item',
                        '.search-result-item',
                        '.result-item',
                        '.feed-card',
                        '.card-item',
                        'article',
                        '.search-result',
                        '.result',
                    ];
                    const blocks = new Map();
                    selectorSet.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => blocks.set(el, true));
                    });
                    const items = [];
                    const seenTitles = new Set();

                    blocks.forEach((_, block) => {
                        const links = Array.from(block.querySelectorAll('a[href]'));
                        let main = null;
                        for (const a of links) {
                            const t = (a.innerText || '').replace(/\s+/g, ' ').trim();
                            if (t.length >= 6) { main = a; break; }
                        }
                        if (!main) return;

                        const title = (main.innerText || main.textContent || '').replace(/\s+/g, ' ').trim();
                        if (!title || title.length < 3) return;
                        if (seenTitles.has(title)) return;
                        seenTitles.add(title);

                        let href = (main.getAttribute('href') || '').trim();
                        if (!href) return;
                        if (href.startsWith('//')) href = 'https:' + href;
                        else if (href.startsWith('/')) href = 'https://so.toutiao.com' + href;
                        if (!href.startsWith('http')) return;

                        if (href.includes('/search?pd=') || href.includes('pd=synthesis') || href.includes('related_search')) return;
                        if (href.includes('so.toutiao.com/search?') || href.includes('so.toutiao.com/search?keyword=')) return;

                        const txt = (block.innerText || '').replace(/\s+/g, ' ').trim();
                        if (!txt || txt.length < 15) return;

                        let date = '未知';
                        const dm = txt.match(/\d{4}[/-]\d{1,2}[/-]\d{1,2}/) || txt.match(/\d{4}年\d{1,2}月\d{1,2}日/);
                        if (dm) date = dm[0];

                        const summary = txt.replace(title, '').trim().substring(0, 400) || '（无摘要）';
                        items.push({ title, href, summary, date });
                    });

                    if (items.length === 0) {
                        const fallback = [];
                        document.querySelectorAll('a[href]').forEach(a => {
                            const t = (a.innerText || '').replace(/\s+/g, ' ').trim();
                            const href = (a.getAttribute('href') || '').trim();
                            if (!t || t.length < 6 || !href || !href.startsWith('http')) return;
                            if (href.includes('/search?pd=') || href.includes('pd=synthesis') || href.includes('related_search')) return;
                            if (href.includes('so.toutiao.com/search?')) return;
                            fallback.push({ title: t, href });
                        });
                        return fallback.slice(0, 15);
                    }

                    return items;
                }
            """)

            self.app._last_fetch_raw_count = len(items) if items else 0

            results = []
            seen_urls = set()
            for it in items:
                real = self._extract_real_url(it.get('href', ''))
                if not real:
                    continue
                # 只过滤明显的搜索页/导航页，不过滤真实落地页；头条的 "search/jump" 可能只是中间跳转链，不代表无效结果。
                if any(d in real.lower() for d in ('so.toutiao.com/search?', 'so.toutiao.com/search?keyword=', 'zjurl.cn')):
                    continue
                if real in seen_urls:
                    continue
                seen_urls.add(real)

                long_href = (it.get('href') or '').strip()
                if not long_href.startswith('http'):
                    long_href = real
                results.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=real,
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author='头条搜索',
                    content_url=long_href,
                    long_url=long_href,
                    is_zhinengti=False,
                ))
            return results
        except Exception as e:
            self.log(f"  [头条搜索错误] {type(e).__name__}: {e}")
            return []
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass