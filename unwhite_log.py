# -*- coding: utf-8 -*-
"""未加白链接文档：把所有「搜到过、但还没加白」的最终链接写进一个本地 txt。

文档长这样（不带任何标记，方便你在文档里整段复制）：
    书名A	夸克
    https://xxx
    https://yyy
    书名A	百度
    https://zzz

- 同一个书名的链接放一起，按「书名 + 搜索引擎」分组；
- 组与组之间、链接与链接之间都只用一个回车分隔；
- 新发现的排在最上面（组按组里最新那条排，组内也是新的在上）；
- 加了白名单的链接会自动从这个文档里消失，不用手动删。
"""

import json
import threading
import time

from config import (UNWHITE_RECORD_FILE, UNWHITE_DOC_FILE, UNWHITE_LEGACY_BOOK,
                    SRC_LABEL)

_lock = threading.Lock()
_records = None          # [{"url":..., "book":..., "source":..., "seq":...}]，seq 越大越新
_keys = set()            # (book, source, url)，用来去重
_seq = 0
_white_cache = set()     # 最近一次传进来的白名单
_last_doc_at = 0.0


def _load():
    """读记录文件（只在第一次读）"""
    global _records, _keys, _seq
    if _records is not None:
        return
    data = []
    try:
        with open(UNWHITE_RECORD_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = []
    if not isinstance(data, list):
        data = []
    _records = []
    _keys = set()
    for it in data:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or "").strip()
        if not url:
            continue
        book = str(it.get("book") or "").strip()
        source = str(it.get("source") or "").strip()
        try:
            seq = int(it.get("seq") or 0)
        except Exception:
            seq = 0
        _records.append({"url": url, "book": book, "source": source, "seq": seq})
        _keys.add((book, source, url))
        if seq > _seq:
            _seq = seq


def _save():
    try:
        with open(UNWHITE_RECORD_FILE, "w", encoding="utf-8") as f:
            json.dump(_records, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _label(source):
    """引擎显示名：SRC_LABEL 里取短的（「夸克 quark.sm.cn」→「夸克」）"""
    text = str(SRC_LABEL.get(source, source) or source)
    return text.split()[0] if text.split() else text


def _add(book, source, url):
    """（内部用，调用前要已经加锁）真新增返回 True"""
    global _seq
    key = (book, source, url)
    if key in _keys:
        return False
    _seq += 1
    _records.append({"url": url, "book": book, "source": source, "seq": _seq})
    _keys.add(key)
    return True


def _is_url(text):
    return str(text or "").strip().lower().startswith(("http://", "https://"))


def record(url, book, source):
    """记一条「搜到但还没加白」的最终链接（重复的不会记两次）"""
    url = str(url or "").strip()
    if not _is_url(url):
        return False
    book = str(book or "").strip() or "（未知书名）"
    source = str(source or "").strip()
    global _last_doc_at
    changed = False
    with _lock:
        _load()
        if _add(book, source, url):
            _save()
            changed = True
    # 隔几秒刷一次文档就行，不用每条都重写
    if changed and (time.time() - _last_doc_at) > 3:
        build_doc()
    return changed


def all_records():
    """给「载入历史数据」用：返回全部记录（新的在前），每条 {url, book, source, seq}"""
    with _lock:
        _load()
        return [dict(it) for it in
                sorted(_records, key=lambda r: -int(r.get("seq") or 0))]


def seed_from_seen(seen_map, book=None):
    """第一次运行时，把老的 monitor_seen_*.json 补进记录里。

    老记录里只存了网址、没有书名，所以统一放在「旧记录」这一组。
    """
    book = book or UNWHITE_LEGACY_BOOK
    added = 0
    with _lock:
        _load()
        if _records:
            return 0
        for source in sorted((seen_map or {}).keys()):
            for url in list(seen_map.get(source) or []):
                url = str(url or "").strip()
                if not _is_url(url):
                    continue
                if _add(book, source, url):
                    added += 1
        if added:
            _save()
    return added


def build_doc(whitelist=None):
    """按「新发现的在上」重新生成 txt 文档（白名单里的链接不写进去）"""
    global _white_cache, _last_doc_at
    if whitelist is not None:
        _white_cache = set(whitelist)
    with _lock:
        _load()
        groups = {}
        for it in _records:
            if it["url"] in _white_cache:
                continue
            groups.setdefault((it["book"], it["source"]), []).append((it["seq"], it["url"]))
        ordered = sorted(groups.items(), key=lambda kv: -max(s for s, _u in kv[1]))
        lines = []
        for (book, source), items in ordered:
            lines.append(f"{book}\t{_label(source)}")
            for _seq_no, url in sorted(items, key=lambda t: -t[0]):
                lines.append(url)
        # 用 \n 拼，Windows 上按文本模式写出就是回车换行（不会变成两个回车）
        text = "\n".join(lines) + ("\n" if lines else "")
    try:
        with open(UNWHITE_DOC_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        _last_doc_at = time.time()
    except Exception:
        pass
    return len(lines)


def init_once(seen_map, whitelist=None):
    """程序启动时调一次：先补旧记录，再把文档生成好"""
    added = seed_from_seen(seen_map)
    build_doc(whitelist)
    return added
