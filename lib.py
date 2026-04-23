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

REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.environ.get("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.environ.get("REDDIT_PASSWORD", "")
REDDIT_UA_STRING = "optimal-bet-bot/0.1 by " + (REDDIT_USERNAME or "anon")

REDDIT_UA = {"User-Agent": REDDIT_UA_STRING}

_reddit_token = {"value": None, "exp": 0}


def reddit_oauth_token():
    """Fetch + cache a Reddit OAuth access token (script-app password grant)."""
    if _reddit_token["value"] and time.time() < _reddit_token["exp"]:
        return _reddit_token["value"]
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD]):
        return None
    import base64
    auth = base64.b64encode(
        f"{REDDIT_CLIENT_ID}:{REDDIT_CLIENT_SECRET}".encode()
    ).decode()
    data = urllib.parse.urlencode({
        "grant_type": "password",
        "username": REDDIT_USERNAME,
        "password": REDDIT_PASSWORD,
    }).encode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token",
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "User-Agent": REDDIT_UA_STRING,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())
    _reddit_token["value"] = resp["access_token"]
    _reddit_token["exp"] = time.time() + resp.get("expires_in", 3600) - 60
    return resp["access_token"]


def reddit_get(path):
    """GET against oauth.reddit.com if creds are set, else fall back to public JSON."""
    token = reddit_oauth_token()
    if token:
        url = "https://oauth.reddit.com" + path
        headers = {
            "Authorization": f"bearer {token}",
            "User-Agent": REDDIT_UA_STRING,
        }
    else:
        url = "https://www.reddit.com" + path
        headers = REDDIT_UA
    return http_get(url, headers=headers)

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
    # convert full URL to path + .json suffix for OAuth endpoint
    from urllib.parse import urlparse
    parsed = urlparse(reddit_url)
    path = parsed.path.rstrip("/") + ".json?limit=10&sort=top"
    data = reddit_get(path)
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
    data = reddit_get(f"/r/{subreddit}/new.json?limit={limit}")
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
