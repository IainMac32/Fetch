# MessageShopper

Text `SEARCH` in a private LINQ chat to search the public web for groceries. Browserbase finds up to 10 links and fetches their page content; OpenAI extracts the product details into a common format; the app checks the evidence and applies fixed rules to choose up to three matches.

```text
LINQ message → fixed query → Browserbase Search → Browserbase Fetch → OpenAI field extraction → local standardization and scoring → LINQ reply
```

For this first version, `SEARCH` always uses **`unsweetened oat milk 1L buy online Canada`**. Change `DEMO_SEARCH_QUERY` in `shopper/search.py` to change the demo. Free-form grocery messages are not parsed yet. The query goes directly to Browserbase; OpenAI runs after retrieval.

Search uses public pages and does not require a DoorDash account, Browserbase browser session, project ID or Stagehand model. The previous `CONNECT` flow and its frontend remain available separately.

## Setup

Use Python 3.13 (the tested version). For a fresh checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

Create `.env` from `.env.example` if it does not exist. If you already have `.env`, add the new settings without replacing your existing values:

```dotenv
BROWSERBASE_API_KEY=your-browserbase-key
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=gpt-4.1-mini
```

Keep keys in `.env`, which is ignored by Git. Browserbase Search and Fetch must be available for your account. The model is configurable and must support the Responses API with structured outputs. Missing API keys stop the search before any provider request.

## Offline tests

Run backend, provider-contract and CLI tests:

```sh
.venv/bin/python -m pytest tests/test_flow.py tests/test_search.py -q
```

Include the existing frontend checks if Google Chrome is installed:

```sh
.venv/bin/python -m pytest tests/test_flow.py tests/test_search.py tests/browser_checks.py -q
```

All provider calls are mocked. The test fixture fails unexpected HTTP calls from the application's HTTP clients. Frontend tests use a temporary local Flask server and headless Chrome; they do not sign in to a retailer. No MongoDB server, API keys or paid requests are needed for these tests.

The product record includes product type, variant, package volume converted to millilitres, listed price and explicit currency, unit price per litre, availability and quotes linking those fields back to page text. Unsupported or unclear values remain unknown instead of being guessed. The current score uses product match (40 points), requested variant (20), size closeness (20), stated availability (5) and comparable CAD unit price (15 when at least two results have comparable prices). Known conflicting variants and out-of-stock items are excluded. The sorted reasons show which fields determined the order.

This is a prototype standard for the current milk demo, not yet a universal grocery taxonomy. It handles common oat, almond, soy and dairy milk terms and liquid sizes in litres/millilitres. Other units or product types need explicit normalization rules before their prices can be compared.

## Purchase interaction

The current implementation does not add items to carts or place orders. A purchase step would use a Browserbase browser session with Stagehand or Playwright to open the chosen retailer page and interact with its controls; Search and Fetch alone only read public pages. A LINQ text conversation can present a prepared cart's item and full checkout total, then require a clear confirmation before the browser submits an order. If the retailer changes the total or asks for a login, verification code or payment action, pause and ask the user again. The current Stagehand example is a search demo, not a checkout flow.

## Live test 1: terminal only

This uses your Browserbase and OpenAI accounts. It does not use LINQ, MongoDB or a shopping login.

```sh
.venv/bin/python searchtest.py
```

Expected progress: searching the web, reading results, then comparing readable pages. The final output shows how many pages were read and up to three ranked matches with source URLs and quoted prices when available. A successful run may return fewer matches or no confident match, depending on the pages found. Errors print a message and exit with status 1.

To try another query from the terminal:

```sh
.venv/bin/python searchtest.py "lactose free milk 2L buy online Canada"
```

Check each recommendation against its linked page: product, size, listed price and currency should agree with the evidence. Confirm delivery and local availability on the retailer's site.

## Live test 2: LINQ chat

1. Start MongoDB locally, or set `MONGODB_URI` to a reachable MongoDB instance. It stores webhook IDs to prevent duplicate processing.
2. In `.env`, configure `LINQ_API_KEY`, `LINQ_WEBHOOK_SECRET`, `DEMO_USER_HANDLE` (your exact sender handle, usually an E.164 phone number) and `PUBLIC_BASE_URL` (your app's public HTTPS origin, without a path). Keep the Browserbase and OpenAI settings from above.
3. Start the app:

   ```sh
   .venv/bin/python test.py
   ```

4. Point your HTTPS tunnel or deployment at port 5000. Configure LINQ's `message.received` webhook to `https://YOUR-ORIGIN/linq-webhook`, with the matching signing secret.
5. From the configured private chat, text `SEARCH`. Expect an acknowledgement containing the fixed demo query, followed by the ranked results.
6. During a search, text `STATUS` to see progress. Sending `SEARCH` again while it is running returns its status without starting another search.
7. Start another search and text `CANCEL` before it completes. Results should be suppressed after cancellation. A request already sent to a provider can finish; wait for it to settle before starting again. Already-sent messages cannot be withdrawn.

The server accepts commands only from `DEMO_USER_HANDLE` in a private chat. Group messages and other senders are ignored. `STOP` and the existing opt-out command aliases also cancel active work.

## Current limits and troubleshooting

- This is a single-process prototype with one active search at a time. Run one application process. Search jobs/status live in memory and do not resume after a restart.
- A search makes one Search request, up to 10 Fetch requests (at most three concurrently), and one OpenAI request when pages are readable. It does not purchase items or submit account credentials.
- At most 8,000 characters per page are sent to OpenAI. Unreadable pages are skipped. Bot challenges, missing product data or relevant details beyond that limit can reduce match quality.
- AI extracts candidate details; deterministic local rules order the normalized offers. Source IDs and quotes are checked against fetched text. Retail pages can still be stale, incomplete or ambiguous, so this does not guarantee the lowest delivered price or current local stock.
- If Search/Fetch reports account access or billing errors, check those APIs in your Browserbase account. If OpenAI fails, check its API key, billing and configured model. Provider response bodies and keys are not copied into chat errors.
- If the terminal test works but chat does not, check MongoDB connectivity, the public webhook URL, signing secret and exact demo sender handle. The root webpage still displays the legacy connection interface; trigger the new search by text.

Provider references: [Browserbase Search](https://docs.browserbase.com/reference/api/web-search), [Browserbase Fetch](https://docs.browserbase.com/reference/api/fetch-a-page), [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [LINQ message format](https://docs.linqapp.com/channel/imessage/guides/messaging/sending-messages/).
