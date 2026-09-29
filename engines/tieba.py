# engines/tieba.py
import time
import random
from urllib.parse import quote
from utils import title_or_summary_matches
from engines.base import BaseEngine

class TiebaEngine(BaseEngine):
    SOURCE_ID = 'tieba'
    SOURCE_LABEL = '百度贴吧'
    SEARCH_URL = 'https://tieba.baidu.com/f/search/res?ie=utf-8&qw={kw}'
    EXTRA_URL = 'https://newcopyright.baidu.com/'

    def fetch(self, context, term, page_num=0, book_kw=''):
        page = None
        try:
            url = f"https://tieba.baidu.com/f/search/res?ie=utf-8&qw={quote(term)}&pn={page_num}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="networkidle", timeout=40000)
            time.sleep(4)

            cur = (page.url or "").lower()
            if "wappass" in cur or "verify" in cur or "captcha" in cur or "passport" in cur:
                self.pause_for_user("百度贴吧要求验证码/登录")
                return []

            raw = page.evaluate(r"""
                () => {
                    const results = [];
                    const seen = new Set();
                    const blocks = document.querySelectorAll('.threadcardclass');
                    blocks.forEach(block => {
                        let title = '';
                        const titleEl = block.querySelector('.title-wrap span');
                        if (titleEl) title = (titleEl.innerText || '').trim();
                        if (!title || title.length < 5) return;
                        let href = '';
                        const aEl = block.querySelector('a[href*="/p/"]');
                        if (aEl) href = aEl.getAttribute('href') || '';
                        if (!href) return;
                        if (href.startsWith('//')) href = 'https:' + href;
                        else if (href.startsWith('/')) href = 'https://tieba.baidu.com' + href;
                        else if (!href.startsWith('http')) return;
                        const cleanUrl = href.split('?')[0].split('#')[0];
                        if (seen.has(cleanUrl)) return;
                        seen.add(cleanUrl);
                        let summary = '';
                        const absEl = block.querySelector('.abstract-wrap span');
                        if (absEl) summary = (absEl.innerText || '').trim();
                        if (!summary) summary = (block.innerText || '').replace(title, '').trim();
                        summary = summary.replace(/\s+/g, ' ').substring(0, 500);
                        let author = '百度贴吧';
                        const forumEl = block.querySelector('.forum-name-text');
                        if (forumEl) author = (forumEl.innerText || '').trim() || author;
                        let date = '未知';
                        const blockText = (block.innerText || '').replace(/\s+/g, ' ');
                        const dm = blockText.match(/发布于\s*(\d{4}[-\/]\d{1,2}[-\/]\d{1,2})/);
                        if (dm) date = dm[1];
                        results.push({ title, url: cleanUrl, date, author, summary });
                    });
                    return {
                        results: results,
                        containerCount: blocks.length,
                        bodyLen: document.body ? document.body.innerText.length : 0
                    };
                }
            """)

            if isinstance(raw, dict):
                self.log(f"  [贴吧] 卡片数={raw.get('containerCount')} body={raw.get('bodyLen')} 提取={len(raw.get('results', []))}")
                items = raw.get('results', [])
            else:
                items = []

            self.app._last_fetch_raw_count = len(items)

            filtered = []
            for it in items:
                title_text = it.get('title', '')
                summary_text = it.get('summary', '')
                if title_or_summary_matches(book_kw, title_text, summary_text):
                    filtered.append(it)
                else:
                    self.log(f"    ✗ 标题和摘要都不含书名：{title_text[:40]}")

            self.log(f"  [贴吧] 筛选后 {len(filtered)} 条")
            for it in filtered[:5]:
                self.log(f"    · {it['title'][:45]} | {it['date']} | {it['author']}")

            normalized = []
            for it in filtered:
                normalized.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=it.get('url', ''),
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author=it.get('author', '百度贴吧'),
                    content_url=it.get('url', ''),
                    long_url=it.get('url', ''),
                    is_zhinengti=False,
                ))
            return normalized
        except Exception as e:
            self.log(f"  [贴吧站内搜索错误] {type(e).__name__}: {e}")
            return []
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass