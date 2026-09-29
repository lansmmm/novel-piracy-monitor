# engines/quark.py
"""夸克（神马）移动端网页搜索。

结构参照头条手机端引擎：
- 命中验证码/风控页时，保留页面并调用 pause_for_user（有头模式下等你手动过验证）
- 结果卡片用多套选择器兜底，尽量不因改版而抓空
- 最终网址 = 落地页；源链接 = 卡片里的原始链接（若为跳转链则原样保留）
"""
import random
import time
from urllib.parse import quote, urlparse, parse_qs, unquote

from config import BLOCKED_DOMAINS
from engines.base import BaseEngine


class QuarkEngine(BaseEngine):
    SOURCE_ID = 'quark'
    SOURCE_LABEL = '夸克'
    SEARCH_URL = 'https://quark.sm.cn/s?q={kw}'
    FETCH_URL = ''                      # 实际抓取网址；留空就用上面的 SEARCH_URL
    HOME_BASE = 'https://quark.sm.cn'   # 补全相对链接用（子类改成自己的域名）
    EXTRA_URL = 'https://ipp.quark.cn/#/home'

    # 风控/验证码特征
    CAPTCHA_URL_TOKENS = ('_____tmd_____', '/punish', 'captcha', 'verify', 'wappass')
    CAPTCHA_TEXT_TOKENS = ('验证码拦截', '安全验证', '人机验证', '请完成验证', '滑块验证')

    # 需要登录的特征：夸克没登录时会「假装」没搜到 —— 页面只有一个登录框 +
    # 「抱歉，未找到相关内容！」，以前这种情况一声不吭，现在会提醒去浏览器登录一次
    LOGIN_TEXT_TOKENS = ('请先登录', '登录后查看', '登录后继续', '请登录',
                         '扫码登录', '账号登录', '登录后即可')
    NO_RESULT_TEXT_TOKENS = ('未找到相关内容', '没有找到相关', '暂无相关结果')

    # 这一段在页面里找「登录框 / 登录弹窗 / 有没有结果」
    LOGIN_STATE_JS = r"""
        () => {
            const body = (document.body.innerText || '');
            const noResult = ['未找到相关内容', '没有找到相关', '暂无相关结果']
                .some(t => body.includes(t));
            let loginBox = '';
            let dialog = '';
            document.querySelectorAll('[class*="login"], [id*="login"]').forEach(el => {
                if (!el.offsetParent) return;                 // 看不见的不算
                const cls = String(el.className || '');
                const t = (el.innerText || '').trim();
                if (/dialog|modal|popup|passport/i.test(cls)) { if (!dialog) dialog = cls; return; }
                if (!loginBox && t && t.length <= 20 && t.includes('登录')) loginBox = cls;
            });
            const resultCount = document.querySelectorAll(
                '.result, .result-item, .c-result, .c-result-content, article').length;
            return {noResult: noResult, loginBox: loginBox, dialog: dialog, resultCount: resultCount};
        }
    """

    def _need_login(self, info, body_text):
        """夸克是不是没登录（没登录就搜不出东西）"""
        text = body_text or ''
        if any(tok in text for tok in self.LOGIN_TEXT_TOKENS):
            return True
        info = info or {}
        if info.get('dialog'):
            return True
        # 登录框在 + 一条结果都没有 → 基本就是没登录
        return bool(info.get('loginBox')) and (
            info.get('noResult') or not info.get('resultCount'))

    @staticmethod
    def _decode_real_url(href):
        """尝试从跳转链里解出真实落地页（url= / target= / u= 参数）"""
        if not href:
            return ''
        href = href.strip()
        if href.startswith('//'):
            href = 'https:' + href
        for _ in range(5):
            try:
                u = urlparse(href)
                qs = parse_qs(u.query)
                nxt = ''
                for k in ('url', 'target', 'u', 'to'):
                    v = (qs.get(k) or [''])[0]
                    if v:
                        nxt = v
                        break
                if not nxt:
                    break
                nxt = unquote(nxt)
                if not nxt.startswith('http') or nxt == href:
                    break
                href = nxt
            except Exception:
                break
        return href

    def fetch(self, context, term, page_num=0, book_kw=''):
        results = []
        page = None
        try:
            offset = max(0, int(page_num or 0)) * 10
            tpl = self.FETCH_URL or self.SEARCH_URL
            url = tpl.format(kw=quote(term)) + f"&offset={offset}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(3, 5))

            cur = (page.url or '').lower()
            body_text = (page.text_content('body') or '')
            title_now = (page.title() or '')
            if (any(tok in cur for tok in self.CAPTCHA_URL_TOKENS)
                    or any(tok in body_text for tok in self.CAPTCHA_TEXT_TOKENS)
                    or any(tok in title_now for tok in self.CAPTCHA_TEXT_TOKENS)):
                # ★ 不关页面：有头模式下留给用户手动过验证
                self.app._last_fetch_raw_count = 0
                self.pause_for_user('夸克要求验证码/登录')
                return results

            # ★ 需要登录的检测：夸克没登录时会假装没搜到（只有登录框），必须提醒
            try:
                login_state = page.evaluate(self.LOGIN_STATE_JS)
            except Exception:
                login_state = {}
            if self._need_login(login_state, body_text):
                self.app._last_fetch_raw_count = 0
                self.pause_for_user('夸克要求登录（请在浏览器里登录一次）')
                return results

            items = page.evaluate(r"""
                (homeBase) => {
                    const selectorSet = [
                        '.result', '.result-item', '.c-result', '.c-result-content',
                        '.card', '.card-item', '.item', '.search-result', 'article', 'section',
                    ];
                    const blocks = new Map();
                    selectorSet.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => blocks.set(el, true));
                    });
                    const items = [];
                    const seenTitles = new Set();

                    blocks.forEach((_, block) => {
                        const links = Array.from(block.querySelectorAll('a[href]'));
                        let main = null;
                        for (const a of links) {
                            const t = (a.innerText || '').replace(/\s+/g, ' ').trim();
                            if (t.length >= 6) { main = a; break; }
                        }
                        if (!main) return;

                        const title = (main.innerText || main.textContent || '').replace(/\s+/g, ' ').trim();
                        if (!title || title.length < 4) return;
                        if (seenTitles.has(title)) return;
                        seenTitles.add(title);

                        let href = (main.getAttribute('href') || '').trim();
                        if (!href || href.startsWith('javascript:')) return;
                        if (href.startsWith('//')) href = 'https:' + href;
                        else if (href.startsWith('/')) href = homeBase + href;
                        if (!href.startsWith('http')) return;
                        if (href.includes('/s?q=') || href.includes('quark.cn/s?')) return;

                        const txt = (block.innerText || '').replace(/\s+/g, ' ').trim();
                        if (!txt || txt.length < 12) return;

                        let date = '未知';
                        const dm = txt.match(/\d{4}[/-]\d{1,2}[/-]\d{1,2}/)
                                || txt.match(/\d{4}年\d{1,2}月\d{1,2}日/);
                        if (dm) date = dm[0];

                        const summary = txt.split(title).join(' ').replace(/\s+/g, ' ').trim().substring(0, 400);
                        items.push({ title, href, summary: summary || '（无摘要）', date });
                    });

                    if (items.length === 0) {
                        const fallback = [];
                        document.querySelectorAll('a[href]').forEach(a => {
                            const t = (a.innerText || '').replace(/\s+/g, ' ').trim();
                            let href = (a.getAttribute('href') || '').trim();
                            if (!t || t.length < 6) return;
                            if (href.startsWith('//')) href = 'https:' + href;
                            if (!href.startsWith('http')) return;
                            if (href.includes('/s?q=')) return;
                            fallback.push({ title: t, href, summary: '（无摘要）', date: '未知' });
                        });
                        return fallback.slice(0, 15);
                    }

                    return items;
                }
            """, self.HOME_BASE)

            self.app._last_fetch_raw_count = len(items) if items else 0
            if not items:
                self.log("  ⚠️ 夸克这一页没抓到结果（可能被风控或需要登录）；"
                         "一直抓不到就切有头模式在浏览器里登录一次")
                return results

            seen = set()
            for it in items:
                long_href = (it.get('href') or '').strip()
                real = self._decode_real_url(long_href)
                if not real or not real.startswith('http'):
                    continue
                low = real.lower()
                if any(d in low for d in BLOCKED_DOMAINS):
                    self.log(f"    🚫 屏蔽域名: {real[:80]}")
                    continue
                if low in seen:
                    continue
                seen.add(low)

                try:
                    host = (urlparse(real).hostname or '').lower()
                    if host.startswith('www.'):
                        host = host[4:]
                    author = host or '夸克搜索'
                except Exception:
                    author = '夸克搜索'

                nr = self.normalize_result(
                    source=self.SOURCE_ID,
                    title=it.get('title', ''),
                    url=real,
                    summary=it.get('summary', ''),
                    date=it.get('date', '未知'),
                    author=author,
                    is_zhinengti=False,
                )
                if long_href:
                    nr['content_url'] = long_href
                    nr['long_url'] = long_href
                results.append(nr)

            return results
        except Exception as e:
            self.log(f"  [夸克搜索错误] {type(e).__name__}: {e}")
            return results
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass
