import random
import time
from urllib.parse import quote

from engines.base import BaseEngine


class SogouPCEngine(BaseEngine):
    SOURCE_ID = 'sogou_pc'
    SOURCE_LABEL = '搜狗PC'
    SEARCH_URL = 'https://www.sogou.com/web?query={kw}'
    EXTRA_URL = 'https://mail.qq.com/'

    def fetch(self, context, term, page_num=0, book_kw=''):
        page = None
        try:
            url = f"https://www.sogou.com/web?query={quote(term)}&page={page_num + 1}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2, 4))

            cur = (page.url or '').lower()
            body_text = (page.text_content('body') or '').lower()
            if 'captcha' in cur or 'verify' in cur or 'antispider' in cur or '验证码' in body_text or '人机验证' in body_text:
                # ★ 不关页面：有头模式下把验证页留给用户手动处理
                self.pause_for_user('搜狗搜索要求验证码/登录，建议切回有头模式验证一次')
                return []

            items = page.evaluate(r"""
                () => {
                    const blocks = Array.from(document.querySelectorAll('.vrwrap'));
                    const results = [];
                    const seen = new Set();

                    blocks.forEach(block => {
                        const titleEl = block.querySelector('h3.vr-title a[href]');
                        if (!titleEl) return;

                        const title = (titleEl.innerText || titleEl.textContent || '').replace(/\s+/g, ' ').trim();
                        if (!title || title.length < 3) return;

                        // ★ 搜狗返回的是相对路径 /link?url=...，用 .href 让浏览器补全为绝对地址
                        let longHref = (titleEl.href || titleEl.getAttribute('href') || '').trim();
                        if (longHref.startsWith('/')) {
                            longHref = 'https://www.sogou.com' + longHref;
                        }
                        let finalUrl = '';
                        const duEls = block.querySelectorAll('[data-url]');
                        for (const el of duEls) {
                            const v = (el.getAttribute('data-url') || '').trim();
                            if (v.startsWith('http')) { finalUrl = v; break; }
                        }
                        // 个别结果是直接外链（如微信文章），也可以直接用
                        if (!finalUrl && longHref.startsWith('http') && !longHref.includes('sogou.com/link')) {
                            finalUrl = longHref;
                        }
                        if (!finalUrl) return;
                        finalUrl = finalUrl.split('?')[0].split('#')[0];
                        if (seen.has(finalUrl)) return;
                        seen.add(finalUrl);

                        const summaryEl = block.querySelector('.space-txt, .fz-mid, .text-layout') || block;
                        let summary = (summaryEl.innerText || '').replace(title, '').trim();
                        summary = (summary || '（无摘要）').replace(/\s+/g, ' ').substring(0, 500);

                        let date = '未知';
                        const textAll = (block.innerText || '').replace(/\s+/g, ' ');
                        const dm = textAll.match(/\d{4}[/-]\d{1,2}[/-]\d{1,2}/) || textAll.match(/\d{4}年\d{1,2}月\d{1,2}日/);
                        if (dm) date = dm[0];

                        results.push({
                            title,
                            url: finalUrl,
                            longHref,
                            summary,
                            date,
                            author: '搜狗搜索',
                            isZhinengti: false,
                        });
                    });

                    return results;
                }
            """)

            items = items or []
            self.app._last_fetch_raw_count = len(items) if items else 0
            normalized = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                long_href = (it.get('longHref') or it.get('url', '') or '').strip()
                # ★ 兜底：万一 JS 那边漏了，这里再补一次
                if long_href.startswith('//'):
                    long_href = 'https:' + long_href
                elif long_href.startswith('/'):
                    long_href = 'https://www.sogou.com' + long_href
                final_url = (it.get('url') or '').strip()
                normalized.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=final_url,
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author=it.get('author', '搜狗搜索'),
                    content_url=long_href or final_url,
                    long_url=long_href or final_url,
                    is_zhinengti=False,
                ))
            return normalized
        except Exception as e:
            self.log(f"  [搜狗搜索错误] {type(e).__name__}: {e}")
            return []
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass