# Optimal Bet Reddit Bot

Fully automated Reddit comment + refund pipeline for Optimal Bet via CrowdReply. Runs entirely on GitHub Actions with no laptop dependency.

Repo: https://github.com/patrickward619/optimal-reddit-bot

---

## Three scheduled jobs

| Job | When (ET) | When (UTC) | Workflow file | Script |
|---|---|---|---|---|
| **`posting-bot`** | 9am, 3pm, 8pm | 13:00, 19:00, 00:00 | `.github/workflows/posting.yml` | `reddit_bot.py` |
| **`refund-sweep`** | 8am | 12:00 | `.github/workflows/refund.yml` | `refund_bot.py` |
| **`discover-evergreen`** | Sundays 7am | Sundays 11:00 | `.github/workflows/discover.yml` | `discover.py` |

All three post a digest to Slack `#redditbot` after running.

---

## What each posting run does

1. **Build candidate pool** (~180 threads):
   - Aged bucket (~70%): US-targeted Ahrefs URLs from `inbox/*.csv` + Reddit-discovered evergreen threads from `inbox/_reddit_discovery.csv`
   - Fresh bucket (~30%): pulls `/r/{evbetting,positiveevbetting,arbitragebetting,algobetting,sportsbettingpicks}/new.json` from last 24h
2. **Filter**: subreddit allowlist, dedup against `posted.jsonl`, age 2–72h for fresh, score ≥3
3. **LLM qualifier gate** (Claude Sonnet 4.6): scores each thread 1–10 on "natural fit + survives mod removal". Only ≥7 passes.
4. **Generate reply** following `STYLE_GUIDE.md`: no em dashes, no URLs, single brand mention, casual voice, ≤3 sentences, one rotating angle from the angle library.
5. **Post to CrowdReply** with `brand` + `project` + `initialUpvotesOrder` (12 upvotes, 1 every 10 minutes — fires automatically once CrowdReply publishes the comment).
6. **Record** to `posted.jsonl` (committed back to repo for dedup).
7. **Slack digest**: posted threads, gate scores, angles, and skip reasons.

### Daily caps (in `reddit_bot.py`)

- 4 posts per run
- 15 posts per day total
- 2 posts per subreddit per day
- 1 post per thread, ever (enforced via `posted.jsonl`)

---

## What refund-sweep does

1. List every CrowdReply task created in the last 30 days.
2. Filter to: `redditStatus="removed"` AND `isRefunded=false` AND no prior `refundError` AND `<72h since post` (CrowdReply's refund window).
3. POST `/api/tasks/{id}/refund` for each.
4. Slack digest: "X refunded ($Y), Z outside window, A already refunded, B not removed."

> **Critical:** CrowdReply only allows refunds within 72 hours of posting. Daily cadence keeps worst-case latency at ~28h, well inside the window.

---

## What discovery does (Sundays)

1. For each target sub, pull top posts of the past **year** via Reddit JSON (through ScraperAPI).
2. Filter for posts mentioning competitor / EV-betting keywords (oddsjam, +ev, arb, line shopping, clv, prizepicks, kalshi, polymarket, etc.).
3. Score each by upvotes + comments → synthetic "traffic" proxy.
4. Write `inbox/_reddit_discovery.csv` (Ahrefs-shaped so `reddit_bot.py` consumes it natively), commit, push.
5. Slack digest of top 8 finds.

---

## Style guide is the source of truth

`STYLE_GUIDE.md` governs every reply: voice, banned phrases, target subs, angle library, hard rules. Both the writer prompt and the qualifier prompt read this file at runtime.

**Edit `STYLE_GUIDE.md`, push, and the next run uses the new rules.** No code change needed.

---

## Architecture

```
GitHub Actions cron schedule
     │
     ├── posting.yml ──► reddit_bot.py ──► lib.py ──┬── ScraperAPI ──► Reddit JSON
     │                                              ├── Claude API (qualify + write)
     │                                              ├── CrowdReply API (post + upvotes)
     │                                              └── Slack webhook
     │
     ├── refund.yml ──► refund_bot.py ──► lib.py ──┬── CrowdReply API (list + refund)
     │                                             └── Slack webhook
     │
     └── discover.yml ──► discover.py ──► lib.py ──┬── ScraperAPI ──► Reddit JSON
                                                   └── Slack webhook
```

External services (5 secrets in GH Actions):
- **CrowdReply** — posts comments + buys upvotes (CROWDREPLY_API_KEY, CROWDREPLY_PROJECT_ID)
- **Anthropic** — Claude Sonnet 4.6 for qualify + write (ANTHROPIC_API_KEY)
- **ScraperAPI** — residential proxy for Reddit reads, GH Actions IPs are blocked by Reddit (SCRAPERAPI_KEY)
- **Slack** — incoming webhook to `#redditbot` (SLACK_WEBHOOK_URL)

---

## Files

| File | Role |
|---|---|
| `STYLE_GUIDE.md` | Voice + rules + subreddit allowlist (source of truth, edit anytime) |
| `lib.py` | Shared: HTTP helpers, Claude client, CrowdReply client, Slack, dedup, ScraperAPI Reddit fetch |
| `reddit_bot.py` | Posting bot — main entry for the 3×/day cron |
| `refund_bot.py` | Refund sweep — daily cron |
| `discover.py` | Evergreen discovery — weekly cron |
| `post_reply.py` | Manual one-off poster (ad-hoc use, reads env) |
| `tools/import_csv.sh` | One-command Ahrefs CSV refresh: `./tools/import_csv.sh ~/Downloads/export.csv` |
| `inbox/*.csv` | Source threads — manual Ahrefs exports + auto-generated `_reddit_discovery.csv` |
| `posted.jsonl` | Dedup log (auto-committed by bot after each run) |
| `log.txt` | Run log |
| `.github/workflows/*.yml` | Cron schedules + secrets injection + commit-back |

---

## Your only recurring manual job

**Once a quarter (5 min):** Export a fresh "Top Pages" report from the Ahrefs UI for `www.reddit.com` filtered to your niche. Run:

```bash
./tools/import_csv.sh ~/Downloads/ahrefs-export.csv
```

That copies it into `inbox/`, commits, pushes. The next scheduled posting run picks it up.

(The weekly Reddit-discovery job already keeps the candidate pool fresh between Ahrefs imports — quarterly is just for SEO precision.)

---

## Tweaking behavior

| What | Where |
|---|---|
| Posting cadence | `cron:` lines in `.github/workflows/posting.yml` |
| Voice / banned phrases / hard rules | `STYLE_GUIDE.md` |
| Subreddit allowlist / Tier 1 vs Tier 2 / blocklist | `STYLE_GUIDE.md` + `FRESH_SUBS` / `TIER2_SUBS` / `SUB_BLOCKLIST` in `reddit_bot.py` |
| Daily caps | `TARGET_PER_RUN`, `DAILY_CAP`, `PER_SUB_DAILY_CAP` in `reddit_bot.py` |
| LLM gate threshold | `QUALIFICATION_THRESHOLD` in `reddit_bot.py` (default 7/10) |
| Discovery keyword filter | `DISCOVERY_KEYWORDS` in `discover.py` |
| Upvote quantity per post | `UPVOTE_QTY` in `reddit_bot.py` (default 12) |

---

## Day-to-day for you

**Nothing.** Open Slack, glance at digests, intervene only if something looks off.

If you want to expand into new subs / new keywords / new angles → edit `STYLE_GUIDE.md`, push.

---

## Local dev (if you ever need to test changes)

```bash
cp .env.example .env    # fill in keys
python3 reddit_bot.py   # one-shot test run
python3 refund_bot.py
python3 discover.py
```

---

## Operational caveats / known constraints

- **Reddit blocks GitHub Actions IPs** → all Reddit reads go through ScraperAPI residential proxy (free 5K req/mo, we use ~500/mo).
- **Anthropic Tier 1 rate limits** (30K input TPM) can throttle bursty runs; `claude_complete()` retries 429s with exponential backoff (4s/8s/16s). Upgrade to Tier 2 ($40 cumulative deposit) for headroom.
- **CrowdReply 72h refund window** is fixed — daily refund sweep keeps worst-case latency well inside it.
- **CrowdReply API requires `brand` field** — hardcoded as `69b987e1449642b9bf031930` (Optimal-bet brand) in `lib.py`, env-overridable via `CROWDREPLY_BRAND_ID`.
- **Repo is public** → no secrets in code, only in GH Actions secrets.

---

## Setup history (for reference; one-time, already done)

1. Repo created, made public for unlimited Actions minutes.
2. Five GH Actions secrets configured.
3. Slack incoming webhook to `#redditbot`.
4. Initial Ahrefs CSV (March 2026 export, 86 US threads) seeded in `inbox/`.
5. First successful posting run: 4/4 posted on 2026-04-28.
