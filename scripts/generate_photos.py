"""Give every vendor a picture.

For each vendor menu (vendors/data/*.json and vendors/fixtures/*.json) without a photo_url:
1. a real business: its Google Places photo;
2. otherwise (fictional demo vendors, or nothing on Places): ask the Agentverse "Image Generator Agent"
   (external agent, OpenAI image model) for a photo of its food, save it to assets/vendors/<slug>.jpg and
   link it from the public repo (ASSET_BASE_URL).
Run from the repo root, then commit assets/vendors and the menus:  python -m scripts.generate_photos

Owner: Person B
"""

import asyncio
import base64
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from PIL import Image
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, ResourceContent, TextContent, chat_protocol_spec,
)
from uagents_core.identity import Identity
from uagents_core.storage import ExternalStorage

load_dotenv()

from integrations.places import place_photo  # noqa: E402
from shared.menu_schema import VendorMenu  # noqa: E402
from vendors.vendor_host import register_mailbox  # noqa: E402

IMAGE_AGENT = "agent1q0utywlfr3dfrfkwk4fjmtdrfew0zh692untdlr877d6ay8ykwpewydmxtl"  # "Image Generator Agent"
IMAGE_TIMEOUT_S = 90
ASSET_DIR = Path("assets/vendors")
ASSET_BASE_URL = os.getenv("ASSET_BASE_URL",
                           "https://raw.githubusercontent.com/naman-gupta-02/Haggle/concierge/assets/vendors")
CHAT_DIGEST = "proto:30a801ed3a83f9a0ff0a9f1e6fe958cb91da1fc2218b153df7b6cbf87bd33d62"
SEED = f"haggle-photo-studio-{os.environ['VENDOR_SEED_SECRET']}"

logger = logging.getLogger("generate_photos")


def menus() -> list[Path]:
    return sorted(Path("vendors/data").glob("*.json")) + sorted(Path("vendors/fixtures").glob("*.json"))


def prompt_for(menu: VendorMenu) -> str:
    dishes = ", ".join(i.name for i in menu.items[:5]) or "catering dishes"
    return (f"A warm, appetizing, realistic photo of a catering spread for an event, featuring {dishes}, "
            "served on a buffet table. Natural light. No text, no logos, no people.")


async def places_pass() -> list[Path]:
    """Real businesses first: their own Google Places photo. Returns the menus still without a photo."""
    missing = []
    async with httpx.AsyncClient(timeout=15) as client:
        for path in menus():
            menu = VendorMenu.model_validate_json(path.read_text())
            if menu.photo_url:
                continue
            if menu.place_id:
                menu.photo_url = await place_photo(menu.place_id, client, menu.name) or ""
            if menu.photo_url:
                path.write_text(menu.model_dump_json(indent=2) + "\n")
                print(f"{menu.slug:<34} Google Places photo", flush=True)
            else:
                missing.append(path)
    return missing


def save_image(menu: VendorMenu, raw: bytes) -> str:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((800, 800))
    out = ASSET_DIR / f"{menu.slug}.jpg"
    img.save(out, "JPEG", quality=82)
    return f"{ASSET_BASE_URL}/{out.name}"


def generate(missing: list[Path]) -> None:
    """Ask the Image Generator Agent for each picture, one at a time."""
    agent = Agent(name="haggle-photo-studio", seed=SEED, port=8208, mailbox=True)
    chat = Protocol(spec=chat_protocol_spec)
    storage = ExternalStorage(identity=Identity.from_seed(SEED, 0), storage_url="https://agentverse.ai/v1/storage")
    waiting: dict[str, asyncio.Future] = {}

    @chat.on_message(ChatMessage)
    async def on_reply(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
        for c in msg.content:
            if isinstance(c, ResourceContent) and waiting.get("current") and not waiting["current"].done():
                waiting["current"].set_result(str(c.resource_id))

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    @agent.on_event("startup")
    async def run(ctx: Context):
        for path in missing:
            menu = VendorMenu.model_validate_json(path.read_text())
            waiting["current"] = asyncio.get_running_loop().create_future()
            await ctx.send(IMAGE_AGENT, ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                                                    content=[TextContent(type="text", text=prompt_for(menu))]))
            try:
                asset_id = await asyncio.wait_for(waiting["current"], IMAGE_TIMEOUT_S)
                data = await asyncio.to_thread(storage.download, asset_id)
                raw = base64.b64decode(data["contents"]) if isinstance(data["contents"], str) else data["contents"]
                menu.photo_url = save_image(menu, raw)
                path.write_text(menu.model_dump_json(indent=2, exclude={"extracted_at"} if "fixtures" in str(path) else None) + "\n")
                print(f"{menu.slug:<34} generated -> {menu.photo_url}", flush=True)
            except Exception as e:  # never block: the vendor just has no photo
                print(f"{menu.slug:<34} no photo ({e.__class__.__name__}: {e})", flush=True)
        os._exit(0)

    agent.include(chat)
    agent.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    missing = asyncio.run(places_pass())
    print(f"{len(missing)} vendors need a generated photo", flush=True)
    if missing:
        ok = asyncio.run(register_mailbox(SEED, "haggle-photo-studio", "Haggle helper that requests vendor photos.",
                                          "# Haggle photo studio", [CHAT_DIGEST]))
        asyncio.set_event_loop(asyncio.new_event_loop())
        generate(missing) if ok else print("couldn't register the mailbox; no photos generated")
