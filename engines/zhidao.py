# engines/zhidao.py
import time
import random
from urllib.parse import quote
from engines.base import BaseEngine

class ZhidaoEngine(BaseEngine):
    SOURCE_ID = 'zhidao'
    SOURCE_LABEL = '百度知道'
    SEARCH_URL = 'https://zhidao.baidu.com/search?word={kw}'
    EXTRA_URL = 'https://newcopyright.baidu.com/'

    def fetch(self, context, term, page_num=0, book_kw=''):
        results = []
        page = None
        try:
            url = f"https://zhidao.baidu.com/search?word={quote(term)}&pn={page_num * 10}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(3, 5))

            cur = (page.url or "").lower()
            if "wappass" in cur or "verify" in cur or "captcha" in cur or "passport" in cur:
                try:
                    page.close()
                except Exception:
                    pass
                self.pause_for_user("百度知道要求验证码/登录")
                return results

            items = page.evaluate(r"""
                () => {
                    const results = [];
                    const seen = new Set();
                    document.querySelectorAll('a').forEach(a => {
                        let href = a.getAttribute('href') || '';
                        if (!href.includes('/question/')) return;
                        if (!/\/question\/\d+/.test(href)) return;
                        if (href.startsWith('//')) href = 'https:' + href;
                        else if (href.startsWith('/')) href = 'https://zhidao.baidu.com' + href;
                        else if (!href.startsWith('http')) return;
                        const cleanUrl = href.split('?')[0].split('#')[0];
                        if (seen.has(cleanUrl)) return;
                        seen.add(cleanUrl);
                        const title = (a.innerText || '').trim();
                        if (!title || title.length < 5) return;
                        let container = a;
                        for (let i = 0; i < 6; i++) {
                            if (!container.parentElement) break;
                            container = container.parentElement;
                            if ((container.innerText || '').length > title.length + 30) break;
                        }
                        let summary = (container.innerText || '').replace(title, '').trim();
                        summary = summary.replace(/\s+/g, ' ').substring(0, 500);
                        let date = '未知';
                        const absDate = summary.match(/\d{4}-\d{1,2}-\d{1,2}/) || summary.match(/\d{4}年\d{1,2}月\d{1,2}日/);
                        if (absDate) date = absDate[0];
                        else {
                            const relDate = summary.match(/\d+\s*(?:秒|分钟|小时|天|周|个月|年)\s*之?前|刚刚|昨天|前天|今天/);
                            if (relDate) date = relDate[0];
                        }
                        results.push({ title, url: cleanUrl, date, author: '百度知道',
                                       summary: summary || '（无摘要）' });
                    });
                    return results;
                }
            """)
            self.app._last_fetch_raw_count = len(items) if items else 0
            normalized = []
            for it in items:
                normalized.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=it.get('url', ''),
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author=it.get('author', '百度知道'),
                    content_url=it.get('url', ''),
                    long_url=it.get('url', ''),
                    is_zhinengti=False,
                ))
            return normalized
        except Exception as e:
            self.log(f"  [知道站内搜索错误] {type(e).__name__}: {e}")
            return results
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass