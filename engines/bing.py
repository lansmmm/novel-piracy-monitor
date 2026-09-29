# engines/bing.py
import time
import random
from urllib.parse import quote
from config import BLOCKED_DOMAINS, BLOCKED_URL_TOKENS
from engines.base import BaseEngine


class BingEngine(BaseEngine):
    SOURCE_ID = 'bing'
    SOURCE_LABEL = '必应'
    SEARCH_URL = 'https://cn.bing.com/search?q={kw}'
    EXTRA_URL = 'https://www.bing.com/webmaster/tools/contentremovalform'

    def _build_search_url(self, term, page_num):
        return f"https://www.bing.com/search?q={quote(term)}&first={page_num * 10 + 1}"

    def fetch(self, context, term, page_num=0, book_kw=''):
        results = []
        page = None
        try:
            url = self._build_search_url(term, page_num)
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2, 4))

            cur = (page.url or "").lower()
            body_text = (page.text_content('body') or '').lower()

            # ★ 放宽检测条件 + 无论后台/前台都先 log 一句
            hits = []
            if any(tok in cur for tok in ('captcha', 'verify', 'security', 'challenge')):
                hits.append(f"url={cur[:80]}")
            for kw in ('请解决以下难题', '请验证', '安全验证', '人机验证', 'verify you are human', 'unusual traffic'):
                if kw in body_text:
                    hits.append(f"body含「{kw}」")
                    break

            if hits:
                self.log(f"  [必应] 检测到验证码/安全验证（{'；'.join(hits)}）")
                self.pause_for_user("必应搜索要求验证码/安全验证")
                return results

            items = page.evaluate(r"""
                () => {
                    const results = [];
                    const seen = new Set();
                    const selectors = ['li.b_algo', 'div.b_algo', '.b_results > li'];
                    let elements = [];
                    for (const sel of selectors) {
                        elements = elements.concat(Array.from(document.querySelectorAll(sel)));
                    }

                    elements.forEach(el => {
                        const titleEl = el.querySelector('h2 a') || el.querySelector('a');
                        if (!titleEl) return;
                        const href = titleEl.getAttribute('href') || '';
                        if (!href || !href.startsWith('http')) return;
                        const title = (titleEl.innerText || '').trim();
                        if (!title || title.length < 3) return;
                        const cleanUrl = href.split('?')[0].split('#')[0];
                        if (seen.has(cleanUrl)) return;
                        seen.add(cleanUrl);

                        const summaryEl = el.querySelector('.b_caption p') || el.querySelector('p') || el.querySelector('.b_algoSlug');
                        let summary = summaryEl ? (summaryEl.innerText || '').trim() : '';
                        summary = summary.replace(/\s+/g, ' ').substring(0, 500);

                        let date = '未知';
                        const dateEl = el.querySelector('.b_attribution span') || el.querySelector('span.b_caption span');
                        if (dateEl) {
                            const dateText = dateEl.innerText || '';
                            const dateMatch = dateText.match(/\d{4}-\d{1,2}-\d{1,2}/) ||
                                              dateText.match(/\d{4}年\d{1,2}月\d{1,2}日/);
                            if (dateMatch) date = dateMatch[0];
                        }

                        results.push({
                            title,
                            url: href,
                            date,
                            author: '必应搜索',
                            summary: summary || '（无摘要）',
                            isZhinengti: false,
                        });
                    });
                    return results;
                }
            """)

            items = items or []
            # ★ 统一口径：原始 N = DOM 抓到的原始条数（过滤前），跟屏蔽规则无关
            self.app._last_fetch_raw_count = len(items) if items else 0

            filtered_items = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                url = (it.get('url') or '').lower()
                if any(d in url for d in BLOCKED_DOMAINS) or any(tok in url for tok in BLOCKED_URL_TOKENS):
                    continue
                filtered_items.append(it)
            normalized = []
            for it in filtered_items:
                normalized.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=it.get('url', ''),
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author=it.get('author', '必应搜索'),
                    content_url=it.get('url', ''),
                    long_url=it.get('url', ''),
                    is_zhinengti=False,
                ))
            return normalized

        except Exception as e:
            self.log(f"  [必应搜索错误] {type(e).__name__}: {e}")
            return results
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass



