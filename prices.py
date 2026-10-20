#!/usr/bin/env python3
"""
Market prices for the dashboard.

Fetches daily prices (last ~3 months) for the main markets and every stock
in your watchlist, and saves them to data/markets.json. No API key needed.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "markets.json"
TZ = ZoneInfo("Europe/Oslo")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"}

# The big markets, with a plain-English explanation of each
MARKETS = [
    {"symbol": "OSEBX.OL", "name": "Oslo Børs (OSEBX)", "kind": "index",
     "explain": "The main index of the Oslo Stock Exchange: the average move of the biggest "
                "Norwegian companies. If it's up, Norwegian stocks had a good day overall."},
    {"symbol": "^GSPC", "name": "S&P 500", "kind": "index",
     "explain": "The 500 biggest US companies. The best single measure of how the US stock market is doing."},
    {"symbol": "^IXIC", "name": "Nasdaq", "kind": "index",
     "explain": "US index full of tech companies (Apple, Nvidia, Meta…). Moves more than the S&P 500 "
                "and shows how tech is doing."},
    {"symbol": "BZ=F", "name": "Brent oil", "kind": "commodity", "unit": "USD/barrel",
     "explain": "The world price of oil. Very important for Norway: when oil goes up, Equinor, Aker BP "
                "and Oslo Børs usually go up too."},
    {"symbol": "NOK=X", "name": "USD / NOK", "kind": "fx", "unit": "NOK per dollar",
     "explain": "How many kroner one US dollar costs. If it goes up, the krone got weaker: US stocks "
                "become more expensive for you, but are worth more in kroner if you own them."},
]


def yahoo_symbol(company: dict) -> str | None:
    if company.get("yahoo"):
        return company["yahoo"]
    t = company.get("ticker")
    if not t:
        return None
    return f"{t}.OL" if (company.get("market") or "").lower() == "oslo" else t


def summarize(symbol: str, closes: list[float], currency: str, mtime: float | None = None) -> dict | None:
    closes = [round(float(c), 4) for c in closes if c is not None and c == c]  # drop None/NaN
    if len(closes) < 2:
        return None
    price, prev = closes[-1], closes[-2]

    def pct(old):
        return round((price / old - 1) * 100, 2) if old else None

    return {
        "symbol": symbol,
        "currency": currency,
        "price": round(price, 2),
        "change_pct": pct(prev),
        "week_pct": pct(closes[-6]) if len(closes) >= 6 else None,
        "month_pct": pct(closes[-22]) if len(closes) >= 22 else pct(closes[0]),
        "history": [round(c, 2) for c in closes[-66:]],
        "market_time": mtime or time.time(),
    }


def fetch_yfinance(symbol: str) -> dict | None:
    try:
        try:
            import yfinance as yf
        except ImportError:  # installed on first use, so the workflow file needs no change
            import subprocess, sys
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "yfinance"], check=True)
            import yfinance as yf
        t = yf.Ticker(symbol)
        h = t.history(period="3mo", interval="1d", auto_adjust=False)
        if h is None or h.empty:
            return None
        cur = (getattr(t, "history_metadata", None) or {}).get("currency", "")
        return summarize(symbol, list(h["Close"]), cur, h.index[-1].timestamp())
    except Exception as exc:
        print(f"  yfinance {symbol}: {exc.__class__.__name__}: {str(exc)[:120]}")
        return None


STOOQ = {"OSEBX.OL": None, "^GSPC": "^spx", "^IXIC": "^ndq", "BZ=F": "cb.f", "NOK=X": "usdnok"}


def fetch_stooq(symbol: str, currency_guess: str) -> dict | None:
    code = STOOQ.get(symbol, symbol.replace(".OL", ".no") if symbol.endswith(".OL") else symbol + ".us")
    if not code:
        return None
    try:
        r = requests.get("https://stooq.com/q/d/l/", params={"s": code.lower(), "i": "d"}, headers=UA, timeout=20)
        rows = [l.split(",") for l in r.text.strip().splitlines()[1:]]
        closes = [float(x[4]) for x in rows if len(x) > 4 and x[4] not in ("", "null")]
        return summarize(symbol, closes[-70:], currency_guess)
    except Exception as exc:
        print(f"  stooq {symbol}: {exc.__class__.__name__}")
        return None


def fetch(symbol: str) -> dict | None:
    d = fetch_yahoo(symbol) or fetch_yfinance(symbol)
    if d:
        return d
    guess = "NOK" if symbol.endswith(".OL") or symbol == "NOK=X" else "USD"
    return fetch_stooq(symbol, guess)


def fetch_yahoo(symbol: str) -> dict | None:
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}"
        try:
            r = requests.get(url, params={"range": "3mo", "interval": "1d"}, headers=UA, timeout=20)
            if r.status_code != 200:
                print(f"  yahoo {symbol}: HTTP {r.status_code}")
                continue
            res = r.json()["chart"]["result"][0]
        except Exception:
            continue
        meta = res.get("meta", {})
        ts = res.get("timestamp") or []
        closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        pts = [(t, c) for t, c in zip(ts, closes) if c is not None]
        if not pts:
            continue
        price = meta.get("regularMarketPrice") or pts[-1][1]
        mtime = meta.get("regularMarketTime") or pts[-1][0]
        last_day = datetime.fromtimestamp(pts[-1][0], TZ).date()
        today_bar = last_day == datetime.fromtimestamp(mtime, TZ).date()
        prev = pts[-2][1] if today_bar and len(pts) > 1 else pts[-1][1]
        closes_only = [c for _, c in pts]
        if not today_bar:
            closes_only.append(price)

        def pct(old):
            return round((price / old - 1) * 100, 2) if old else None

        return {
            "symbol": symbol,
            "currency": meta.get("currency", ""),
            "price": round(price, 2),
            "change_pct": pct(prev),
            "week_pct": pct(closes_only[-6]) if len(closes_only) >= 6 else None,
            "month_pct": pct(closes_only[-22]) if len(closes_only) >= 22 else pct(closes_only[0]),
            "history": [round(c, 2) for c in closes_only[-66:]],
            "market_time": mtime,
        }
    return None


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    out = {"updated": time.time(), "markets": [], "stocks": [], "problems": []}

    for m in MARKETS:
        d = fetch(m["symbol"])
        if d:
            out["markets"].append({**m, **d})
        else:
            out["problems"].append(m["symbol"])
        time.sleep(0.4)

    for c in cfg.get("watchlist") or []:
        sym = yahoo_symbol(c)
        if not sym:
            continue
        d = fetch(sym)
        label = c["name"] + (f" ({c['ticker']})" if c.get("ticker") else "")
        if d:
            out["stocks"].append({"name": c["name"], "ticker": c.get("ticker", ""), "label": label,
                                  "market": c.get("market", ""), **d})
        else:
            out["problems"].append(sym)
        time.sleep(0.4)

    if not out["markets"] and not out["stocks"] and OUT.exists():
        print("No prices fetched; keeping the previous file.")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(out['markets'])} markets and {len(out['stocks'])} stocks. Problems: {out['problems']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
