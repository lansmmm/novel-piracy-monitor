import random
import time
from urllib.parse import quote

from engines.base import BaseEngine


class SogouMobileEngine(BaseEngine):
    SOURCE_ID = 'sogou_mobile'
    SOURCE_LABEL = '搜狗移动'
    SEARCH_URL = 'https://m.sogou.com/web/searchList.jsp?keyword={kw}'
    EXTRA_URL = 'https://mail.qq.com/'

    def fetch(self, context, term, page_num=0, book_kw=''):
        page = None
        try:
            # ★ 第 1 页：正常打开
            url = f"https://m.sogou.com/web/searchList.jsp?keyword={quote(term)}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2, 4))

            # ★ 第 2 页及以后：找到「下一页」按钮并点击
            for _ in range(page_num):
                try:
                    next_btn = page.locator('a:has-text("下一页"), .next-page, a.next')
                    if next_btn.count() > 0:
                        next_btn.first.click()
                        page.wait_for_timeout(random.uniform(2000, 3500))
                    else:
                        break
                except Exception as e:
                    self.log(f"  [搜狗移动] 点击下一页失败：{e}")
                    break

            # ---- 验证码检测 ----
            cur = (page.url or '').lower()
            body_text = (page.text_content('body') or '').lower()
            if ('captcha' in cur or 'verify' in cur or 'antispider' in cur
                    or '验证码' in body_text or '人机验证' in body_text):
                self.app._last_fetch_raw_count = 0
                self.pause_for_user('搜狗移动搜索要求验证码/登录')
                return []

            # ---- 抓取结果 ----
            items = page.evaluate(r"""
                () => {
                    const absUrl = (raw) => {
                        if (!raw) return '';
                        let v = String(raw).trim();
                        if (!v || v.startsWith('javascript:') || v.startsWith('#')) return '';
                        if (v.startsWith('//')) v = 'https:' + v;
                        if (v.startsWith('/')) v = 'https://m.sogou.com' + v;
                        return v.startsWith('http') ? v : '';
                    };

                    const results = [];
                    const seen = new Set();

                    // 移动版结果卡片常见的几种容器，多套几个选择器兜底
                    const selectors = [
                        '.vrwrap',
                        '.result',
                        '.rb',
                        'section.result',
                        'div[class*="result"]',
                    ];
                    let blocks = [];
                    for (const sel of selectors) {
                        const found = Array.from(document.querySelectorAll(sel));
                        if (found.length > blocks.length) blocks = found;
                    }

                    for (const block of blocks) {
                        const titleEl = block.querySelector('h3 a[href]')
                                     || block.querySelector('a[href]');
                        if (!titleEl) continue;

                        const title = (titleEl.innerText || titleEl.textContent || '')
                                        .replace(/\s+/g, ' ').trim();
                        if (!title || title.length < 3) continue;

                        // 搜狗返回的可能是相对路径 /link?url=...，用 .href 让浏览器补绝对地址
                        let longHref = (titleEl.href || titleEl.getAttribute('href') || '').trim();
                        if (longHref.startsWith('/')) longHref = 'https://m.sogou.com' + longHref;

                        // 真实落地页：优先看 data-url，其次看外链
                        let finalUrl = '';
                        const duEls = block.querySelectorAll('[data-url]');
                        for (const el of duEls) {
                            const v = absUrl(el.getAttribute('data-url'));
                            if (v) { finalUrl = v; break; }
                        }
                        if (!finalUrl && longHref.startsWith('http')
                                && !longHref.includes('sogou.com/link')) {
                            finalUrl = longHref;
                        }
                        if (!finalUrl) continue;
                        finalUrl = finalUrl.split('?')[0].split('#')[0];
                        if (seen.has(finalUrl)) continue;
                        seen.add(finalUrl);

                        const sumEl = block.querySelector('.space-txt, .fz-mid, .text-layout, .str-text-info, .str_info, p');
                        let summary = sumEl ? (sumEl.innerText || '').replace(title, '').trim() : '';
                        if (!summary) summary = (block.innerText || '').replace(title, '').trim();
                        summary = (summary || '（无摘要）').replace(/\s+/g, ' ').substring(0, 500);

                        let date = '未知';
                        const textAll = (block.innerText || '').replace(/\s+/g, ' ');
                        const dm = textAll.match(/\d{4}[/-]\d{1,2}[/-]\d{1,2}/)
                                || textAll.match(/\d{4}年\d{1,2}月\d{1,2}日/);
                        if (dm) date = dm[0];

                        results.push({
                            title,
                            url: finalUrl,
                            longHref,
                            summary,
                            date,
                            author: '搜狗移动搜索',
                        });
                    }
                    return results;
                }
            """)

            # ★ 统一口径：原始 N = DOM 抓到的原始条数（过滤前）
            items = items or []
            self.app._last_fetch_raw_count = len(items)

            normalized = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                long_href = (it.get('longHref') or it.get('url', '') or '').strip()
                # 兜底补全
                if long_href.startswith('//'):
                    long_href = 'https:' + long_href
                elif long_href.startswith('/'):
                    long_href = 'https://m.sogou.com' + long_href
                final_url = (it.get('url') or '').strip()
                normalized.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=final_url,
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author=it.get('author', '搜狗移动搜索'),
                    content_url=long_href or final_url,
                    long_url=long_href or final_url,
                    is_zhinengti=False,
                ))
            return normalized

        except Exception as e:
            self.log(f"  [搜狗移动搜索错误] {type(e).__name__}: {e}")
            return []
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass