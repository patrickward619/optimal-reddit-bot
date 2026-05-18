#!/usr/bin/env python3
"""
Optimal Bet Reddit posting bot.

Runs 3x/day via GitHub Actions. Each run:
  1. Pulls candidate threads from two sources:
       - Aged bucket: top Ahrefs URLs from inbox/*.csv (Google-ranking threads)
       - Fresh bucket: /r/{sub}/new.json for target subreddits (last ~24h)
  2. Filters + dedupes (posted.jsonl)
  3. LLM qualification gate (score >= 7)
  4. Posts via CrowdReply, buys top-comment-beating upvotes
  5. Reports to Slack

Style guide (STYLE_GUIDE.md) is the source of truth for writer + qualifier.
"""

import csv
import glob
import json
import os
import random
import time
from collections import Counter
from datetime import datetime, timezone

from lib import (
    CrowdReply,
    build_qualifier_system_prompt,
    build_writer_system_prompt,
    claude_complete,
    fetch_subreddit_new,
    fetch_thread,
    load_posted_urls,
    log,
    record_posted,
    reddit_search,
    scraperapi_credits_left,
    slack_notify,
    ROOT,
)

# ── Config ────────────────────────────────────────────────────────────────────

INBOX_DIR = ROOT / "inbox"
PROCESSED_DIR = ROOT / "processed"

# Tier 1 subs — source fresh threads from /new (vetted permissive)
FRESH_SUBS = [
    "evbetting",
    "arbitragebetting",
    "algobetting",
    "sportsbettingpicks",
    "SportsBettingTools",
    "cantbeatthebookie",
    "sportsbookcheatcodes",
    "sharpsports",
    "Bookies",
    "parlays",
    "handicappers",
    "sportsbettingadvice",
    "Kalshi",
    "PolymarketIntel",
    "RecreationalGambling",
]

# Blocklist — never post here even if a CSV / search contains the URL
# r/positiveevbetting had 100% removal rate (11/11) — moved here 2026-05-18
SUB_BLOCKLIST = {
    "sportsbookadvice",
    "sportsbookftc",
    "positiveevbetting",
}

# Tier 2 — only post if the thread is old (Ahrefs-ranking evergreen)
TIER2_SUBS = {"sportsbetting", "sportsbook"}

# Keyword filter for fresh-bucket + site-wide search threads
FRESH_KEYWORDS = [
    "oddsjam", "odds jam", "positive ev", "+ev", "positive-ev",
    "line shopping", "line shop", "arbitrage", "arb betting", "arbing",
    "sharp bet", "closing line value", "clv", "outlier bet", "rithmm",
    "props cash", "ev betting", "prizepicks edge", "kalshi sports",
    "polymarket sports", "algo betting", "beat the book", "expected value",
    "novig", "pikkit", "best betting tool", "best ev betting",
]

# Caps (tuned 2026-05-18 to lift volume ceiling — Jan ran 14-25 installs/day)
TARGET_PER_RUN = 8
DAILY_CAP = 25
PER_SUB_DAILY_CAP = 4
FRESH_SHARE = 0.45  # 45% fresh / wide-search, 55% aged
QUALIFICATION_THRESHOLD = 6


# ── CSV (aged) ingestion ─────────────────────────────────────────────────────

def load_aged_candidates():
    """Load Reddit URLs from all CSVs in inbox/, sorted by traffic desc.
    Filters to US-targeted threads only (Current top keyword: Country == 'United States')."""
    rows = []
    for csv_path in sorted(glob.glob(str(INBOX_DIR / "*.csv"))):
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                url = (r.get("URL") or "").split("?")[0]
                if not url.startswith("https://www.reddit.com"):
                    continue
                if r.get("Status", "") == "Lost":
                    continue
                if r.get("Current top keyword: Country", "") != "United States":
                    continue
                try:
                    traffic = int(r.get("Current traffic") or 0)
                except ValueError:
                    traffic = 0
                rows.append({
                    "url": url,
                    "traffic": traffic,
                    "source": "aged",
                    "keyword": r.get("Current top keyword") or "",
                })
    rows.sort(key=lambda r: r["traffic"], reverse=True)
    return rows


def load_fresh_candidates():
    """Fetch /new from each target sub; keyword-filter + sanity-filter."""
    cands = []
    for sub in FRESH_SUBS:
        try:
            posts = fetch_subreddit_new(sub, limit=25)
        except Exception as e:
            log(f"  fresh fetch failed for r/{sub}: {e}")
            continue
        for p in posts:
            if p["age_hours"] < 2 or p["age_hours"] > 72:
                continue
            if p["score"] < 3 or p["num_comments"] < 1:
                continue
            cands.append({
                "url": p["url"],
                "traffic": 0,
                "source": "fresh",
                "sub": sub,
                "age_hours": p["age_hours"],
                "score": p["score"],
            })
        time.sleep(1)  # polite
    cands.sort(key=lambda p: (p.get("score", 0) * (1 if p.get("age_hours", 0) < 24 else 0.5)),
               reverse=True)
    return cands


def load_wide_search_candidates():
    """Site-wide Reddit search for threads mentioning competitor / EV keywords.
    Captures threads in subs we don't track but where these conversations happen."""
    queries = [
        "oddsjam alternative",
        "best ev betting tool",
        "outlier vs oddsjam",
        "positive ev betting app",
        "line shopping app",
        "best arbitrage betting",
        "sharp betting tools",
        "props cash alternative",
        "prizepicks edge tool",
    ]
    cands = []
    seen_urls = set()
    for q in queries:
        try:
            posts = reddit_search(q, sort="new", t="month", limit=15)
        except Exception as e:
            log(f"  wide search failed for '{q}': {e}")
            continue
        for p in posts:
            url = p["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if p["age_hours"] < 2 or p["age_hours"] > 24 * 30:
                continue
            if p["score"] < 2:
                continue
            cands.append({
                "url": url,
                "traffic": 0,
                "source": "wide",
                "sub": p["subreddit"],
                "query": q,
                "age_hours": p["age_hours"],
                "score": p["score"],
            })
        time.sleep(1)
    cands.sort(key=lambda p: p.get("score", 0), reverse=True)
    return cands


# ── Qualification ────────────────────────────────────────────────────────────

def sub_allowed(subreddit, age_hours):
    s = (subreddit or "").lower()
    if s in SUB_BLOCKLIST:
        return False, "blocklisted subreddit"
    if s in TIER2_SUBS:
        # Tier 2 only for aged threads (>6 months = ~4320 hours)
        if age_hours < 4320:
            return False, f"tier2 sub requires age >= 6 months (got {age_hours/24:.0f}d)"
    return True, ""


def qualify_thread(thread):
    """LLM gate. Returns (score, reason, angle) or (0, reason, 'skip')."""
    ctx = (
        f"Thread URL: {thread['url']}\n"
        f"Subreddit: r/{thread['subreddit']}\n"
        f"Age: {thread['age_hours']:.1f}h | Score: {thread['score']} | Comments: {thread['num_comments']}\n\n"
        f"Title: {thread['title']}\n\n"
        f"Original post:\n{thread['selftext'][:1500]}\n\n"
        f"Top comments:\n" + "\n---\n".join(c[:400] for c in thread["top_comments"][:3])
    )
    try:
        raw = claude_complete(
            system=build_qualifier_system_prompt(),
            user=f"Evaluate this thread:\n\n{ctx}",
            max_tokens=200,
        )
        # strip code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        return int(parsed.get("score", 0)), parsed.get("reason", ""), parsed.get("angle", "skip")
    except Exception as e:
        log(f"  qualify parse error: {e}; raw={raw[:200] if 'raw' in dir() else ''}")
        return 0, f"parse error: {e}", "skip"


# ── Reply generation ─────────────────────────────────────────────────────────

def generate_reply(thread, angle_hint):
    ctx = (
        f"Thread title: {thread['title']}\n\n"
        f"Original post: {thread['selftext'][:1200]}\n\n"
        f"Top comments:\n" + "\n---\n".join(c[:300] for c in thread["top_comments"][:3])
    )
    prompt = (
        f"Write a reply for this Reddit thread. Use angle: {angle_hint}.\n\n{ctx}"
    )
    return claude_complete(
        system=build_writer_system_prompt(),
        user=prompt,
        max_tokens=300,
    )


# ── Posting pipeline ─────────────────────────────────────────────────────────

def daily_post_count(posted_urls_by_day):
    today = datetime.now(timezone.utc).date().isoformat()
    return posted_urls_by_day.get(today, 0)


def run():
    log("=== posting_bot run start ===")
    cr = CrowdReply()

    # Dedupe: load all prior posted URLs + today-per-sub counts
    posted_urls = load_posted_urls()
    today = datetime.now(timezone.utc).date().isoformat()
    daily_count = 0
    per_sub_today = Counter()
    if (ROOT / "posted.jsonl").exists():
        for line in (ROOT / "posted.jsonl").read_text().splitlines():
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("date") == today:
                daily_count += 1
                per_sub_today[entry.get("subreddit", "")] += 1

    if daily_count >= DAILY_CAP:
        log(f"daily cap {DAILY_CAP} hit ({daily_count}); exiting")
        slack_notify(f":brake: posting_bot skipped — daily cap {DAILY_CAP} hit")
        return

    # Surface infrastructure health before doing work
    sa_credits = scraperapi_credits_left()
    if sa_credits is not None and sa_credits < 100:
        log(f"WARN ScraperAPI credits low: {sa_credits}")
        slack_notify(f":warning: ScraperAPI credits low: {sa_credits}. "
                     "Bot will use Reddit OAuth if configured, otherwise stall.")

    # Build candidate queue: fresh + wide-search + aged
    aged = load_aged_candidates()
    fresh = load_fresh_candidates()
    wide = load_wide_search_candidates()
    log(f"aged candidates: {len(aged)}   fresh candidates: {len(fresh)}   wide candidates: {len(wide)}")
    log("aged top 5: " + " | ".join(
        f"{a['traffic']}/mo {a.get('keyword','')[:25]}" for a in aged[:5]))
    log("fresh top 5: " + " | ".join(
        f"r/{f.get('sub','?')}:{f.get('score','?')}up" for f in fresh[:5]))
    log("wide top 5: " + " | ".join(
        f"r/{w.get('sub','?')}:{w.get('score','?')}up [{w.get('query','')[:18]}]" for w in wide[:5]))

    # Mix: 45% from fresh+wide, 55% aged. Oversample 4x for gate rejections.
    fresh_pool = fresh + wide
    random.shuffle(fresh_pool)
    n_fresh = max(2, int(TARGET_PER_RUN * FRESH_SHARE))
    n_aged = TARGET_PER_RUN - n_fresh
    queue = fresh_pool[: n_fresh * 4] + aged[: n_aged * 4]

    posted_this_run = []
    attempts = 0
    for cand in queue:
        if len(posted_this_run) >= TARGET_PER_RUN:
            break
        if daily_count + len(posted_this_run) >= DAILY_CAP:
            log("daily cap reached mid-run; stopping")
            break
        attempts += 1
        url = cand["url"]

        if url in posted_urls:
            continue

        try:
            thread = fetch_thread(url)
        except Exception as e:
            log(f"  skip {url}: fetch failed {e}")
            continue

        ok, reason = sub_allowed(thread["subreddit"], thread["age_hours"])
        if not ok:
            log(f"  skip {url}: {reason}")
            continue

        if per_sub_today[thread["subreddit"]] >= PER_SUB_DAILY_CAP:
            log(f"  skip {url}: r/{thread['subreddit']} daily cap hit")
            continue

        # LLM gate
        score, reason, angle = qualify_thread(thread)
        log(f"  gate r/{thread['subreddit']} score={score} angle={angle} reason={reason[:80]}")
        if score < QUALIFICATION_THRESHOLD or angle == "skip":
            continue

        # Write reply
        try:
            reply = generate_reply(thread, angle)
        except Exception as e:
            log(f"  skip {url}: reply generation failed {e}")
            continue

        # Hard style checks
        if "—" in reply:
            log(f"  skip {url}: reply contained em dash (rewriting would risk pattern)")
            continue
        if "http" in reply.lower() or "optimal-bet.com" in reply.lower():
            log(f"  skip {url}: reply contained URL")
            continue

        # Post it (bundle upvote order so it auto-fires once CrowdReply publishes)
        UPVOTE_QTY = 12
        try:
            task_id = cr.create_comment(url, reply, upvote_quantity=UPVOTE_QTY)
            log(f"  POSTED task={task_id} r/{thread['subreddit']} (upvotes={UPVOTE_QTY} queued)")
            qty = UPVOTE_QTY
        except Exception as e:
            log(f"  FAIL post {url}: {e}")
            continue

        entry = {
            "url": url,
            "subreddit": thread["subreddit"],
            "task_id": task_id,
            "source": cand["source"],
            "score_gate": score,
            "angle": angle,
            "upvotes_ordered": qty,
            "reply": reply,
            "date": today,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        record_posted(entry)
        per_sub_today[thread["subreddit"]] += 1
        posted_urls.add(url)
        posted_this_run.append(entry)

    # Slack digest
    if posted_this_run:
        blocks = [{"type": "header", "text": {"type": "plain_text",
                   "text": f"Reddit bot: {len(posted_this_run)} posted"}}]
        for e in posted_this_run:
            blocks.append({"type": "section", "text": {"type": "mrkdwn",
                           "text": f"*r/{e['subreddit']}* · {e['source']} · gate {e['score_gate']}/10 · {e['angle']}\n<{e['url']}|{e['url'][:80]}>\n>{e['reply'][:240]}"}})
        slack_notify(f"Posted {len(posted_this_run)}/{TARGET_PER_RUN} this run", blocks=blocks)
    else:
        slack_notify(f":warning: Reddit bot: 0 posted this run ({attempts} candidates evaluated)")

    log(f"=== run end: posted {len(posted_this_run)}/{TARGET_PER_RUN}, attempts {attempts} ===")


if __name__ == "__main__":
    run()
