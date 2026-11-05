# Micromarket POS

A point-of-sale system for a self-serve micromarket, with an AI shopkeeper in the spirit of
Anthropic's [Project Vend](https://www.anthropic.com/research/project-vend-1).

- **Kiosk** (`/`): product grid, cart, checkout, and a "message the shopkeeper" box.
- **Owner dashboard** (`/` → Owner tab): cash/revenue/profit, inventory, restock orders, sales + refunds, audit log, and a button to run the AI agent.
- **EV charging** (`micromarket/charging.py`): chargers tagged by location (lot, lounge, …), start/stop sessions, per-kWh billing into the same cash ledger, and usage trends (by hour, day and location, utilization, margin over energy cost, and how often drivers also shop).
- **AI shopkeeper** (`micromarket/agent.py`): Claude with tools to read inventory/sales, reprice, order stock, and keep a notes scratchpad.

## Run it

```bash
pip install -r requirements.txt
python -m micromarket.seed                      # demo products + $200 starting cash
export ANTHROPIC_API_KEY=...                    # needed only for the agent features
export ADMIN_TOKEN=choose-a-secret              # protects /api/admin/* (recommended)
uvicorn micromarket.app:create_app --factory
pytest
```

Env vars: `MICROMARKET_DB` (SQLite path), `MICROMARKET_MODEL` (default `claude-opus-5-5`), `ADMIN_TOKEN`.

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
