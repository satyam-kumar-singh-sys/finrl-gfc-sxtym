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

* **Daily (10 seconds):** glance at the Actions tab. Red run = the alert; open
  the log, fix, re-run manually.
* **Weekly:** check `results/live_summary.md`; every Monday's rebalance should
  show up in `logs/trades.csv`.
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

## 9. Cost & limits

* Alpaca paper trading: free, no market-data subscription needed (IEX feed is
  enough for daily bars and market orders).
* Yahoo Finance: free, no key. Used only for daily closes.
* GitHub Actions: the default 2,000 free minutes/month on public repos; this
  workflow uses ≈ 40 s per run × ~2 runs × 21 sessions ≈ **30 minutes/month**.
