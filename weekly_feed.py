#!/usr/bin/env python3
"""RoundSignal — self-contained weekly builder for GitHub Actions.

One file, no local package: the GitHub Actions cron runs this weekly, it builds
the issue (live funding feeds -> Claude -> scored accounts) and writes
feed.xml + sample.html + sample.csv to the repo root. The workflow then commits
them; GitHub Pages serves them; beehiiv RSS-to-Send (Max plan) emails the new
issue to subscribers. Fully unattended.

Needs only the ANTHROPIC_API_KEY env var (a GitHub Actions secret).
Deps: anthropic, feedparser, tenacity  (see requirements-ci.txt).
"""
from __future__ import annotations

import csv
import datetime as _dt
import html
import io
import json
import os
import sys
import time
from dataclasses import dataclass
from email.utils import format_datetime
from urllib.parse import quote_plus

import anthropic
import feedparser
from tenacity import retry, stop_after_attempt, wait_exponential

sys.stdout.reconfigure(encoding="utf-8")

# ── Config ───────────────────────────────────────────────────────────────────
MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "https://getroundsignal.com").rstrip("/")
USER_AGENT = "RoundSignal/1.0 (signals@getroundsignal.com)"
INK, ACCENT = "#0B1F3A", "#1FB6A6"

DEFAULT_FEEDS = [
    {"source": "FinSMEs", "signal": "funding", "url": "https://www.finsmes.com/feed/"},
    {"source": "TechCrunch Funding", "signal": "funding", "url": "https://techcrunch.com/tag/funding/feed/"},
    {"source": "EU-Startups", "signal": "funding", "url": "https://www.eu-startups.com/feed/"},
    {"source": "Tech.eu", "signal": "funding", "url": "https://tech.eu/feed/"},
    {"source": "Crunchbase News", "signal": "funding", "url": "https://news.crunchbase.com/feed/"},
    {"source": "SEC EDGAR Form D", "signal": "funding-filing",
     "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=D&output=atom"},
]
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

LAUNCH_VERTICAL = {
    "name": "Freshly-funded B2B SaaS & tech startups (pre-seed -> Series B)",
    "audience": "agencies, freelancers and B2B service providers (dev, design, "
                "growth/marketing, recruiting, fractional CFO/CMO, RevOps, PR) "
                "who sell services TO recently-funded startups",
    "queries": [
        '"seed round" B2B SaaS raised funding',
        '"Series A" SaaS startup raises',
        'B2B software startup secures funding',
        'pre-seed startup raises round',
    ],
    "max_accounts": 25,
}

_EXTRACT_SYSTEM = (
    "You are the analyst behind RoundSignal, a weekly buying-signal digest. Your "
    "readers SELL services to freshly-funded startups and act within 48h of a "
    "raise. From a noisy stream of news items you produce a clean, DE-DUPLICATED, "
    "ranked list of real funding/expansion events that fit the target vertical. "
    "Be ruthless: drop duplicates, drop items that are not a genuine company "
    "funding/expansion trigger, drop anything off-vertical. Never invent a "
    "company, amount, or fact not supported by the item. If the amount/round is "
    "unknown, say so rather than guessing.\n"
    "TRUST RULES (the brand lives or dies on these): (1) When the same company "
    "appears in several items - including an SEC Form-D filing - MERGE them into "
    "one entry and treat multi-source corroboration as higher heat/confidence. "
    "(2) Put the source name and the freshest 'as of' date inside the trigger "
    "line so provenance is transparent. (3) If a round appears in only one thin "
    "item and is not corroborated, prefix why_now with '(unverified) '. (4) "
    "Prefer the freshest signals; the value decays within ~3 weeks."
)


@dataclass
class ScoredAccount:
    company: str
    trigger: str
    why_now: str
    role_to_contact: str
    outreach_angle: str
    score: int
    source_url: str


# ── 1) Ingest ────────────────────────────────────────────────────────────────
def fetch_signals(extra_queries=None, max_age_days=9, per_feed_limit=40):
    feeds = list(DEFAULT_FEEDS) + [
        {"source": f"Google News: {q}", "signal": "funding",
         "url": GOOGLE_NEWS_RSS.format(q=quote_plus(q))}
        for q in (extra_queries or [])
    ]
    cutoff = time.time() - max_age_days * 86400
    out = []
    for feed in feeds:
        try:
            parsed = feedparser.parse(feed["url"], agent=USER_AGENT)
        except Exception as exc:
            print(f"WARN feed failed {feed['url']}: {exc}")
            continue
        for entry in parsed.entries[:per_feed_limit]:
            ts = entry.get("published_parsed") or entry.get("updated_parsed")
            if ts and time.mktime(ts) < cutoff:
                continue
            out.append({
                "source": feed["source"],
                "title": (entry.get("title", "") or "").strip(),
                "summary": (entry.get("summary", "") or "")[:1200].strip(),
                "link": entry.get("link", ""),
                "published": entry.get("published", "") or entry.get("updated", ""),
            })
    print(f"Fetched {len(out)} raw signals from {len(feeds)} feeds")
    return out


# ── 2) Claude: extract / dedupe / score ──────────────────────────────────────
@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=30))
def _claude_json(system: str, user: str, max_tokens: int = 16000) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system + "\n\nRespond with a single valid JSON object and nothing "
                        "else - no prose, no markdown code fences.",
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            raise
        return json.loads(raw[start:end + 1])


def build_digest(vertical=LAUNCH_VERTICAL):
    raw = fetch_signals(extra_queries=vertical.get("queries"))
    items = raw[:120]
    user = (
        f"TARGET VERTICAL: {vertical['name']}.\n"
        f"READERS (who must act on this): {vertical['audience']}.\n\n"
        f"From the NEWS ITEMS below, return the {vertical['max_accounts']} strongest, "
        "de-duplicated, on-vertical buying signals. For EACH account return: "
        "company, trigger (round/amount/date as supported), why_now (1-2 sentences "
        "written FOR the seller), role_to_contact, outreach_angle (one concrete, "
        "non-generic opener), score (1-100 = heat/fit), source_url.\n\n"
        'Return JSON: {"accounts":[{"company","trigger","why_now",'
        '"role_to_contact","outreach_angle","score","source_url"}, ...]} sorted by '
        "score descending.\n\nNEWS ITEMS (JSON):\n" + json.dumps(items, ensure_ascii=False)
    )
    data = _claude_json(_EXTRACT_SYSTEM, user)
    accounts = []
    for a in data.get("accounts", []):
        try:
            accounts.append(ScoredAccount(
                company=a["company"], trigger=a.get("trigger", ""),
                why_now=a.get("why_now", ""), role_to_contact=a.get("role_to_contact", ""),
                outreach_angle=a.get("outreach_angle", ""),
                score=int(a.get("score", 0)), source_url=a.get("source_url", ""),
            ))
        except (KeyError, ValueError):
            continue
    accounts.sort(key=lambda x: x.score, reverse=True)
    print(f"Digest built: {len(accounts)} scored accounts")
    return accounts


def _issue_label():
    y, w, _ = _dt.date.today().isocalendar()
    return f"{y}-W{w:02d}"


# ── 3) Render: archive HTML, CSV, RSS feed ──────────────────────────────────
def _heat(score):
    # contrast-safe text colors (WCAG AA on white)
    return "#15803d" if score >= 80 else "#b45309" if score >= 60 else "#64748b"


def render_archive_html(accounts, vertical_name, issue_label, gated_note="",
                        gated=False, public=False, free_n=5):
    """Render the issue. gated=True locks all but the top `free_n` (public teaser);
    public=True makes it indexable + adds a signup CTA. The subscriber/RSS copy
    is always ungated (gated=False)."""
    rows = []
    for i, a in enumerate(accounts):
        if gated and i >= free_n:
            rows.append(f"""
        <div style="border:1px solid #e2e8f0;border-radius:14px;padding:18px 20px;margin:14px 0;background:#fff">
          <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px">
            <h2 style="margin:0;font-size:18px;color:{INK}">{html.escape(a.company)}</h2>
            <span style="font-weight:700;color:{_heat(a.score)};font-size:14px">heat {a.score}</span>
          </div>
          <p style="margin:6px 0;color:#334155;font-size:14px"><strong>Trigger:</strong> {html.escape(a.trigger)}</p>
          <p style="margin:10px 0 0;color:#475569;font-size:14px">&#128274; Why now &middot; Who to contact &middot; Angle &mdash; subscribers only</p>
        </div>""")
        else:
            rows.append(f"""
        <div style="border:1px solid #e2e8f0;border-radius:14px;padding:18px 20px;margin:14px 0;background:#fff">
          <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px">
            <h2 style="margin:0;font-size:18px;color:{INK}">{html.escape(a.company)}</h2>
            <span style="font-weight:700;color:{_heat(a.score)};font-size:14px">heat {a.score}</span>
          </div>
          <p style="margin:6px 0;color:#334155;font-size:14px"><strong>Trigger:</strong> {html.escape(a.trigger)}</p>
          <p style="margin:6px 0;color:#334155;font-size:14px"><strong>Why now:</strong> {html.escape(a.why_now)}</p>
          <p style="margin:6px 0;color:#334155;font-size:14px"><strong>Who to contact:</strong> {html.escape(a.role_to_contact)}</p>
          <p style="margin:6px 0;color:#0f766e;font-size:14px"><strong>Angle:</strong> {html.escape(a.outreach_angle)}</p>
          <a href="{html.escape(a.source_url)}" style="font-size:12px;color:#64748b">source</a>
        </div>""")
    robots = "" if public else '<meta name="robots" content="noindex">'
    canonical = f'<link rel="canonical" href="{SITE_BASE_URL}/sample.html">' if public else ""
    # GoatCounter analytics (public page only; inert until the site code is registered)
    analytics = ('<script data-goatcounter="https://roundsignal.goatcounter.com/count" '
                 'async src="https://gc.zgo.at/count.js"></script>') if public else ""
    social = (
        f'<meta property="og:type" content="website">'
        f'<meta property="og:title" content="RoundSignal {issue_label} &mdash; freshly-funded startups worth selling to">'
        f'<meta property="og:description" content="{len(accounts)} freshly-funded accounts, scored, with the role to contact.">'
        f'<meta property="og:url" content="{SITE_BASE_URL}/sample.html">'
        f'<meta property="og:image" content="{SITE_BASE_URL}/og-image.png">'
        f'<meta name="twitter:card" content="summary_large_image">'
        f'<meta name="twitter:image" content="{SITE_BASE_URL}/og-image.png">'
    ) if public else ""
    locked_note = (
        f'<p style="color:#475569;font-size:13px;margin:0 0 18px">Free preview &mdash; full details on the top {free_n}. '
        f'<a href="{SITE_BASE_URL}/#pricing" style="color:#0f766e;font-weight:600">Unlock all {len(accounts)} accounts &rarr;</a></p>'
        if gated else ""
    )
    cta = (
        f'<div style="text-align:center;margin:30px 0 6px">'
        f'<a href="{SITE_BASE_URL}/#cta" style="display:inline-block;background:{ACCENT};color:{INK};'
        f'font-weight:700;padding:13px 24px;border-radius:12px;text-decoration:none">'
        f'Get this list every Monday &mdash; free &rarr;</a></div>'
        if (public or gated) else ""
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{robots}{canonical}{social}<title>RoundSignal {issue_label} &mdash; freshly-funded startups worth selling to</title>
<meta name="description" content="{html.escape(vertical_name)} &mdash; {len(accounts)} freshly-funded accounts, scored, with the role to contact. Week {issue_label}."></head>
<body style="margin:0;background:#f1f5f9;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<main style="max-width:720px;margin:0 auto;padding:28px 18px">
  <header style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
    <a href="{SITE_BASE_URL}/" style="font-weight:800;font-size:22px;color:{INK};text-decoration:none">Round<span style="color:{ACCENT}">Signal</span></a>
    <span style="margin-left:auto;color:#64748b;font-size:13px">Issue {issue_label}</span>
  </header>
  <h1 style="color:{INK};font-size:22px;font-weight:800;margin:0 0 4px">Freshly-funded {html.escape(vertical_name)}</h1>
  <p style="color:#64748b;font-size:13px;margin:0 0 14px">{len(accounts)} trigger-ready accounts, scored and contextualized. {gated_note}</p>
  {locked_note}
  {''.join(rows)}
  {cta}
  <p style="color:#64748b;font-size:12px;margin-top:24px">&copy; RoundSignal. Signals sourced from public news; verify before outreach.</p>
</main>{analytics}</body></html>"""


def digest_to_csv(accounts, public=False):
    buf = io.StringIO()
    w = csv.writer(buf)
    if public:
        # public teaser: no paid why-now/contact/angle columns
        w.writerow(["company", "trigger", "score"])
        for a in accounts:
            w.writerow([a.company, a.trigger, a.score])
    else:
        w.writerow(["company", "trigger", "why_now", "role_to_contact", "outreach_angle", "score", "source_url"])
        for a in accounts:
            w.writerow([a.company, a.trigger, a.why_now, a.role_to_contact, a.outreach_angle, a.score, a.source_url])
    return buf.getvalue()


def build_rss(accounts, issue_label, pubdate):
    content = render_archive_html(accounts, LAUNCH_VERTICAL["name"], issue_label)
    title = f"RoundSignal {issue_label}: {len(accounts)} freshly-funded accounts to sell to this week"
    link = f"{SITE_BASE_URL}/sample.html"
    built = format_datetime(_dt.datetime.now(_dt.timezone.utc))
    # guid stable per (issue, build-date) so a same-week corrective re-run IS re-sent.
    guid = f"roundsignal-{issue_label}-{pubdate:%Y%m%d}"
    item = (
        "  <item>\n"
        f"    <title>{html.escape(title)}</title>\n"
        f"    <link>{html.escape(link)}</link>\n"
        f'    <guid isPermaLink="false">{html.escape(guid)}</guid>\n'
        f"    <pubDate>{format_datetime(pubdate)}</pubDate>\n"
        f"    <description><![CDATA[{content}]]></description>\n"
        "  </item>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n<channel>\n'
        "  <title>RoundSignal - Weekly funded-startup signals</title>\n"
        f"  <link>{SITE_BASE_URL}/</link>\n"
        f'  <atom:link href="{SITE_BASE_URL}/feed.xml" rel="self" type="application/rss+xml"/>\n'
        "  <description>Each week: freshly-funded startups, the role to contact, and a ready-to-send angle.</description>\n"
        "  <language>en</language>\n"
        f"  <lastBuildDate>{built}</lastBuildDate>\n"
        f"{item}\n</channel>\n</rss>\n"
    )


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY not set")
    accounts = build_digest()
    if not accounts:
        sys.exit("Empty digest - aborting (feeds or API issue)")
    label = _issue_label()
    now = _dt.datetime.now(_dt.timezone.utc)
    with open("feed.xml", "w", encoding="utf-8") as f:
        f.write(build_rss(accounts, label, now))
    # PUBLIC sample = indexable, GATED teaser (full details only on the top 5).
    with open("sample.html", "w", encoding="utf-8") as f:
        f.write(render_archive_html(accounts, LAUNCH_VERTICAL["name"], label,
                                    gated_note="Generated live from public funding sources.",
                                    gated=True, public=True))
    # PUBLIC csv = teaser columns only (paid columns withheld).
    with open("sample.csv", "w", encoding="utf-8") as f:
        f.write(digest_to_csv(accounts, public=True))
    # Refresh sitemap lastmod so the weekly-changing pages carry an honest date.
    today = now.date().isoformat()
    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'  <url><loc>{SITE_BASE_URL}/</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>1.0</priority></url>\n'
            f'  <url><loc>{SITE_BASE_URL}/sample.html</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>\n'
            f'  <url><loc>{SITE_BASE_URL}/privacy.html</loc><lastmod>2026-06-29</lastmod><changefreq>yearly</changefreq><priority>0.3</priority></url>\n'
            f'  <url><loc>{SITE_BASE_URL}/terms.html</loc><lastmod>2026-06-29</lastmod><changefreq>yearly</changefreq><priority>0.3</priority></url>\n'
            '</urlset>\n'
        )
    print(f"OK: {len(accounts)} accounts, issue {label} -> feed.xml + sample.html (gated) + sample.csv (teaser) + sitemap.xml")


if __name__ == "__main__":
    main()
