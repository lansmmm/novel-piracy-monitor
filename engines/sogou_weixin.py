import random
import re
import time
from urllib.parse import quote

from engines.base import BaseEngine
from utils import title_or_summary_matches


class SogouWeixinEngine(BaseEngine):
    SOURCE_ID = 'sogou_weixin'
    SOURCE_LABEL = '微信'
    SEARCH_URL = 'https://weixin.sogou.com/weixin?type=2&query={kw}'
    EXTRA_URL = 'https://mp.weixin.qq.com/'

    @staticmethod
    def _normalize_weixin_link(raw_href):
        """把搜狗微信的 /link?url=... 补成绝对地址。

        ★ 注意：url= 后面是加密 token，不是真实网址，绝不能当 URL 解出来。
        """
        if not raw_href:
            return ''
        href = str(raw_href).strip()
        if not href:
            return ''
        if href.startswith('https:/link?'):
            href = href.replace('https:/link?', 'https://weixin.sogou.com/link?')
        if href.startswith('/link?'):
            href = 'https://weixin.sogou.com' + href
        if href.startswith('//'):
            href = 'https:' + href
        return href

    @staticmethod
    def _js_var(html, name):
        m = re.search(r'var\s+' + re.escape(name) + r'\s*=\s*"([^"]*)"', html or '')
        return (m.group(1) or '').strip() if m else ''

    def _resolve_weixin_article(self, context, link_url):
        """打开 /link 页，拿到真实文章地址。

        /link 其实是个 JS 模板页，里面带着 biz / mid / idx / sn 变量：
        先等它自己跳到 mp.weixin.qq.com，等不到就用这些变量拼出文章地址。
        """
        page = None
        try:
            page = context.new_page()
            page.goto(link_url, referer='https://weixin.sogou.com/',
                      wait_until='domcontentloaded', timeout=20000)
            try:
                page.wait_for_url(
                    lambda u: 'mp.weixin.qq.com' in (u or ''), timeout=6000)
            except Exception:
                pass

            final = page.url or ''
            if 'mp.weixin.qq.com' in final:
                return final

            html = page.content() or ''
            biz = self._js_var(html, 'biz')
            mid = self._js_var(html, 'mid')
            idx = self._js_var(html, 'idx')
            sn = self._js_var(html, 'sn')
            if biz and mid:
                url = f"https://mp.weixin.qq.com/s?__biz={biz}&mid={mid}&idx={idx or '1'}"
                if sn:
                    url += f"&sn={sn}"
                return url
            return ''
        except Exception as e:
            self.log(f"  [微信文章地址解析失败] {type(e).__name__}: {e}")
            return ''
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass

    def fetch(self, context, term, page_num=0, book_kw=''):
        page = None
        try:
            url = f"https://weixin.sogou.com/weixin?query={quote(term)}&type=2&ie=utf8&page={page_num + 1}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2, 4))

            cur = (page.url or '').lower()
            body_text = (page.text_content('body') or '').lower()
            if 'captcha' in cur or 'verify' in cur or '验证码' in body_text or '人机验证' in body_text:
                self.pause_for_user('搜狗微信搜索要求验证码/登录')
                return []

            items = page.evaluate(r"""
                () => {
                    const pickHref = (el) => {
                        if (!el) return '';
                        const cand = [
                            el.getAttribute('href'),
                            el.getAttribute('data-url'),
                            el.getAttribute('data-link'),
                            el.getAttribute('data-href'),
                            el.getAttribute('url'),
                        ].find(v => !!v && !String(v).startsWith('javascript:'));
                        if (!cand) return '';
                        let v = String(cand).trim();
                        if (!v) return '';
                        if (v.startsWith('//')) v = 'https:' + v;
                        if (v.startsWith('/')) v = 'https://weixin.sogou.com' + v;
                        return v.startsWith('http') ? v : '';
                    };

                    // 结果条目固定是 ul.news-list 下的 li，标题在 .txt-box h3 a
                    let blocks = Array.from(document.querySelectorAll('ul.news-list > li'));
                    if (!blocks.length) {
                        blocks = Array.from(document.querySelectorAll('.news-list > li, .news-box > li, .txt-box'));
                    }
                    const results = [];
                    const seen = new Set();

                    blocks.forEach(block => {
                        const titleEl = (block.querySelector && block.querySelector('.txt-box h3 a[href], h3 a[href], .tit a[href]')) || null;
                        if (!titleEl) return;
                        const title = ((titleEl.innerText || titleEl.textContent || '').replace(/\s+/g, ' ').trim());
                        if (!title || title.length < 3) return;

                        const href = pickHref(titleEl);
                        if (!href) return;
                        // 没有 /link 的，只接受外部真实文章链接，排掉搜狗自家导航（更多>>、搜索帮助、免责声明等）
                        if (href.indexOf('/link?') < 0 && href.toLowerCase().indexOf('sogou.com') >= 0) return;
                        if (href.includes('weixin.sogou.com/weixin')) return;
                        if (seen.has(href)) return;
                        seen.add(href);

                        const summaryEl = (block.querySelector && (block.querySelector('.txt-info, .txt-box .txt, .summary, .brief, .desc') || block)) || block;
                        let summary = (summaryEl.innerText || '').replace(title, '').trim();
                        summary = (summary || '（无摘要）').replace(/\s+/g, ' ').substring(0, 500);

                        let date = '未知';
                        const textAll = (block.innerText || '').replace(/\s+/g, ' ');
                        const dm = textAll.match(/\d{4}[/-]\d{1,2}[/-]\d{1,2}/) || textAll.match(/\d{4}年\d{1,2}月\d{1,2}日/);
                        if (dm) date = dm[0];

                        results.push({ title, url: href, summary, date, author: '微信' });
                    });

                    const dedup = [];
                    const seen2 = new Set();
                    results.forEach(item => {
                        // ★ 用完整 URL 去重：/link?url=<token> 里 token 才是条目标识，
                        //   截掉查询串会把所有文章压成同一条，看起来就像「抓不到结果」
                        const key = item.url;
                        if (!seen2.has(key)) {
                            seen2.add(key);
                            dedup.push(item);
                        }
                    });
                    return dedup.slice(0, 20);
                }
            """)

            raw_items = list(items or [])
            self.app._last_fetch_raw_count = len(raw_items)

            # 先按书名过滤，再解析文章地址（解析要开新页面，很慢）
            if book_kw:
                todo = [it for it in raw_items
                        if title_or_summary_matches(
                            book_kw, it.get('title', ''), it.get('summary', ''))]
            else:
                todo = raw_items

            normalized = []
            for it in todo:
                link_url = self._normalize_weixin_link(it.get('url', ''))
                if 'weixin.sogou.com/link' in link_url.lower():
                    final_url = self._resolve_weixin_article(context, link_url) or link_url
                else:
                    final_url = link_url or it.get('url', '')

                normalized.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=final_url,
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author=it.get('author', '微信'),
                    content_url=link_url or final_url,
                    long_url=link_url or final_url,
                    is_zhinengti=False,
                ))
            return normalized
        except Exception as e:
            self.log(f"  [搜狗微信搜索错误] {type(e).__name__}: {e}")
            return []
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass
