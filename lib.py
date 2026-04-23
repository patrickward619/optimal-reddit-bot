"""
Shared utilities for Optimal Bet Reddit bots.

Used by reddit_bot.py (posting) and refund_bot.py (refund sweep).
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent

CROWDREPLY_BASE = "https://crowdreply.io/api"
CROWDREPLY_API_KEY = os.environ.get("CROWDREPLY_API_KEY", "")
CROWDREPLY_PROJECT_ID = os.environ.get("CROWDREPLY_PROJECT_ID", "")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

REDDIT_UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}

POSTED_LOG = ROOT / "posted.jsonl"
RUN_LOG = ROOT / "log.txt"


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def http_get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def http_post(url, body, headers=None, timeout=20):
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read())
        except Exception:
            payload = {"error": str(e)}
        return e.code, payload


# ── Style guide + system prompt ──────────────────────────────────────────────

def load_style_guide():
    path = ROOT / "STYLE_GUIDE.md"
    if not path.exists():
        raise FileNotFoundError(f"STYLE_GUIDE.md not found at {path}")
    return path.read_text()


def build_writer_system_prompt():
    guide = load_style_guide()
    return (
        "You write Reddit replies for Optimal Bet. Follow this style guide "
        "EXACTLY — every rule is load-bearing. Reply with ONLY the comment "
        "text, no intro or explanation.\n\n"
        "=== STYLE GUIDE ===\n\n" + guide
    )


def build_qualifier_system_prompt():
    guide = load_style_guide()
    return (
        "You evaluate whether a Reddit thread is a good target for an "
        "Optimal Bet mention. Score 1-10 on TWO combined dimensions:\n"
        "  (a) Will the mention feel natural + helpful given the thread?\n"
        "  (b) Will it survive moderator / auto-mod removal?\n\n"
        "Use the style guide for the voice/placement rules you are scoring "
        "against. Your entire response must be valid JSON: "
        '{\"score\": <int 1-10>, \"reason\": \"<1 sentence>\", '
        '\"angle\": \"<one of: price|banned|sharp_data|not_ev|beginner|clv|'
        'prediction_markets|pikkit|skip>\"}\n\n'
        "=== STYLE GUIDE ===\n\n" + guide
    )


# ── Claude client ────────────────────────────────────────────────────────────

def claude_complete(system, user, max_tokens=400):
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    code, data = http_post(
        "https://api.anthropic.com/v1/messages",
        {
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        timeout=45,
    )
    if code != 200:
        raise RuntimeError(f"Claude API {code}: {data}")
    return data["content"][0]["text"].strip()


# ── CrowdReply client ────────────────────────────────────────────────────────

class CrowdReply:
    def __init__(self, api_key=None, project_id=None):
        self.api_key = api_key or CROWDREPLY_API_KEY
        self.project_id = project_id or CROWDREPLY_PROJECT_ID
        if not self.api_key:
            raise RuntimeError("CROWDREPLY_API_KEY not set")
        self.headers = {"x-api-key": self.api_key}

    def list_tasks(self, max_pages=10):
        all_tasks = []
        seen = set()
        for page in range(1, max_pages + 1):
            data = http_get(
                f"{CROWDREPLY_BASE}/tasks?pageNum={page}",
                headers=self.headers,
            )
            lst = data.get("list", []) if isinstance(data, dict) else data
            if not lst:
                break
            for t in lst:
                if t["_id"] in seen:
                    continue
                seen.add(t["_id"])
                all_tasks.append(t)
            if len(lst) < 20:
                break
        return all_tasks

    def get_task(self, task_id):
        return http_get(f"{CROWDREPLY_BASE}/tasks/{task_id}", headers=self.headers)

    def create_comment(self, thread_url, content):
        code, data = http_post(
            f"{CROWDREPLY_BASE}/tasks",
            {
                "taskData": {
                    "taskType": "comment",
                    "type": "RedditCommentTask",
                    "platform": "reddit",
                    "project": self.project_id,
                    "content": content,
                    "threadUrl": thread_url,
                }
            },
            headers=self.headers,
        )
        if code >= 400:
            raise RuntimeError(f"CrowdReply create_comment {code}: {data}")
        return data["newTask"]["_id"]

    def buy_upvotes(self, task_id, quantity):
        if quantity <= 0:
            quantity = 8
        code, data = http_post(
            f"{CROWDREPLY_BASE}/tasks/{task_id}/upvotes",
            {
                "delivery": {"upvotesPerInterval": 2, "intervalUnit": "day"},
                "quantity": quantity,
                "triggerAt": None,
            },
            headers=self.headers,
        )
        if code >= 400:
            raise RuntimeError(f"CrowdReply buy_upvotes {code}: {data}")
        return data

    def get_top_upvotes(self, task_id):
        time.sleep(4)
        task = self.get_task(task_id)
        return task.get("topLevelCommentUpvotes", 0)

    def refund(self, task_id):
        """Returns (success: bool, detail: str)."""
        code, data = http_post(
            f"{CROWDREPLY_BASE}/tasks/{task_id}/refund",
            {},
            headers=self.headers,
        )
        if code == 200:
            return True, "refunded"
        return False, data.get("error", f"HTTP {code}: {data}")

    def balance(self):
        return http_get(f"{CROWDREPLY_BASE}/billing/balance", headers=self.headers)


# ── Slack ─────────────────────────────────────────────────────────────────────

def slack_notify(text, blocks=None, webhook_url=None):
    url = webhook_url or SLACK_WEBHOOK_URL
    if not url:
        return
    body = {"text": text}
    if blocks:
        body["blocks"] = blocks
    try:
        http_post(url, body, timeout=10)
    except Exception as e:
        print(f"Slack notify failed: {e}")


def slack_block_section(text):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


# ── Dedup log ────────────────────────────────────────────────────────────────

def load_posted_urls():
    if not POSTED_LOG.exists():
        return set()
    urls = set()
    for line in POSTED_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            urls.add(json.loads(line)["url"])
        except Exception:
            continue
    return urls


def record_posted(entry):
    with POSTED_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Log ──────────────────────────────────────────────────────────────────────

def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()}  {msg}"
    print(line, flush=True)
    try:
        with RUN_LOG.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Reddit helpers ───────────────────────────────────────────────────────────

def fetch_thread(reddit_url):
    json_url = reddit_url.rstrip("/") + ".json?limit=10&sort=top"
    data = http_get(json_url, headers=REDDIT_UA)
    post = data[0]["data"]["children"][0]["data"]
    title = post.get("title", "")
    selftext = post.get("selftext", "")
    subreddit = post.get("subreddit", "")
    num_comments = post.get("num_comments", 0)
    score = post.get("score", 0)
    locked = post.get("locked", False) or post.get("archived", False)
    created_utc = post.get("created_utc", 0)

    comments = []
    for child in data[1]["data"]["children"][:5]:
        if child["kind"] == "t1":
            body = child["data"].get("body", "")
            if body and body not in ("[deleted]", "[removed]"):
                comments.append(body)

    return {
        "url": reddit_url,
        "title": title,
        "selftext": selftext,
        "subreddit": subreddit,
        "num_comments": num_comments,
        "score": score,
        "locked": locked,
        "created_utc": created_utc,
        "age_hours": (time.time() - created_utc) / 3600 if created_utc else 0,
        "top_comments": comments,
    }


def fetch_subreddit_new(subreddit, limit=25):
    """Fetch newest posts from a subreddit, return list of post dicts."""
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
    data = http_get(url, headers=REDDIT_UA)
    posts = []
    for child in data["data"]["children"]:
        if child["kind"] != "t3":
            continue
        p = child["data"]
        if p.get("stickied") or p.get("locked") or p.get("archived"):
            continue
        posts.append({
            "url": "https://www.reddit.com" + p.get("permalink", ""),
            "title": p.get("title", ""),
            "selftext": p.get("selftext", "") or "",
            "subreddit": p.get("subreddit", ""),
            "num_comments": p.get("num_comments", 0),
            "score": p.get("score", 0),
            "created_utc": p.get("created_utc", 0),
            "age_hours": (time.time() - p.get("created_utc", 0)) / 3600,
        })
    return posts
