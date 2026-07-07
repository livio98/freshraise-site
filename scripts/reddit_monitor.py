#!/usr/bin/env python3
"""
RoundSignal — Reddit READ-ONLY monitor.

Surfaces (a) mentions of RoundSignal and (b) fresh relevant threads worth engaging,
into a digest file. It NEVER posts, comments, votes, or messages — pure read.

Why read-only: our Reddit account is flagged as "AI content"; automated posting
would be filtered anyway AND against Reddit's Responsible Builder Policy. Reading
to find opportunities is fully compliant and zero ban-risk. The human still writes
and posts any reply manually (assist-only). See ASSIST-ONLY-PLAYBOOK.md.

Uses app-only (read-only) OAuth — it does NOT log in as the flagged account.
Env (GitHub Actions secrets): REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET
Optional: REDDIT_USER_AGENT (default "roundsignal-monitor/1.0")
Output: social/reddit-digest.md
"""
import os
import sys
import pathlib
import datetime

OUT = pathlib.Path(__file__).resolve().parent.parent / "social" / "reddit-digest.md"

SUBS = [
    "SaaS", "Entrepreneur", "startups", "sales", "smallbusiness",
    "EntrepreneurRideAlong", "microsaas", "growmybusiness", "advancedentrepreneur",
]
MENTION_TERMS = ["roundsignal", "getroundsignal"]
INTENT_TERMS = [
    "recently funded", "just raised", "just closed", "sell to startups",
    "funded startups", "outbound list", "lead list", "buying signals",
    "who to contact", "find startups", "cold outbound", "prospect list",
    "series a", "seed round",
]


def main() -> int:
    cid = os.environ.get("REDDIT_CLIENT_ID")
    csec = os.environ.get("REDDIT_CLIENT_SECRET")
    ua = os.environ.get("REDDIT_USER_AGENT", "roundsignal-monitor/1.0")
    if not (cid and csec):
        print("Reddit secrets not set — skipping.")
        return 0

    import praw

    reddit = praw.Reddit(client_id=cid, client_secret=csec, user_agent=ua)
    reddit.read_only = True

    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# RoundSignal — Reddit monitor digest",
        f"_generated {now} UTC · read-only, no posting_",
        "",
        "## Mentions of RoundSignal",
    ]

    found_mention = False
    for term in MENTION_TERMS:
        try:
            for s in reddit.subreddit("all").search(term, sort="new", time_filter="week", limit=10):
                found_mention = True
                lines.append(f"- **{s.title}** — r/{s.subreddit.display_name} — https://reddit.com{s.permalink}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"- (search error for '{term}': {e})")
    if not found_mention:
        lines.append("- none this week")

    lines += ["", "## Relevant fresh threads (engagement opportunities)"]
    seen = set()
    for sub in SUBS:
        try:
            for s in reddit.subreddit(sub).new(limit=30):
                blob = (s.title + " " + (s.selftext or "")).lower()
                if s.id not in seen and any(t in blob for t in INTENT_TERMS):
                    seen.add(s.id)
                    lines.append(f"- [r/{sub}] **{s.title}** — https://reddit.com{s.permalink}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"- (error reading r/{sub}: {e})")
    if not seen:
        lines.append("- none matched the intent keywords in the latest posts")

    lines += [
        "",
        "> Assist-only: review these; I'll draft ultra-human replies for the ones worth it. "
        "Federico posts manually. Never auto-post from the flagged account.",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote digest: {len(seen)} opportunities, mentions={'yes' if found_mention else 'no'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
