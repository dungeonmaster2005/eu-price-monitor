# EU Price Monitor — project rules

Portfolio demo: daily competitor price monitoring across European online shops
(one niche — wireless headphones — several countries), written to Google Sheets,
with change analysis and Telegram alerts. Runs on GitHub Actions.

## Workflow
- Work in phases (0–6). After EVERY phase: stop, show the result (output, sample
  data, what works / what doesn't) and wait for the user's "ok". Never start the
  next phase on your own.

## Stack
- Python 3.11 (CI pins 3.11; code must not use newer syntax).
- requests + BeautifulSoup/lxml; Playwright only if a site renders content with JS.
- gspread for Google Sheets, Telegram Bot API over plain HTTPS.

## Secrets
- Only from `.env` locally (python-dotenv) and GitHub Secrets in CI.
- Never commit `.env`, credential JSON files or tokens (`.gitignore` covers them).
- `.env.example` lists every variable with an empty/placeholder value.

## Code rules
- `logging`, never `print` (except CLI output that is the product itself).
- One failing scraper must never break the others: catch, log, continue.
- Clean, readable, typed code — this is a portfolio piece. Small functions,
  docstrings where intent isn't obvious, no dead code.

## Polite scraping
- Check robots.txt before fetching (urllib.robotparser); skip disallowed URLs.
- Random delay 2–5 s between requests to the same site.
- Honest, normal User-Agent (configured in one place).
- If a site blocks us (403, captcha, JS challenge) or forbids scraping — do NOT
  work around it; log it and propose a replacement site instead.

## Multi-country data
- Keep original price + currency AND price converted to EUR.
- FX rates once a day from a free ECB-based API (frankfurter.app), cached per day
  in `data/`. If the API is down — use the last saved rates and log a warning.
- Price parsing handles locales: "1.299,00 €", "1 299,00 zł", "€1,299.00",
  "1299 Kč" — one dedicated function, covered by tests.
- Every record has `country` and `currency`.
- Product matching across shops: model / part number or EAN when present on the
  page, otherwise a normalised name. Report matching quality to the user before
  moving on.

## Decisions log
- Shops: morele (PL), alternate.de (DE), coolblue.nl (NL), planeo.cz (CZ), ldlc.com (FR).
  bcc.nl dropped — it is an affiliate price-comparison site, not a shop.
- mironet.cz disabled 2026-11-14 (kept in sites.yaml with `enabled: false` +
  `disabled_reason`): from 2026-11-12 it redirects GitHub runner IPs to an ALTCHA
  captcha (/altcha/verify) with HTTP 200. Replaced by planeo.cz (EAN in product
  page JSON-LD, checked from CI). mironet rows stay in the history sheet.
- A captcha page (redirect to /altcha/, /captcha, challenge, or a bot-check title)
  is a block even with HTTP 200; a 200 listing page with 0 products logs its
  final URL and title.
- alternate.de: Cloudflare 429 challenge on fast product-page crawling → slow
  delay (10–20 s) + daily budget `max_new_id_pages`; never solve the challenge.
- LDLC pages 2+ and mironet stock are filled by JS/XHR → read price/stock from
  the product page JSON-LD instead of calling internal endpoints.
- Cross-country price comparison uses official sellers only; marketplace offers
  are stored with `is_official = false`.
- `data/product_ids.json` and `data/fx_rates.json` are committed caches (no secrets).
- Offline re-matching: `python -m src.main --from-file data/<saved>.json`.

## Output record
`date, country, site, name, model, ean, price, currency, price_eur, in_stock, url`

## Language
- Code, README and Telegram messages — English.
- Conversation with the user — Russian.
