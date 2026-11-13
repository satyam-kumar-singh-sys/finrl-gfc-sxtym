# Making the automation actually automatic

## The problem, stated honestly

On 2026-11-13 the very first scheduled run never fired. GitHub's `schedule`
trigger is **best-effort**: the docs say runs may be delayed, and under load
queued runs are **dropped**. There is no error, no email, no issue — the
repository simply stops changing. You had to press *Run workflow* by hand, which
is exactly what you did not want.

Three layers now protect you. Layer 1 is in the repo; layer 2 is the retry slots;
layer 3 is an external heartbeat that does not depend on GitHub's scheduler at all.

| Layer | What it is | Catches |
|---|---|---|
| 1 | The scheduled crons (14:45 trade, 21:15 snapshot) | normal days |
| 2 | Retry slots (15:15, 15:45 trade; 21:35 snapshot; report 22:15) | a dropped run |
| 3 | External heartbeat → `workflow_dispatch` API | a broken scheduler, i.e. what happened on day 1 |

Layer 3 is the one that makes "no manual intervention" true. It takes ~10 minutes
once, and then nothing needs clicking, ever.

---

## Layer 3 setup — a free external cron that pokes GitHub

Any service that can send a scheduled HTTP POST works. The instructions below use
**cron-job.org** (free, no server, supports custom headers and POST bodies). If
you prefer EasyCron, UptimeRobot, or a Raspberry Pi with `cron`, the request is
identical — only the UI differs.

### Step 1 — create a token that can do exactly one thing

1. Click your avatar (top right) → **Settings**.
2. Left sidebar, bottom: **Developer settings**.
3. **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
4. Fill in:
   - **Token name:** `ata-heartbeat`
   - **Expiration:** 90 days (set a calendar reminder to renew — or 1 year)
   - **Repository access:** *Only select repositories* → `finrl-gfc-sxtym`
   - **Permissions** → *Repository permissions* → find **Actions** → set to
     **Read and write**. Leave everything else as *No access*.
5. **Generate token** and copy it (`github_pat_…`). It is shown once.

This token can only start workflows in this one repository. It cannot read your
code, cannot trade, cannot see your Alpaca keys. That is the point.

### Step 2 — create the heartbeat job

1. Sign up at <https://cron-job.org> (free) and verify your e-mail.
2. **Create cronjob**.
3. **Title:** `ATA trade heartbeat`
4. **URL:**
   ```
   https://api.github.com/repos/satyam-kumar-singh-sys/finrl-gfc-sxtym/actions/workflows/live.yml/dispatches
   ```
5. **Schedule:** *Every day* → **Selected days: Monday–Friday**, time
   **14:50** (choose **UTC** as the timezone — this is important; the market
   schedule is in UTC).
6. **Advanced / Request settings:**
   - **Request method:** `POST`
   - **Headers** (add each one):

     | Header | Value |
     |---|---|
     | `Authorization` | `Bearer github_pat_YOUR_TOKEN_HERE` |
     | `Accept` | `application/vnd.github+json` |
     | `Content-Type` | `application/json` |
     | `User-Agent` | `ata-heartbeat` |

   - **Request body:**
     ```json
     {"ref":"main"}
     ```
7. **Save**, then press **TEST RUN**.
8. Expected result: `204 No Content` (GitHub returns an empty success). A `401`
   means the token/header is wrong; a `404` means the workflow filename is wrong;
   a `422` means the body is malformed.

### Step 3 — a second job for the closing snapshot

Repeat Step 2 with:

- **Title:** `ATA snapshot heartbeat`
- **Schedule:** Monday–Friday, **21:20 UTC**
- **Request body:**
  ```json
  {"ref":"main","inputs":{"snapshot":true}}
  ```

That is the entire setup. Two jobs, ~10 minutes, then it runs itself.

### Step 4 — confirm it works

Next weekday at 14:50 UTC, the Actions tab shows a new `live-paper-trading` run
with event **workflow_dispatch** (that is the heartbeat), and by ~15:00 UTC
`logs/decisions.csv` has a fresh row.

**Nothing double-trades.** If the 14:45 cron already ran, the 14:50 heartbeat
finds a decision from the last 90 minutes and logs `action=skipped_recent_run`
without touching the account. If the cron was dropped, the heartbeat is the run
that trades. The same logic protects the retry slots.

---

## Why this still counts as "no manual intervention"

The rule is that the strategy must run itself: it must compute its own signal,
size its own orders, and log them without a human deciding. A scheduler poking a
workflow is infrastructure, not intervention — exactly like GitHub's own cron,
just hosted somewhere more reliable. The evidence stays honest because every run
is labelled: `schedule` when GitHub's timer fired it, `workflow_dispatch` when the
heartbeat did. Both are visible in the run list and in the timestamps.

If you would rather not use an external service at all, an always-on machine you
already own does the same job with one crontab line:

```cron
50 14 * * 1-5  curl -s -X POST -H "Authorization: Bearer $GITHUB_PAT" \
  -H "Accept: application/vnd.github+json" \
  -d '{"ref":"main"}' \
  https://api.github.com/repos/satyam-kumar-singh-sys/finrl-gfc-sxtym/actions/workflows/live.yml/dispatches
```

---

## Layer 2 in detail — what the retry slots do

| UTC | New York | Job | Behaviour if an earlier slot already ran |
|---|---|---|---|
| 14:45 | 09:45 | trade | the real run |
| 15:15 | 10:15 | trade | `skipped_recent_run` (harmless) |
| 15:45 | 10:45 | trade | `skipped_recent_run` (harmless) |
| 21:15 | 16:15 | snapshot | the closing equity row |
| 21:35 | 16:35 | snapshot | a second row; report keeps the last one per day |
| 21:45 | 17:45 | report | report + health + dashboard |
| 22:15 | 18:15 | report | retry |

The 90-minute window is deliberate: long enough to cover all three trade slots,
short enough that tomorrow's 14:45 run is never blocked by yesterday's.

---

## Layer 1 — three things that can still stop a schedule

1. **GitHub disables scheduled workflows in a repository with no activity for 60
   days.** The bot commits every session, so this will not happen while it runs.
2. **A cron in a workflow file on a non-default branch does nothing.** Both files
   live on `main`. Keep it that way.
3. **Schedules are queued, not guaranteed, at the exact minute.** Expect 5–30
   minutes late. That is why the trade slot is 09:45 New York, not 09:31.

---

## How you will know it worked, without clicking anything

| Signal | Where | Meaning |
|---|---|---|
| A new row in `logs/decisions.csv` | daily | the bot ran |
| A new row in `results/daily_equity.csv` | daily | the closing snapshot ran |
| `docs/index.html` Status badge: **LIVE** | daily | orders exist and the snapshot is recent |
| Status badge: **STALE** | any time | no snapshot for 5+ days — something is wrong |
| An issue labelled `bot-alert` | only on failure | a run broke; comment-on-existing means it stayed broken |
| `python -m scripts.review` | whenever you like | five checks, one line each |

The one thing that never announces itself is a dead scheduler — which is why the
heartbeat exists.
