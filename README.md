# Optimal Bet Reddit Bot

Automated Reddit posting + refund management for Optimal Bet via CrowdReply.

## What it does

Two jobs running in GitHub Actions:

| Job | Schedule | What |
|---|---|---|
| `reddit_bot.py` | 3×/day (9am, 3pm, 8pm ET) | Finds qualified threads (aged Ahrefs + fresh `/r/sub/new`), gates with Claude, posts via CrowdReply, buys upvotes |
| `refund_bot.py` | 1×/day (8am ET) | Sweeps removed comments, requests refunds inside CrowdReply's 72h window |

Both post a digest to Slack.

## Files

- `STYLE_GUIDE.md` — source of truth for voice, rules, and allowlists. The writer + qualifier prompts read this at runtime.
- `lib.py` — shared HTTP, Claude, CrowdReply, Slack, dedup helpers.
- `reddit_bot.py` — posting bot (main entry for 3×/day).
- `refund_bot.py` — refund sweep.
- `post_reply.py` — manual one-off poster (unchanged, for ad-hoc use).
- `inbox/*.csv` — drop Ahrefs CSV exports here; bot reads + moves to `processed/`.
- `posted.jsonl` — dedup log (committed by bot after each run).
- `log.txt` — run log.

## Setup

### 1. GitHub repo
```bash
gh repo create optimal-reddit-bot --private --source=. --push
```

### 2. Secrets
In `Settings → Secrets and variables → Actions`, add:

- `CROWDREPLY_API_KEY`
- `CROWDREPLY_PROJECT_ID`
- `ANTHROPIC_API_KEY`
- `SLACK_WEBHOOK_URL` (see below)

### 3. Slack webhook
1. https://api.slack.com/apps → **Create New App** → From scratch → name it `Reddit Bot` in your workspace.
2. **Incoming Webhooks** → toggle on → **Add New Webhook to Workspace** → pick a channel (suggest `#reddit-bot`).
3. Copy the `https://hooks.slack.com/services/...` URL and paste into the `SLACK_WEBHOOK_URL` GitHub secret.

### 4. Verify
Trigger manually from the Actions tab: `posting-bot` → **Run workflow**. Should post to Slack within ~1 minute.

## Adding new Ahrefs targets

1. Export an Ahrefs top-pages CSV (filter for `subdomain = www.reddit.com`, sort by traffic).
2. Drop into `inbox/`, commit, push.
3. Next posting run consumes the top entries and moves file to `processed/`.

## Tweaking behavior

- **Posting cadence** → edit `cron:` lines in `.github/workflows/posting.yml`.
- **Subreddit allow/blocklist** → `FRESH_SUBS`, `TIER2_SUBS`, `SUB_BLOCKLIST` at top of `reddit_bot.py`.
- **Style rules** → `STYLE_GUIDE.md`. Changes take effect on next run (no code change needed).
- **Daily caps** → `TARGET_PER_RUN`, `DAILY_CAP`, `PER_SUB_DAILY_CAP` in `reddit_bot.py`.

## Local dev

```bash
cp .env.example .env    # fill in keys
python3 reddit_bot.py   # one-shot test run
python3 refund_bot.py
```
