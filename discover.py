#!/usr/bin/env python3
"""
Reddit-based evergreen thread discovery.

Runs weekly. Scans target subreddits for top posts of the past year that
mention competitor / EV-betting keywords. Writes them as an Ahrefs-shaped CSV
(`inbox/_reddit_discovery.csv`) so reddit_bot.py picks them up alongside the
manual Ahrefs exports.

Synthetic traffic: upvotes * 0.5 (rough Google-traffic proxy for SEO threads).
"""

import csv
import time
from pathlib import Path

from lib import ROOT, http_get, log, slack_notify, SCRAPERAPI_KEY
import urllib.parse

OUT_PATH = ROOT / "inbox" / "_reddit_discovery.csv"

# Subs to mine for evergreen threads
DISCOVERY_SUBS = [
    "evbetting",
    "positiveevbetting",
    "arbitragebetting",
    "algobetting",
    "dfsports",
    "sportsbettingpicks",
    "sportsbetting",
]

# Keywords (lowercase) — any match in title or selftext qualifies
DISCOVERY_KEYWORDS = [
    "oddsjam", "odds jam",
    "+ev", "positive ev", "ev betting", "expected value",
    "line shopping", "line shop",
    "arb", "arbing", "arbitrage",
    "outlier", "rithmm", "props cash", "prizepicks", "pikkit",
    "closing line value", "clv",
    "sharp bet", "sharp betting", "beat the book",
    "best betting tool", "best sports betting", "best ev",
    "novig", "kalshi sports", "polymarket sports",
    "betting software", "betting analytics",
    "juice reel", "crazy ninja", "odds shopper",
]

MIN_UPVOTES = 10
MAX_PER_SUB = 30


def reddit_top_year(sub):
    """Pull top posts of the past year for a subreddit via ScraperAPI."""
    target = f"https://www.reddit.com/r/{sub}/top.json?t=year&limit=100"
    if not SCRAPERAPI_KEY:
        raise RuntimeError("SCRAPERAPI_KEY required for discovery")
    url = ("https://api.scraperapi.com/?api_key="
           + SCRAPERAPI_KEY + "&url=" + urllib.parse.quote(target, safe=""))
    return http_get(url, timeout=60)


def matches_keyword(title, body):
    haystack = (title + " " + body).lower()
    for kw in DISCOVERY_KEYWORDS:
        if kw in haystack:
            return kw
    return None


def discover():
    log("=== discover run start ===")
    rows = []
    for sub in DISCOVERY_SUBS:
        try:
            data = reddit_top_year(sub)
        except Exception as e:
            log(f"  fetch failed for r/{sub}: {e}")
            continue

        children = data.get("data", {}).get("children", [])
        sub_matched = 0
        for child in children:
            if child.get("kind") != "t3":
                continue
            p = child["data"]
            score = p.get("score", 0)
            if score < MIN_UPVOTES:
                continue
            if p.get("locked") or p.get("archived") or p.get("over_18"):
                continue
            title = p.get("title", "") or ""
            body = p.get("selftext", "") or ""
            kw = matches_keyword(title, body)
            if not kw:
                continue
            url = "https://www.reddit.com" + p.get("permalink", "")
            num_comments = p.get("num_comments", 0)
            synth_traffic = int(score * 0.5 + num_comments * 0.3)
            rows.append({
                "URL": url,
                "Status": "Active",
                "Current traffic": str(synth_traffic),
                "Current top keyword": kw,
                "Current top keyword: Country": "United States",
                "Page type": "Reddit thread",
                "_subreddit": sub,
                "_score": score,
                "_comments": num_comments,
                "_title": title,
            })
            sub_matched += 1
            if sub_matched >= MAX_PER_SUB:
                break
        log(f"  r/{sub}: {sub_matched} matches added")
        time.sleep(2)

    rows.sort(key=lambda r: int(r["Current traffic"]), reverse=True)

    # write Ahrefs-shaped CSV (only the columns reddit_bot reads)
    fieldnames = [
        "URL", "Status", "Current traffic", "Current top keyword",
        "Current top keyword: Country", "Page type",
    ]
    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})

    log(f"=== discover end: {len(rows)} threads written to {OUT_PATH.name} ===")

    # Slack digest
    if rows:
        top = rows[:8]
        detail = "\n".join(
            f"• r/{r['_subreddit']} · {r['_score']}↑ · `{r['Current top keyword']}` "
            f"· {r['_title'][:70]}"
            for r in top
        )
        slack_notify(
            f"Discovery: {len(rows)} evergreen threads added (top 8 below).",
            blocks=[
                {"type": "header", "text": {"type": "plain_text",
                 "text": f"Reddit discovery: {len(rows)} threads"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": detail}},
            ],
        )
    else:
        slack_notify(":warning: Discovery run found 0 matching threads.")


if __name__ == "__main__":
    discover()
