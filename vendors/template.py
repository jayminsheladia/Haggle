"""make_vendor_agent(menu, seed) -> Agent. Claimed vs unclaimed behavior.

One agent per business, all built from this template with different VendorMenu data.

UNCLAIMED (built from the public menu): answers RFQs with estimates only, never negotiates, never
accepts, never implies the business agreed. Its rules are Haggle's defaults, used only to filter.
CLAIMED (owner verified, set their own rules): quotes and negotiates on its own inside the owner's
rules; anything outside them (or above the auto-approve limit) goes to the owner for approval, and
the Negotiator gets Quote(status="needs_approval") until the owner decides.

All numbers come from vendors/pricing.py. The LLM only answers chat questions from the menu data and
reads owner replies when the regex can't.

Owner: Person C
"""

import json
import logging
import os
import random
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, TextContent, chat_protocol_spec

from integrations.notify import notify_owner
from shared import observability
from shared.cards import card_message, owner_rules_form, text_message, usd
from shared.llm import chat_json, chat_text
from shared.menu_schema import OwnerRules, VendorMenu
from shared.models import RFQ, Accept, Counter, Decline, OwnerReply, OwnerReplyResult, Quote
from shared.selection import parse_selection
from vendors.pricing import SMALL_ORDER_USD, UNLABELED, check_rules, counter_response, floor_price, price_for

PROTOCOL_NAME = "HaggleNegotiation"
PROTOCOL_VERSION = "0.1.0"
# Digest of the HaggleNegotiation protocol as built below. Discovery filters Agentverse search by it;
# vendors/vendor_host.py checks it on startup, so update it if the messages or handlers change.
PROTOCOL_DIGEST = "proto:ddbe385f541a21e0d340705c876eb1a27888090f2ca8fc0c8d535e5851f201f7"

DATA_DIR = Path(__file__).parent / "data"
README_DIR = Path(__file__).parent / "readme"

APPROVAL_TTL = timedelta(minutes=int(os.getenv("APPROVAL_TTL_MIN", "15")))
CLAIM_CODE_TTL = timedelta(minutes=10)
QUOTE_EXPIRES_S = 24 * 3600
OWNER_FIXABLE = ("needs ", "maximum headcount")  # rule violations the owner may waive

UNCLAIMED_NOTE = "Estimate from public menu — not confirmed by the business"
NO_PRICES = "No public menu or prices yet — Haggle can email them for a quote"
PRICE_ON_REQUEST = "No public prices — request a quote from the business"
MENU_PREVIEW_ITEMS = 8
UNCLAIMED_COUNTER = "unclaimed vendor — Haggle will request a quote from the business by email"
UNCLAIMED_ACCEPT = "unclaimed vendors can't be booked — Haggle will request a quote from the business by email"

MenuChanged = Callable[[VendorMenu], Awaitable[None]]


def negotiation_protocol() -> Protocol:
    return Protocol(name=PROTOCOL_NAME, version=PROTOCOL_VERSION)


# ---- Agentverse profile -----------------------------------------------------

def description_for(menu: VendorMenu) -> str:
    if menu.claimed:
        return f"{menu.display_name}: catering in Los Angeles. Quotes and negotiates through Haggle."
    return f"{menu.display_name}: catering in Los Angeles. Estimates only until the business claims this agent."


def readme_for(menu: VendorMenu) -> str:
    diets = sorted({t for i in menu.items for t in i.dietary_tags if not t.startswith("contains")})
    sample = [i for i in menu.priced_items[:6]] or menu.items[:6]
    lines = [
        f"# {menu.display_name}",
        "",
        "![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)",
        "![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)",
        "",
        f"Haggle vendor agent for **{menu.name}**{f', {menu.address}' if menu.address else ''}. "
        "It answers catering quote requests from Haggle's Negotiator over the HaggleNegotiation protocol, "
        "and you can chat with it about the menu.",
        "",
        f"- **Status:** {'claimed by the business ✓ — negotiates within the owner’s rules' if menu.claimed else 'unclaimed — estimates only, built from the public menu'}",
        f"- **Area served:** within {menu.rules.delivery_radius_mi:g} miles, Los Angeles",
        f"- **Group size:** {menu.rules.min_headcount}–{menu.rules.max_headcount} guests, {menu.rules.lead_time_hours}h notice",
        f"- **Dietary options:** {', '.join(diets) if diets else 'not listed on the public menu'}",
    ]
    if sample:
        lines += ["", "## Sample items", ""]
        lines += [f"- {i.name}" + (f" — {usd(i.price_per_person)}/person" if i.price_per_person else "") for i in sample]
    if not menu.claimed:
        lines += ["", "Are you the owner? Message this agent **claim** to verify and set your own rules."]
    lines += ["", "Keywords: catering, event catering, group catering, quotes, Los Angeles, Haggle", ""]
    return "\n".join(lines)


def write_readme(menu: VendorMenu) -> Path:
    README_DIR.mkdir(exist_ok=True)
    path = README_DIR / f"{menu.slug}.md"
    path.write_text(readme_for(menu))
    return path


def save_menu(menu: VendorMenu) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / f"{menu.slug}.json").write_text(menu.model_dump_json(indent=2) + "\n")


# ---- Owner replies ----------------------------------------------------------

def parse_owner_decision(text: str) -> Optional[tuple[str, Optional[float]]]:
    """YES / NO / a counter price, by regex. None if the text is none of those."""
    t = text.strip().lower()
    m = re.search(r"\$?\s*(\d[\d,]*(?:\.\d{1,2})?)", t)
    if m and not re.fullmatch(r"(yes|y|no|n)\b.*", t):
        return "counter", float(m.group(1).replace(",", ""))
    if re.fullmatch(r"(yes|y|ok|okay|approve[d]?|accept(ed)?|sure|deal)[.! ]*", t):
        return "approve", None
    if re.fullmatch(r"(no|n|nope|decline[d]?|reject(ed)?|pass)[.! ]*", t):
        return "decline", None
    return None


async def llm_owner_decision(text: str) -> Optional[tuple[str, Optional[float]]]:
    """Fallback when the regex can't read the owner's reply. The LLM only reads the text."""
    try:
        data = await chat_json(
            'Classify a business owner\'s reply to a price approval request. Return ONLY JSON: '
            '{"decision": "approve" | "decline" | "counter" | "unclear", "price": number or null}. '
            '"price" is the counter price the owner wrote, if any.',
            text, max_tokens=60, reasoning_effort="minimal")
    except Exception:
        return None
    if not data or data.get("decision") not in ("approve", "decline", "counter"):
        return None
    price = data.get("price")
    if data["decision"] == "counter" and not isinstance(price, (int, float)):
        return None
    return data["decision"], float(price) if price is not None else None


def haggle_note(action: str, price: float, headcount: int, small: bool = False) -> str:
    """What the vendor says back (varied wording; every number comes from code)."""
    p = usd(price)
    lines = {
        "accept": [f"Deal. {p} works for us.", f"You've got it: {p}.", f"Okay, {p} it is. Looking forward to it."],
        "counter": [f"That's a bit too low for us. We can come down to {p}.",
                    f"We can't go that far, but we can do {p} for {headcount} guests.",
                    f"Meet us in the middle? {p}."],
        "best": [f"{p} is really the best we can do on this order.",
                 f"We've sharpened our pencil: {p} is our final price.",
                 f"Lowest we can go is {p}. Ingredients and staff for {headcount} add up."],
        "no_room": ([f"For an order this size we can't take anything off, sorry. It stays at {p}.",
                     f"Small orders are already at our best rate, so {p} is firm."] if small else
                    [f"We're already at our lowest for this order: {p}.", f"Sorry, {p} is as low as we go."]),
        "owner": [f"That's below what I can agree to on my own. Let me check {p} with the owner.",
                  f"I'll have to ask the owner about {p}. Give me a moment."],
    }[action]
    return lines[(int(price * 100) + headcount) % len(lines)]  # stable variety per offer


def _strip_mention(text: str) -> str:
    return re.sub(r"^(@[\w.-]+[\s,:]*)+", "", text).strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- The agent --------------------------------------------------------------

def make_vendor_agent(menu: VendorMenu | dict, seed: str, on_menu_change: Optional[MenuChanged] = None,
                      **agent_kwargs) -> Agent:
    """One agent per business. Pass port/endpoint or mailbox=True via agent_kwargs."""
    if isinstance(menu, dict):
        menu = VendorMenu.model_validate(menu)
    log = logging.getLogger(f"vendor.{menu.slug}")
    agent = Agent(name=menu.slug, seed=seed, readme_path=str(write_readme(menu)),
                  description=description_for(menu), avatar_url=menu.photo_url or None, **agent_kwargs)
    neg = negotiation_protocol()
    chat = Protocol(spec=chat_protocol_spec)

    def decided(ctx: Context, rfq_id: str, action: str, price: Optional[float] = None, reason: str = "") -> None:
        ctx.logger.info(f"[{rfq_id}] {action}" + (f" {usd(price)}" if price is not None else "")
                        + (f" ({reason})" if reason else ""))

    def key(rfq_id: str, version: int) -> str:
        return f"rfq:{rfq_id}:v{version}"

    def build_quote(rfq: RFQ, price: float, lines, ok: bool, conflicts: list[str], status: str = "quoted",
                    note: Optional[str] = None) -> Quote:
        if note is None:
            note = "Thanks for the request!" if menu.claimed else UNCLAIMED_NOTE
        preview = [f"{i.name} — {usd(i.price_per_person)}/person" if i.price_per_person is not None
                   else f"{i.name}" + (f" — {i.description}" if i.description else "")
                   for i in menu.items[:MENU_PREVIEW_ITEMS]]
        return Quote(
            rfq_id=rfq.rfq_id, vendor_id=menu.slug, vendor_name=menu.name, total_usd=price,
            per_head_usd=round(price / max(rfq.headcount, 1), 2), delivery_fee_usd=menu.rules.delivery_fee_usd,
            items=lines, claimed=menu.claimed, estimate=not menu.claimed, status=status, note=note,
            version=rfq.version, dietary_ok=ok, conflicts=conflicts,
            expires_in_s=QUOTE_EXPIRES_S if menu.claimed else 0, menu_preview=preview,
            menu_url=menu.source_url, photo_url=menu.photo_url, trace=rfq.trace,
        )

    def decline(rfq_id: str, version: int, reason: str, trace: str = "") -> Decline:
        return Decline(rfq_id=rfq_id, vendor_id=menu.slug, reason=reason, version=version, trace=trace)

    # ---- approvals ----

    async def create_approval(ctx: Context, kind: str, rfq: RFQ, summary: str, proposed: float,
                              negotiator: str) -> dict:
        approvals = ctx.storage.get("approvals") or {}
        aid = next(a for a in (f"A{random.randint(1000, 9999)}" for _ in range(100)) if a not in approvals)
        deadline = _now() + APPROVAL_TTL
        approval = {"approval_id": aid, "kind": kind, "rfq_id": rfq.rfq_id, "version": rfq.version,
                    "summary": summary, "proposed_usd": proposed, "deadline_iso": deadline.isoformat(),
                    "status": "pending", "negotiator": negotiator, "trace": rfq.trace}
        approvals[aid] = approval
        ctx.storage.set("approvals", approvals)
        channel = await notify_owner(
            menu, f"Haggle request {aid} for {menu.name}: {summary}. Reply YES, NO, or a counter price "
                  f"(e.g. \"counter {aid} 750\") within {int(APPROVAL_TTL.total_seconds() // 60)} minutes.",
            subject=f"Haggle approval needed ({aid})")
        ctx.logger.info(f"[{rfq.rfq_id}] approval {aid} created ({kind}, {usd(proposed)}), owner notified via {channel}")
        return approval

    async def apply_decision(ctx: Context, aid: str, action: str, price: Optional[float]) -> str:
        approvals = ctx.storage.get("approvals") or {}
        a = approvals.get(aid.upper())
        if not a:
            return f"No request {aid}."
        if a["status"] != "pending":
            return f"Request {aid} is already {a['status']}."
        with observability.span("vendor.approval", f"{menu.slug} {aid} {action}", a["trace"], rfq_id=a["rfq_id"]):
            k = key(a["rfq_id"], a["version"])
            st = ctx.storage.get(k)
            current = Quote.parse_raw(st["current"])
            heads = max(RFQ.parse_raw(st["rfq"]).headcount, 1)
            if action == "decline":
                reply = decline(a["rfq_id"], a["version"], "declined by the owner", a["trace"])
                close_approval(st, a, reply)
            else:
                final = round(price if action == "counter" else a["proposed_usd"], 2)
                status = "countered" if action == "counter" else ("accepted" if a["kind"] == "counter" else "quoted")
                reply = current.copy(update={
                    "total_usd": final, "per_head_usd": round(final / heads, 2), "status": status,
                    "note": "Counter offer from the owner" if action == "counter" else "Approved by the owner",
                })
                st["current"] = reply.json()
            ctx.storage.set(k, st)
            a["status"] = {"approve": "approved", "decline": "declined", "counter": "countered"}[action]
            ctx.storage.set("approvals", approvals)
            await ctx.send(a["negotiator"], reply)
            decided(ctx, a["rfq_id"], f"owner {a['status']} {aid}", getattr(reply, "total_usd", None))
        return f"Done: request {aid} {a['status']}" + (f" at {usd(reply.total_usd)}." if action != "decline" else ".")

    def close_approval(st: dict, a: dict, reply: Decline) -> None:
        """Owner said no (or never answered): a declined counter leaves the earlier price standing;
        a declined request ends it."""
        if a["kind"] == "counter" and st.get("current"):
            current = Quote.parse_raw(st["current"])
            st["current"] = current.copy(update={"status": "countered" if current.round > 1 else "quoted",
                                                 "note": reply.reason}).json()
        else:
            st.pop("current", None)
            st["decline"] = reply.json()

    @agent.on_interval(period=30)
    async def expire_approvals(ctx: Context):
        approvals = ctx.storage.get("approvals") or {}
        changed = False
        for a in approvals.values():
            if a["status"] == "pending" and datetime.fromisoformat(a["deadline_iso"]) < _now():
                a["status"] = "expired"
                changed = True
                reply = decline(a["rfq_id"], a["version"], "owner didn't respond in time", a["trace"])
                k = key(a["rfq_id"], a["version"])
                st = ctx.storage.get(k)
                if st:
                    close_approval(st, a, reply)
                    ctx.storage.set(k, st)
                await ctx.send(a["negotiator"], reply)
                decided(ctx, a["rfq_id"], f"approval {a['approval_id']} expired")
        if changed:
            ctx.storage.set("approvals", approvals)

    # ---- negotiation ----

    @neg.on_message(RFQ, replies={Quote, Decline})
    async def on_rfq(ctx: Context, sender: str, rfq: RFQ):
        with observability.span("vendor.rfq", f"{menu.slug} RFQ {rfq.rfq_id}", rfq.trace, vendor_id=menu.slug):
            k = key(rfq.rfq_id, rfq.version)
            st = ctx.storage.get(k)
            if st:  # duplicate RFQ: same answer, no re-pricing
                reply = Quote.parse_raw(st["current"]) if st.get("current") else Decline.parse_raw(st["decline"])
                decided(ctx, rfq.rfq_id, "duplicate RFQ, resent previous answer")
                await ctx.send(sender, reply)
                return

            violations = check_rules(menu.rules, rfq, vendor_lat=menu.lat, vendor_lng=menu.lng)
            day = {r: n for r, n in (ctx.storage.get("booked") or {}).get(rfq.event_date, {}).items() if r != rfq.rfq_id}
            if menu.claimed and sum(day.values()) + rfq.headcount > menu.rules.max_headcount:
                violations.append(f"fully booked on {rfq.event_date}")  # don't quote what Accept would refuse
            # Unclaimed public menus rarely label diets: quote anyway and flag it; allergies stay hard filters.
            price, lines, ok, conflicts = price_for(menu, rfq, unlabeled_ok=not menu.claimed)
            hard_conflicts = [c for c in conflicts if UNLABELED not in c]
            st = {"rfq": rfq.json(), "negotiator": sender}
            if not menu.priced_items or menu.extraction_status != "extracted":
                # No (or not enough) public prices: still show the caterer, price on request, so the user can
                # have Haggle email them for a quote. Never priced by us.
                reply = build_quote(rfq, 0.0, [], False, ["no public prices"], status="price_on_request",
                                    note=PRICE_ON_REQUEST if menu.items else NO_PRICES)
            elif not menu.claimed:
                if violations or hard_conflicts:
                    reply = decline(rfq.rfq_id, rfq.version, "; ".join(violations + hard_conflicts), rfq.trace)
                else:
                    reply = build_quote(rfq, price, lines, ok, conflicts)
            else:
                fixable = [v for v in violations if v.startswith(OWNER_FIXABLE)]
                hard = [v for v in violations if v not in fixable]
                if hard or hard_conflicts:
                    reply = decline(rfq.rfq_id, rfq.version, "; ".join(hard + hard_conflicts), rfq.trace)
                elif fixable or price > menu.rules.auto_approve_limit_usd:
                    why = ", ".join(fixable) or f"above your auto-approve limit of {usd(menu.rules.auto_approve_limit_usd)}"
                    a = await create_approval(
                        ctx, "rfq", rfq, f"{rfq.headcount} guests on {rfq.event_date} {rfq.event_time} for "
                                         f"{usd(price)} ({why})", price, sender)
                    reply = build_quote(rfq, price, lines, ok, conflicts, status="needs_approval",
                                        note=f"Waiting on the owner ({why}), request {a['approval_id']}")
                else:
                    reply = build_quote(rfq, price, lines, ok, conflicts)
            if isinstance(reply, Quote):
                st["list"] = st["current"] = reply.json()
                decided(ctx, rfq.rfq_id, f"quote ({reply.status})", reply.total_usd,
                        "estimate" if reply.estimate else "")
            else:
                st["decline"] = reply.json()
                decided(ctx, rfq.rfq_id, "decline", reason=reply.reason)
            ctx.storage.set(k, st)
            await ctx.send(sender, reply)

    @neg.on_message(Counter, replies={Quote, Decline})
    async def on_counter(ctx: Context, sender: str, c: Counter):
        with observability.span("vendor.counter", f"{menu.slug} counter r{c.round}", c.trace, vendor_id=menu.slug):
            k = key(c.rfq_id, c.version)
            st = ctx.storage.get(k)
            if not st or not st.get("current"):
                reply = decline(c.rfq_id, c.version, "no open quote for this rfq", c.trace)
                decided(ctx, c.rfq_id, "decline counter", reason=reply.reason)
                await ctx.send(sender, reply)
                return
            if not menu.claimed:
                decided(ctx, c.rfq_id, "decline counter", reason="unclaimed")
                await ctx.send(sender, decline(c.rfq_id, c.version, UNCLAIMED_COUNTER, c.trace))
                return
            current = Quote.parse_raw(st["current"])
            if current.round >= c.round or current.status == "needs_approval":
                decided(ctx, c.rfq_id, "counter replay or waiting on owner, resent current quote", current.total_usd)
                await ctx.send(sender, current)
                return

            rfq = RFQ.parse_raw(st["rfq"])
            floor = floor_price(Quote.parse_raw(st["list"]).total_usd, menu.rules)
            action, value = counter_response(current.total_usd, c.target_usd, floor, c.round)
            if action == "no_room":  # small order (or already at the floor): the price stands
                reply = current.copy(update={"round": c.round, "status": "countered", "note": haggle_note(
                    "no_room", current.total_usd, rfq.headcount, small=current.total_usd < SMALL_ORDER_USD)})
            elif action == "decline":
                a = await create_approval(
                    ctx, "counter", rfq, f"customer offers {usd(c.target_usd)} for {rfq.headcount} guests on "
                                         f"{rfq.event_date} (your floor is {usd(floor)}, current quote "
                                         f"{usd(current.total_usd)})", c.target_usd, sender)
                reply = current.copy(update={"round": c.round, "status": "needs_approval",
                                             "note": haggle_note("owner", c.target_usd, rfq.headcount)})
            elif value > menu.rules.auto_approve_limit_usd:
                a = await create_approval(
                    ctx, "counter", rfq, f"{usd(value)} for {rfq.headcount} guests on {rfq.event_date} is above your "
                                         f"auto-approve limit", value, sender)
                reply = current.copy(update={"round": c.round, "status": "needs_approval",
                                             "note": f"Owner approval needed for {usd(value)} ({a['approval_id']})"})
            else:
                reply = current.copy(update={
                    "total_usd": value, "per_head_usd": round(value / max(rfq.headcount, 1), 2), "round": c.round,
                    "status": "accepted" if action == "accept" else "countered",
                    "note": haggle_note(action, value, rfq.headcount),
                })
            st["current"] = reply.json()
            ctx.storage.set(k, st)
            decided(ctx, c.rfq_id, f"counter r{c.round} target {usd(c.target_usd)} floor {usd(floor)} -> {reply.status}",
                    reply.total_usd)
            await ctx.send(sender, reply)

    @neg.on_message(Accept, replies={Quote, Decline})
    async def on_accept(ctx: Context, sender: str, msg: Accept):
        if not menu.claimed:
            decided(ctx, msg.rfq_id, "refuse booking", reason="unclaimed")
            await ctx.send(sender, decline(msg.rfq_id, msg.version, UNCLAIMED_ACCEPT, msg.trace))
            return
        st = ctx.storage.get(key(msg.rfq_id, msg.version))
        if not st or not st.get("current"):
            await ctx.send(sender, decline(msg.rfq_id, msg.version, "no open quote for this rfq", msg.trace))
            return
        rfq, current = RFQ.parse_raw(st["rfq"]), Quote.parse_raw(st["current"])
        booked = ctx.storage.get("booked") or {}
        day = booked.setdefault(rfq.event_date, {})
        if msg.rfq_id not in day:  # idempotent: a repeated Accept doesn't double-book
            if sum(day.values()) + rfq.headcount > menu.rules.max_headcount:
                reason = f"fully booked on {rfq.event_date}"
                decided(ctx, msg.rfq_id, "refuse booking", reason=reason)
                await ctx.send(sender, decline(msg.rfq_id, msg.version, reason, msg.trace))
                return
            day[msg.rfq_id] = rfq.headcount
            ctx.storage.set("booked", booked)
        reply = current.copy(update={"status": "accepted", "note": f"Booked for {rfq.event_date} {rfq.event_time}"})
        st["current"] = reply.json()
        ctx.storage.set(key(msg.rfq_id, msg.version), st)
        decided(ctx, msg.rfq_id, "booked", reply.total_usd, msg.deposit_txn)
        await ctx.send(sender, reply)

    @neg.on_message(Decline)
    async def on_user_decline(ctx: Context, sender: str, msg: Decline):
        decided(ctx, msg.rfq_id, "customer passed", reason=msg.reason)

    # ---- owner reply from the Photon bridge ----

    @agent.on_rest_post("/owner-reply", OwnerReply, OwnerReplyResult)
    async def on_owner_reply(ctx: Context, req: OwnerReply) -> OwnerReplyResult:
        if req.vendor_id != menu.slug:
            return OwnerReplyResult(ok=False, detail="wrong vendor")
        decision = parse_owner_decision(req.text) or await llm_owner_decision(req.text)
        if not decision:
            return OwnerReplyResult(ok=False, detail="couldn't read the reply; send YES, NO or a price")
        detail = await apply_decision(ctx, req.approval_id, *decision)
        return OwnerReplyResult(ok=detail.startswith("Done"), detail=detail)

    # ---- chat: customers and the owner ----

    async def say(ctx: Context, to: str, text: str) -> None:
        await ctx.send(to, text_message(text))

    async def menu_changed() -> None:
        save_menu(menu)
        write_readme(menu)
        if on_menu_change:
            await on_menu_change(menu)

    async def answer_customer(ctx: Context, sender: str, question: str) -> None:
        facts = {
            "name": menu.name, "claimed": menu.claimed, "address": menu.address,
            "items": [{"name": i.name, "price_per_person_usd": i.price_per_person, "serves": i.serves,
                       "dietary": i.dietary_tags} for i in menu.items[:60]],
            "min_guests": menu.rules.min_headcount, "max_guests": menu.rules.max_headcount,
            "notice_hours": menu.rules.lead_time_hours, "delivery_radius_mi": menu.rules.delivery_radius_mi,
        }
        system = ("You answer customer questions for a catering business using ONLY the JSON data given. "
                  "Quote prices exactly as given; never invent items, prices or dietary options. If the data "
                  "doesn't say, say you don't know and suggest getting a quote through @haggle. "
                  + ("" if menu.claimed else "These are estimates from the public menu, not confirmed by the business. ")
                  + "Keep it to 2-4 sentences.")
        try:
            reply = await chat_text(system, f"DATA: {json.dumps(facts)}\n\nQUESTION: {question}")
        except Exception:
            ctx.logger.exception("customer answer failed")
            reply = ""
        if not reply:
            priced = ", ".join(f"{i.name} ({usd(i.price_per_person)}/person)" for i in menu.priced_items[:5])
            reply = f"{menu.display_name}. " + (f"Some items: {priced}." if priced else "No public menu prices yet.")
        await say(ctx, sender, reply)

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=_now(), acknowledged_msg_id=msg.msg_id))
        text = _strip_mention("".join(c.text for c in msg.content if isinstance(c, TextContent)))
        if not text:
            return
        lower = text.lower().strip(" .!?")
        is_owner = menu.claimed and menu.owner_contact == sender
        sel = parse_selection(text) if text.lstrip().startswith("{") else {}

        if sel.get("action") == "save_rules":
            if not (is_owner or ctx.storage.get("claim_verified") == sender):
                await say(ctx, sender, "Only the verified owner can change these rules. Send \"claim\" first.")
                return
            current = menu.rules.model_dump()
            for name, value in sel.items():
                if name not in current or value in (None, ""):
                    continue
                current[name] = ([d.strip() for d in str(value).split(",") if d.strip()]
                                 if name == "blackout_dates" else value)
            try:
                rules = OwnerRules.model_validate(current)
            except ValidationError as e:
                await say(ctx, sender, f"Some values didn't look right ({e.error_count()} problems). Please try again.")
                await ctx.send(sender, card_message("Your Haggle rules", "form", owner_rules_form(menu.slug, current)))
                return
            first_claim = not menu.claimed
            menu.rules, menu.claimed, menu.owner_contact = rules, True, sender
            ctx.storage.set("claim_verified", None)
            await menu_changed()
            ctx.logger.info(f"rules saved by owner {sender[:16]} (first claim: {first_claim}): {rules.model_dump()}")
            await say(ctx, sender, f"{menu.display_name}. Saved: up to {rules.floor_discount_pct:g}% off list, "
                                   f"{rules.min_headcount}-{rules.max_headcount} guests, {rules.lead_time_hours}h notice, "
                                   f"{rules.delivery_radius_mi:g} mi delivery, approval above {usd(rules.auto_approve_limit_usd)}. "
                                   "I'll negotiate within these and ask you about anything outside them.")
            return

        if lower == "claim":
            if menu.claimed and not is_owner:
                await say(ctx, sender, f"{menu.name} has already been claimed by its owner.")
                return
            code = f"{random.randint(0, 999999):06d}"
            ctx.storage.set("claim", {"code": code, "sender": sender,
                                      "expires": (_now() + CLAIM_CODE_TTL).isoformat()})
            channel = await notify_owner(menu, f"Your Haggle verification code for {menu.name} is {code}.",
                                         subject="Haggle verification code")
            ctx.logger.info(f"claim started by {sender[:16]}, code sent via {channel}")
            await say(ctx, sender, f"To verify you own {menu.name}, I sent a 6-digit code to the phone or email on "
                                   "its business listing (during testing it goes to the team's test contact). "
                                   "Reply with the code.")
            return

        if re.fullmatch(r"\d{6}", lower):
            claim = ctx.storage.get("claim")
            if (not claim or claim["sender"] != sender or claim["code"] != lower
                    or datetime.fromisoformat(claim["expires"]) < _now()):
                await say(ctx, sender, "That code doesn't match or has expired. Send \"claim\" to get a new one.")
                return
            ctx.storage.set("claim", None)
            ctx.storage.set("claim_verified", sender)
            await ctx.send(sender, card_message(
                f"Verified. Now set the rules I'll negotiate {menu.name}'s catering within.", "form",
                owner_rules_form(menu.slug, menu.rules.model_dump())))
            return

        if is_owner:
            approvals = ctx.storage.get("approvals") or {}
            pending = [a for a in approvals.values() if a["status"] == "pending"]
            if lower in ("show pending requests", "show pending", "pending", "pending requests"):
                await say(ctx, sender, "\n".join(f"{a['approval_id']}: {a['summary']}" for a in pending)
                          or "No pending requests.")
                return
            m = re.fullmatch(r"(approve|decline|counter)\s+(a\d{4})(?:\s+\$?([\d,]+(?:\.\d{1,2})?))?", lower)
            if m:
                action, aid, price = m.group(1), m.group(2).upper(), m.group(3)
                if action == "counter" and not price:
                    await say(ctx, sender, f"Add your price, e.g. \"counter {aid} 750\".")
                    return
                await say(ctx, sender, await apply_decision(ctx, aid, action,
                                                            float(price.replace(",", "")) if price else None))
                return
            if "update rules" in lower or lower == "rules":
                await ctx.send(sender, card_message("Your current rules", "form",
                                                    owner_rules_form(menu.slug, menu.rules.model_dump())))
                return
            if len(pending) == 1:  # a bare YES / NO / price answers the only pending request
                decision = parse_owner_decision(text)
                if decision:
                    await say(ctx, sender, await apply_decision(ctx, pending[0]["approval_id"], *decision))
                    return

        await answer_customer(ctx, sender, text)

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    agent.include(neg, publish_manifest=True)
    agent.include(chat, publish_manifest=True)
    return agent
