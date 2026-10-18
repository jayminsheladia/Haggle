# Haggle

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

**Tell ASI:One what your event needs. Haggle's agents find vendors, make them compete, negotiate, and book — in under 3 minutes.**

Haggle is a multi-agent group catering marketplace for Los Angeles, built on Fetch.ai (uAgents, Agentverse, ASI:One) for LA Hacks AI 2026. A user describes their event in ASI:One; agents find real LA caterers, collect quotes, negotiate within each business's rules, take a deposit through the Payment Protocol (Stripe), book, and rescue leftover food after the event.

## Agents

| Agent | File | Address | Agentverse profile |
|---|---|---|---|
| Concierge `@haggle` | `agents/concierge.py` | _TBD_ | _TBD_ |
| Discovery | `agents/discovery.py` | _TBD_ | _TBD_ |
| Vendor Agent Factory | `agents/factory.py` | _TBD_ | _TBD_ |
| Negotiator | `agents/negotiator.py` | `agent1qwv99f5t6fyjgyjemmdsnluzmayarx8z4v40ytx0df0njqffl4vy6hdgj2z` | _TBD_ |
| Booker | `agents/booker.py` | _TBD_ | _TBD_ |
| Follow-up | `agents/followup.py` | _TBD_ | _TBD_ |

### Vendor agents

| Vendor | Status | Address | Agentverse profile |
|---|---|---|---|
| _TBD_ | claimed / unclaimed | _TBD_ | _TBD_ |

Every agent prints its address on startup — paste it here right away.

## Setup

Requires Python 3.10+.

```bash
git clone https://github.com/naman-gupta-02/Haggle.git
cd Haggle
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys
```

### Keys

| Key | Where to get it |
|---|---|
| `ASI1_API_KEY` | asi1.ai dashboard → API keys |
| `AGENTVERSE_API_KEY` | agentverse.ai → profile → API keys |
| `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY` | Stripe dashboard (test mode) |
| `GOOGLE_PLACES_API_KEY` | Google Cloud console → enable Places API (New) |
| `RESEND_API_KEY` | resend.com |
| `PHOTON_API_KEY`, `PHOTON_PROJECT_ID` | Photon dashboard |
| `SENTRY_DSN` | Sentry project settings |
| `*_SEED`, `VENDOR_SEED_SECRET` | Generate long random strings |
| `TEAM_INBOX_EMAIL`, `TEAM_PHONE` | Team inbox / phone — all dev emails and texts go here |

## Run

_TBD — commands to start every agent and the vendor host._

## Test

_TBD — how to run a full request → quote → negotiate → pay flow in ASI:One (Stripe test card `4242 4242 4242 4242`)._

## Repo layout

```
shared/        message models, card builders, selection parsing, LLM client, Sentry
agents/        the 6 Haggle agents (mailbox mode, one server)
tools/         menu matching, review summary, location
vendors/       vendor agent template, Bureau host, vendors/data/*.json menus
integrations/  Stripe, Google Places, email, Photon, calendar
photon-bridge/ TypeScript bridge (only if Photon has no REST API)
data/          food_rescue_orgs.json
docs/          build plan, demo script, observability notes
```

## Contributing

Read [CLAUDE.md](CLAUDE.md) first. Work on your slice branch (`concierge`, `discovery-factory`, `negotiation`, `booker`) and merge into `main` only after a local test — `main` must always run.
