# EU Price Monitor

Daily competitor price monitoring for wireless headphones across European online
shops (PL, DE, NL, CZ, FR). Prices are normalised to EUR, matched across shops,
written to Google Sheets, and notable changes are pushed to Telegram.

> Work in progress — the full documentation is written in phase 6.

**Prices** are collected exactly as shown by each shop: **VAT included, shipping excluded**.
Cross-country comparisons use the shops' own offers only (marketplace partners are stored but flagged).

## Running on GitHub Actions

The workflow [`.github/workflows/daily.yml`](.github/workflows/daily.yml) runs every day
and can be started by hand.

### Schedule

GitHub cron uses **UTC and does not follow daylight saving time**. The job is scheduled
at **07:17 UTC**, which is **08:17 in Warsaw in winter (CET)** and **09:17 in summer (CEST)**.
It deliberately avoids `:00`, because scheduled runs at the top of the hour are often delayed.
GitHub may still start a scheduled run a few minutes late.

### What one run does

1. Scrapes the five shops (in parallel, each with its own polite delay), checks site health.
2. Converts prices to EUR, matches products, writes the `compare`, `summary`, `today`,
   `status` and `history` sheets.
3. Sends a Telegram alert if something changed (nothing is sent on a quiet day).
4. Commits the refreshed caches in `data/` back to the repository with a
   `[skip ci]` message — only when they changed (`git pull --rebase` before pushing).
5. If the run fails, or every site is stale, sends a short Telegram warning the same day.

Runs never overlap (`concurrency` group), so two runs cannot fight over `data/` or the sheet.

### Repository secrets

Settings → Secrets and variables → Actions → *New repository secret*:

| Secret | Value |
|---|---|
| `GOOGLE_CREDS` | The whole service-account JSON key, pasted as text |
| `SHEET_ID` | The spreadsheet id — the part of its URL between `/d/` and `/edit` |
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Your chat id (e.g. from [@userinfobot](https://t.me/userinfobot)); send `/start` to the bot first |

The spreadsheet must be shared with the service account's e-mail (`client_email` in the
JSON) as **Editor**, and the Google Sheets API must be enabled in its Google Cloud project.
Locally the same values live in `.env` (see `.env.example`), with `GOOGLE_CREDS_PATH`
pointing to the JSON file instead of `GOOGLE_CREDS`.

### Checking that it works

1. **Actions → daily-price-check → Run workflow.** Leave *no_history* unticked for a
   normal run; tick it for a trial that updates the sheet without touching `history`.
2. When the run is green, open it and check:
   - the **Run price monitor** log ends with `Google Sheet updated` and either
     `Telegram message sent` or `No changes worth an alert`;
   - every site line says `OK` (a `STALE` line explains why, e.g. `HTTP 429`);
   - the **Commit refreshed caches** step pushed a `chore(data): refresh caches … [skip ci]`
     commit (or printed `No cache changes to commit`);
   - the **run-results** artifact holds the log, today's offers and the alert text.
3. In the sheet: the `status` tab shows today's date as *Last successful run* for every
   site, and `history` has rows dated today.
4. To test Telegram alone, run locally: `python -m src.main --test-alert`.
