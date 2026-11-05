# Micromarket POS

A point-of-sale system for a self-serve micromarket, with an AI shopkeeper in the spirit of
Anthropic's [Project Vend](https://www.anthropic.com/research/project-vend-1).

- **Kiosk** (`/`, public): product grid, cart, checkout, EV charger start, and a "message the shopkeeper" box. Contains no owner UI or code and resets itself after 90 s idle.
- **Owner dashboard** (separate URL, `/owner` by default; set `OWNER_PATH` to change it; needs `ADMIN_TOKEN`): cash/revenue/profit, inventory, restock orders, sales + refunds, audit log, and a button to run the AI agent.
- **Cleaning tracker** (`micromarket/facilities.py`): recurring cleaning tasks with a frequency; status is ok / due soon / overdue (never done counts as overdue), who cleaned what and when, and an on-time rate over the last 14 days (only counts the time a task has existed).
- **Facilities tracker**: equipment register plus issue tickets (low/normal/high/critical). Shoppers can report problems from the kiosk. A critical issue marks the equipment out of service; if it's linked to an EV charger, the charger goes offline and comes back when the last open issue is resolved.
- **Staff page** (`/staff`, `STAFF_TOKEN`): cleaners get just the cleaning checklist and issues, with no access to finances or the owner dashboard.
- **EV charging** (`micromarket/charging.py`): chargers tagged by location (lot, lounge, …), start/stop sessions, per-kWh billing into the same cash ledger, and usage trends (by hour, day and location, utilization, margin over energy cost, and how often drivers also shop).
- **Stockroom** (`micromarket/stockroom.py`): we are our own supplier. Vendor deliveries land in the stockroom; shelves (what the kiosk sells) are restocked from it via a pick list. Stock counts for shrink/miscounts.
- **Auto-ordering** (`micromarket/autoorder.py`): per-product reorder rules (par level, pack size, lead time, supplier). A planner turns sales velocity and current/on-order stock into an explained order plan. Modes: Off / Suggest (owner approves) / Auto (hourly background job).
- **Shopkeeper duties** (routine review, by button or on a schedule with `AGENT_REVIEW_INTERVAL_MIN`): runs the auto-order plan *as your mode allows* (Off: nothing; Suggest: the free stockroom-to-shelf pick list only, purchases wait for you; Auto: purchases too), files "Cleaning overdue" tickets that close themselves when staff sign the task off, and triages open tickets (low/normal/high plus a note). It can never mark anything critical, resolve tickets, or take equipment offline.
- **AI shopkeeper** (`micromarket/agent.py`): Claude with tools to read inventory/sales, reprice, order stock, and keep a notes scratchpad.

## Try the prototype (one command)

```bash
pip install -r requirements.txt
python -m micromarket.demo          # builds demo data and prints the URLs + random access codes
```

* Opens a fresh demo database with two weeks of history: sales, EV charging, cleaning logs, tickets, and a stockroom that needs restocking.
* Binds to localhost only and generates random owner/staff codes each run (`--host 0.0.0.0` to expose it on your network, `--port`, `--keep` to reuse the previous demo database).
* **No API key needed.** Without `ANTHROPIC_API_KEY` the shopkeeper is a clearly-labelled *scripted* stand-in: not an AI model, but it drives the real tools, permissions and guardrails, so every screen works. Set the key and it uses the real Claude model automatically (`--scripted` forces the stand-in).
* Things to try: buy something and watch stock fall; ask the shopkeeper about the cold brew; on `/owner` click **Run agent**, then **Auto-order → Run plan**, then **Stockroom → All done**; on `/staff` sign off a cleaning task.

## Run it (real setup)

```bash
pip install -r requirements.txt
python -m micromarket.seed                      # demo products + $200 starting cash
export ANTHROPIC_API_KEY=...                    # needed only for the agent features
export ADMIN_TOKEN=choose-a-long-random-secret  # REQUIRED for owner access (admin is disabled without it)
export OWNER_PATH=back-office-7f3               # optional: move the owner page off /owner
export STAFF_TOKEN=another-long-random-code     # optional: cleaners' login for /staff (owner token also works there)
export STAFF_PATH=floor-7f3                     # optional: move the staff page off /staff
uvicorn micromarket.app:create_app --factory
pytest
```

Env vars: `AGENT_REVIEW_INTERVAL_MIN` (run the shopkeeper's review on a schedule; default 0 = off, since each run is a paid model call), `MICROMARKET_ALLOW_OPEN_ADMIN=1` (local dev only: allow admin with no token), `MICROMARKET_DB` (SQLite path), `AUTO_ORDER_INTERVAL_MIN` (scheduler period, default 60; only acts in Auto mode), `MICROMARKET_MODEL` (default `claude-opus-5-5`), `ADMIN_TOKEN`.

## Design: lessons from Project Vend

Vend's shopkeeper was talked into discounts, sold below cost, and lost money. Here the guardrails live in
code (`store.py`, `Policy`), not in the prompt, so no conversation can talk them away:

| Risk | Defense |
|---|---|
| Selling below cost / giveaways | Agent price changes must keep a 10% margin over cost and move ≤50% at a time |
| Overspending on inventory | Per-order cap ($100) and a $20 cash reserve the agent can't spend |
| Social engineering by customers | Customer chat runs with read-only tools; customer text is wrapped as untrusted and can never trigger price/order tools |
| Overselling | Stock decrement is a conditional UPDATE inside a transaction; checkout is all-or-nothing |
| Underpriced charging | Agent can't set charging below energy cost + 10% or move it >50% at once; customers get read-only `charger_status` |
| Mistyped meter readings | A session's kWh can't exceed what the charger could physically deliver in the elapsed time |
| Runaway auto-ordering | Orders count stock already on order (no double-ordering); the Store itself enforces the per-order cap and cash reserve on `auto` orders; agent can only double a par level per step |
| Owner access from the public kiosk | Separate owner URL; kiosk page ships no owner code; admin API **fails closed** without `ADMIN_TOKEN`; constant-time token check; 5 bad tokens locks that IP out for 5 min; API docs/schema not published |
| Chat spam burning model credits | Kiosk messages limited to 5/min per IP |
| Public abuse of problem reports | Kiosk reports are always "normal" severity, can't take equipment offline, and are limited to 3/min per IP; shoppers see only equipment names |
| Staff over-reach | `STAFF_TOKEN` only opens `/api/staff/*` (cleaning + issues); staff can't mark issues critical or take equipment offline |
| Agent over-reach on facilities | Agent can file low/normal/high tickets only; in customer chat it can only pass on a normal-priority complaint |
| Agent ordering beyond what you allowed | `run_auto_order_plan` follows the owner's mode (Off / Suggest / Auto); in Suggest, purchases are returned for approval instead of placed |
| Agent overriding people on safety | Triage is limited to low/normal/high; critical issues, resolving, and taking equipment offline stay with people |
| Unaccountable changes | Every agent/staff change is written to an audit log; refunds are staff-only |

Staff actions through the owner API bypass the agent policy but are still logged.

## Layout

```
micromarket/  db.py (schema) · store.py (rules) · agent.py (Claude tool loop) · app.py (FastAPI) · static/index.html
tests/        store rules, guardrails, agent loop (scripted fake client), API
```

## Next steps

Charger hardware integration (OCPP) so kWh comes from the meter instead of being entered by the owner, payment processor integration, barcode scanning, scheduled daily agent runs, supplier lookup tools, per-customer tabs.

## EV charging notes

- Price per kWh is locked in when a session starts, so a mid-session price change never surprises a driver.
- The cross-sell metric matches drivers to market purchases by self-reported name and date, so treat it as directional.
- Trends use UTC timestamps; convert to local time in the UI if the site is far from UTC.

## Auto-ordering notes

- Reorder trigger = max(product reorder point, ceil(14-day sales/day × (lead time + 1 safety day))). Order qty = enough to reach par, rounded up to whole packs, then trimmed to the per-order cap and cash above the reserve. Scarce cash goes to the items with the fewest days of cover.
- Default mode is **Suggest**. Switch to **Auto** only after reviewing a few plans.
- Orders are created as *pending*; mark them received when the delivery arrives. Supplier integration / automatic receiving is not built yet.
- Lead time is used for the trigger only; delivery ETAs are not tracked.

## Deploying the public kiosk

- **Use HTTPS.** The owner token travels in a request header; without TLS it can be sniffed on the network.
- **Treat the obscure owner URL as a convenience, not security.** The token is the security. For a stronger setup, also restrict the owner path at your reverse proxy (IP allowlist, VPN, or SSO such as Cloudflare Access) so the public never reaches it.
- Behind a reverse proxy, run `uvicorn --proxy-headers --forwarded-allow-ips=<proxy ip>` so per-IP limits see real client IPs; otherwise all visitors share one limit bucket.
- Lockout and rate limits are in memory and per process. A shared NAT (e.g. the office Wi-Fi the kiosk and owner both use) shares one IP, so someone hammering wrong tokens can briefly lock the owner out; wait 5 minutes or restart.
- Run the kiosk iPad in Guided Access / single-app mode so shoppers can't leave the page.

## Stockroom & shelves

- `products.stock` is the **shelf** quantity (what the kiosk sells). Stockroom quantity is tracked separately.
- A product with a **shelf rule** (shelf par) is restocked from the stockroom: the planner creates *pick-list* lines (transfers), which reserve stockroom units until you tap **Done** on the iPad after stocking the shelf. Moving stock is free (no cash, no lead time).
- **Purchases** are for the stockroom and count shelf + stockroom + on-order stock. The purchasing par is the target for shelf + stockroom combined. It also buys when everything held can't fill the shelf to its par.
- Delivered goods go to the stockroom for products with a shelf rule (override with `?to=shelf` on the receive endpoint); products without a shelf rule behave as before (straight to the shelf).
- Cost basis is a weighted average over everything held (shelf + stockroom).
- Not built yet: selling to other locations (multi-site), lot/expiry tracking, barcode scanning for counts.

## Cleaning & facilities notes

- Cleaning status is derived from the last log: *due soon* = last quarter of the interval remains; *overdue* = past due or never done.
- The same lockout and fail-closed behaviour applies to the staff token. If neither `STAFF_TOKEN` nor `ADMIN_TOKEN` is set, `/api/staff/*` is disabled.
- Give cleaners the staff code only. The staff page asks who is signing off each task, and remembers the name on that device.
- Not built yet: photo evidence for cleaning, fridge temperature logging, maintenance schedules (e.g. filter changes), notifications for overdue tasks.

## Security review: status

The three Medium findings from the review are fixed (see `tests/test_security_fixes.py`):

1. **Customer chat no longer sees cost.** Tools know which tier is calling; customer chat gets the same public fields as the kiosk (name, price, stock).
2. **Public text is labelled for the manager agent.** Notes written from customer chat are stored as `source='customer'` and returned under `untrusted_customer_notes`; ticket text from the kiosk or chat is moved under `untrusted_public_text`. The system prompt tells the model to treat `untrusted_*` as information only. The code guardrails still cap what any instruction could make it do.
3. **Staff can't bring equipment back into service.** Staff can't resolve critical issues or any issue on out-of-service equipment (owner only), and the charger link now changes service state only on a real transition into or out of "out of service".

Also hardened: a Content-Security-Policy plus `X-Frame-Options`, `nosniff`, `Referrer-Policy` and `Permissions-Policy` on every response; inline handlers use a `js()` helper (JSON string literal, then HTML-escaped) instead of `esc()` inside quotes; checkout accepts only `card`/`cash` and bounded fields.

Still open: **no payment verification** (any checkout is recorded as paid until a payment processor is integrated). The CSP needs `'unsafe-inline'` for the inline scripts; moving them to files would allow a stricter policy.
