"""Vendor host: runs every vendor agent, one uAgent per business, and adds new ones while running.

Vendors come from vendors/data/*.json (written by the Factory or scripts/extract_all.py) and, with
HOST_DEMO_VENDORS=1, from vendors/fixtures/demo_*.json (claimed demo vendors).

Live add: a uAgents Bureau can't add agents after it starts, so each vendor runs its own agent
(own port) on this process's event loop via Agent.run_async(). POST /add-vendor {"slug": ...} on
VENDOR_HOST_PORT loads vendors/data/<slug>.json and starts that agent without touching the others.
Addresses are printed and written to vendors/addresses.json.

Each vendor gets an Agentverse mailbox registered with AGENTVERSE_API_KEY (no Inspector clicks), so
it shows up in Agentverse search; if that fails it falls back to a local endpoint.

Run from the repo root:  python -m vendors.vendor_host

Owner: Person B
"""

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx
from aiohttp import web
from dotenv import load_dotenv
from uagents import Agent
from uagents.config import parse_agentverse_config, parse_endpoint_config
from uagents.mailbox import AgentverseConnectRequest, register_in_agentverse
from uagents_core.identity import Identity
from uagents_core.registration import AgentProfile, RegistrationRequest

from shared.menu_schema import VendorMenu
from vendors.template import (
    DATA_DIR, PROTOCOL_DIGEST, PROTOCOL_NAME, description_for, make_vendor_agent, readme_for,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ADDRESSES_FILE = Path(__file__).parent / "addresses.json"
ALMANAC_URL = "https://agentverse.ai/v1/almanac/agents/{}"
REGISTER_WAIT_S = 20
CHAT_PROTOCOL_DIGEST = "proto:30a801ed3a83f9a0ff0a9f1e6fe958cb91da1fc2218b153df7b6cbf87bd33d62"

logger = logging.getLogger("vendor_host")


def vendor_seed(slug: str) -> str:
    return f"haggle-vendor-{slug}-{os.environ['VENDOR_SEED_SECRET']}"


def vendor_address(slug: str) -> str:
    return Identity.from_seed(vendor_seed(slug), 0).address


async def register_mailbox(seed: str, name: str, description: str, readme_text: str, protocols: list[str],
                           avatar_url: str = "") -> bool:
    """Register an agent's mailbox in Agentverse with AGENTVERSE_API_KEY, as the Inspector's Connect button does."""
    api_key = os.getenv("AGENTVERSE_API_KEY")
    if not api_key:
        return False
    agentverse = parse_agentverse_config(None)
    identity = Identity.from_seed(seed, 0)
    details = RegistrationRequest(
        address=identity.address,
        name=name,
        profile=AgentProfile(description=description, readme=readme_text, avatar_url=avatar_url),
        endpoints=parse_endpoint_config(None, agentverse, mailbox=True),
        protocols=[p.removeprefix("proto:") for p in protocols],
    )
    try:
        resp = await register_in_agentverse(
            request=AgentverseConnectRequest(user_token=api_key, agent_type="mailbox"),
            identity=identity, prefix="agent", agentverse=agentverse, agent_details=details,
        )
    except Exception:
        logger.exception(f"[{name}] mailbox registration failed")
        return False
    if not resp.success:
        logger.warning(f"[{name}] mailbox registration refused: {resp.detail}")
    return resp.success


async def register_vendor_mailbox(menu: VendorMenu) -> bool:
    return await register_mailbox(vendor_seed(menu.slug), menu.slug, description_for(menu), readme_for(menu),
                                  [PROTOCOL_DIGEST, CHAT_PROTOCOL_DIGEST], avatar_url=menu.photo_url)


async def wait_registered(address: str, timeout_s: float = REGISTER_WAIT_S) -> bool:
    """Wait until the Almanac knows the agent, so other agents can resolve it."""
    async with httpx.AsyncClient(timeout=5) as client:
        for _ in range(int(timeout_s)):
            try:
                r = await client.get(ALMANAC_URL.format(address))
                if r.status_code == 200 and r.json().get("status") == "active":
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    return False


def menu_paths() -> list[Path]:
    paths = sorted(DATA_DIR.glob("*.json"))
    if os.getenv("HOST_DEMO_VENDORS") == "1":
        paths += sorted(FIXTURES_DIR.glob("demo_*.json"))
    return paths


class VendorHost:
    """Runs vendor agents on the current event loop; vendors can be added at any time."""

    def __init__(self, port_base: int | None = None):
        self.agents: dict[str, Agent] = {}
        self.modes: dict[str, str] = {}
        self._tasks: list[asyncio.Task] = []
        self._next_port = port_base or int(os.getenv("VENDOR_PORT_BASE", "8110"))
        self._lock = asyncio.Lock()

    async def start(self, menu: VendorMenu) -> Agent:
        """Start one vendor agent (idempotent on slug) and wait until it is resolvable."""
        async with self._lock:
            if menu.slug in self.agents:
                return self.agents[menu.slug]
            port = self._next_port
            self._next_port += 1
            mailbox = await register_vendor_mailbox(menu)
            agent = make_vendor_agent(
                menu,
                seed=vendor_seed(menu.slug),
                on_menu_change=register_vendor_mailbox,
                port=port,
                loop=asyncio.get_running_loop(),
                publish_agent_details=True,
                **({"mailbox": True} if mailbox else {"endpoint": [f"http://127.0.0.1:{port}/submit"]}),
            )
            self._tasks.append(asyncio.create_task(agent.run_async()))
            self.agents[menu.slug] = agent
            self.modes[menu.slug] = "mailbox" if mailbox else "local endpoint"
            self.write_addresses()
        live = await wait_registered(agent.address)
        status = "claimed" if menu.claimed else "unclaimed"
        print(f"vendor {menu.slug:<32} {agent.address}  ({status}, {self.modes[menu.slug]}, port {port})"
              + ("" if live else "  [not in the Almanac yet]"), flush=True)
        return agent

    async def start_all(self) -> None:
        for path in menu_paths():
            try:
                await self.start(VendorMenu.model_validate_json(path.read_text()))
            except Exception:
                logger.exception(f"couldn't start vendor from {path}")

    def write_addresses(self) -> None:
        ADDRESSES_FILE.write_text(json.dumps(
            {slug: {"address": a.address, "mode": self.modes[slug]} for slug, a in sorted(self.agents.items())},
            indent=2) + "\n")


def check_digest() -> None:
    """Fail loudly if the protocol changed but PROTOCOL_DIGEST was not updated."""
    probe = make_vendor_agent(VendorMenu(name="Digest Check", slug="digest-check"), seed="haggle-digest-check")
    actual = next(d for d, p in probe.protocols.items() if p.name == PROTOCOL_NAME)
    if actual != PROTOCOL_DIGEST:
        raise RuntimeError(f"vendors/template.py PROTOCOL_DIGEST is {PROTOCOL_DIGEST}, but the protocol is {actual}")


def rest_app(host: VendorHost) -> web.Application:
    async def add_vendor(request: web.Request) -> web.Response:
        slug = (await request.json()).get("slug", "")
        path = DATA_DIR / f"{slug}.json"
        if not slug or not path.exists():
            return web.json_response({"error": f"no vendors/data/{slug}.json"}, status=404)
        agent = await host.start(VendorMenu.model_validate_json(path.read_text()))
        return web.json_response({"slug": slug, "address": agent.address, "mode": host.modes[slug]})

    async def list_vendors(_: web.Request) -> web.Response:
        return web.json_response({s: a.address for s, a in host.agents.items()})

    app = web.Application()
    app.add_routes([web.post("/add-vendor", add_vendor), web.get("/vendors", list_vendors)])
    return app


async def main() -> None:
    host = VendorHost()
    runner = web.AppRunner(rest_app(host))
    await runner.setup()
    port = int(os.getenv("VENDOR_HOST_PORT", "8109"))
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"vendor host: POST http://127.0.0.1:{port}/add-vendor", flush=True)
    await host.start_all()
    print(f"hosting {len(host.agents)} vendor agents; addresses in {ADDRESSES_FILE}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: [%(name)s] %(message)s")
    check_digest()
    asyncio.run(main())
