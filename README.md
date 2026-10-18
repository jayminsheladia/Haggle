# Haggle

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

**Tell ASI:One what your event needs. Haggle's agents find vendors, make them compete, negotiate, and book — in under 3 minutes.**

Haggle is a multi-agent group catering marketplace for Los Angeles, built on Fetch.ai (uAgents, Agentverse, ASI:One) for LA Hacks AI 2026. A user describes their event in ASI:One; agents find real LA caterers, collect quotes, negotiate within each business's rules, take a deposit through the Payment Protocol (Stripe), book, and rescue leftover food after the event.

## Agents

| Agent | File | Address | Agentverse profile |
|---|---|---|---|
| Concierge `@haggle` | `agents/concierge.py` | `agent1qtczn7424hmp43jqetnl9pydsmqhcfzfg8muzyl5ajxwyep0mt7lxxewe9k` | [profile](https://agentverse.ai/agents/details/agent1qtczn7424hmp43jqetnl9pydsmqhcfzfg8muzyl5ajxwyep0mt7lxxewe9k/profile) |
| Discovery | `agents/discovery.py` | `agent1q27868llc79a2pncexj4z6nejtjw87rg4p3cv4tjt27q9vuh2s8w7clzg5y` | [profile](https://agentverse.ai/agents/details/agent1q27868llc79a2pncexj4z6nejtjw87rg4p3cv4tjt27q9vuh2s8w7clzg5y/profile) |
| Vendor Agent Factory | `agents/factory.py` | `agent1qdzh8nw69qe2x0rw3tc7rg8ddt9e6lda8x7y6hqfjwfaldk2k7kzukj7rh5` | _TBD (needs a free mailbox slot)_ |
| Negotiator | `agents/negotiator.py` | `agent1qwv99f5t6fyjgyjemmdsnluzmayarx8z4v40ytx0df0njqffl4vy6hdgj2z` | _TBD_ |
| Booker | `agents/booker.py` | _TBD_ | _TBD_ |
| Follow-up | `agents/followup.py` | _TBD_ | _TBD_ |

### Vendor agents

| Vendor | Status | Address | Agentverse profile |
|---|---|---|---|
| `alfredo-catering-inc` | unclaimed | `agent1qt9gzagq7z8zauncjqh57ppu5ckfqfj5vr7ma0ed525vwxq9lplkgpf092a` | _local endpoint (no mailbox slot)_ |
| `beverly-catering` | unclaimed | `agent1q0tyc5rg495qj2nxch7hdydz9sgh5cshfav4jcr3vwj7fn5vgpz5uhltzxf` | _local endpoint (no mailbox slot)_ |
| `bite-catering-couture` | unclaimed | `agent1qt3d4tjkk0j95rxxrx96e8ev5jv4c7axzdmn0sjvdk3cgj69uxag7pftz0m` | _local endpoint (no mailbox slot)_ |
| `california-fresh` | unclaimed | `agent1qvdktefhz79faf8ett243nxqks467jwaq2wcxzywcd2p32f3pl3pccyl094` | [profile](https://agentverse.ai/agents/details/agent1qvdktefhz79faf8ett243nxqks467jwaq2wcxzywcd2p32f3pl3pccyl094/profile) |
| `demo-bruin-tacos` | claimed (demo) | `agent1qg9hwj6783d4eax7tl0taj94h3msmyp23ul896zhvwr3f0efen0dxmnlu53` | _local endpoint (no mailbox slot)_ |
| `demo-westwood-grill` | claimed (demo) | `agent1q2c5qk4ach3u45xlwqyk8mrpymg9vl7m8uqsk064khatvp0cvw65umpasnp` | _local endpoint (no mailbox slot)_ |
| `rutts-hawaiian-cafe-catering` | unclaimed | `agent1qfqdcnj54d0tgg0v58sq4vkr2cg9ganf7egee2jkvrgzj03956h5jgglmcd` | _local endpoint (no mailbox slot)_ |
| `santa-maria-bbq-on-site-catering` | unclaimed | `agent1qtuxvjfct873q3xpnfkjshcytf9rnjprg8dkuepl8f2q0ga6xt6lgjq2gzj` | _local endpoint (no mailbox slot)_ |
| `sunnin-lebanese-cafe` | unclaimed | `agent1qf5h5462n8txj65027u0nmufrxezdd799twvql67qvus77ntxe9gk3jxvtx` | [profile](https://agentverse.ai/agents/details/agent1qf5h5462n8txj65027u0nmufrxezdd799twvql67qvus77ntxe9gk3jxvtx/profile) |
| `sweetpea-boutique-kitchen` | unclaimed | `agent1qdf9fhtwsgltfhw5urvjtfvym9umjxagjlcdfartjvphkj07xlq57n8kn8g` | _local endpoint (no mailbox slot)_ |
| `tender-greens` | unclaimed | `agent1qffmfuystcvh2zswkf6stu43rekq02cd0qt2hqtpapgtak54a7sgxd9p0re` | [profile](https://agentverse.ai/agents/details/agent1qffmfuystcvh2zswkf6stu43rekq02cd0qt2hqtpapgtak54a7sgxd9p0re/profile) |
| `ucla-conferences-catering` | unclaimed | `agent1qtcdzyl760qzmmprqmtruy0dpz8wumrlsl3u0n5v60xmke85dcxwudtw4fs` | [profile](https://agentverse.ai/agents/details/agent1qtcdzyl760qzmmprqmtruy0dpz8wumrlsl3u0n5v60xmke85dcxwudtw4fs/profile) |
| `yekta-catering-takeout` | unclaimed | `agent1q2ka6zhjew0y9uzhjrgsvgzpqpj9e2vg4afvgrpxt2rk07md8ks674h0l6p` | _local endpoint (no mailbox slot)_ |

Every agent prints its address on startup — paste it here right away.

Vendor addresses come from each vendor's slug + `VENDOR_SEED_SECRET`, so they match only with the team's shared secret; the vendor host writes the current list to `vendors/addresses.json`.

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

Run every agent from the repo root as a module:

```bash
python -m vendors.vendor_host   # every vendor agent (REST on 8109, agents on 8110+)
python -m agents.factory        # Vendor Agent Factory, port 8004 (needs the vendor host)
python -m agents.negotiator     # Negotiator, port 8003
python -m agents.discovery      # Discovery, port 8002
python -m agents.concierge      # @haggle, port 8001
```

Start them in that order, each in its own terminal. Agents register their Agentverse mailbox with
`AGENTVERSE_API_KEY` where they can; without a free mailbox slot the Factory and vendors fall back to a
local endpoint, which works when everything runs on one machine.

Point the Concierge at the agents you run with `DISCOVERY_ADDRESS`, `NEGOTIATOR_ADDRESS` and
`FACTORY_ADDRESS` in `.env`. `HOST_DEMO_VENDORS=1` also hosts the two claimed demo vendors.

To (re)read the caterers in `vendors/sources.csv` into `vendors/data/`: `python -m scripts.extract_all`.

_TBD — the Booker and Follow-up agents._

## Test

Discovery (with the agent running): `python -m scripts.test_discovery "Ackerman Union, UCLA"` sends one search and prints the caterers it returns.

Pricing unit tests: `python -m unittest discover tests`

Negotiation without ASI:One (2 demo vendors + Negotiator in one Bureau): `python -m vendors.demo_negotiation`

In ASI:One: `@haggle catering for 60 Friday 6pm at Ackerman Union UCLA, $900 max, 10 vegetarian, 3 halal, no allergies`
→ tick caterers → quotes carousel → open one → Negotiate lower. Unclaimed caterers give labeled estimates and never negotiate.

Vendor agents can be chatted with directly: ask about the menu, or as the owner send `claim`, then the code,
then fill in the rules form. Owners answer approvals with `approve A1234`, `decline A1234`, `counter A1234 750`
(or `POST /owner-reply` on the vendor's port, for the Photon bridge).

_TBD — how to run a full request → quote → negotiate → pay flow in ASI:One (Stripe test card `4242 4242 4242 4242`)._

## Repo layout

```
shared/        message models, card builders, selection parsing, LLM client, Sentry
agents/        the 6 Haggle agents (mailbox mode, one server)
tools/         menu extraction, menu matching, review summary, location
vendors/       vendor agent template, pricing, vendor host, vendors/data/*.json menus
integrations/  Stripe, Google Places, email, Photon, calendar
scripts/       extract_all (menus from sources.csv), test scripts
tests/         unit tests
photon-bridge/ TypeScript bridge (only if Photon has no REST API)
data/          food_rescue_orgs.json
docs/          build plan, demo script, observability notes
```

## Contributing

Read [CLAUDE.md](CLAUDE.md) first. Work on your slice branch (`concierge`, `discovery-factory`, `negotiation`, `booker`) and merge into `main` only after a local test — `main` must always run.
