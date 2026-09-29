# engines/baidu.py
import re
import time
import random
from urllib.parse import quote, urlparse
from config import BLOCKED_DOMAINS
from utils import title_matches, title_or_summary_matches
from engines.base import BaseEngine

class BaiduEngine(BaseEngine):
    SOURCE_ID = 'baidu'
    SOURCE_LABEL = '百度'
    SEARCH_URL = 'https://www.baidu.com/s?wd={kw}'
    EXTRA_URL = 'https://newcopyright.baidu.com/'

    def fetch(self, context, term, page_num=0, book_kw=''):
        results = []
        page = None
        try:
            url = f"https://www.baidu.com/s?wd={quote(term)}&pn={page_num * 10}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(3, 5))

            cur = (page.url or "").lower()
            if "wappass" in cur or "verify" in cur or "captcha" in cur:
                # ★ 不关页面：有头模式下把验证页留给用户手动处理
                self.pause_for_user("百度要求验证码/登录")
                return results

            items = page.evaluate(r"""
                () => {
                    const results = [];
                    const blocks = document.querySelectorAll('div.result, div.c-container, div.result-op');
                    blocks.forEach(block => {
                        const text = block.innerText || '';
                        const titleEl = block.querySelector('h3 a, a.c-title-text');
                        const title = titleEl ? titleEl.innerText.trim() : '';
                        if (!title) return;

                        // ★ 从 data-feedback 拿真实 URL（百度在结果卡片里嵌了 JSON）
                        let href = '';
                        const fbEl = block.querySelector('[data-feedback]');
                        if (fbEl) {
                            try {
                                const fbData = JSON.parse(fbEl.getAttribute('data-feedback'));
                                href = (fbData && fbData.url) ? String(fbData.url).trim() : '';
                            } catch (e) {
                                href = '';
                            }
                        }
                        if (!href || !href.startsWith('http')) return;
                        const kws = ['智能分身', '实时回复', '文心智能体', 'AI生成', 'AI 生成'];
                        let isZhinengti = false;
                        for (const k of kws) { if (text.includes(k)) { isZhinengti = true; break; } }
                        let summary = text;
                        if (title) summary = summary.split(title).join(' ');
                        summary = summary.replace(/\s+/g, ' ').trim();
                        if (summary.length > 500) summary = summary.substring(0, 500);
                        let author = '';
                        if (isZhinengti) {
                            const lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
                            let titleIdx = -1;
                            for (let i = 0; i < lines.length; i++) {
                                if (/智能分身|实时回复|文心智能体|AI生成|AI 生成/.test(lines[i])) {
                                    titleIdx = i; break;
                                }
                            }
                            author = '文心';
                            let authorLine = '';
                            if (titleIdx >= 0 && titleIdx + 1 < lines.length) {
                                const nextLine = lines[titleIdx + 1];
                                const greetMatch = nextLine.match(/(您好|你好|哈喽|哈啰|Hello|Hi|嗨|嘿嘿|我是|I'm|I am)/i);
                                if (greetMatch && greetMatch.index > 0) {
                                    const cand = nextLine.substring(0, greetMatch.index).trim();
                                    if (cand.length >= 2 && cand.length <= 25) {
                                        author = cand; authorLine = cand;
                                    }
                                } else if (!greetMatch) {
                                    if (nextLine.length >= 2 && nextLine.length <= 20
                                        && !/[，。！？；：、,.!?;:]/.test(nextLine)) {
                                        author = nextLine; authorLine = nextLine;
                                    }
                                }
                            }
                            if (authorLine) summary = summary.split(authorLine).join(' ').trim();
                        } else {
                            const domainMatch = text.match(/\b((?:[a-zA-Z0-9\-]+\.)+[a-zA-Z]{2,})\b/);
                            author = domainMatch ? domainMatch[1] : '盗版网站';
                        }
                        results.push({ title, url: href, author, summary, isZhinengti });
                    });
                    return results;
                }
            """)

            self.app._last_fetch_raw_count = len(items) if items else 0
            if not items: return results

            for it in items:
                title_text = it.get('title', '') or ''
                summary_text = it.get('summary', '') or ''
                if book_kw and not title_or_summary_matches(book_kw, title_text, summary_text):
                    continue

                raw_url = it.get('url') or ''

                # 白名单命中 → 跳过解析
                if raw_url and raw_url in self.app.whitelist_common:
                    self.log(f"    ⚪ 白名单命中，跳过解析: {raw_url[:80]}")
                    it['real_url'] = raw_url
                    it['redirects'] = []
                    date = '未知'
                    m = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日)', it['summary'])
                    if m: date = m.group(1)
                    else:
                        m2 = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', it['summary'])
                        if m2: date = m2.group(1)
                    it['date'] = date
                    results.append(self.normalize_result(
                        source=self.SOURCE_ID,
                        title=it.get('title', ''),
                        url=raw_url,
                        summary=it.get('summary', ''),
                        date=date,
                        author=it.get('author', '百度搜索'),
                        content_url=raw_url,
                        long_url=raw_url,
                        is_white=True,
                        is_zhinengti=bool(it.get('isZhinengti', False)),
                    ))
                    continue

                real_url = raw_url
                it['real_url'] = real_url
                it['redirects'] = []

                low = (real_url or '').lower()
                if any(d in low for d in BLOCKED_DOMAINS):
                    self.log(f"    🚫 屏蔽域名: {real_url[:80]}")
                    continue

                if not it.get('isZhinengti') and real_url and 'baidu.com/link?' not in real_url:
                    try:
                        host = (urlparse(real_url).hostname or '').lower()
                        if host.startswith('www.'): host = host[4:]
                        if host and 'baidu.com' not in host: it['author'] = host
                    except Exception: pass

                date = '未知'
                m = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日)', it['summary'])
                if m: date = m.group(1)
                else:
                    m2 = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', it['summary'])
                    if m2: date = m2.group(1)
                it['date'] = date
                results.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=real_url or raw_url,
                    summary=it.get('summary', ''),
                    date=date,
                    author=it.get('author', '百度搜索'),
                    content_url=raw_url,
                    long_url=real_url or raw_url,
                    is_zhinengti=bool(it.get('isZhinengti', False)),
                ))

            return results
        except Exception as e:
            self.log(f"  [百度搜索错误] {type(e).__name__}: {e}")
            return results
        finally:
            try:
                if page: page.close()
            except Exception: pass