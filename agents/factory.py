"""Vendor Agent Factory: menu extraction (LLM -> validated JSON), vendor agent creation.

OnboardRequest (or chat "onboard <url>") -> read the menu page (tools/menu_extract.py) -> save
vendors/data/<slug>.json -> POST /add-vendor to the vendor host -> OnboardResult with the new agent's
address. status is "live" (menu extracted), "estimated" (agent live, but no usable public prices,
clearly labeled) or "failed".

Idempotent on slug: a business that already has menu data is not re-read for an OnboardRequest, and
a claimed vendor's data is never overwritten.

Run from the repo root (with the vendor host running):  python -m agents.factory

Owner: Person B
"""

import asyncio
import csv
import os
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, TextContent, chat_protocol_spec

from integrations import places
from shared import observability
from shared.cards import text_message
from shared.menu_schema import VendorMenu, slugify
from shared.models import OnboardRequest, OnboardResult
from tools.menu_extract import extract_menu_with_reason
from vendors.template import DATA_DIR
from vendors.vendor_host import register_mailbox

load_dotenv()

VENDOR_HOST_URL = os.getenv("VENDOR_HOST_URL", f"http://127.0.0.1:{os.getenv('VENDOR_HOST_PORT', '8109')}")
SOURCES = Path(__file__).parent.parent / "vendors" / "sources.csv"
README_PATH = Path(__file__).with_name("factory_README.md")
DESCRIPTION = "Haggle Vendor Agent Factory: turns any Los Angeles caterer's menu page into a Haggle vendor agent."

Progress = Callable[[str], Awaitable[None]]


def menu_url_for(place_id: str, website: str) -> str:
    """Prefer the catering menu page we know about (vendors/sources.csv) over the business homepage."""
    if place_id and SOURCES.exists():
        for row in csv.DictReader(SOURCES.open()):
            if row.get("place_id") == place_id and row.get("catering_menu_url"):
                return row["catering_menu_url"]
    return website


async def onboard(ctx: Context, req: OnboardRequest, progress: Progress | None = None,
                  reuse: bool = True) -> OnboardResult:
    started = time.monotonic()
    timings: dict[str, float] = {}
    slug = slugify(req.business_name) if req.business_name else ""

    async def step(text: str) -> None:
        if progress:
            await progress(text)

    path = DATA_DIR / f"{slug}.json" if slug else None
    existing = VendorMenu.model_validate_json(path.read_text()) if path and path.exists() else None
    reason = ""
    if existing and (existing.claimed or reuse):
        menu = existing  # never overwrite a claimed vendor; reuse menus we already read
    else:
        url = menu_url_for(req.place_id, req.website)
        await step("Reading menu…")
        t = time.monotonic()
        with observability.span("factory.extract", f"extract {req.business_name or url}", req.trace):
            if url:
                menu, reason = await extract_menu_with_reason(url, req.business_name or None, req.place_id,
                                                              req.phone, req.email, address=req.address)
            else:
                menu, reason = VendorMenu(name=req.business_name, slug=slug, place_id=req.place_id,
                                          phone=req.phone, email=req.email, address=req.address), "no website to read"
        timings["extract"] = time.monotonic() - t
        if not menu.photo_url and menu.place_id:
            async with httpx.AsyncClient(timeout=10) as client:
                menu.photo_url = await places.place_photo(menu.place_id, client, menu.name) or ""
        DATA_DIR.mkdir(exist_ok=True)
        (DATA_DIR / f"{menu.slug}.json").write_text(menu.model_dump_json(indent=2) + "\n")

    await step("Creating agent…")
    t = time.monotonic()
    try:
        with observability.span("factory.create_agent", f"add vendor {menu.slug}", req.trace):
            async with httpx.AsyncClient(timeout=40) as client:
                r = await client.post(f"{VENDOR_HOST_URL}/add-vendor", json={"slug": menu.slug})
                r.raise_for_status()
                address = r.json()["address"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        ctx.logger.error(f"[{menu.slug}] vendor host failed: {e}")
        return OnboardResult(vendor_id=menu.slug, agent_address="", status="failed", error=str(e),
                             message="Couldn't start the vendor agent (is the vendor host running?)", trace=req.trace)
    timings["create_agent"] = time.monotonic() - t

    status = "live" if menu.extraction_status == "extracted" else "estimated"
    ctx.logger.info(f"[{menu.slug}] onboarded as {status} -> {address} in {time.monotonic() - started:.1f}s "
                    + " ".join(f"{k}={v:.1f}s" for k, v in timings.items()) + (f" ({reason})" if reason else ""))
    message = (f"{len(menu.priced_items)} priced menu items" if status == "live"
               else f"Estimates only: {reason or 'no usable public menu prices'}")
    return OnboardResult(vendor_id=menu.slug, agent_address=address, claimed=menu.claimed,
                         menu_items=len(menu.items), status=status, message=message, trace=req.trace)


FACTORY_PORT = 8004
factory = Protocol(name="HaggleFactory", version="0.1.0")
chat = Protocol(spec=chat_protocol_spec)


@factory.on_message(OnboardRequest, replies=OnboardResult)
async def on_onboard(ctx: Context, sender: str, req: OnboardRequest):
    observability.tag(req.trace)
    try:
        result = await onboard(ctx, req)
    except Exception as e:  # never crash: always answer
        ctx.logger.exception(f"onboarding {req.business_name!r} crashed")
        result = OnboardResult(vendor_id=slugify(req.business_name), agent_address="", status="failed",
                               error=str(e), message="Onboarding failed", trace=req.trace)
    await ctx.send(sender, result)


@chat.on_message(ChatMessage)
async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
    text = re.sub(r"^(@[\w.-]+[\s,:]*)+", "", "".join(c.text for c in msg.content if isinstance(c, TextContent))).strip()
    m = re.search(r"\bonboard\s+(?:this business\s+)?(\S+)", text, re.I)
    if not m:
        await ctx.send(sender, text_message("Send \"onboard <menu page URL>\" and I'll turn that business into a "
                                            "Haggle vendor agent in about a minute."))
        return
    url = m.group(1) if m.group(1).startswith("http") else f"https://{m.group(1)}"

    async def progress(step: str) -> None:
        await ctx.send(sender, text_message(step))

    result = await onboard(ctx, OnboardRequest(business_name="", website=url, trace=uuid4().hex),
                           progress, reuse=False)
    if result.status == "failed":
        await ctx.send(sender, text_message(f"Sorry, that didn't work: {result.message}"))
    else:
        await ctx.send(sender, text_message(
            f"Agent live at {result.agent_address} ({result.vendor_id}). {result.message}. "
            f"Profile: https://agentverse.ai/agents/details/{result.agent_address}/profile"))


@chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


def build_agent(mailbox: bool) -> Agent:
    agent = Agent(
        name="haggle-factory",
        seed=os.environ["FACTORY_SEED"],
        port=FACTORY_PORT,
        publish_agent_details=True,
        readme_path=str(README_PATH),
        description=DESCRIPTION,
        handle_messages_concurrently=True,  # onboard several businesses at once
        **({"mailbox": True} if mailbox else {"endpoint": [f"http://127.0.0.1:{FACTORY_PORT}/submit"]}),
    )

    @agent.on_event("startup")
    async def on_startup(ctx: Context):
        ctx.logger.info(f"Factory address: {agent.address} ({'mailbox' if mailbox else 'local endpoint'})")

    agent.include(factory, publish_manifest=True)
    agent.include(chat, publish_manifest=True)
    return agent


if __name__ == "__main__":
    observability.init("factory")
    # Register the mailbox with AGENTVERSE_API_KEY (no Inspector click); without a free mailbox slot the
    # Factory still works for agents on this machine through a local endpoint.
    has_mailbox = asyncio.run(register_mailbox(os.environ["FACTORY_SEED"], "haggle-factory", DESCRIPTION,
                                               README_PATH.read_text(), [factory.digest, chat.digest]))
    asyncio.set_event_loop(asyncio.new_event_loop())  # asyncio.run closed the previous one
    build_agent(has_mailbox).run()
