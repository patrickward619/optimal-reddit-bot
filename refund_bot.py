#!/usr/bin/env python3
"""
Optimal Bet CrowdReply refund sweep.

Runs 1x/day via GitHub Actions. CrowdReply only allows refunds within 72h
of posting, so daily cadence keeps worst-case latency well inside the window.

For each CrowdReply task:
  - status == "published"
  - redditStatus == "removed"  (comment was removed on Reddit)
  - isRefunded != True
  - no pre-existing refundError

→ POST /api/tasks/{id}/refund
"""

from datetime import datetime, timedelta, timezone

from lib import CrowdReply, log, slack_notify


REFUND_WINDOW_HOURS = 72


def run():
    log("=== refund_bot run start ===")
    cr = CrowdReply()

    tasks = cr.list_tasks(max_pages=10)
    log(f"fetched {len(tasks)} tasks")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=REFUND_WINDOW_HOURS)

    refunded = []
    skipped_window = 0
    already_refunded = 0
    not_removed = 0
    errored_prior = 0

    for t in tasks:
        status = t.get("status")
        reddit_status = t.get("redditStatus")
        is_refunded = bool(t.get("isRefunded"))
        prior_err = t.get("refundError")

        if is_refunded:
            already_refunded += 1
            continue
        if reddit_status != "removed":
            not_removed += 1
            continue
        if prior_err:
            errored_prior += 1
            continue
        if status != "published":
            continue

        # check age — refunds only accepted within 72h of posting
        created = t.get("createdAt") or t.get("assignedAt")
        if created:
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except Exception:
                created_dt = None
        else:
            created_dt = None

        if created_dt and created_dt < cutoff:
            skipped_window += 1
            continue

        task_id = t["_id"]
        ok, detail = cr.refund(task_id)
        refunded.append({
            "task_id": task_id,
            "subreddit": t.get("subreddit"),
            "thread": t.get("threadUrl"),
            "price": t.get("clientPrice"),
            "success": ok,
            "detail": detail,
        })
        log(f"  refund {task_id} r/{t.get('subreddit')} ${t.get('clientPrice')}: {detail}")

    successes = [r for r in refunded if r["success"]]
    failures = [r for r in refunded if not r["success"]]
    recovered = sum(r.get("price", 0) for r in successes)

    summary = (
        f"Refund sweep: {len(successes)} refunded (${recovered}), "
        f"{len(failures)} errored, {skipped_window} outside 72h window, "
        f"{already_refunded} already refunded, {not_removed} not removed."
    )
    log(summary)

    blocks = [{"type": "header", "text": {"type": "plain_text", "text": "Refund sweep"}},
              {"type": "section", "text": {"type": "mrkdwn", "text": summary}}]
    if successes:
        detail = "\n".join(f"• r/{r['subreddit']} ${r['price']} · {r['task_id']}" for r in successes[:15])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Refunded:*\n{detail}"}})
    if failures:
        detail = "\n".join(f"• r/{r['subreddit']} ${r['price']} · {r['detail'][:80]}" for r in failures[:10])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Errored:*\n{detail}"}})

    slack_notify(summary, blocks=blocks)
    log("=== refund_bot run end ===")


if __name__ == "__main__":
    run()
