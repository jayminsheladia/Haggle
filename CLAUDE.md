# CLAUDE.md — Haggle (LA Hacks AI 2026, Fetch.ai track)

Instructions for Claude (and any AI coding assistant) working in this repo.

## Rules you must follow

1. **Ask before adding any feature.** Before building anything that is not explicitly requested in the current task or listed in the build plan, stop and ask: "Do you want me to add <feature>?" Explain in one or two sentences what it does and what it would cost in time. Do not add it until the person says yes. This includes extra endpoints, new agents, new cards, new dependencies, refactors, and "nice to have" improvements.
2. **Never add Claude as a contributor.** When committing or pushing:
   - Do not add `Co-Authored-By: Claude ...` or any other Claude/Anthropic co-author trailer.
   - Do not add "Generated with Claude Code" or similar lines to commit messages, PR descriptions, or code comments.
   - Do not change git `user.name` / `user.email`. Commits are authored by the team member running the command.
3. **Stay in scope.** Haggle is group catering in Los Angeles. One workflow: request → quotes → negotiate → review → deposit → confirmation → follow-up / food rescue. If a change pushes outside this, ask first.
4. **Never commit secrets.** Keys live in `.env` only. Update `.env.example` (names only, no values) when you add a new key.
5. **Don't break `main`.** `main` must always run. Work on a branch named after your slice (`concierge`, `discovery-factory`, `negotiation`, `booker`), test locally, then merge.

## What we are building

Haggle is a multi-agent marketplace inside ASI:One. A user describes their event; agents find real LA caterers, collect quotes, negotiate within each business's rules, take a deposit through Fetch.ai's Payment Protocol (Stripe), book, and rescue leftover food after the event.

## Agents (6 Haggle agents + one agent per vendor)

| Agent | File | Owns |
|---|---|---|
| Concierge `@haggle` | `agents/concierge.py` | ASI:One entry point, intake checklist, cards, session state, Payment Protocol (seller) |
| Discovery | `agents/discovery.py` | Agentverse search, Google Places, review-summary tool |
| Vendor Agent Factory | `agents/factory.py` | Menu extraction, vendor agent creation |
| Negotiator | `agents/negotiator.py` | Parallel quote requests, counter rounds, menu-matching tool, ranking |
| Booker | `agents/booker.py` | Stripe verification, confirmations, email, calendar, Photon texts |
| Follow-up | `agents/followup.py` | Reminders, leftover food rescue |
| Vendor agents | `vendors/template.py`, `vendors/vendor_host.py` | One per business; claimed vs unclaimed |

## Hard rules in the code

- **Prices, floors, budgets and payments are always computed in code, never by the LLM.** The LLM only parses text, extracts menus, writes friendly notes, and picks a negotiation strategy.
- **Concierge intake:** keep the internal checklist; never assume dietary needs or allergies ("none" must be explicit); no quote requests before the user confirms the summary.
- **Unclaimed vendors never negotiate and are never charged for.** They get an estimate (labeled "Estimate · not confirmed") and a quote request by email/SMS with a claim link.
- **Claimed vendors negotiate only within the owner's rules**; anything outside goes to owner approval.
- **During development, all emails, texts and calls go to team inboxes/phones only.**
- **Interactive Cards:** always include `card_protocol_version: "1"` and `requires_card_interaction: "true"`; all metadata values are strings (`json.dumps` the payload); parse replies with `json.loads` first, then regex fallback; put `rfq_id` and `vendor_id` in every `selection`.
- **Don't send `EndSessionContent`** until the booking is done; session state lives in `ctx.storage` keyed by sender.
- **Every action is idempotent on `rfq_id`** (no double booking or double charging).
- **Sentry:** every agent calls `shared/observability.py` init; every message model carries the `trace` field.
- **Hosting:** all agents run on one server in mailbox mode (`mailbox=True`, `publish_agent_details=True`), not in the Agentverse hosted editor.

## Conventions

- Python 3.10+, `uagents>=0.25.5`. LLM via OpenAI SDK with `base_url=https://api.asi1.ai/v1`, `model="asi1"`.
- Shared message models only in `shared/models.py`; card builders only in `shared/cards.py`.
- Every agent prints its address on startup; add it to the README agents table right away.
- Every agent's Agentverse README includes both badges:
  `![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)` and `![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)`.

## Key references

- Hackpack: https://www.fetch.ai/events/hackathons/la-hacks-ai-hackathon-2026/hackpack
- ASI:One agent example: https://uagents.fetch.ai/docs/examples/asi-1
- Interactive Cards: https://innovationlab.fetch.ai/resources/docs/interactive-cards/asi-interactive-cards
- Payment Protocol: https://innovationlab.fetch.ai/resources/docs/agent-transaction/agent-payment-protocol
- Stripe example: https://innovationlab.fetch.ai/resources/docs/examples/agent-transaction/stripe-horoscope-payment-protocol
- Agentverse search: https://uagents.fetch.ai/docs/guides/search
- Photon docs: https://photon.codes/docs/beta/api-reference
- Full build plan: see the team's "Haggle — LA Hacks AI 2026 Build Plan" doc.

## Deadline

Submissions close **Sunday Oct 18, 12:00 PM PDT**. Feature freeze 9:00 AM Sunday. After the freeze: bug fixes only.
