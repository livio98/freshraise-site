#!/usr/bin/env python3
"""
RoundSignal — X (Twitter) auto-poster.

Posts the account's OWN content from a queue via the OFFICIAL X API v2, using
OAuth 1.0a user context (tokens that don't expire — ideal for a headless cron).

ToS-compliant by design: it ONLY publishes our own posts/replies. It never likes,
follows, retweets, scrapes, or reads other users' content — those are the things
that get accounts suspended. See LINKEDIN-POLICY-DOCTRINE.md / ASSIST-ONLY-PLAYBOOK.md.

Queue: social/x-queue.json  ->  {"posts": [ {id, status, text, in_reply_to} ]}
  status meanings:
    "draft"   -> ignored (parking/review state)
    "pending" -> will be posted (oldest first, up to X_MAX_PER_RUN per run)
    "posted"  -> already sent (tweet_id recorded)
    "error"   -> send failed (error recorded; fix + set back to "pending" to retry)

Env (GitHub Actions secrets): X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
Optional: X_MAX_PER_RUN (default "1" — keep it human-paced)
"""
import json
import os
import sys
import pathlib

QUEUE = pathlib.Path(__file__).resolve().parent.parent / "social" / "x-queue.json"
ENDPOINT = "https://api.twitter.com/2/tweets"


def main() -> int:
    ck = os.environ.get("X_API_KEY")
    cs = os.environ.get("X_API_SECRET")
    at = os.environ.get("X_ACCESS_TOKEN")
    ats = os.environ.get("X_ACCESS_TOKEN_SECRET")
    if not all([ck, cs, at, ats]):
        print("X secrets not set — skipping (nothing posted).")
        return 0

    if not QUEUE.exists():
        print(f"No queue file at {QUEUE} — nothing to post.")
        return 0

    from requests_oauthlib import OAuth1Session

    data = json.loads(QUEUE.read_text(encoding="utf-8"))
    posts = data.get("posts", [])
    pending = [p for p in posts if p.get("status") == "pending"]
    if not pending:
        print("No pending posts.")
        return 0

    max_per_run = int(os.environ.get("X_MAX_PER_RUN", "1"))
    oauth = OAuth1Session(ck, client_secret=cs,
                          resource_owner_key=at, resource_owner_secret=ats)

    posted = 0
    for p in pending:
        if posted >= max_per_run:
            break
        text = (p.get("text") or "").strip()
        if not text:
            p["status"] = "error"
            p["error"] = "empty text"
            continue
        if len(text) > 280:
            p["status"] = "error"
            p["error"] = f"too long ({len(text)} > 280 chars)"
            print(f"SKIP (too long): {text[:60]}...", file=sys.stderr)
            continue
        payload = {"text": text}
        if p.get("in_reply_to"):
            payload["reply"] = {"in_reply_to_tweet_id": str(p["in_reply_to"])}
        try:
            r = oauth.post(ENDPOINT, json=payload, timeout=30)
        except Exception as e:  # noqa: BLE001
            p["status"] = "error"
            p["error"] = f"request failed: {e}"
            print(f"ERROR (request): {e}", file=sys.stderr)
            continue
        if r.status_code in (200, 201):
            tid = (r.json().get("data") or {}).get("id")
            p["status"] = "posted"
            p["tweet_id"] = tid
            posted += 1
            print(f"Posted -> {tid}: {text[:70]}")
        else:
            p["status"] = "error"
            p["error"] = f"{r.status_code}: {r.text[:300]}"
            print(f"ERROR ({r.status_code}): {r.text[:300]}", file=sys.stderr)

    QUEUE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Done. Posted {posted} this run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
