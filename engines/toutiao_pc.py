# engines/toutiao_pc.py
"""头条网页端搜索（dvpf=pc）。

设计参照百度引擎：
- 标题链接形如 https://sou.toutiao.com/search/jump?url=<encode>&aid=..&jtoken=..
  → 最终网址直接从 url= 参数里解出来（可能多一层嵌套），无需真跳转，简单可靠。
- 源链接（右键标题复制到的长链）= 完整 jump 链
- 链接栏（最终跳转网址）= 解出来的落地页

自愈：共用存档里的头条 cookie 一旦被风控，服务端只会回「热榜」、不给结果卡片。
     此时清掉头条系 cookie 再试一次（整个进程只清一次，避免死循环）。
"""
import random
import re
import time
from urllib.parse import quote, urlparse, parse_qs, unquote

from config import BLOCKED_DOMAINS
from engines.base import BaseEngine


class ToutiaoPCEngine(BaseEngine):
    SOURCE_ID = 'toutiao_pc'
    SOURCE_LABEL = '头条PC'
    SEARCH_URL = 'https://so.toutiao.com/search?dvpf=pc&keyword={kw}'
    EXTRA_URL = 'https://mail.qq.com/'

    # 整个进程只自愈清理一次
    _cookie_cleared = False

    @staticmethod
    def _decode_real_url(href):
        """sou.toutiao.com/search/jump?...&url=<encoded> → 真实落地页（可能多层嵌套）"""
        if not href:
            return ''
        href = href.strip()
        if href.startswith('//'):
            href = 'https:' + href
        for _ in range(5):
            try:
                u = urlparse(href)
                if 'search/jump' not in u.path:
                    break
                nxt = (parse_qs(u.query).get('url') or [''])[0]
                if not nxt or nxt == href:
                    break
                href = unquote(nxt)
            except Exception:
                break
        return href

    # 头条系验证码常见文案（出现在正文里就认为遇到验证码）
    CAPTCHA_TEXTS = (
        '请完成下列验证', '请完成安全验证', '请完成验证',
        '完成下列验证后继续', '完成验证后继续',
        '人机验证', '安全验证', '滑动验证', '滑块验证',
        '拖动完成', '拖动滑块', '按住左边按钮拖动',
        '拼图', '请依次点击', '请按顺序点击',
    )

    def _has_captcha(self, page):
        """判断页面是否弹了验证码：URL / 正文文案 / 弹窗 DOM，任一命中即为真。"""
        # 1) URL 特征
        try:
            cur = (page.url or '').lower()
            if any(k in cur for k in ('captcha', 'verify', 'challenge', 'secsdk')):
                return True
        except Exception:
            pass
        # 2) 正文关键词（截图里那种"请完成下列验证后继续"就靠这里命中）
        try:
            body_text = page.text_content('body') or ''
            if any(k in body_text for k in self.CAPTCHA_TEXTS):
                return True
        except Exception:
            pass
        # 3) 弹窗 DOM（头条验证码一般是浮层容器 + 拼图/滑块）
        try:
            hit = page.evaluate(r"""
                () => {
                    const sels = [
                        '.captcha', '#captcha',
                        '.captcha-verify', '#captcha-verify',
                        '.secsdk-captcha', '.verify-wrap',
                        '.captcha_verify_container',
                        '.vc-container', '.verify-bar',
                        'iframe[src*="captcha"]', 'iframe[src*="verify"]',
                    ];
                    for (const s of sels) {
                        const el = document.querySelector(s);
                        if (el && el.offsetParent !== null) return true;
                    }
                    return false;
                }
            """)
            if hit:
                return True
        except Exception:
            pass
        return False
    def _scrape(self, context, term, page_num):
        """抓取一页，返回原始条目（title/href/summary/date）"""
        page = None
        try:
            offset = max(0, int(page_num or 0)) * 10
            url = (f"https://so.toutiao.com/search?dvpf=pc&source=input"
                   f"&keyword={quote(term)}&offset={offset}&enable_druid_v2=1")
            page = self._create_stealth_page(context)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(4, 6))

            items = page.evaluate(r"""
                () => {
                    const out = [];
                    document.querySelectorAll('.result-content').forEach(block => {
                        const txt = (block.innerText || '').replace(/\s+/g, ' ').trim();
                        // 跳过「头条热榜 / 相关搜索」这类非结果卡片
                        if (!txt || txt.includes('头条热榜') || txt.includes('相关搜索')) return;
                        const a = block.querySelector('a[href]');
                        if (!a) return;
                        const title = (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim();
                        if (!title || title.length < 4) return;
                        const href = (a.getAttribute('href') || '').trim();
                        if (!href.startsWith('http')) return;
                        let summary = txt.split(title).join(' ').replace(/\s+/g, ' ').trim();
                        if (summary.length > 500) summary = summary.substring(0, 500);
                        let date = '未知';
                        const dm = txt.match(/\d{4}[/-]\d{1,2}[/-]\d{1,2}/)
                                || txt.match(/\d{4}年\d{1,2}月\d{1,2}日/);
                        if (dm) date = dm[0];
                        out.push({ title, href, summary: summary || '（无摘要）', date });
                    });
                    return out;
                }
            """) or []

            # ★ 先看抓没抓到结果；抓不到再判断是不是验证码，
            #   避免正文里恰好出现"安全验证"这类词被误判
            if not items and self._has_captcha(page):
                self.pause_for_user('头条搜索要求验证码/登录')
                return []

            return items
        except Exception as e:
            self.log(f"  [头条PC错误] {type(e).__name__}: {e}")
            return []
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass

    def _clear_toutiao_cookies(self, context):
        """清掉头条系 cookie（风控自愈）"""
        try:
            context.clear_cookies(domain=re.compile(r".*toutiao\.com.*"))
            return True
        except Exception as e:
            self.log(f"  ⚠️ 清理头条 cookie 失败：{type(e).__name__}: {e}")
            return False

    def fetch(self, context, term, page_num=0, book_kw=''):
        items = self._scrape(context, term, page_num)

        # 存档里的头条 cookie 被风控时，服务端只回热榜、不给结果卡片 → 清一次 cookie 重试
        if not items and page_num == 0 and not ToutiaoPCEngine._cookie_cleared:
            ToutiaoPCEngine._cookie_cleared = True
            if self._clear_toutiao_cookies(context):
                self.log("  🧹 头条PC 首页 0 结果：已清理头条 cookie，重试一次")
                items = self._scrape(context, term, page_num)

        self.app._last_fetch_raw_count = len(items)
        results = []
        if not items:
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
                author = host or '头条搜索'
            except Exception:
                author = '头条搜索'

            nr = self.normalize_result(
                source=self.SOURCE_ID,
                title=it.get('title', ''),
                url=real,
                summary=it.get('summary', ''),
                date=it.get('date', '未知'),
                author=author,
                is_zhinengti=False,
            )
            # 源链接保留完整 jump 链（含 query），供「复制源链接」用
            if long_href:
                nr['content_url'] = long_href
                nr['long_url'] = long_href
            results.append(nr)

        return results
