import random
import time
from urllib.parse import quote

from engines.base import BaseEngine


class So360PCEngine(BaseEngine):
    SOURCE_ID = 'so360_pc'
    SOURCE_LABEL = '360PC'
    SEARCH_URL = 'https://www.so.com/s?q={kw}'
    EXTRA_URL = 'https://mail.qq.com/'

    def fetch(self, context, term, page_num=0, book_kw=''):
        page = None
        try:
            url = f"https://www.so.com/s?q={quote(term)}&pn={page_num * 10}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2, 4))

            cur = (page.url or '').lower()
            if 'captcha' in cur or 'verify' in cur or 'wappass' in cur:
                # ★ 不关页面：有头模式下把验证页留给用户手动处理
                self.pause_for_user('360搜索要求验证码/登录')
                return []

            items = page.evaluate(r"""
                () => {
                    const cleanUrl = (raw) => {
                        if (!raw) return '';
                        let href = String(raw).trim();
                        if (!href) return '';
                        if (href.startsWith('//')) href = 'https:' + href;
                        if (!href.startsWith('http')) return '';
                        return href.split('?')[0].split('#')[0];
                    };

                    const realTargetUrl = (block) => {
                        // 360 把真实网址放在 data-mdurl 属性里
                        const el = block.querySelector('[data-mdurl]');
                        if (el) return cleanUrl(el.getAttribute('data-mdurl'));
                        return '';
                    };

                    const blocks = Array.from(document.querySelectorAll('li.res-list'));
                    const result = [];
                    const seen = new Set();

                    blocks.forEach(block => {
                        // 标题只认 h3 里的链接，避免抓到广告图标、反馈等杂链接
                        const titleEl = block.querySelector('h3 a[href]');
                        if (!titleEl) return;

                        let title = (titleEl.innerText || titleEl.textContent || '').replace(/\s+/g, ' ').trim();
                        if (!title) {
                            const attr = titleEl.getAttribute('title') || '';
                            title = attr.trim();
                        }
                        if (!title || title.length < 3) return;

                        const titleHref = (titleEl.getAttribute('href') || '').trim();
                        // 360 AI 问答卡不是网页结果，直接跳过
                        if (titleHref.includes('ai.so.com')) return;

                        // 长链接 = 右键标题复制到的那种（so.com/link?m=...）
                        let longHref = titleHref;
                        if (longHref.startsWith('//')) longHref = 'https:' + longHref;

                        const href = realTargetUrl(block);
                        if (!href) return;
                        if (seen.has(href)) return;
                        seen.add(href);

                        const summaryEl = block.querySelector('.res-list-summary, .res-desc, .summary, .res-content, .res-snippet, p');
                        let summary = (summaryEl ? (summaryEl.innerText || '') : '').replace(title, '').trim();
                        summary = (summary || '（无摘要）').replace(/\s+/g, ' ').substring(0, 500);

                        let date = '未知';
                        const textAll = (block.innerText || '').replace(/\s+/g, ' ');
                        const dm = textAll.match(/\d{4}[/-]\d{1,2}[/-]\d{1,2}/) || textAll.match(/\d{4}年\d{1,2}月\d{1,2}日/);
                        if (dm) date = dm[0];

                        result.push({
                            title,
                            url: href,
                            longHref,
                            summary,
                            date,
                            author: '360搜索',
                            isZhinengti: false,
                        });
                    });

                    return result;
                }
            """)

            self.app._last_fetch_raw_count = len(items) if items else 0
            normalized = []
            for it in items:
                nr = self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=it.get('url', ''),
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author=it.get('author', '360搜索'),
                    content_url=it.get('url', ''),
                    long_url=it.get('url', ''),
                    is_zhinengti=False,
                )
                # 链接栏 = 最终跳转网址；源链接 = 右键标题复制到的长链接
                long_href = (it.get('longHref') or '').strip()
                if long_href.startswith('http'):
                    nr['content_url'] = long_href
                    nr['long_url'] = long_href
                normalized.append(nr)
            return normalized
        except Exception as e:
            self.log(f"  [360搜索错误] {type(e).__name__}: {e}")
            return []
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass