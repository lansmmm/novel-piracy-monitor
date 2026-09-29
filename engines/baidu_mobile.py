# engines/baidu_mobile.py
"""百度手机版（m.baidu.com/s?word=书名）。

和百度 PC 版（baidu.py）的区别：
- 结果卡片是 div.c-result，卡片自带的 data-log 里就写着真实落地页（mu 字段），
  不用像 PC 版那样一条条点开跳转链，快很多、也不容易被风控
- 只有极少数卡片没带 mu 时，才退回去点开链接解析

常规模式搜手机版，深度模式再加搜 PC 版（见 config.SOURCE_GROUPS）。
"""
import json
import random
import re
import time
from urllib.parse import quote, unquote, urlparse

from config import BLOCKED_DOMAINS, BASE_DIR
from engines.base import BaseEngine
from utils import title_or_summary_matches


class BaiduMobileEngine(BaseEngine):
    SOURCE_ID = 'baidu_mobile'
    SOURCE_LABEL = '百度m'
    SEARCH_URL = 'https://m.baidu.com/s?word={kw}'
    EXTRA_URL = 'https://newcopyright.baidu.com/'

    # 风控/验证码特征
    CAPTCHA_URL_TOKENS = ('wappass', 'verify', 'captcha', '/punish', 'sec-bot')
    CAPTCHA_TEXT_TOKENS = ('安全验证', '人机验证', '请完成验证', '滑块验证', '验证码拦截')
    # 广告卡片（品牌广告）不要
    AD_TOKENS = ('品牌广告',)

    def _resolve_link(self, context, url):
        """备用：点开跳转链，看最终落到哪个网址（正常用不上，卡片里就带着真实网址）"""
        if not url or 'baidu.com' not in url:
            return url
        p = None
        try:
            p = self._create_stealth_page(context)
            p.goto(url, wait_until="domcontentloaded", timeout=12000)
            time.sleep(1.5)
            return p.url
        except Exception:
            return url
        finally:
            if p:
                try:
                    p.close()
                except Exception:
                    pass

    @staticmethod
    def _real_url_from_click_info(raw):
        """从卡片的 data-click-info 里抠出真实网址（百度把原网址转义了两层）"""
        if not raw:
            return ''
        try:
            info = json.loads(raw)
            extra = info.get('extra')
            if isinstance(extra, str):
                extra = json.loads(extra)
            val = (extra or {}).get('url') or ''
        except Exception:
            m = re.search(r'url[^"]{0,40}?((?:https?%3A|https%253A)[^"\\]+)', raw)
            val = m.group(1) if m else ''
        if not val:
            return ''
        for _ in range(3):
            dec = unquote(val)
            if dec == val:
                break
            val = dec
        return val if val.lower().startswith('http') else ''

    def fetch(self, context, term, page_num=0, book_kw=''):
        results = []
        page = None
        try:
            url = f"https://m.baidu.com/s?word={quote(term)}&pn={page_num * 10}"
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(3, 5))

            cur = (page.url or '').lower()
            body_text = (page.text_content('body') or '')
            captcha_hit = any(tok in cur for tok in self.CAPTCHA_URL_TOKENS)
            if not captcha_hit and any(tok in body_text for tok in self.CAPTCHA_TEXT_TOKENS):
                # 有结果卡片就说明页面是正常的（正常页面正文里偶尔也会出现「安全验证」字样）
                try:
                    cards = page.evaluate(
                        "() => document.querySelectorAll('div.c-result, div.result').length")
                except Exception:
                    cards = 0
                captcha_hit = not cards
            if captcha_hit:
                # ★ 不关页面：有头模式下把验证页留给用户手动处理
                self.app._last_fetch_raw_count = 0
                self.pause_for_user("百度手机版要求验证码/登录")
                return results

            # ★ 异常页面诊断：原始结果异常少时存截图 + HTML
            try:
                import os
                from datetime import datetime
                debug_dir = os.path.join(BASE_DIR, "debug")
                os.makedirs(debug_dir, exist_ok=True)
                # 先快速数一下结果卡片有多少个
                _card_cnt = page.evaluate(
                    "() => document.querySelectorAll('.result, .result-op, [class*=\"result\"]').length"
                )
                if _card_cnt < 3:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    safe = re.sub(r'[\\/:*?"<>|]', '_', term)[:40]
                    png = os.path.join(debug_dir, f"baidu_{safe}_p{page_num}_{ts}.png")
                    html = os.path.join(debug_dir, f"baidu_{safe}_p{page_num}_{ts}.html")
                    page.screenshot(path=png, full_page=True)
                    with open(html, "w", encoding="utf-8") as f:
                        f.write(page.content())
                    self.log(f"  📸 结果异常少（{_card_cnt} 个卡片），已存截图+HTML: {png}")
            except Exception as _e:
                self.log(f"  ⚠️ 诊断截图失败：{_e}")

            items = page.evaluate(r"""
                () => {
                    const items = [];
                    const blocks = document.querySelectorAll('div.c-result, div.result, div.c-container');
                    blocks.forEach(block => {
                        // 「大家还在搜」这类聚合卡片不是正文结果，跳过
                        const tpl = block.getAttribute('tpl') || '';
                        if (tpl === 'nvl_recommend_list') return;

                        const titleEl = block.querySelector('h3')
                                      || block.querySelector('[class*="title"]');
                        const title = titleEl ? (titleEl.innerText || '').replace(/\s+/g, ' ').trim() : '';
                        if (!title) return;
                        if (title.indexOf('大家还在搜') === 0 || title.indexOf('大家还在看') === 0) return;

                        // 百度自己把真实网址存在卡片的 data-log 里（mu 字段）
                        let mu = '';
                        const dlog = block.getAttribute('data-log') || '';
                        if (dlog) {
                            try {
                                mu = JSON.parse(dlog.replace(/&quot;/g, '"')).mu || '';
                            } catch (e) {
                                const m = dlog.match(/"mu"\s*:\s*"([^"]+)"/);
                                if (m) mu = m[1];
                            }
                        }

                        const a = block.querySelector('a[href]');
                        let href = a ? (a.getAttribute('href') || '') : '';
                        if (href.startsWith('//')) href = 'https:' + href;
                        else if (href.startsWith('/')) href = 'https://m.baidu.com' + href;

                        const txt = (block.innerText || '').replace(/\s+/g, ' ').trim();
                        let date = '未知';
                        const dm = txt.match(/\d{4}年\d{1,2}月\d{1,2}日/)
                                || txt.match(/\d{4}-\d{1,2}-\d{1,2}/);
                        if (dm) date = dm[0];

                        let isZhinengti = false;
                        const kws = ['智能分身', '实时回复', '文心智能体', 'AI生成', 'AI 生成'];
                        for (const k of kws) { if (txt.includes(k)) { isZhinengti = true; break; } }

                        let summary = txt.split(title).join(' ').replace(/\s+/g, ' ').trim();
                        if (summary.length > 400) summary = summary.substring(0, 400);

                        items.push({
                            title: title,
                            mu: mu,
                            href: href,
                            clickInfo: block.getAttribute('data-click-info') || '',
                            text: txt,
                            summary: summary,
                            date: date,
                            isZhinengti: isZhinengti
                        });
                    });
                    return items;
                }
            """)

            self.app._last_fetch_raw_count = len(items) if items else 0
            if not items:
                return results

            for it in items:
                text = it.get('text', '') or ''
                if any(tok in text for tok in self.AD_TOKENS):
                    continue

                title_text = it.get('title', '') or ''
                summary_text = it.get('summary', '') or ''
                if book_kw and not title_or_summary_matches(book_kw, title_text, summary_text):
                    continue

                raw_url = (it.get('mu') or '').strip()
                if raw_url and not raw_url.lower().startswith('http'):
                    raw_url = ''          # javascript:void(0) 这类假链接不要
                if not raw_url:
                    raw_url = self._real_url_from_click_info(it.get('clickInfo') or '')
                if raw_url and raw_url.lower().startswith('http'):
                    real_url = raw_url
                else:
                    # 卡片里既没有 mu 也抠不出真实网址 → 跳过（点开这类卡片往往是相关搜索页）
                    continue

                low = real_url.lower()
                if any(d in low for d in BLOCKED_DOMAINS):
                    self.log(f"    🚫 屏蔽域名: {real_url[:80]}")
                    continue

                author = '百度搜索'
                if not it.get('isZhinengti'):
                    try:
                        host = (urlparse(real_url).hostname or '').lower()
                        if host.startswith('www.'):
                            host = host[4:]
                        if host and 'baidu.com' not in host:
                            author = host
                    except Exception:
                        pass

                is_white = bool(raw_url and raw_url in self.app.whitelist_common)
                results.append(self.normalize_result(
                    source=self.SOURCE_ID,
                    title=title_text,
                    url=real_url,
                    summary=summary_text,
                    date=it.get('date', '未知'),
                    author=author,
                    content_url=raw_url or real_url,
                    long_url=real_url,
                    is_white=is_white,
                    is_zhinengti=bool(it.get('isZhinengti', False)),
                ))

            return results
        except Exception as e:
            self.log(f"  [百度手机版错误] {type(e).__name__}: {e}")
            return results
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass
