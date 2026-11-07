<div align="center">

<img src="dashboard/assets/margeai-mark.png" alt="MargeAI" height="140">

# MargeAI

**The AI co-pilot that minds your margin.**
A product-improvement and sourcing agent for Amazon fashion sellers.

*Built at the AI Tinkerers x ZooWork Fashion & Finance hackathon, NYC, 7 Nov 2026.*

</div>

---

## The problem

An Amazon fashion seller's profit is decided long before the sale: by what buyers complain about, what the
product costs to fix, what suppliers will quote, and what duty does to the landed cost. Today that is four
tabs, a spreadsheet and a lot of WhatsApp. Sellers guess, and a tariff change or a bad quote quietly eats the
margin.

## What MargeAI does

Paste an Amazon listing link (or drop a photo, or describe the product). One page walks through five steps:

1. **Your product.** Reads the listing.
2. **Where you stand.** The market, and you versus your competitors on price, rating and sales.
3. **What buyers say.** The real review themes, with verbatim quotes. Every claim is tied to cited reviews.
4. **What to change.** Specific product changes, each with its cost, its price effect and its effect on profit.
5. **Brief your suppliers.** A spec sheet, then quotes from your vendors compared on **landed cost** (price,
   shipping and duty), ending in a purchase order and a message to the supplier that you approve.

You stay in control of the last mile. Pick how vendors are contacted:

- **Review first (default):** read and edit each vendor's message before it goes out.
- **Fully automatic:** one toggle, and the agent messages your vendors, collects quotes and drafts the order.
  You approve only the order.

You can upload your own vendor sheet (CSV or Excel), or use one of four pre-loaded sample sets. Extra
suggested vendors beyond your own need your sign-off.

## How it works

| Layer | What it does | Real or simulated |
|---|---|---|
| Listing, competitors, reviews | [ZooData](https://zoodata.ai) Amazon data | **Real** |
| Product advisor agent | A ZooWork managed agent that proposes changes, citing only reviews in the evidence | **Real** (LLM) |
| Margin math, landed cost, ranking | Plain Python, tested, no LLM | **Real** (deterministic) |
| Vendors and their replies | ZooWork agents role-playing suppliers | **Simulated** |
| Duty rates, cost deltas | Editable demo assumptions | **Assumed** |
| Purchase order and message | Written to `data/outbox/` | **Never sent** |

Design choices worth noting:

- **Grounded, not guessed.** The advisor may only cite review themes and ids that exist in the evidence.
  Counts like "34 buyers mention fit" are computed in code, and quotes are verbatim.
- **Money is deterministic.** The agents argue and draft; the arithmetic (unit economics, break-even price,
  landed cost) is code with unit tests.
- **Human sign-off where it matters.** Nothing goes to a supplier or Amazon without an explicit approval.
- **Fast.** A full run takes about 35 seconds, by running agent calls in parallel with a fast model.
  An **Instant demo** replays a recorded real run in under a second.

## Run it

Requires Python 3.12+ and Windows, macOS or Linux.

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
cp .env.example .env                               # then add your keys (never commit .env)
.venv/Scripts/streamlit run dashboard/app.py
```

Keys in `.env`: `ZOOWORK_API_KEY` (agents) and `ZOODATA_API_KEY` (live Amazon data). Without them the app
falls back to scripted vendors and bundled sample prices, so it still runs offline.

Tests: `python -m unittest discover -s tests`

## Layout

```
dashboard/   Streamlit one-page app (pages/0_Improve_My_Product.py, quotes_section.py, style.py)
agent/       ZooWork agents: product advisor, vendor and buyer simulation (sourcing.py)
tools/       Unit economics, RFQ and landed cost, ZooData reviews/market, vendor sheets, demo recordings
data/        Sample vendor sets; the outbox for approved POs and messages (git-ignored)
tests/       Unit tests for the math, RFQ flow, review grounding and vendor parsing
```

## Why I built this

I'm a product manager who builds 0-to-1. MargeAI is practice for the market I think is forming:
**agents that do a small business's operational work, with the human approving the decisions.**
It is a deliberately narrow, verticalized product, not a chatbot:

- A painful, quantifiable workflow (margin) with a clear user (the Amazon seller).
- Real data underneath, agents on top, deterministic code for anything involving money.
- A trust model designed in from the start: cited evidence, editable messages, explicit approvals.
- A path from demo to product: bring-your-own vendors, WhatsApp or SMS outreach, live duty and tariff
  data, and reorder triggers are the natural next steps.

## Status and next steps

Hackathon prototype. Vendors are fictional and nothing is sent. Next: real outreach channels, live duty
rates, supplier memory across orders, and more categories beyond fashion.

## Contributors

- **Arjun Ghai**: product, sourcing flow, vendor outreach and brand
- **Arun Kumar R**: tech lead; kept the front end and back end lined up and the plumbing under the hood solid, and built the MarginGuard base this started from
- **Akshara Gopakumar**: contributor; advised the team and gave feedback on our builds
- **Amanda**: contributor; advised the team and gave feedback on our builds

Built on [ZooWork](https://zoowork.ai) Managed Agents and the ZooData API.
