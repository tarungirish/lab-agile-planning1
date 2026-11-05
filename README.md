# Micromarket POS

A point-of-sale system for a self-serve micromarket, with an AI shopkeeper in the spirit of
Anthropic's [Project Vend](https://www.anthropic.com/research/project-vend-1).

- **Kiosk** (`/`, public): product grid, cart, checkout, EV charger start, and a "message the shopkeeper" box. Contains no owner UI or code and resets itself after 90 s idle.
- **Owner dashboard** (separate URL, `/owner` by default; set `OWNER_PATH` to change it; needs `ADMIN_TOKEN`): cash/revenue/profit, inventory, restock orders, sales + refunds, audit log, and a button to run the AI agent.
- **EV charging** (`micromarket/charging.py`): chargers tagged by location (lot, lounge, …), start/stop sessions, per-kWh billing into the same cash ledger, and usage trends (by hour, day and location, utilization, margin over energy cost, and how often drivers also shop).
- **Auto-ordering** (`micromarket/autoorder.py`): per-product reorder rules (par level, pack size, lead time, supplier). A planner turns sales velocity and current/on-order stock into an explained order plan. Modes: Off / Suggest (owner approves) / Auto (hourly background job).
- **AI shopkeeper** (`micromarket/agent.py`): Claude with tools to read inventory/sales, reprice, order stock, and keep a notes scratchpad.

## Run it

```bash
pip install -r requirements.txt
python -m micromarket.seed                      # demo products + $200 starting cash
export ANTHROPIC_API_KEY=...                    # needed only for the agent features
export ADMIN_TOKEN=choose-a-long-random-secret  # REQUIRED for owner access (admin is disabled without it)
export OWNER_PATH=back-office-7f3               # optional: move the owner page off /owner
uvicorn micromarket.app:create_app --factory
pytest
```

Env vars: `MICROMARKET_ALLOW_OPEN_ADMIN=1` (local dev only: allow admin with no token), `MICROMARKET_DB` (SQLite path), `AUTO_ORDER_INTERVAL_MIN` (scheduler period, default 60; only acts in Auto mode), `MICROMARKET_MODEL` (default `claude-opus-5-5`), `ADMIN_TOKEN`.

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
