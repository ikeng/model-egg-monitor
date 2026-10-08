#!/usr/bin/env python3
"""
ai-egg-monitor — 大模型厂商"赛博鸡蛋"监控脚本

主流程：
  1. 读取 config/sources.yaml
  2. 抓取每个 enabled source (html / rss)
  3. 关键词过滤，命中即视为"赛博鸡蛋"候选
  4. 简单抽取：厂商 / 标题 / 福利摘要 / 链接 / 来源类型 / 是否需登录 / 时间
  5. 去重：按 guid (sha1(vendor|title|link)) 去重
  6. 读 scripts/state/egg_state.json，标记本次新增
  7. 生成 docs/latest.md + docs/history/YYYY-MM-DD-HH.md
  8. 生成订阅：atom.xml / feed.json / rss.xml / atom-<vendor>.xml / feed-<vendor>.json
  9. 写回 egg_state.json
 10. 可选：FEISHU_WEBHOOK 存在时 POST 飞书文本消息
 11. 日志：每个源成功 / 失败计数；绝不打印任何 secret

设计原则：
  - 单个源失败不影响整体
  - 公开仓库：禁止 secret / cookie / token / 账号密码进入代码、日志、状态文件
  - 遇登录墙：仅标记"需登录查看规则"，保留链接，不尝试登录
  - 不自动领取，不绕过风控
  - 拉取式订阅，不做 WebSub / PubSubHubbub
  - 主推 Atom 1.0，辅以 JSON Feed 1.1，RSS 2.0 仅兼容兜底
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from jinja2 import Environment, BaseLoader

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "sources.yaml"
STATE_PATH = ROOT / "scripts" / "state" / "egg_state.json"
DOCS_DIR = ROOT / "docs"
HISTORY_DIR = DOCS_DIR / "history"

USER_AGENT = (
    "ai-egg-monitor/0.1 "
    "(+https://github.com/ikeng/model-egg-monitor)"
)
TIMEOUT = 15
DEFAULT_FETCH_WORKERS = 8  # 并发抓取线程数，可用 site.fetch_workers 覆盖

# 登录墙检测：仅当页面正文较短且含登录字眼时才标记
LOGIN_PATTERNS = [
    "登录", "登入", "请登录", "需要登录",
    "sign in", "log in", "please log in", "please sign in",
]
LOGIN_TEXT_MIN = 600  # 字符 < 此值 且含登录字眼 → 视为登录墙

CST = datetime.timezone(datetime.timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("egg-monitor")

# jinja2：autoescape=False，由模板里的 | e 过滤器手动转义 XML
J2 = Environment(loader=BaseLoader(), autoescape=False, trim_blocks=True, lstrip_blocks=True)


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------
def now_cst() -> datetime.datetime:
    return datetime.datetime.now(CST)


def iso_cst(dt: datetime.datetime | None = None) -> str:
    """RFC 3339 / ISO 8601，带 +08:00。"""
    dt = dt or now_cst()
    return dt.isoformat(timespec="seconds")


def rfc822_cst(dt: datetime.datetime | None = None) -> str:
    """RFC 822 (用于 RSS pubDate)。"""
    dt = dt or now_cst()
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0800")


def parse_iso(s: str) -> datetime.datetime | None:
    if not s:
        return None
    try:
        # 兼容 +08:00 与 Z
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 唯一键 & 文件名工具
# ---------------------------------------------------------------------------
def make_guid(vendor: str, title: str, link: str) -> str:
    raw = f"{vendor}|{title}|{link}"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"tag:ai-egg-monitor,2026:activity:{h}"


def vendor_slug(name: str) -> str:
    r"""厂商名小写、空格转 -，便于文件名拼接。
    保留 CJK 字符（\w 在 Python 3 默认 Unicode 模式下匹配中文）。
    例：DeepSeek → deepseek，智谱 GLM → 智谱-glm，通义千问 → 通义千问。
    """
    s = name.strip().lower()
    s = re.sub(r"\s+", "-", s)
    # 只去掉文件名不友好的字符，保留 CJK / 字母数字 / 连字符 / 下划线 / 点
    s = re.sub(r"[^\w\-.]", "-", s, flags=re.UNICODE)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "vendor"


# ---------------------------------------------------------------------------
# 配置 & 状态
# ---------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("state 不是 dict")
            return data
        except Exception as e:
            log.warning(f"状态文件读取失败，将重建：{e}")
    return {"activities": {}, "last_run": None, "failures": []}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------
def fetch(url: str) -> str:
    resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def parse_html_page(html: str) -> dict:
    """从 HTML 抽取标题 + 正文摘要 + 全文。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "iframe"]):
        tag.decompose()
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    # 优先用 article / main / body
    main_node = soup.find("main") or soup.find("article") or soup.body or soup
    text = main_node.get_text(separator=" ", strip=True) if main_node else ""
    summary = text[:300]
    return {"title": title, "text": text, "summary": summary}


def parse_rss_feed(url: str) -> tuple[list[dict], bool]:
    """返回 (items, bozo)。"""
    # feedparser 用自己内部的 UA；通过 request_headers 传入
    parsed = feedparser.parse(url, request_headers={"User-Agent": USER_AGENT})
    items = []
    for e in parsed.entries[:30]:
        items.append({
            "title": getattr(e, "title", "") or "",
            "summary": getattr(e, "summary", "") or getattr(e, "description", "") or "",
            "link": getattr(e, "link", "") or "",
        })
    return items, bool(parsed.bozo)


def match_keywords(text: str, keywords: list[str]) -> str | None:
    text_lower = (text or "").lower()
    for kw in keywords:
        if not kw:
            continue
        if kw.lower() in text_lower:
            return kw
    return None


def detect_login(text: str) -> bool:
    """仅当正文较短且含登录字眼时标记为"需登录查看"。"""
    if not text:
        return False
    if len(text) >= LOGIN_TEXT_MIN:
        return False
    text_lower = text.lower()
    for pat in LOGIN_PATTERNS:
        if pat.lower() in text_lower:
            return True
    return False


# ---------------------------------------------------------------------------
# 处理单个 source
# ---------------------------------------------------------------------------
def process_source(
    vendor_name: str,
    source: dict,
    default_keywords: list[str],
    now_iso: str,
) -> tuple[list[dict], dict]:
    """返回 (activities, status)。status = {ok, error, url, type, vendor}。"""
    activities: list[dict] = []
    status = {
        "ok": True,
        "error": None,
        "url": source.get("url", ""),
        "type": source.get("type", ""),
        "vendor": vendor_name,
    }
    keywords = source.get("keywords") or default_keywords
    try:
        stype = (source.get("type") or "").lower()
        url = source["url"]
        if stype == "html":
            html = fetch(url)
            page = parse_html_page(html)
            kw = match_keywords(page["title"] + " " + page["summary"], keywords)
            if kw:
                activities.append({
                    "guid": make_guid(vendor_name, page["title"] or url, url),
                    "vendor": vendor_name,
                    "title": page["title"] or url,
                    "summary": page["summary"],
                    "link": url,
                    "source_type": "html",
                    "needs_login": detect_login(page["text"]),
                    "matched_keyword": kw,
                    "first_seen": now_iso,
                    "updated": now_iso,
                })
        elif stype == "rss":
            items, _bozo = parse_rss_feed(url)
            for item in items:
                kw = match_keywords(item["title"] + " " + item["summary"], keywords)
                if kw:
                    link = item["link"] or url
                    activities.append({
                        "guid": make_guid(vendor_name, item["title"] or link, link),
                        "vendor": vendor_name,
                        "title": item["title"] or link,
                        "summary": item["summary"],
                        "link": link,
                        "source_type": "rss",
                        "needs_login": detect_login(item["summary"]),
                        "matched_keyword": kw,
                        "first_seen": now_iso,
                        "updated": now_iso,
                    })
        else:
            raise ValueError(f"未知 source type: {stype}")
    except Exception as e:
        status["ok"] = False
        status["error"] = f"{type(e).__name__}: {e}"
        log.warning(f"source 失败 {source.get('url')}: {e}")
    return activities, status


# ---------------------------------------------------------------------------
# 生成 Markdown 公告
# ---------------------------------------------------------------------------
MD_TEMPLATE = """# 赛博鸡蛋日报 {{ date }}

> 每日两次监控大模型厂商公开活动，发现"赛博鸡蛋"。
> 仅做提醒与资讯，不自动领取，不登录，不绕过风控。

今日新增：{{ new_count }}
需登录确认：{{ login_count }}
失败源：{{ fail_count }}

## 新增
{% if new_items %}
{% for item in new_items %}
- [{{ item.vendor }}] {{ item.title }} | {{ item.summary }} | 状态：待领取 | 需登录：{% if item.needs_login %}是{% else %}否{% endif %} | [链接]({{ item.link }})
{% endfor %}
{% else %}
（本次无新增）
{% endif %}

{% if login_items %}
## 需登录确认
{% for item in login_items %}
- [{{ item.vendor }}] {{ item.title }} | 规则需登录查看 | [入口]({{ item.link }})
{% endfor %}

{% endif %}
{% if failures %}
## 失败 / 失效
{% for fail in failures %}
- 源 `{{ fail.url }}` ({{ fail.type }})：{{ fail.error }}
{% endfor %}

{% endif %}
## 订阅
- Atom: [atom.xml]({{ base_url }}/atom.xml)
- JSON Feed: [feed.json]({{ base_url }}/feed.json)
- RSS: [rss.xml]({{ base_url }}/rss.xml)
- 说明: [subscribe]({{ base_url }}/subscribe)

> 请遵守厂商条款，不共享账号，不绕过验证码 / 风控。
"""


def render_markdown(
    config: dict,
    new_items: list[dict],
    login_items: list[dict],
    failures: list[dict],
) -> str:
    tmpl = J2.from_string(MD_TEMPLATE)
    return tmpl.render(
        date=now_cst().strftime("%Y-%m-%d %H:%M"),
        new_count=len(new_items),
        login_count=len(login_items),
        fail_count=len(failures),
        new_items=new_items,
        login_items=login_items,
        failures=failures,
        base_url=config["site"]["base_url"].rstrip("/"),
    )


# ---------------------------------------------------------------------------
# Atom 1.0
# ---------------------------------------------------------------------------
ATOM_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>{{ feed_id | e }}</id>
  <title>{{ title | e }}</title>
  <updated>{{ updated | e }}</updated>
  <subtitle>{{ subtitle | e }}</subtitle>
  <link rel="self" href="{{ self_url | e }}"/>
  <link rel="alternate" href="{{ base_url | e }}/"/>
  <generator uri="https://github.com/ikeng/model-egg-monitor" version="0.1">ai-egg-monitor</generator>
  <rights>MIT License</rights>
  <author>
    <name>{{ author | e }}</name>
  </author>
{% for item in items %}  <entry>
    <id>{{ item.guid | e }}</id>
    <title>{{ item.title | e }}</title>
    <updated>{{ item.updated | e }}</updated>
    <published>{{ item.first_seen | e }}</published>
    <link rel="alternate" href="{{ item.link | e }}"/>
    <author><name>{{ item.vendor | e }}</name></author>
    <category term="{{ item.vendor | e }}"/>
    <summary type="text">{{ item.summary | e }}</summary>
    <content type="html">{{ item.content_html | e }}</content>
  </entry>
{% endfor %}</feed>
"""


def build_content_html(item: dict) -> str:
    """构造 Atom content / JSON Feed content_html 用的 HTML 片段。"""
    parts = [
        f"<p><strong>厂商</strong>：{item['vendor']}</p>",
        f"<p><strong>标题</strong>：{item['title']}</p>",
        f"<p><strong>福利摘要</strong>：{item['summary']}</p>",
        f"<p><strong>需登录</strong>：{'是' if item['needs_login'] else '否'}</p>",
        f"<p><strong>来源类型</strong>：{item['source_type']}</p>",
        f"<p><strong>命中关键词</strong>：{item.get('matched_keyword', '')}</p>",
        f"<p><strong>首次发现</strong>：{item['first_seen']}</p>",
        f"<p><strong>最后更新</strong>：{item['updated']}</p>",
        f'<p><strong>原始链接</strong>：<a href="{item["link"]}">{item["link"]}</a></p>',
    ]
    return "\n".join(parts)


def render_atom(
    config: dict,
    items: list[dict],
    *,
    vendor: str | None = None,
) -> str:
    site = config["site"]
    base_url = site["base_url"].rstrip("/")
    if vendor:
        slug = vendor_slug(vendor)
        feed_id = f"{base_url}/atom-{slug}.xml"
        self_url = feed_id
        title = f"{site['title']} · {vendor}"
    else:
        feed_id = f"{base_url}/atom.xml"
        self_url = feed_id
        title = site["title"]
    items_with_content = [{**i, "content_html": build_content_html(i)} for i in items]
    tmpl = J2.from_string(ATOM_TEMPLATE)
    return tmpl.render(
        feed_id=feed_id,
        title=title,
        updated=iso_cst(),
        subtitle=site.get("subtitle", ""),
        self_url=self_url,
        base_url=base_url,
        author=site.get("author", "ai-egg-monitor"),
        items=items_with_content,
    )


# ---------------------------------------------------------------------------
# JSON Feed 1.1
# ---------------------------------------------------------------------------
def render_json_feed(
    config: dict,
    items: list[dict],
    *,
    vendor: str | None = None,
) -> str:
    site = config["site"]
    base_url = site["base_url"].rstrip("/")
    if vendor:
        slug = vendor_slug(vendor)
        feed_url = f"{base_url}/feed-{slug}.json"
        title = f"{site['title']} · {vendor}"
    else:
        feed_url = f"{base_url}/feed.json"
        title = site["title"]

    payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": title,
        "home_page_url": f"{base_url}/",
        "feed_url": feed_url,
        "description": site.get("description", site.get("subtitle", "")),
        "language": site.get("language", "zh-CN"),
        "authors": [{"name": site.get("author", "ai-egg-monitor")}],
        "items": [
            {
                "id": i["guid"],
                "url": i["link"],
                "title": i["title"],
                "content_html": build_content_html(i),
                "summary": i["summary"],
                "date_published": i["first_seen"],
                "date_modified": i["updated"],
                "tags": [i["vendor"], i["source_type"], i.get("matched_keyword", "")],
            }
            for i in items
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# RSS 2.0 兜底
# ---------------------------------------------------------------------------
RSS_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>{{ title | e }}</title>
    <link>{{ base_url | e }}/</link>
    <description>{{ description | e }}</description>
    <language>{{ language | e }}</language>
    <lastBuildDate>{{ updated_rfc822 }}</lastBuildDate>
    <generator>ai-egg-monitor</generator>
{% for item in items %}    <item>
      <title>{{ item.title | e }}</title>
      <link>{{ item.link | e }}</link>
      <description>{{ item.summary | e }}</description>
      <guid isPermaLink="false">{{ item.guid | e }}</guid>
      <pubDate>{{ item.pubdate_rfc822 | e }}</pubDate>
      <category>{{ item.vendor | e }}</category>
    </item>
{% endfor %}  </channel>
</rss>
"""


def iso_to_rfc822(iso_str: str) -> str:
    dt = parse_iso(iso_str)
    if dt is None:
        return rfc822_cst()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")


def render_rss(config: dict, items: list[dict]) -> str:
    site = config["site"]
    base_url = site["base_url"].rstrip("/")
    items_with_pubdate = [{**i, "pubdate_rfc822": iso_to_rfc822(i["first_seen"])} for i in items]
    tmpl = J2.from_string(RSS_TEMPLATE)
    return tmpl.render(
        title=site["title"],
        base_url=base_url,
        description=site.get("description", site.get("subtitle", "")),
        language=site.get("language", "zh-CN"),
        updated_rfc822=rfc822_cst(),
        items=items_with_pubdate,
    )


# ---------------------------------------------------------------------------
# 飞书 webhook（可选）
# ---------------------------------------------------------------------------
def send_feishu(webhook_url: str, markdown_text: str) -> None:
    """发送飞书机器人文本消息。失败仅记录日志，不影响主流程。"""
    # 不打印 webhook url，避免泄漏
    try:
        payload = {
            "msg_type": "text",
            "content": {"text": markdown_text[:30000]},  # 飞书单条文本上限
        }
        resp = requests.post(
            webhook_url,
            json=payload,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        log.info(f"飞书 webhook 已发送，HTTP {resp.status_code}")
    except Exception as e:
        log.warning(f"飞书 webhook 发送失败：{type(e).__name__} (不会中断主流程)")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    log.info("==== ai-egg-monitor 开始 ====")

    config = load_config()
    site = config["site"]
    default_keywords = config.get("default_keywords", [])
    max_items = int(site.get("max_items", 100))

    now = iso_cst()
    state = load_state()
    state["last_run"] = now

    # ---------- 1. 抓取所有源 ----------
    all_activities: list[dict] = []
    all_failures: list[dict] = []
    # 列出所有待抓取的 (厂商, source) 对；禁用的厂商直接跳过
    jobs: list[tuple[str, dict]] = []
    for vendor in config.get("vendors", []):
        if not vendor.get("enabled", True):
            log.info(f"厂商 {vendor['name']} 已禁用，跳过")
            continue
        for source in vendor.get("sources", []):
            # 支持单条 source 级 enabled: false，用于临时关掉在 CI 网络下不可达的页面
            if not source.get("enabled", True):
                log.info(f"源已禁用，跳过 {vendor['name']} {source.get('url')}")
                continue
            jobs.append((vendor["name"], source))

    workers = int(site.get("fetch_workers", DEFAULT_FETCH_WORKERS)) or 1
    log.info(
        f"共 {len(config.get('vendors', []))} 个厂商配置，{len(jobs)} 个源待抓取"
        f"（并发 {min(workers, max(len(jobs), 1))}）"
    )

    # 并发抓取。process_source 内部已自带 try/except，这里再包一层防意外。
    # 结果按 jobs 顺序回填，保证输出不受线程完成顺序影响（可重复构建）。
    results: list[tuple[list[dict], dict] | None] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, max(len(jobs), 1)))) as pool:
        futures = {
            pool.submit(process_source, vname, src, default_keywords, now): idx
            for idx, (vname, src) in enumerate(jobs)
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:  # pragma: no cover - 兜底
                vname, src = jobs[idx]
                log.warning(f"source 线程异常 {src.get('url')}: {e}")
                results[idx] = ([], {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "url": src.get("url", ""),
                    "type": src.get("type", ""),
                    "vendor": vname,
                })

    for item in results:
        if item is None:  # pragma: no cover - 理论不可达
            continue
        activities, status = item
        all_activities.extend(activities)
        if not status["ok"]:
            all_failures.append(status)

    log.info(f"抓取完成：候选 {len(all_activities)}，失败源 {len(all_failures)}")

    # ---------- 2. 合并到 state，标记新增 ----------
    state_acts = state.setdefault("activities", {})
    new_items: list[dict] = []
    for act in all_activities:
        guid = act["guid"]
        if guid in state_acts:
            # 已存在：保留 first_seen，更新 updated
            old = state_acts[guid]
            act["first_seen"] = old.get("first_seen", act["first_seen"])
            act["updated"] = now
            state_acts[guid] = act
        else:
            state_acts[guid] = act
            new_items.append(act)

    state["failures"] = all_failures

    # ---------- 3. 计算订阅用的 items 列表 ----------
    all_known = list(state_acts.values())
    all_known.sort(key=lambda x: x.get("updated", ""), reverse=True)
    feed_items = all_known[:max_items]

    # ---------- 4. 生成 Markdown 公告 ----------
    login_items = [a for a in new_items if a.get("needs_login")]
    md = render_markdown(config, new_items, login_items, all_failures)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "latest.md").write_text(md, encoding="utf-8")
    history_name = now_cst().strftime("%Y-%m-%d-%H") + ".md"
    (HISTORY_DIR / history_name).write_text(md, encoding="utf-8")
    log.info(f"已写 docs/latest.md 与 docs/history/{history_name}")

    # ---------- 5. 生成订阅文件 ----------
    atom_main = render_atom(config, feed_items)
    (DOCS_DIR / "atom.xml").write_text(atom_main, encoding="utf-8")

    json_main = render_json_feed(config, feed_items)
    (DOCS_DIR / "feed.json").write_text(json_main, encoding="utf-8")

    rss_main = render_rss(config, feed_items)
    (DOCS_DIR / "rss.xml").write_text(rss_main, encoding="utf-8")
    log.info("已写 atom.xml / feed.json / rss.xml")

    # 按厂商拆分
    vendor_names = sorted({a["vendor"] for a in all_known})
    for vname in vendor_names:
        v_items = [a for a in all_known if a["vendor"] == vname][:max_items]
        slug = vendor_slug(vname)
        (DOCS_DIR / f"atom-{slug}.xml").write_text(render_atom(config, v_items, vendor=vname), encoding="utf-8")
        (DOCS_DIR / f"feed-{slug}.json").write_text(render_json_feed(config, v_items, vendor=vname), encoding="utf-8")
        log.info(f"已写 厂商 {vname} → atom-{slug}.xml / feed-{slug}.json")

    # ---------- 6. 写回 state ----------
    save_state(state)
    log.info(f"状态已写回 {STATE_PATH}")

    # ---------- 7. 飞书 webhook ----------
    webhook = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if webhook:
        log.info("检测到 FEISHU_WEBHOOK，准备推送公告")
        send_feishu(webhook, md)
    else:
        log.info("未设置 FEISHU_WEBHOOK，跳过推送")

    # ---------- 8. 总结 ----------
    log.info(
        f"总结：新增 {len(new_items)}，需登录确认 {len(login_items)}，"
        f"失败源 {len(all_failures)}，订阅总条数 {len(feed_items)}"
    )
    log.info("==== ai-egg-monitor 结束 ====")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log.exception(f"主流程异常：{e}")
        sys.exit(1)
