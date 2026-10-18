# Haggle

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

**Tell ASI:One what your event needs. Haggle's agents find vendors, make them compete, negotiate, and book — in under 3 minutes.**

Haggle is a multi-agent group catering marketplace for Los Angeles, built on Fetch.ai (uAgents, Agentverse, ASI:One) for LA Hacks AI 2026. A user describes their event in ASI:One; agents find real LA caterers, collect quotes, negotiate within each business's rules, take a deposit through the Payment Protocol (Stripe), book, and rescue leftover food after the event.

## Agents

| Agent | File | Address | Agentverse profile |
|---|---|---|---|
| Concierge `@haggle` | `agents/concierge.py` | `agent1qtczn7424hmp43jqetnl9pydsmqhcfzfg8muzyl5ajxwyep0mt7lxxewe9k` | [profile](https://agentverse.ai/agents/details/agent1qtczn7424hmp43jqetnl9pydsmqhcfzfg8muzyl5ajxwyep0mt7lxxewe9k/profile) |
| Discovery | `agents/discovery.py` | `agent1qvrfzf7mnxhz6zmzgn2q6htwjec6fs5rg0wnz6ucj4cjhqlaefwr6l26g2g` | [profile](https://agentverse.ai/agents/details/agent1qvrfzf7mnxhz6zmzgn2q6htwjec6fs5rg0wnz6ucj4cjhqlaefwr6l26g2g/profile) |
| Vendor Agent Factory | `agents/factory.py` | `agent1qdzh8nw69qe2x0rw3tc7rg8ddt9e6lda8x7y6hqfjwfaldk2k7kzukj7rh5` | [profile](https://agentverse.ai/agents/details/agent1qdzh8nw69qe2x0rw3tc7rg8ddt9e6lda8x7y6hqfjwfaldk2k7kzukj7rh5/profile) |
| Negotiator | `agents/negotiator.py` | `agent1qg68ah5xmm3t9lcecua0dvqqknkqhqv4scrpf3cjjhx64lrqkpvd7erglhl` | [profile](https://agentverse.ai/agents/details/agent1qg68ah5xmm3t9lcecua0dvqqknkqhqv4scrpf3cjjhx64lrqkpvd7erglhl/profile) |
| Booker | `agents/booker.py` | _TBD_ | _TBD_ |
| Follow-up | `agents/followup.py` | _TBD_ | _TBD_ |

### Vendor agents

| Vendor | Status | Address | Agentverse profile |
|---|---|---|---|
| `alfredo-catering-inc` | unclaimed | `agent1qt9gzagq7z8zauncjqh57ppu5ckfqfj5vr7ma0ed525vwxq9lplkgpf092a` | [profile](https://agentverse.ai/agents/details/agent1qt9gzagq7z8zauncjqh57ppu5ckfqfj5vr7ma0ed525vwxq9lplkgpf092a/profile) |
| `beverly-catering` | unclaimed | `agent1q0tyc5rg495qj2nxch7hdydz9sgh5cshfav4jcr3vwj7fn5vgpz5uhltzxf` | [profile](https://agentverse.ai/agents/details/agent1q0tyc5rg495qj2nxch7hdydz9sgh5cshfav4jcr3vwj7fn5vgpz5uhltzxf/profile) |
| `bite-catering-couture` | unclaimed | `agent1qt3d4tjkk0j95rxxrx96e8ev5jv4c7axzdmn0sjvdk3cgj69uxag7pftz0m` | [profile](https://agentverse.ai/agents/details/agent1qt3d4tjkk0j95rxxrx96e8ev5jv4c7axzdmn0sjvdk3cgj69uxag7pftz0m/profile) |
| `california-fresh` | unclaimed | `agent1qvdktefhz79faf8ett243nxqks467jwaq2wcxzywcd2p32f3pl3pccyl094` | [profile](https://agentverse.ai/agents/details/agent1qvdktefhz79faf8ett243nxqks467jwaq2wcxzywcd2p32f3pl3pccyl094/profile) |
| `demo-bruin-tacos` | claimed (demo) | `agent1qg9hwj6783d4eax7tl0taj94h3msmyp23ul896zhvwr3f0efen0dxmnlu53` | [profile](https://agentverse.ai/agents/details/agent1qg9hwj6783d4eax7tl0taj94h3msmyp23ul896zhvwr3f0efen0dxmnlu53/profile) |
| `demo-green-fork` | claimed (demo) | `agent1qvqphfwpa6qq6748zmr63mzq0afq3rk4a0yzh5ygq04n20c5yd76sv2kdzq` | [profile](https://agentverse.ai/agents/details/agent1qvqphfwpa6qq6748zmr63mzq0afq3rk4a0yzh5ygq04n20c5yd76sv2kdzq/profile) |
| `demo-saffron-sage` | claimed (demo) | `agent1qfsgpykjxu9wart9tf887vey6tcc3vrurq4yagqzkr5p4rxfufzzs9flrtx` | [profile](https://agentverse.ai/agents/details/agent1qfsgpykjxu9wart9tf887vey6tcc3vrurq4yagqzkr5p4rxfufzzs9flrtx/profile) |
| `demo-westwood-grill` | claimed (demo) | `agent1q2c5qk4ach3u45xlwqyk8mrpymg9vl7m8uqsk064khatvp0cvw65umpasnp` | [profile](https://agentverse.ai/agents/details/agent1q2c5qk4ach3u45xlwqyk8mrpymg9vl7m8uqsk064khatvp0cvw65umpasnp/profile) |
| `rutts-hawaiian-cafe-catering` | unclaimed | `agent1qfqdcnj54d0tgg0v58sq4vkr2cg9ganf7egee2jkvrgzj03956h5jgglmcd` | [profile](https://agentverse.ai/agents/details/agent1qfqdcnj54d0tgg0v58sq4vkr2cg9ganf7egee2jkvrgzj03956h5jgglmcd/profile) |
| `santa-maria-bbq-on-site-catering` | unclaimed | `agent1qtuxvjfct873q3xpnfkjshcytf9rnjprg8dkuepl8f2q0ga6xt6lgjq2gzj` | [profile](https://agentverse.ai/agents/details/agent1qtuxvjfct873q3xpnfkjshcytf9rnjprg8dkuepl8f2q0ga6xt6lgjq2gzj/profile) |
| `sunnin-lebanese-cafe` | unclaimed | `agent1qf5h5462n8txj65027u0nmufrxezdd799twvql67qvus77ntxe9gk3jxvtx` | [profile](https://agentverse.ai/agents/details/agent1qf5h5462n8txj65027u0nmufrxezdd799twvql67qvus77ntxe9gk3jxvtx/profile) |
| `sweetpea-boutique-kitchen` | unclaimed | `agent1qdf9fhtwsgltfhw5urvjtfvym9umjxagjlcdfartjvphkj07xlq57n8kn8g` | [profile](https://agentverse.ai/agents/details/agent1qdf9fhtwsgltfhw5urvjtfvym9umjxagjlcdfartjvphkj07xlq57n8kn8g/profile) |
| `tender-greens` | unclaimed | `agent1qffmfuystcvh2zswkf6stu43rekq02cd0qt2hqtpapgtak54a7sgxd9p0re` | [profile](https://agentverse.ai/agents/details/agent1qffmfuystcvh2zswkf6stu43rekq02cd0qt2hqtpapgtak54a7sgxd9p0re/profile) |
| `ucla-conferences-catering` | unclaimed | `agent1qtcdzyl760qzmmprqmtruy0dpz8wumrlsl3u0n5v60xmke85dcxwudtw4fs` | [profile](https://agentverse.ai/agents/details/agent1qtcdzyl760qzmmprqmtruy0dpz8wumrlsl3u0n5v60xmke85dcxwudtw4fs/profile) |
| `yekta-catering-takeout` | unclaimed | `agent1q2ka6zhjew0y9uzhjrgsvgzpqpj9e2vg4afvgrpxt2rk07md8ks674h0l6p` | [profile](https://agentverse.ai/agents/details/agent1q2ka6zhjew0y9uzhjrgsvgzpqpj9e2vg4afvgrpxt2rk07md8ks674h0l6p/profile) |

Every agent prints its address on startup — paste it here right away.

Vendors with slugs starting `demo-` (Bruin Tacos, Westwood Grill, Saffron & Sage, Green Fork) are fictional businesses with realistic prices, claimed so they can negotiate; cards show them as **Connected**. Every other vendor is a real LA caterer, **Not connected** (unclaimed): its prices are copied from its public menu (Sunnin, Yekta, Alfredo, UCLA, California Fresh), or it publishes no prices and asks for a quote instead.

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

Or start all of them with one command (what the server runs): `python -m scripts.run_all`.
Otherwise start them in that order, each in its own terminal. Agents register their Agentverse mailbox with
`AGENTVERSE_API_KEY` where they can; without a free mailbox slot the Factory and vendors fall back to a
local endpoint, which works when everything runs on one machine.

Point the Concierge at the agents you run with `DISCOVERY_ADDRESS`, `NEGOTIATOR_ADDRESS` and
`FACTORY_ADDRESS` in `.env`. `HOST_DEMO_VENDORS=1` also hosts the two claimed demo vendors.

To (re)read the caterers in `vendors/sources.csv` into `vendors/data/`: `python -m scripts.extract_all`.

_TBD — the Booker and Follow-up agents._

## Deploy (always-on server)

All agents run in one container (`Dockerfile`, `python -m scripts.run_all`); mailbox agents need no inbound ports.

**Railway** (`railway.json` is in the repo):
1. railway.com → New Project → Deploy from GitHub repo → `naman-gupta-02/Haggle`, branch `concierge` (or `main` after merging).
2. Service → Variables → Raw editor → paste every line of your `.env` (keys and seeds; never commit them).
3. Deploy. The logs show each agent's address; they must match the table above (same seeds = same addresses).
4. **Stop any local copy of the agents** (same seeds on two machines fight over the mailboxes).

Agent storage (sessions, approvals) lives in the container and resets on redeploy; vendor menus ship with the repo.

## External agents

- **Image Generator Agent** (`agent1q0utywlfr3dfrfkwk4fjmtdrfew0zh692untdlr877d6ay8ykwpewydmxtl`, OpenAI image model):
  generates a food photo for vendors that have no photo of their own (the fictional demo vendors, or a business with
  nothing on Google Places). Used offline by `python -m scripts.generate_photos` (90 s timeout per image); images are
  saved to `assets/vendors/` and served from the public repo. Fallback: the vendor simply has no photo. Real businesses
  always get their Google Places photo first.

Not used in the live flow: Evaluated as backups for finding menus, and rejected (CLAUDE.md requires answers in ~5 s):
- "Website Scraper Agent" (`agent1qwnjmzwwdq9rjs30y3qw988htrvte6lk2xaak9xg4kz0fsdz0t9ws4mwsgs`): blocked by the same
  sites (HTTP 403), 13.6 s.
- "Tavily Search Agent" (`agent1qt5uffgp0l3h9mqed8zh8vy5vs374jl2f8y0mjjvqm44axqseejqzmzx9v8`): 17.7 s, returns a prose
  summary without page links (it confirmed Beverly Catering publishes no prices).

Instead the Factory follows each site's own menu/catering links and PDFs (and /catering, /menu), reads PDF pages and
menu images with the ASI:One vision model (OCR), and keeps the page with the most prices; photos come from the site's
preview image or Google Places.
If the business's own site has no prices, the Factory searches the web for its menu on menu platforms (SinglePlatform,
Toast, ezCater, AllMenus, ...; `tools/menu_search.py`) and only uses a page that shows the business's own phone number or
street address. Search uses `SERPER_API_KEY` (Google results) or `TAVILY_API_KEY` if set, else DuckDuckGo, which
rate-limits after a few queries. Also evaluated: the Agentverse "Brave Search Agent"
(`agent1q0vdmfxx6mhfam5j82ur6f47dydn2uth7llmff2amwc9g0v7armmymz5lu3`): acknowledged after 11.6 s, no answer in 45 s.

Caterers whose real menu has no public prices are shown as **Price on request** with their menu and a
"Request a confirmed quote" button; we never invent a price.

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
