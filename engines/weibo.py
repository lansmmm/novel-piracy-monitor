import random
import time
from urllib.parse import quote

from engines.base import BaseEngine


class WeiboEngine(BaseEngine):
    SOURCE_ID = 'weibo'
    SOURCE_LABEL = '微博'
    SEARCH_URL = 'https://s.weibo.com/weibo?q={kw}'
    EXTRA_URL = 'https://service.account.weibo.com/rights/movie'   # 微博版权投诉页

    def fetch(self, context, term, page_num=0, book_kw=''):
        page = None
        try:
            url = f"https://s.weibo.com/weibo?q={quote(term)}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2, 4))

            cur = (page.url or '').lower()
            body_text = (page.text_content('body') or '').lower()
            if 'captcha' in cur or 'verify' in cur or '验证码' in body_text or '人机验证' in body_text:
                self.pause_for_user('微博搜索要求验证码/登录')
                return []

            items = page.evaluate(r"""
                () => {
                    const blocks = Array.from(document.querySelectorAll('.card-wrap, .card, .feed, li, .search-result'));
                    const results = [];
                    const seen = new Set();

                    blocks.forEach(block => {
                        const titleEl = block.querySelector('a[href], h3 a[href], .txt a[href], .name a[href]') || block.querySelector('a');
                        if (!titleEl) return;
                        const title = (titleEl.innerText || titleEl.textContent || '').replace(/\s+/g, ' ').trim();
                        if (!title || title.length < 3) return;

                        const href = (titleEl.getAttribute('href') || '').trim();
                        if (!href || href.startsWith('javascript:')) return;
                        let clean = href;
                        if (clean.startsWith('//')) clean = 'https:' + clean;
                        if (!clean.startsWith('http')) return;
                        clean = clean.split('?')[0].split('#')[0];
                        if (clean.includes('s.weibo.com')) return;
                        if (seen.has(clean)) return;
                        seen.add(clean);

                        const summaryEl = block.querySelector('.txt, .content, .text, .feed_text, .summary') || block;
                        let summary = (summaryEl.innerText || '').replace(title, '').trim();
                        summary = (summary || '（无摘要）').replace(/\s+/g, ' ').substring(0, 500);

                        let date = '未知';
                        const textAll = (block.innerText || '').replace(/\s+/g, ' ');
                        const dm = textAll.match(/\d{4}[/-]\d{1,2}[/-]\d{1,2}/) || textAll.match(/\d{4}年\d{1,2}月\d{1,2}日/);
                        if (dm) date = dm[0];

                        results.push({ title, url: clean, summary, date, author: '微博搜索' });
                    });

                    return results.slice(0, 20);
                }
            """)

            normalized = []
            for it in items:
                normalized.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=it.get('url', ''),
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author=it.get('author', '微博搜索'),
                    content_url=it.get('url', ''),
                    long_url=it.get('url', ''),
                    is_zhinengti=False,
                ))
            return normalized
        except Exception as e:
            self.log(f"  [微博搜索错误] {type(e).__name__}: {e}")
            return []
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass
