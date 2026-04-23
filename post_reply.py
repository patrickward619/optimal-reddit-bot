#!/usr/bin/env python3
"""
Post a reply to a Reddit thread via CrowdReply and automatically buy
enough upvotes to become the top comment (top comment upvotes + 8).

Usage:
    python3 post_reply.py <thread_url> <reply_content>

Example:
    python3 post_reply.py "https://reddit.com/r/sportsbook/comments/abc123/..." "Only way to be profitable long term is +EV..."
"""

import sys
import json
import time
import urllib.request
import urllib.error

import os

API_KEY = os.environ.get("CROWDREPLY_API_KEY", "")
PROJECT_ID = os.environ.get("CROWDREPLY_PROJECT_ID", "")
BASE_URL = "https://crowdreply.io/api"

if not API_KEY or not PROJECT_ID:
    raise RuntimeError("Set CROWDREPLY_API_KEY and CROWDREPLY_PROJECT_ID in env (source .env)")

HEADERS = {
    "x-api-key": API_KEY,
    "Content-Type": "application/json"
}


def api_call(method, endpoint, body=None):
    url = f"{BASE_URL}{endpoint}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def post_comment(thread_url, content):
    print(f"📝 Posting comment to: {thread_url}")
    result = api_call("POST", "/tasks", {
        "taskType": "comment",
        "type": "RedditCommentTask",
        "platform": "reddit",
        "project": PROJECT_ID,
        "content": content,
        "threadUrl": thread_url
    })
    task_id = result["_id"]
    print(f"✓ Comment posted — Task ID: {task_id}")
    return task_id


def get_top_comment_upvotes(task_id):
    print("🔍 Fetching thread data to find top comment upvotes...")
    # Give CrowdReply a moment to fetch thread data
    time.sleep(3)
    task = api_call("GET", f"/tasks/{task_id}")
    top_upvotes = task.get("topLevelCommentUpvotes", 0)
    print(f"✓ Top comment currently has {top_upvotes} upvotes")
    return top_upvotes


def buy_upvotes(task_id, quantity):
    print(f"👍 Buying {quantity} upvotes (top comment + 8)...")
    api_call("POST", f"/tasks/{task_id}/upvotes", {
        "delivery": {
            "upvotesPerInterval": 2,
            "intervalUnit": "day"
        },
        "quantity": quantity,
        "triggerAt": None
    })
    print(f"✓ Upvote order placed for {quantity} upvotes (delivered 2/day)")


def run(thread_url, content):
    # Step 1: Post the comment
    task_id = post_comment(thread_url, content)

    # Step 2: Get top comment upvote count
    top_upvotes = get_top_comment_upvotes(task_id)

    # Step 3: Buy top upvotes + 8
    upvotes_to_buy = top_upvotes + 8
    buy_upvotes(task_id, upvotes_to_buy)

    print(f"\n🎯 Done! Comment posted and {upvotes_to_buy} upvotes ordered.")
    print(f"   Task ID: {task_id}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 post_reply.py <thread_url> <reply_content>")
        sys.exit(1)

    thread_url = sys.argv[1]
    content = sys.argv[2]
    run(thread_url, content)
