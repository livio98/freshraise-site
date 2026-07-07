# Social automation — setup (RoundSignal)

Two GitHub Actions, both **compliant with the platforms' official APIs** (no browser
automation, no scraping — the things that got the accounts flagged). They **no-op
harmlessly (green runs) until you add the secrets**, so nothing breaks in the meantime.

Repo: `livio98/freshraise-site` → **Settings → Secrets and variables → Actions → New repository secret**.

---

## (a) X auto-post  ·  `.github/workflows/x-post.yml`
Posts our **own** queued content to X once/day (max 1/run). We add drafts to
`social/x-queue.json`; you can review/edit/delete them via git before they fire.

**Uses OAuth 1.0a** (tokens that never expire — no rotation headache).

### What you do (once, ~10 min)
1. Go to **developer.x.com** → sign in as **@Getroundsignal** → **check your billing status**.
   ⚠️ Since **Feb 2026 there is no free tier for new developer accounts** — you're on **pay-per-use**. Cost for us: **~$0.015/post without a link, ~$0.20/post WITH a link** → roughly **$6–18/month** at our cadence. (If your dev account predates Feb 2026 you may have migrated access + a $10 credit — check.) You'll need to **add a payment method** for posts to actually send.
2. **Create a Project + App** in the developer portal.
3. In the App's **User authentication settings**: set app permissions to **Read and write**, App type **Web App / Automated App**, add any callback URL (e.g. `https://getroundsignal.com`).
4. In **Keys and tokens**: copy the **API Key**, **API Key Secret**, then generate the **Access Token** and **Access Token Secret** (must show "Read and Write").
5. Add these 4 as repo **secrets**:
   - `X_API_KEY`
   - `X_API_SECRET`
   - `X_ACCESS_TOKEN`
   - `X_ACCESS_TOKEN_SECRET`

That's it — the daily action starts posting `pending` items. Tell me when secrets are in and I'll fill the queue with drafts. To keep cost down we'll keep the link in the **X bio / a pinned post**, not in every post.

---

## (b) Reddit monitor (read-only)  ·  `.github/workflows/reddit-monitor.yml`
**Reads only** — finds mentions of RoundSignal + fresh relevant threads and writes
`social/reddit-digest.md` daily. **Never posts** (our account is flagged; posting via
API would be filtered anyway and against Reddit policy). This just saves you checking
Reddit by hand — I turn the digest into draft replies you post manually.

Uses **app-only read access** — it does **not** log in as the flagged account.

### What you do (once, ~5 min, free)
1. Go to **reddit.com/prefs/apps** → **create app** → type **script** → name `roundsignal-monitor`, redirect URI `http://localhost:8080` (unused).
2. Copy the **client id** (under the app name) and the **secret**.
3. Add as repo **secrets**:
   - `REDDIT_CLIENT_ID`
   - `REDDIT_CLIENT_SECRET`

Done — the digest refreshes daily (free). No payment, no risk.

---

## Notes
- Both workflows also have a **"Run workflow" button** (workflow_dispatch) to trigger manually after you add secrets, without waiting for the cron.
- **LinkedIn API = deferred** on purpose: the account is under identity verification; adding API traffic now would fail and look bad in review. We build it only after it's verified (real name) and warmed up 1–2 weeks.
- Nothing here does connect/DM/like/follow/scrape — those stay manual (assist-only) forever, by design, to avoid bans.
