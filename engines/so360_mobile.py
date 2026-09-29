import random
import time
from urllib.parse import quote

from engines.base import BaseEngine


class So360MobileEngine(BaseEngine):
    SOURCE_ID = 'so360_mobile'
    SOURCE_LABEL = '360移动'
    SEARCH_URL = 'https://m.so.com/s?q={kw}'
    EXTRA_URL = 'https://mail.qq.com/'

    def fetch(self, context, term, page_num=0, book_kw=''):
        page = None
        try:
            url = f"https://m.so.com/s?q={quote(term)}&pn={page_num * 10}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2, 4))

            cur = (page.url or '').lower()
            body_text = (page.text_content('body') or '').lower()
            if 'captcha' in cur or 'verify' in cur or 'wappass' in cur or '验证码' in body_text or '人机验证' in body_text:
                self.pause_for_user('360移动搜索要求验证码/登录')
                return []

            items = page.evaluate(r"""
                () => {
                    const unescapeNested = (v) => {
                        let cur = String(v || '');
                        for (let i = 0; i < 3; i++) {
                            let d;
                            try { d = decodeURIComponent(cur); } catch (e) { break; }
                            if (d === cur) break;
                            cur = d;
                        }
                        return cur;
                    };

                    const paramOf = (href, key) => {
                        if (!href) return '';
                        const qi = href.indexOf('?');
                        if (qi < 0) return '';
                        for (const part of href.slice(qi + 1).split('&')) {
                            const ei = part.indexOf('=');
                            if (ei < 0) continue;
                            if (part.slice(0, ei) !== key) continue;
                            return unescapeNested(part.slice(ei + 1));
                        }
                        return '';
                    };

                    const absUrl = (raw) => {
                        if (!raw) return '';
                        let v = String(raw).trim();
                        if (!v || v.startsWith('javascript:') || v.startsWith('#')) return '';
                        if (v.startsWith('//')) v = 'https:' + v;
                        return v.startsWith('http') ? v : '';
                    };

                    const results = [];
                    const seen = new Set();
                    // 真实结果块的 class 都带 res-list；相关推荐/其他人还在搜也是 res-list，需要排掉
                    const blocks = Array.from(document.querySelectorAll('.res-list'));

                    for (const block of blocks) {
                        const cls = String(block.className || '');
                        if (cls.includes('guide-rel') || cls.includes('mso-recommend') || cls.includes('mso-rec')) continue;

                        const a = block.querySelector('a[href*="jump?u="]') || block.querySelector('a[href]');
                        if (!a) continue;

                        const title = ((a.innerText || a.textContent || '')).replace(/\s+/g, ' ').trim();
                        if (!title || title.length < 3) continue;

                        const rawHref = a.getAttribute('href') || '';
                        // ★ 真实落地页藏在 m.so.com/jump?u=<编码后的地址> 里
                        let realUrl = paramOf(rawHref, 'u') || paramOf(rawHref, 'url') || absUrl(rawHref);
                        if (!realUrl) continue;
                        const low = realUrl.toLowerCase();
                        if (low.includes('m.so.com/s?q=') || low.includes('so.com/s?q=') || low.includes('ai.so.com')) continue;
                        if (seen.has(realUrl)) continue;
                        seen.add(realUrl);

                        let summary = '';
                        const sumEl = block.querySelector('.res-desc, .res-abstract, .summary, .snippet, .content, p');
                        if (sumEl) summary = (sumEl.innerText || '').replace(title, '');
                        if (!summary) summary = (block.innerText || '').replace(title, '');
                        summary = (summary || '（无摘要）').replace(/\s+/g, ' ').trim().substring(0, 500);

                        let date = '未知';
                        const textAll = (block.innerText || '').replace(/\s+/g, ' ');
                        const dm = textAll.match(/\d{4}[/-]\d{1,2}[/-]\d{1,2}/) || textAll.match(/\d{4}年\d{1,2}月\d{1,2}日/);
                        if (dm) date = dm[0];

                        results.push({
                            title,
                            url: realUrl,
                            longHref: absUrl(rawHref) || realUrl,
                            summary,
                            date,
                            author: '360移动搜索',
                        });
                        if (results.length >= 20) break;
                    }
                    return results;
                }
            """)

            self.app._last_fetch_raw_count = len(items) if items else 0
            normalized = []
            for it in items:
                long_href = (it.get('longHref') or it.get('url', '') or '').strip()
                final_url = (it.get('url') or '').strip()
                normalized.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=final_url,
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author=it.get('author', '360移动搜索'),
                    content_url=long_href or final_url,
                    long_url=long_href or final_url,
                    is_zhinengti=False,
                ))
            return normalized
        except Exception as e:
            self.log(f"  [360移动搜索错误] {type(e).__name__}: {e}")
            return []
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass
