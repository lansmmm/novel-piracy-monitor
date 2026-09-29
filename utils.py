import os
import json
import re
from config import *

try:
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def migrate_old_files():
    """老文件迁移（一次即可，幂等）"""
    old_seen = os.path.join(BASE_DIR, "monitor_seen.json")
    if os.path.exists(old_seen) and not os.path.exists(SEEN_ZHIDAO_FILE):
        try: os.rename(old_seen, SEEN_ZHIDAO_FILE)
        except Exception: pass

    old_baidu = os.path.join(BASE_DIR, "monitor_seen_zhinengti.json")
    if os.path.exists(old_baidu) and not os.path.exists(SEEN_BAIDU_FILE):
        try: os.rename(old_baidu, SEEN_BAIDU_FILE)
        except Exception: pass

    # 引擎改名：搜狗PC / 360PC / 头条移动 的旧记录文件自动改名为新名字（保留去重历史）
    for old_path, new_path in (
        (SEEN_SOGOU_LEGACY_FILE, SEEN_SOGOU_PC_FILE),
        (SEEN_SO360_LEGACY_FILE, SEEN_SO360_PC_FILE),
        (SEEN_TOUTIAO_LEGACY_FILE, SEEN_TOUTIAO_MOBILE_FILE),
    ):
        if os.path.exists(old_path) and not os.path.exists(new_path):
            try: os.rename(old_path, new_path)
            except Exception: pass

def generate_icon():
    if not HAS_TRAY:
        return None
    # 优先读 app_icon.ico（用户自定义图标）—— 直接读文件，不调 ensure_ico 避免递归
    try:
        if os.path.exists(ICON_FILE):
            return Image.open(ICON_FILE).copy()
    except Exception:
        pass
    # 兜底：原来的手绘逻辑
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(42, 157, 143, 255))
    d.ellipse((16, 16, 48, 48), fill=(255, 255, 255, 255))
    d.ellipse((24, 24, 40, 40), fill=(230, 57, 70, 255))
    return img

def ensure_ico():
    if os.path.exists(ICON_FILE):
        return ICON_FILE
    img = generate_icon()
    if img is None:
        return None
    try:
        img.save(ICON_FILE, format='ICO', sizes=[(64, 64)])
        return ICON_FILE
    except Exception:
        return None

# ==================== 后缀数据结构 ====================
def normalize_suffixes(sufs):
    out = []
    seen = set()
    for s in sufs or []:
        if isinstance(s, dict):
            text = str(s.get("text", "") or "").strip()
            enabled = bool(s.get("enabled", True))
        else:
            text = str(s or "").strip()
            enabled = True
        if text in seen:
            continue
        seen.add(text)
        out.append({"text": text, "enabled": enabled})
    if "" not in seen:
        out.insert(0, {"text": "", "enabled": True})
    out.sort(key=lambda x: 0 if x["text"] == "" else 1)
    return out

def fmt_suffixes(sufs):
    if not sufs:
        return "（无）"
    enabled = []
    disabled = []
    for s in sufs:
        if isinstance(s, dict):
            text = s.get("text", "")
            on = s.get("enabled", True)
        else:
            text = str(s or "")
            on = True
        (enabled if on else disabled).append(text)
    parts = [(t if t else "（无后缀）") for t in enabled]
    txt = "、".join(parts) if parts else "（无）"
    if disabled:
        dn = [(t if t else "（无后缀）") for t in disabled]
        txt += f"  [未选: {'、'.join(dn)}]"
    return txt

def enabled_suffix_texts(sufs):
    out = []
    for s in sufs or []:
        if isinstance(s, dict):
            if s.get("enabled", True):
                out.append(str(s.get("text", "") or ""))
        else:
            out.append(str(s or ""))
    return out

def fmt_hms(seconds):
    """秒数 → 小时:分钟:秒（如 01:23:45），用来显示「本轮用时」"""
    try:
        total = int(round(float(seconds or 0)))
    except Exception:
        total = 0
    if total < 0:
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

# ==================== 工具函数 ====================
def is_relative_date(date_str):
    s = str(date_str)
    return any(re.search(p, s) for p in RELATIVE_DATE_PATTERNS)

def normalize_date(date_str):
    if not date_str:
        return "未知"
    s = str(date_str).strip()
    if is_relative_date(s):
        return re.sub(r'\s+', '', s)
    nums = re.findall(r'\d+', s)
    if len(nums) >= 3:
        try:
            y, m, d = int(nums[0]), int(nums[1]), int(nums[2])
            return f"{y}/{m:02d}/{d:02d}"
        except Exception:
            pass
    return "未知"

def strip_date_from_text(text):
    if not text:
        return ""
    for p in DATE_PATTERNS:
        text = re.sub(p, '', text)
    for p in RELATIVE_DATE_PATTERNS:
        text = re.sub(p, '', text)
    text = re.sub(r'^[·•\-\–\—\s\.]+', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def filter_summary(summary):
    if not summary:
        return "（无摘要，双击打开查看）"
    s = summary.strip()
    footer_hits = sum(1 for kw in FOOTER_KEYWORDS if kw in s)
    if footer_hits >= 3:
        return "（页脚噪音，双击打开查看）"
    for _ in range(3):
        old = s
        s = re.sub(r'^《[^》]{1,40}》\s*作者[:：]\s*[^，,。；;、\s]{1,25}[，,。；;、\s]*', '', s)
        s = re.sub(r'^《[^》]{1,40}》\s*[，,。；;、\s]*', '', s)
        s = re.sub(r'^[，,。；;：:\s·•\-]+', '', s)
        if s == old:
            break
    parts = re.split(r'([。！？])', s)
    kept = []
    for i in range(0, len(parts), 2):
        sentence = parts[i]
        if not sentence.strip():
            continue
        if any(kw in sentence for kw in FOOTER_KEYWORDS):
            continue
        kept.append(sentence + (parts[i+1] if i+1 < len(parts) else ''))
    s = ''.join(kept).strip()
    for _ in range(2):
        old = s
        s = re.sub(r'^《[^》]{1,40}》\s*[，,。；;、\s]*', '', s)
        s = re.sub(r'^[，,。；;：:\s·•\-]+', '', s)
        if s == old:
            break
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) < 8:
        return "（摘要过短，双击打开查看完整内容）"
    if len(s) > 400:
        s = s[:400] + '...'
    return s

def date_sort_key(date_str):
    s = str(date_str).strip()
    if '刚刚' in s or ('秒' in s and '前' in s):
        return (10001, 0, 0)
    if '分钟' in s and '前' in s:
        return (10000, 0, 0)
    if '小时' in s and '前' in s:
        return (9999, 0, 0)
    if '今天' in s:
        return (9998, 0, 0)
    if '昨天' in s:
        return (9997, 0, 0)
    if '前天' in s:
        return (9996, 0, 0)
    m = re.search(RELDATE_NUM_PATTERNS['day'], s)
    if m:
        return (9000 - int(m.group(1)), 0, 0)
    m = re.search(RELDATE_NUM_PATTERNS['week'], s)
    if m:
        return (8000 - int(m.group(1)), 0, 0)
    m = re.search(RELDATE_NUM_PATTERNS['month'], s)
    if m:
        return (7000 - int(m.group(1)), 0, 0)
    m = re.search(RELDATE_NUM_PATTERNS['year'], s)
    if m:
        return (6000 - int(m.group(1)), 0, 0)
    m = re.match(r'(\d{4})/(\d{2})/(\d{2})', s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (0, 0, 0)

def is_subsequence(needle, haystack):
    it = iter(haystack)
    return all(c in it for c in needle)

def title_matches(title, keyword):
    if not title or not keyword:
        return True
    t = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', title)
    k = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', keyword)
    if not k:
        return True
    return is_subsequence(k, t)


def title_or_summary_matches(keyword, title='', summary='', *extra_urls):
    if not keyword:
        return True
    if title_matches(title, keyword):
        return True
    if title_matches(summary, keyword):
        return True
    for extra in extra_urls:
        if not extra:
            continue
        if isinstance(extra, (list, tuple, set)):
            for item in extra:
                if title_matches(str(item), keyword):
                    return True
            continue
        if title_matches(str(extra), keyword):
            return True
    return False