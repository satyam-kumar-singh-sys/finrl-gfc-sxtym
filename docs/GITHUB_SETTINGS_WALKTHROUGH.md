# GitHub Settings: secrets vs variables, and how to set them

Plain instructions. No coding. About 5 minutes for the first one, 30 seconds after
that.

The short version: **you need the two secrets you already have (the API keys).
You do NOT need to create either variable today.** They are optional switches, and
one of them (`TRADING_PAUSED`) does exactly what its name says — so it stays
unset unless you deliberately want to stop trading.

---

## 1. First, the difference — because this is the confusing part

GitHub keeps two separate lists under one heading:

| | **Secrets** | **Variables** |
|---|---|---|
| Looks like | `APCA_API_KEY` = `PKXXXXXXXX` | `TRADING_PAUSED` = `true` |
| Hidden? | Yes — encrypted, never shown again, masked in logs | No — plain text, anyone browsing the repo settings can read it |
| Use for | passwords, API keys | switches, account numbers, flags |
| You already have | `APCA_API_KEY`, `APCA_API_SECRET` ✅ | (nothing yet) |

**One rule to remember:** if it would hurt to see it in a screenshot, it is a
*secret*. If it is a switch or a label, it is a *variable*. Never paste an API key
into the Variables tab — it would be readable.

Both live on the same page, in two tabs:

```
Your repo → Settings → (left side) Secrets and variables → Actions
```

Direct link (replace the username if needed):
<https://github.com/satyam-kumar-singh-sys/finrl-gfc-sxtym/settings/variables/actions>

That link drops you straight on the **Variables** tab. If you land on *Secrets*,
click the **Variables** tab next to it.

---

## 2. The two variables, and what they do

| Name | Value | What happens | Required? |
|---|---|---|---|
| `APCA_EXPECTED_ACCOUNT_ID` | your Alpaca paper account number, e.g. `PA3ABCD1234` | Every run compares the broker's account number with this. If they differ, the run **refuses to trade** and says so in the log. Catches a rotated or wrong API key before it trades the wrong account. | No — without it you only get a warning in the log |
| `TRADING_PAUSED` | `true` | **No orders at all, for as long as it exists.** The bot still computes and commits its log, the dashboard shows **PAUSED**, and the run finishes cleanly (exit 0, no alert issue). Delete the variable to resume trading. | No — this is the emergency brake. **Leave it unset.** Create it only to stop trading on purpose, or once for the drill in §4 |

> **Do not set `APCA_EXPECTED_ACCOUNT_ID` today.** Today at 14:45 UTC is the first
> real rebalance. A typo in that number blocks the trade, and a blocked trade is a
> missed first session. Wait until the first run has happened, then copy the exact
> number from `logs/provenance.csv` (see §5). Until then the bot runs with a warning
> and behaves perfectly normally.

---

## 3. Adding a variable — exact clicks

1. Open your repo in the browser:
   <https://github.com/satyam-kumar-singh-sys/finrl-gfc-sxtym>
2. Top-right of the repo page: the **⚙ Settings** tab (last one in the row that
   starts with *Code*, *Issues*, *Pull requests*…). Click it.
3. In the **left sidebar**, scroll to the *Security* section and click
   **Secrets and variables**, then **Actions**.
4. At the top of the page there are two tabs: **Secrets** and **Variables**.
   Click **Variables**.
5. Click the green button **New repository variable**.
6. Two boxes appear:
   - **Name** — paste exactly: `TRADING_PAUSED`
     (capitals, underscores; no spaces, no quotes)
   - **Value** — paste exactly: `true`
7. Click **Add variable**.

You should now see one row: `TRADING_PAUSED` — `Updated now`.

**Names are case-sensitive.** `trading_paused` or `TRADING-PAUSED` will be ignored
silently. Copy-paste, do not retype.

---

## 3b. The one thing to understand about `TRADING_PAUSED`

It is a **latching** switch, not a one-shot: as long as the variable exists with
value `true`, **every** scheduled run skips trading. There is no expiry and no
auto-reset. If you set it and forget it, the bot will happily log "paused" every
day for as long as you leave it — the 30-day clock never starts, and the
dashboard keeps saying PAUSED.

So: **set it only when you mean it, and delete it when you are done.** The drill
in §4 finishes by deleting it for exactly this reason. If you are ever unsure
whether it is set, run `python -m scripts.review` locally (or look at the
dashboard badge): `PAUSED` means it is on.

## 4. Testing the brake (do this once, on purpose)

An alarm you have never heard is not an alarm — same for a brake.

1. Set `TRADING_PAUSED` = `true` as above.
2. Go to the **Actions** tab → left sidebar **live-paper-trading** → button
   **Run workflow** (right side) → leave everything unticked → **Run workflow**.
3. Wait ~1 minute, open the newest run, click the **trade** job, and open the
   **Guard report** step. You should see:

   ```
   kill switch : ENGAGED
   verdict     : BLOCKED
     BLOCKER: TRADING_PAUSED is set — kill switch engaged
   ```

   and the *Generate signal, rebalance* step should log
   `TRADING_PAUSED is set → no orders this run` and finish **green** (a
   deliberate pause is a decision, not a fault — it must not open an alarm
   issue). `logs/decisions.csv` gets a row with `action=paused`.
4. Nothing was bought or sold. ✅
5. **Remove it again** so the bot can trade: Settings → Secrets and variables →
   Actions → **Variables** → click the 🗑 bin icon on the `TRADING_PAUSED` row →
   confirm. (Or edit the value to `false` — both work; deleting is cleaner.)

To pause later for a real reason, do steps 1 and 5 in reverse. The log keeps
running while paused, so the audit trail has no gap.

---

## 5. Getting the account number for the pin (after the first run)

Wait until the first live run has finished (after 14:45 UTC today), then:

1. Open `logs/provenance.csv` in the repo → it has one row per run with a column
   `account_number`. That is the exact string to use.
   Or open it on your machine after `git pull`.
2. Add the variable as in §3, with **Name** `APCA_EXPECTED_ACCOUNT_ID` and
   **Value** = that number, exactly (copy-paste, no spaces).
3. Next run's **Guard report** step should say `verdict : OK` and the
   *provenance* line in `results/health.md` should show one account.

If you ever get `guards: BLOCKED — account mismatch`, the value you saved is wrong
or your API key belongs to a different account. Fix or delete the variable; the
bot resumes on the next run.

---

## 6. Where these appear afterwards

| Place | What you will see |
|---|---|
| Actions → any run → **Guard report** step | `kill switch : off`, a code fingerprint, `verdict : OK` |
| `logs/provenance.csv` | one row per run: code fingerprint, account number, config fingerprint |
| `results/health.md` | a `provenance` check — fails if the code or account changed mid-run |
| `docs/index.html` (the public page) | a **Status** badge: LIVE / PAUSED / DRY-RUN / STALE / NO DATA |
| `python -m scripts.review` on your machine | check 4, "same strategy?" — one code version, one account |

---

## 7. If something looks wrong

| Symptom | Cause | Fix |
|---|---|---|
| No **Variables** tab | You are on the *Secrets* page, or on a fork without permissions | Click the *Variables* tab; make sure you are the repo owner |
| Variable added but log still says `kill switch : off` | Name spelled differently (`trading_paused`) | Delete and re-add with the exact name |
| `verdict : BLOCKED — account mismatch` | `APCA_EXPECTED_ACCOUNT_ID` value is wrong | Correct it or delete the variable — the bot resumes next run |
| Bot stops trading with no obvious error | `TRADING_PAUSED` is still `true` | Delete the variable. Confirm with `logs/decisions.csv` — you will see `action=paused` rows |
| Never created `TRADING_PAUSED` at all | Perfect — it is optional and unset by default | Nothing to do |
| You cannot find Settings at all | You are not signed in as the owner | Sign in as `satyam-kumar-singh-sys` |

None of this touches the strategy. The variables are switches *around* the bot,
not part of it — a judge reading the repo sees the same signal either way.
