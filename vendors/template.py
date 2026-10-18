"""make_vendor_agent(menu_json, seed) -> Agent. Claimed vs unclaimed behavior.

Owner: Person C

Menu JSON shape (see vendors/fixtures/*.json):
{
  "vendor_id": "...", "name": "...", "claimed": true,
  "rules": {"floor_pct": 12, "min_headcount": 10, "max_headcount": 80, "lead_time_h": 48,
            "radius_mi": 10, "blackout_dates": ["2026-10-31"], "auto_approve_usd": 1500,
            "delivery_fee_usd": 40},
  "packages": [{"name": "...", "per_head_usd": 15.0, "tags": ["vegetarian"], "allergens": ["nuts"]}]
}

Pricing and the floor-price counter rule are pure functions (price_rfq, counter_offer) so they can be
tested without running an agent. The floor is list total * (1 - floor_pct/100); a vendor never goes below it.
"""
from datetime import datetime, timedelta
from typing import Optional, Union

from uagents import Agent, Context, Protocol

from shared.models import RFQ, Accept, Counter, Decline, LineItem, Quote
from tools.menu_match import match_menu

PROTOCOL_NAME = "HaggleNegotiation"
PROTOCOL_VERSION = "0.1.0"


def negotiation_protocol() -> Protocol:
    return Protocol(name=PROTOCOL_NAME, version=PROTOCOL_VERSION)


def check_rules(menu: dict, rfq: RFQ, now: Optional[datetime] = None) -> Optional[str]:
    """Return a decline reason if the RFQ is outside the vendor's hard rules, else None."""
    r = menu.get("rules", {})
    now = now or datetime.now()
    if rfq.headcount < r.get("min_headcount", 0):
        return f"minimum headcount is {r['min_headcount']}"
    if r.get("max_headcount") and rfq.headcount > r["max_headcount"]:
        return f"maximum headcount is {r['max_headcount']}"
    if rfq.event_date in r.get("blackout_dates", []):
        return f"unavailable on {rfq.event_date}"
    if r.get("radius_mi") and rfq.distance_mi > r["radius_mi"]:
        return f"outside delivery radius ({r['radius_mi']} mi)"
    if r.get("lead_time_h"):
        try:
            event = datetime.fromisoformat(f"{rfq.event_date}T{rfq.event_time or '12:00'}")
            if event - now < timedelta(hours=r["lead_time_h"]):
                return f"needs {r['lead_time_h']}h notice"
        except ValueError:
            pass
    return None


def price_rfq(menu: dict, rfq: RFQ, now: Optional[datetime] = None) -> Union[Quote, Decline]:
    """List price for an RFQ: cheapest compliant package per dietary group + delivery fee."""
    vid = menu["vendor_id"]
    reason = check_rules(menu, rfq, now)
    if reason:
        return Decline(rfq_id=rfq.rfq_id, vendor_id=vid, reason=reason, trace=rfq.trace)

    items, missing = match_menu(menu["packages"], rfq.headcount, rfq.dietary, rfq.allergies)
    if items is None:
        return Decline(rfq_id=rfq.rfq_id, vendor_id=vid,
                       reason=f"no compliant option for: {', '.join(missing)}", trace=rfq.trace)

    fee = float(menu.get("rules", {}).get("delivery_fee_usd", 0))
    food = round(sum(i["subtotal_usd"] for i in items), 2)
    total = round(food + fee, 2)
    claimed = bool(menu.get("claimed"))
    return Quote(
        rfq_id=rfq.rfq_id, vendor_id=vid, vendor_name=menu["name"],
        total_usd=total, per_head_usd=round(total / rfq.headcount, 2), delivery_fee_usd=fee,
        items=[LineItem(**i) for i in items], claimed=claimed, estimate=not claimed,
        note="" if claimed else "Estimate · not confirmed", trace=rfq.trace,
    )


def floor_price(menu: dict, list_total: float) -> float:
    pct = float(menu.get("rules", {}).get("floor_pct", 0))
    return round(list_total * (1 - pct / 100), 2)


def counter_offer(menu: dict, list_quote: Quote, current_usd: float, counter: Counter) -> Union[Quote, Decline]:
    """Floor-price counter rule.

    - Unclaimed vendors never negotiate.
    - target >= floor: accept the target.
    - target < floor: concede half the gap to the floor in round 1, the floor itself from round 2 on.
    - Accepted/offered price above auto_approve_usd -> status "needs_approval" (owner must confirm).
    """
    vid = menu["vendor_id"]
    if not menu.get("claimed"):
        return Decline(rfq_id=counter.rfq_id, vendor_id=vid,
                       reason="unclaimed vendor: estimate only, no negotiation", trace=counter.trace)

    floor = floor_price(menu, list_quote.total_usd)
    if counter.target_usd >= floor:
        price, status = round(min(counter.target_usd, current_usd), 2), "accepted"
    elif current_usd <= floor:
        return Decline(rfq_id=counter.rfq_id, vendor_id=vid,
                       reason=f"best price is ${current_usd:.2f}", trace=counter.trace)
    else:
        price = floor if counter.round >= 2 else round(current_usd - (current_usd - floor) / 2, 2)
        status = "countered"

    auto = menu.get("rules", {}).get("auto_approve_usd")
    if auto and price > auto:
        status = "needs_approval"

    headcount = sum(i.headcount for i in list_quote.items) or 1
    return list_quote.copy(update={
        "total_usd": price, "per_head_usd": round(price / headcount, 2),
        "round": counter.round, "status": status, "note": "", "trace": counter.trace,
    })


def make_vendor_agent(menu_json: dict, seed: str, **agent_kwargs) -> Agent:
    """One agent per business. Pass port/endpoint or mailbox=True via agent_kwargs."""
    menu = menu_json
    agent = Agent(name=f"haggle-vendor-{menu['vendor_id']}", seed=seed, **agent_kwargs)
    proto = negotiation_protocol()

    def _state(ctx: Context, rfq_id: str) -> Optional[dict]:
        return ctx.storage.get(f"rfq:{rfq_id}")

    @proto.on_message(RFQ, replies={Quote, Decline})
    async def on_rfq(ctx: Context, sender: str, msg: RFQ):
        # Idempotent on (rfq_id, version): same request -> same quote, no re-pricing.
        st = _state(ctx, msg.rfq_id)
        if st and st["version"] == msg.version:
            reply = Quote.parse_raw(st["current"]) if st.get("current") else Decline.parse_raw(st["decline"])
        else:
            reply = price_rfq(menu, msg)
            if isinstance(reply, Quote):
                st = {"version": msg.version, "list": reply.json(), "current": reply.json()}
            else:
                st = {"version": msg.version, "decline": reply.json()}
            ctx.storage.set(f"rfq:{msg.rfq_id}", st)
        ctx.logger.info(f"RFQ {msg.rfq_id} v{msg.version} -> {type(reply).__name__} "
                        f"{getattr(reply, 'total_usd', getattr(reply, 'reason', ''))}")
        await ctx.send(sender, reply)

    @proto.on_message(Counter, replies={Quote, Decline})
    async def on_counter(ctx: Context, sender: str, msg: Counter):
        st = _state(ctx, msg.rfq_id)
        if not st or not st.get("current"):
            await ctx.send(sender, Decline(rfq_id=msg.rfq_id, vendor_id=menu["vendor_id"],
                                           reason="no open quote for this rfq", trace=msg.trace))
            return
        current = Quote.parse_raw(st["current"])
        if current.round >= msg.round:  # replayed counter: resend the same answer
            reply = current
        else:
            reply = counter_offer(menu, Quote.parse_raw(st["list"]), current.total_usd, msg)
            if isinstance(reply, Quote):
                st["current"] = reply.json()
                ctx.storage.set(f"rfq:{msg.rfq_id}", st)
        ctx.logger.info(f"Counter {msg.rfq_id} r{msg.round} target ${msg.target_usd} -> "
                        f"{getattr(reply, 'status', 'declined')} {getattr(reply, 'total_usd', '')}")
        await ctx.send(sender, reply)

    @proto.on_message(Accept)
    async def on_accept(ctx: Context, sender: str, msg: Accept):
        ctx.logger.info(f"Accepted {msg.rfq_id} at ${msg.final_usd}")

    @proto.on_message(Decline)
    async def on_user_decline(ctx: Context, sender: str, msg: Decline):
        ctx.logger.info(f"User passed on {msg.rfq_id}: {msg.reason}")

    agent.include(proto, publish_manifest=True)
    return agent
