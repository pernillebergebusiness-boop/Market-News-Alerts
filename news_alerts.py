#!/usr/bin/env python3
"""
Market News Alerts
------------------
Reads free news feeds (RSS), finds headlines about your watchlist or big
market news, and pushes them to your phone with ntfy.

Runs every 15 minutes on GitHub Actions. No AI, no cost.

Environment variables:
  NTFY_TOPIC   your secret ntfy topic name (if missing: dry run, prints only)
  NTFY_SERVER  optional, default https://ntfy.sh
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.yaml"
STATE_FILE = ROOT / "data" / "seen.json"
LOG_FILE = ROOT / "news.md"
NEWS_JSON = ROOT / "data" / "news.json"   # read by the dashboard (index.html)
NEWS_JSON_KEEP = 400

USER_AGENT = "Mozilla/5.0 (compatible; MarketNewsAlerts/1.0; personal use)"
SEEN_KEEP_DAYS = 4
LOG_KEEP_ITEMS = 300


# ---------------------------------------------------------------- helpers

def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_seen() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_seen(seen: dict) -> None:
    cutoff = time.time() - SEEN_KEEP_DAYS * 86400
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(seen, indent=0, sort_keys=True), encoding="utf-8")


def clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def item_key(title: str, link: str) -> str:
    # Same story from different feeds often has the same title -> dedupe on title too
    norm_title = re.sub(r"\W+", " ", title.lower()).strip()
    return hashlib.sha1(norm_title.encode("utf-8")).hexdigest()[:16]


def word_match(phrase: str, text: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(phrase.lower()) + r"(?!\w)"
    return re.search(pattern, text.lower()) is not None


def google_news_url(query: str) -> str:
    q = quote_plus(f'"{query}" when:1d')
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def parse_date(text: str | None) -> float | None:
    if not text:
        return None
    text = text.strip()
    try:  # RSS style: "Tue, 20 Oct 2026 19:10:00 GMT"
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:  # Atom style: "2026-10-20T19:10:00Z"
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(el, *names: str) -> str:
    for child in el:
        if _local(child.tag) in names and child.text:
            return child.text
    return ""


def parse_feed(data: bytes) -> list[dict]:
    """Minimal RSS 2.0 / Atom parser using only the standard library."""
    root = ET.fromstring(data)
    entries = []
    for el in root.iter():
        kind = _local(el.tag)
        if kind not in ("item", "entry"):
            continue
        link = _child_text(el, "link")
        if not link:  # Atom: <link href="..."/>
            for child in el:
                if _local(child.tag) == "link" and child.get("href"):
                    if child.get("rel", "alternate") == "alternate":
                        link = child.get("href")
                        break
        entries.append({
            "title": _child_text(el, "title"),
            "link": (link or "").strip(),
            "summary": _child_text(el, "description", "summary", "content"),
            "published": parse_date(_child_text(el, "pubdate", "published", "updated", "date")),
        })
    return entries


def fetch_feed(url: str) -> list[dict]:
    """Returns feed entries. Supports file:// for testing."""
    if url.startswith("file://"):
        return parse_feed(Path(url[7:]).read_bytes())
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    return parse_feed(resp.content)


# ---------------------------------------------------------------- core

def build_sources(cfg: dict) -> list[dict]:
    sources = [dict(f) for f in cfg.get("feeds") or []]
    for company in cfg.get("watchlist") or []:
        terms = company.get("match") or [company["name"]]
        sources.append({
            "name": f"{company['name']} (Google News)",
            "url": google_news_url(terms[0]),
        })
    return sources


def stock_label(company: dict) -> str:
    label = company["name"]
    if company.get("ticker"):
        label += f" ({company['ticker']})"
    return label


def classify(title: str, summary: str, cfg: dict) -> tuple[list[str], list[str]]:
    """Returns (watchlist hits as 'Name (TICKER)', important keywords hit)."""
    text = f"{title} {summary}"
    for word in cfg.get("ignore_keywords") or []:
        if word_match(word, title):
            return [], []
    stocks = []
    for company in cfg.get("watchlist") or []:
        terms = company.get("match") or [company["name"]]
        if any(word_match(t, text) for t in terms):
            stocks.append(stock_label(company))
    keywords = [k for k in cfg.get("important_keywords") or [] if word_match(k, title)]
    return stocks, keywords


def collect(cfg: dict, seen: dict) -> tuple[list[dict], list[str]]:
    settings = cfg.get("settings") or {}
    max_age = float(settings.get("max_age_hours", 6)) * 3600
    now = time.time()
    found, errors = [], []

    for src in build_sources(cfg):
        try:
            entries = fetch_feed(src["url"])
        except Exception as exc:  # one broken feed must not stop the rest
            errors.append(f"{src['name']}: {exc.__class__.__name__}")
            continue

        for entry in entries[:60]:
            title = clean(entry.get("title", ""))
            link = entry.get("link", "")
            if not title or not link:
                continue
            key = item_key(title, link)
            if key in seen:
                continue
            published = entry.get("published")
            if published and now - published > max_age:
                seen[key] = now  # too old: remember so we never look at it again
                continue
            seen[key] = now
            summary = clean(entry.get("summary", ""))[:400]
            source = src["name"]
            if "news.google.com" in src["url"]:
                # Google News titles look like "Headline - Publisher"
                head, sep, pub = title.rpartition(" - ")
                if sep and head and len(pub) < 60:
                    title, source = head, pub
                summary = ""  # Google's summary only repeats the headline
            stocks, keywords = classify(title, summary, cfg)
            if not stocks and not keywords:
                continue
            found.append({
                "key": key,
                "title": title,
                "summary": summary[:240],
                "link": link,
                "source": source,
                "published": published or now,
                "stocks": stocks,
                "keywords": keywords,
                # watchlist news matters most to you
                "score": 3 * len(stocks) + len(keywords),
            })

    # Same story caught twice in one run (title dedupe already handles most)
    unique = {}
    for item in found:
        unique.setdefault(item["key"], item)
    items = sorted(unique.values(), key=lambda i: (-i["score"], -i["published"]))
    return items, errors


def is_quiet(settings: dict) -> bool:
    tz = ZoneInfo(settings.get("timezone", "Europe/Oslo"))
    hour = datetime.now(tz).hour
    start = int(settings.get("quiet_hours_start", 23))
    end = int(settings.get("quiet_hours_end", 7))
    if start > end:  # e.g. 23 -> 7 crosses midnight
        return hour >= start or hour < end
    return start <= hour < end


def push(title: str, message: str, *, priority: int = 3, tags=None, click: str | None = None) -> None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    payload = {"topic": topic, "title": title, "message": message,
               "priority": priority, "tags": tags or []}
    if click:
        payload["click"] = click
    if not topic:
        print(f"[dry run] {title}\n          {message}\n          {click or ''}")
        return
    resp = requests.post(server, json=payload, timeout=20)
    resp.raise_for_status()


def fmt_time(ts: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M")


def notify(items: list[dict], settings: dict) -> None:
    tz = ZoneInfo(settings.get("timezone", "Europe/Oslo"))
    quiet = is_quiet(settings)
    limit = int(settings.get("max_single_alerts_per_run", 4))

    def prio(item):
        if quiet:
            return 2  # silent at night
        return 4 if item["stocks"] else 3

    if len(items) <= limit:
        for it in items:
            label = ", ".join(it["stocks"]) if it["stocks"] else "Market news"
            push(
                title=label,
                message=f"{it['title']}\n{it['source']} · {fmt_time(it['published'], tz)}",
                priority=prio(it),
                tags=["chart_with_upwards_trend"] if it["stocks"] else ["newspaper"],
                click=it["link"],
            )
        return

    # Too many at once: bundle into one notification
    top = items[:8]
    lines = []
    for it in top:
        label = f"[{it['stocks'][0]}] " if it["stocks"] else ""
        lines.append(f"• {label}{it['title']}")
    extra = len(items) - len(top)
    if extra > 0:
        lines.append(f"+ {extra} more — see news.md")
    push(
        title=f"{len(items)} new market headlines",
        message="\n".join(lines),
        priority=2 if quiet else 3,
        tags=["newspaper"],
        click=top[0]["link"],
    )


def write_log(items: list[dict], settings: dict) -> None:
    if not items:
        return
    tz = ZoneInfo(settings.get("timezone", "Europe/Oslo"))
    old = []
    if LOG_FILE.exists():
        old = [l for l in LOG_FILE.read_text(encoding="utf-8").splitlines() if l.startswith("| ")]
        old = [l for l in old if not l.startswith("| Time")]
    new = []
    for it in items:
        when = datetime.fromtimestamp(it["published"], tz).strftime("%d.%m %H:%M")
        tag = ", ".join(it["stocks"]) or ", ".join(it["keywords"][:2])
        title = it["title"].replace("|", "/")
        new.append(f"| {when} | {tag} | [{title}]({it['link']}) | {it['source']} |")
    rows = (new + old)[:LOG_KEEP_ITEMS]
    header = [
        "# Market news log",
        "",
        "Newest first. Written automatically every 15 minutes.",
        "",
        "| Time | About | Headline | Source |",
        "|---|---|---|---|",
    ]
    LOG_FILE.write_text("\n".join(header + rows) + "\n", encoding="utf-8")


def write_news_json(items: list[dict], cfg: dict, errors: list[str]) -> None:
    """Data file for the dashboard: newest items first, plus the watchlist."""
    old = []
    if NEWS_JSON.exists():
        try:
            old = json.loads(NEWS_JSON.read_text(encoding="utf-8")).get("items", [])
        except (json.JSONDecodeError, AttributeError):
            old = []
    known = {i.get("key") for i in old}
    new = [{k: it[k] for k in ("key", "title", "summary", "link", "source",
                                "published", "stocks", "keywords")}
           for it in items if it["key"] not in known]
    merged = sorted(new + old, key=lambda i: -i.get("published", 0))[:NEWS_JSON_KEEP]
    watchlist = [{"label": stock_label(c), "name": c["name"], "ticker": c.get("ticker", ""),
                  "market": c.get("market", "")} for c in cfg.get("watchlist") or []]
    payload = {
        "updated": time.time(),
        "watchlist": watchlist,
        "feed_problems": errors,
        "items": merged,
    }
    NEWS_JSON.parent.mkdir(parents=True, exist_ok=True)
    NEWS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=0), encoding="utf-8")


def main() -> int:
    cfg = load_config()
    settings = cfg.get("settings") or {}
    seen = load_seen()
    first_run = not seen

    if not NEWS_JSON.exists() and seen:
        # Dashboard is new: fill it with the matching news of the last hours
        # (no notifications for these, they were already "seen").
        backfill, _ = collect(cfg, {})
        write_news_json(backfill, cfg, [])
        print(f"Dashboard backfilled with {len(backfill)} recent headline(s).")

    items, errors = collect(cfg, seen)

    if first_run:
        # Don't flood the phone with everything from the last hours on day one
        print(f"First run: remembered existing headlines ({len(seen)}), no alerts sent.")
        push("Market News Alerts is live ✅",
             "You'll get alerts here about your watchlist and big market news, "
             "checked every 15 minutes.", priority=3, tags=["white_check_mark"])
        write_log(items[:20], settings)
    else:
        print(f"{len(items)} new matching headline(s).")
        if items:
            notify(items, settings)
            write_log(items, settings)

    write_news_json(items, cfg, errors)  # always: refreshes "last updated"
    save_seen(seen)
    for e in errors:
        print(f"Feed problem (skipped): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
