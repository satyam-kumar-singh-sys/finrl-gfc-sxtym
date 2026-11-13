# RUNBOOK — from zero to a live paper account (≈20 minutes)

Everything here has been verified against the framework in this repository.
Steps 1–5 need no code. Step 6 runs the bot locally, step 7 puts it on
autopilot, step 8 covers the daily/weekly habits that keep the entry valid.

---

## 0. Prerequisites

* a GitHub account (the repo must be **public** for the challenge);
* an email you can access (for the Alpaca account and the challenge);
* Python 3.11+ locally if you want to run things on your machine
  (not strictly needed — GitHub Actions does the trading).

Check locally:

```bash
python3 --version          # 3.11 or newer
git --version
```

## 1. Create an Alpaca paper account (10 min)

1. Sign up at <https://alpaca.markets/> → "Sign up" (no funding, no KYC for
   **paper** trading).
2. Open the **Paper Trading** dashboard (<https://app.alpaca.markets/paper/dashboard/overview>).
   It comes with **$100,000** of simulated buying power.
3. Left sidebar → **API Keys** → **Generate New Key**.
4. Copy both values immediately — the secret is shown once:
   * **KEY ID** → `APCA_API_KEY`
   * **SECRET KEY** → `APCA_API_SECRET`
5. Confirm the endpoint shown is `https://paper-api.alpaca.markets`.

Notes
* Paper keys are separate from live keys. There is no way to accidentally trade
  real money with them, and this repo additionally refuses any non-paper URL.
* Alpaca's free paper account supports **fractional shares** for the ETFs used
  here, so a $100k book of SPY/QQQ/GLD/IEF/TLT/DBC/BIL is trivially sized.
* Keep the keys out of the repository. `.env` is git-ignored; GitHub secrets are
  the right place for Actions.

## 2. Put this code in your own public GitHub repo (3 min)

This workspace directory (`finrl-gfc-sxtym/`) is already a complete repo.

```bash
cd finrl-gfc-sxtym
git init -b main
git add -A
git commit -m "ATA v1.0: FinRL-X trend strategy for the AI4Finance Open Trading Challenge"
# create an EMPTY public repo named e.g. finrl-gfc-sxtym on github.com, then:
git remote add origin https://github.com/<your-username>/finrl-gfc-sxtym.git
git push -u origin main
```

If you prefer the official flow: on GitHub open
<https://github.com/AI4Finance-Foundation/FinRL-Trading> → **Fork**, then copy
`src/strategies/adaptive_trend.py`, `scripts/`, `tests/`, `.github/workflows/live.yml`
and the docs into the fork and commit. Both routes satisfy the "fork the template
repository" step; the second is closer to the literal instruction.

Verify: the repo page shows `README.md`, `STRATEGY.md`, `src/`, `logs/`, `results/`.

## 3. Add the Alpaca keys as GitHub secrets (2 min)

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Name | Value |
|---|---|
| `APCA_API_KEY` | your paper key id |
| `APCA_API_SECRET` | your paper secret |

(`APCA_BASE_URL` is already pinned to the paper endpoint inside the workflow —
do not add a live one.)

## 4. Run the strategy once locally (optional but recommended, 2 min)

```bash
pip install -r requirements-live.txt
python -m pytest -q tests                     # 18 tests, no network
python -m scripts.backtest                    # full-sample table
cp .env.example .env                          # paste the two keys
python -m scripts.run_daily --dry-run         # signal + order plan, nothing sent
```

The dry run prints today's target vector and the orders it *would* send. If that
looks sane, `python -m scripts.run_daily` places the first real paper orders —
but only during US market hours (09:30–16:00 New York); otherwise the runner
reports `skipped_market_close` and exits cleanly.

## 5. Turn on the schedule (1 min)

Repo → **Actions** tab → if prompted, click **I understand my workflows, go ahead
and enable them** → select **live-paper-trading** → **Enable workflow**.

What happens next, automatically, every US weekday:

| Time (UTC) | New York (EST) | Job |
|---|---|---|
| 14:45 | 09:45 | refresh prices → compute target → rebalance if needed → commit logs |
| 21:15 | 16:15 | post-close equity snapshot → commit `results/daily_equity.csv` |
| 21:45 | 17:45 | `daily-report` workflow: rebuild `results/live_summary.md` + `figs/live_equity.png` |

Both runs commit their logs back to the repo (`logs/`, `results/`), which is what
makes the equity curve and trade log public and auditable.

Manual trigger: **Actions → live-paper-trading → Run workflow**
(tick `dry_run` to rehearse, `snapshot` to record equity only).

## 6. First-run checklist

- [ ] `Actions → live-paper-trading` shows a green run with a commit
      `logs: …`
- [ ] `logs/decisions.csv` has today's row with a full target JSON
- [ ] `logs/trades.csv` has orders (or the decision is `hold`)
- [ ] Alpaca dashboard shows the matching positions
- [ ] `results/daily_equity.csv` grows by one row after the close
- [ ] `python -m scripts.report` prints the live Sharpe/Sortino/max-DD block

## 7. Ongoing habits

* **Daily (10 seconds):** open `results/live_summary.md` on GitHub — it is
  rewritten every evening with the current return, Sharpe, max drawdown and a
  fresh equity chart. If you prefer, glance at the Actions tab instead: three
  green ticks per weekday is the healthy state, a red cross is the alert.
* **Weekly:** check `logs/trades.csv` (each rebalance is one line per order) and
  confirm the equity chart in `figs/live_equity.png` still looks like the same
  strategy you backtested.
* **Optional public dashboard:** repo **Settings → Pages → Source: Deploy from a
  branch → Branch: `main`, folder `/docs` → Save**. About a minute later
  `https://<your-user>.github.io/finrl-gfc-sxtym/` serves a self-contained page
  (equity curve, current target, health, recent orders) rebuilt every session by
  `scripts/dashboard.py`. No keys, no server, nothing to maintain — and it is
  good evidence for the documentation score.
* **Health checks:** `python -m scripts.monitor` runs five tests (fresh data, run
  cadence, drawdown budget, trading activity, signal sanity) and writes
  `results/health.md`. The `daily-report` workflow runs it daily and opens an
  issue if something fails. Run it by hand any time you are unsure.
* **Never** edit the strategy mid-run. The rules require no manual intervention
  during execution. If you must change something, do it between live periods,
  date it in `CHANGELOG.md`, and restart the 30-day clock.
* **Never** commit `.env`, keys, or your Alpaca secret.
* Keep the repo public until results are announced.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `refusing to trade: APCA_BASE_URL … not a paper endpoint` | live URL in env | set `APCA_BASE_URL=https://paper-api.alpaca.markets` |
| `APCA_API_KEY` missing in Actions | secrets not added, or workflow edited on a fork without secrets | re-add repo secrets |
| `action=skipped_market_closed` | US market closed (holiday / after hours) | expected; next session trades |
| `403 forbidden` from Alpaca | paper key used against live URL, or key revoked | regenerate paper keys |
| yfinance returns empty frames | Yahoo rate-limit | the loader retries and falls back to `data/cache`; re-run later |
| Workflow commits fail with `Permission denied` | `permissions: contents: write` missing | it is set in `live.yml`; check branch protection |
| `git pull --rebase` conflict on logs | two runs raced | `concurrency: ata-trading` prevents it; re-run the job |

## 8b. Getting notified when something breaks

There is no "Actions" checkbox in the Watch dialog on current GitHub, so the
reliable paths are:

1. **Issues (recommended, already wired up).** Both workflows open a GitHub issue
   when a run fails or a health check fails. Watching "Issues" therefore gives you
   an email: **Watch → Custom → Issues → Apply**. Issues are also permanent
   evidence that you noticed and handled problems.
2. **GitHub mobile app** — install it and allow notifications; issue alerts
   arrive as push notifications.
3. **Global Actions emails** — <https://github.com/settings/notifications> →
   *Actions* → "Only notify for failed workflows". Applies to every repo you own.
4. **Nothing at all** — acceptable too. The daily-report workflow means a silent
   failure shows up as a missing `report:` commit, and the health check will
   flag it the next day at the latest.

What you do **not** need: a server, a phone-integrated monitoring service, or a
Slack/Discord webhook. For a strategy that trades once a session, an issue is the
right size of alert.

## 8c. The safety switches (added 2026-11-13)

Three controls exist so that "stop trading" and "am I on the right account?" are
not code changes made in a hurry.

Step-by-step clicks (including what success looks like, and how to undo):
`docs/GITHUB_SETTINGS_WALKTHROUGH.md`.

### The kill switch — stop orders, keep the record

GitHub → **Settings → Secrets and variables → Actions → Variables → New
repository variable**:

| Name | Value | Effect |
|---|---|---|
| `TRADING_PAUSED` | `true` | **No orders for as long as the variable exists** — it does not expire. The run still computes, commits `logs/decisions.csv` with `action=paused`, exits cleanly (so no alert issue) and the dashboard shows **PAUSED**. Delete the variable to resume. Leave it unset in normal operation. |
| `APCA_EXPECTED_ACCOUNT_ID` | e.g. `PA3XXXXXXXXXX` | Pins the account. If the broker reports a different account number, the run refuses to trade. Catches a rotated or wrong API key before it writes to the wrong place. Find the number in Alpaca → the account ID on the dashboard, or in `logs/provenance.csv` after the first run. |

Also available locally: `export TRADING_PAUSED=true` before `python -m
scripts.run_daily`, or `python -m scripts.guards` to see the current verdict.

### Pre-trade guards — what they refuse

`scripts/guards.py` runs before every rebalance and blocks the trade (while still
logging the refusal as `blocked_by_guard`) if any of these is true:

1. the kill switch is on;
2. the account is not `ACTIVE`, is flagged `trading_blocked`/`account_blocked`,
   or is not the pinned account;
3. the target has a negative weight, adds up to more than 100%, contains a
   ticker outside the universe, or puts more than 45% in one *investment*
   (Treasury-bill cash is exempt — 54% in BIL is the strategy working);
4. the signal is older than 5 days, or dated in the future.

Read-only check any time: `python -m scripts.guards`. Tests: `tests/test_guards.py`.

### Provenance — proving nothing changed mid-run

Every run appends to `logs/provenance.csv`: a fingerprint of the strategy code,
the account number, and a fingerprint of the settings. The health check fails if
either the code or the account changes during the live window — the 30-day record
must describe one strategy on one account. `python -m scripts.review` (check 4)
prints it in one line.

### Alerts: one issue per incident, closing = acknowledgement

Both workflows open an issue labelled `bot-alert` when something fails. A repeat
failure **comments on the existing open issue** instead of opening another one —
a week-long outage should not produce five issues and a reason to ignore them.
Closing the issue is the acknowledgement; a failure after that opens a new one.
`REVIEW_CHECKLIST.md` has a ten-minute outage drill so you know the alarm reaches
you before you need it.

### The daily ten minutes

```bash
python -m scripts.review
```

One screen: alive? trading? inside the risk budget? still the same strategy?
how many of the 30 sessions are logged? Exit code 1 prints the single line that
needs you. See `REVIEW_CHECKLIST.md`.

## 9. Cost & limits

* Alpaca paper trading: free, no market-data subscription needed (IEX feed is
  enough for daily bars and market orders).
* Yahoo Finance: free, no key. Used only for daily closes.
* GitHub Actions: the default 2,000 free minutes/month on public repos; this
  workflow uses ≈ 40 s per run × ~2 runs × 21 sessions ≈ **30 minutes/month**.
