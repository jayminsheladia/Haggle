"""Negotiator: parallel RFQs (10 s timeout), counter rounds (max 2), menu matching, ranking.

Owner: Person C

Concierge -> NegotiationRequest -> Negotiator sends RFQ to every vendor at once, waits up to 10 s,
ranks the quotes and replies NegotiationResult. Concierge -> CounterRequest -> one counter round with
one vendor, reply CounterResult. All prices and ranking are computed here in code.

Run:  python -m agents.negotiator
"""
import asyncio
import os
from typing import Dict, List, Optional

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from shared import observability
from shared.models import (
    RFQ, Counter, CounterRequest, CounterResult, Decline, NegotiationRequest,
    NegotiationResult, Quote, RankedQuote,
)

QUOTE_TIMEOUT_S = 10
MAX_ROUNDS = 3  # vendor rounds per quote: open, push, then "check with the owner"
EXCHANGES_PER_TAP = 2  # one "Negotiate lower" runs up to two back-and-forths
DEFAULT_DISCOUNT = 0.10  # ask for 10% off when there is no budget gap to close
# Opening ask without a budget gap, by order size: bigger orders ask for more.
ASK_BY_SIZE = ((800, 0.06), (1500, 0.10))  # (order total below, discount asked); above: 15%
BIG_ORDER_ASK = 0.15


def rank_quotes(quotes: List[Quote], budget_usd: float, ratings: Dict[str, Optional[float]]) -> List[RankedQuote]:
    """Confirmed (claimed) before estimates, in-budget before over-budget, then by score.

    score = 0.8 * price score (budget / total, capped at 1; cheapest-relative without a budget)
          + 0.2 * rating / 5 (neutral 0.5 when unknown)
    """
    if not quotes:
        return []
    cheapest = min(q.total_usd for q in quotes)
    ranked = []
    for q in quotes:
        ref = budget_usd if budget_usd > 0 else cheapest
        price_score = min(1.0, ref / q.total_usd) if q.total_usd else 0.0
        rating = ratings.get(q.vendor_id)
        score = round(0.8 * price_score + 0.2 * ((rating / 5) if rating else 0.5), 4)
        fits = budget_usd <= 0 or q.total_usd <= budget_usd
        ranked.append((q.estimate, not fits, -score, q.total_usd, q, fits, score))
    ranked.sort(key=lambda t: t[:4])
    return [RankedQuote(rank=i + 1, quote=t[4], fits_budget=t[5], score=t[6]) for i, t in enumerate(ranked)]


def _usd(x: float) -> str:
    return f"${x:,.2f}"


def pick_target(quote: Quote, budget_usd: float) -> float:
    """Opening counter target: the budget if the quote is over it, else a discount that grows with order size."""
    if budget_usd > 0 and quote.total_usd > budget_usd:
        return round(budget_usd, 2)
    pct = next((p for below, p in ASK_BY_SIZE if quote.total_usd < below), BIG_ORDER_ASK)
    return round(quote.total_usd * (1 - pct), 2)


def opening_ask(quote: Quote, budget_usd: float, headcount: int) -> tuple[float, str]:
    """(target, what Haggle says). Wording only; the target comes from pick_target."""
    target = pick_target(quote, budget_usd)
    if budget_usd > 0 and quote.total_usd > budget_usd:
        return target, (f"Thanks for the quote of {_usd(quote.total_usd)}. Our customer's budget is {_usd(budget_usd)} "
                        f"for {headcount} guests. Could you do {_usd(target)}?")
    return target, (f"Thanks for the quote of {_usd(quote.total_usd)}. We're comparing a few caterers for this "
                    f"{headcount}-guest event. How close can you get to {_usd(target)}?")


def push_ask(offer: float, last_target: float, budget_usd: float) -> tuple[float, str]:
    """Haggle's second ask: halfway between its last ask and the vendor's offer."""
    target = round((offer + last_target) / 2, 2)
    if budget_usd > 0 and offer > budget_usd:
        return target, (f"Appreciate it. {_usd(offer)} is still over the {_usd(budget_usd)} budget. "
                        f"Can you meet us at {_usd(target)}?")
    return target, f"Thanks, that's closer. If you can do {_usd(target)}, we'll recommend you to the customer."


def owner_ask(offer: float, budget_usd: float) -> tuple[float, str]:
    """Haggle's last ask, after the vendor's best price: go to the owner."""
    target = round(budget_usd, 2) if 0 < budget_usd < offer else round(offer * 0.97, 2)
    return target, f"Understood. Is there any way to get to {_usd(target)}? Could you check with the owner?"


def _ranked_from_state(st: dict) -> List[RankedQuote]:
    quotes = [Quote.parse_raw(q) for q in st["quotes"].values()]
    return rank_quotes(quotes, st["rfq"]["budget_usd"], {v: r["rating"] for v, r in st["vendors"].items()})


def make_negotiator(**agent_kwargs) -> Agent:
    agent = Agent(name="haggle-negotiator", seed=os.getenv("NEGOTIATOR_SEED") or "haggle-negotiator-dev-seed",
                  **agent_kwargs)
    proto = Protocol(name="HaggleNegotiator", version="0.1.0")

    @agent.on_event("startup")
    async def startup(ctx: Context):
        ctx.logger.info(f"Negotiator address: {agent.address}")

    @proto.on_message(NegotiationRequest, replies=NegotiationResult)
    async def on_request(ctx: Context, sender: str, msg: NegotiationRequest):
        rfq = msg.rfq
        observability.tag(msg.trace, rfq.rfq_id)
        key = f"neg:{rfq.rfq_id}:v{rfq.version}"

        st = ctx.storage.get(key)
        if st is None:  # idempotent on rfq_id + version: a replay gets the stored result
            st = {"rfq": rfq.dict(), "sender": sender, "quotes": {}, "declined": {},
                  "vendors": {v.vendor_id: v.dict() for v in msg.vendors}}

            async def ask(v):
                vendor_rfq = rfq.copy(update={"distance_mi": v.distance_mi, "trace": msg.trace})
                reply, status = await ctx.send_and_receive(
                    v.address, vendor_rfq, response_type={Quote, Decline}, timeout=QUOTE_TIMEOUT_S)
                return v, reply, status

            results = await asyncio.gather(*(ask(v) for v in msg.vendors))
            for v, reply, status in results:
                if isinstance(reply, Quote):
                    # Key and label quotes by the vendor_id the Concierge sent, whatever the vendor calls itself.
                    st["quotes"][v.vendor_id] = reply.copy(update={"vendor_id": v.vendor_id}).json()
                elif isinstance(reply, Decline):
                    st["declined"][v.vendor_id] = reply.reason
                else:
                    st["declined"][v.vendor_id] = f"no reply in {QUOTE_TIMEOUT_S}s"
            ctx.storage.set(key, st)
            ctx.storage.set(f"neg:{rfq.rfq_id}:latest", rfq.version)

        ranked = _ranked_from_state(st)
        ctx.logger.info(f"{rfq.rfq_id}: {len(ranked)} quotes, {len(st['declined'])} declined")
        await ctx.send(sender, NegotiationResult(rfq_id=rfq.rfq_id, ranked=ranked,
                                                 declined=st["declined"], trace=msg.trace))

    @proto.on_message(CounterRequest, replies=CounterResult)
    async def on_counter(ctx: Context, sender: str, msg: CounterRequest):
        """One "Negotiate lower": up to EXCHANGES_PER_TAP rounds of ask -> vendor answer, stopping when the vendor
        accepts, gives its best price, has no room, declines, or needs its owner. The transcript goes back too."""
        observability.tag(msg.trace, msg.rfq_id, msg.vendor_id)
        version = ctx.storage.get(f"neg:{msg.rfq_id}:latest")
        key = f"neg:{msg.rfq_id}:v{version}"
        st = ctx.storage.get(key) if version else None
        transcript: list[list[str]] = []
        asked = {"target_usd": 0.0, "reason": ""}

        def result(quote=None, reason=""):
            return CounterResult(rfq_id=msg.rfq_id, vendor_id=msg.vendor_id, quote=quote,
                                 declined_reason=reason, ranked=_ranked_from_state(st) if st else [],
                                 transcript=transcript, trace=msg.trace, **asked)

        if not st or msg.vendor_id not in st["quotes"]:
            await ctx.send(sender, result(reason="no open quote from this vendor"))
            return
        current = Quote.parse_raw(st["quotes"][msg.vendor_id])
        if current.estimate:
            await ctx.send(sender, result(current, "unclaimed vendor: estimate only, no negotiation"))
            return
        if current.round >= MAX_ROUNDS or current.status in ("accepted", "needs_approval"):
            await ctx.send(sender, result(current, "negotiation finished"))
            return

        budget, heads = st["rfq"]["budget_usd"], st["rfq"]["headcount"]
        asks = st.setdefault("asks", {})
        reason = ""
        for _ in range(EXCHANGES_PER_TAP):
            rnd = current.round + 1
            if rnd > MAX_ROUNDS:
                break
            if rnd == 1:
                target, said = (msg.target_usd, f"Could you do {_usd(msg.target_usd)}?") if msg.target_usd \
                    else opening_ask(current, budget, heads)
            elif rnd == 2:
                target, said = push_ask(current.total_usd, asks.get(msg.vendor_id, current.total_usd), budget)
            else:
                target, said = owner_ask(current.total_usd, budget)
            asks[msg.vendor_id] = target
            asked.update(target_usd=target, reason=said)
            transcript.append(["Haggle", said])
            counter = Counter(rfq_id=msg.rfq_id, vendor_id=msg.vendor_id, target_usd=target, reason=said,
                              round=rnd, version=st["rfq"]["version"], trace=msg.trace)
            reply, _ = await ctx.send_and_receive(st["vendors"][msg.vendor_id]["address"], counter,
                                                  response_type={Quote, Decline}, timeout=QUOTE_TIMEOUT_S)
            if not isinstance(reply, Quote):
                reason = reply.reason if isinstance(reply, Decline) else f"no reply in {QUOTE_TIMEOUT_S}s"
                transcript.append([current.vendor_name, reason])
                break
            before = current.total_usd
            current = reply.copy(update={"vendor_id": msg.vendor_id})
            st["quotes"][msg.vendor_id] = current.json()
            transcript.append([current.vendor_name, current.note])
            ctx.logger.info(f"{msg.rfq_id} {msg.vendor_id} r{rnd}: asked ${target}, got ${current.total_usd} "
                            f"({current.status})")
            if current.status in ("accepted", "needs_approval") or current.total_usd >= before or rnd >= 2:
                break  # a deal, the owner, no movement, or the vendor's best price: the user decides next
        ctx.storage.set(key, st)
        await ctx.send(sender, result(current, reason))

    def _vendor_state(ctx: Context, sender: str, rfq_id: str, version: int):
        """The negotiation state and our vendor_id for a vendor's later (unsolicited) message."""
        key = f"neg:{rfq_id}:v{version}"
        st = ctx.storage.get(key)
        if not st:
            return None, None, None
        vid = next((v for v, ref in st["vendors"].items() if ref["address"] == sender), None)
        return key, st, vid

    async def _forward(ctx: Context, st: dict, vid: str, quote: Optional[Quote], reason: str = "") -> None:
        await ctx.send(st["sender"], CounterResult(
            rfq_id=st["rfq"]["rfq_id"], vendor_id=vid, quote=quote, declined_reason=reason,
            ranked=_ranked_from_state(st), trace=st["rfq"].get("trace", "")))

    # A claimed vendor answers later once its owner decides (approve / counter / decline / expired).
    vendor_updates = Protocol(name="HaggleNegotiatorVendorUpdates", version="0.1.0")

    @vendor_updates.on_message(Quote)
    async def on_vendor_quote(ctx: Context, sender: str, q: Quote):
        key, st, vid = _vendor_state(ctx, sender, q.rfq_id, q.version)
        if not st or not vid:
            ctx.logger.warning(f"{q.rfq_id}: unsolicited quote from unknown vendor {sender[:16]}")
            return
        q = q.copy(update={"vendor_id": vid})
        st["quotes"][vid] = q.json()
        st["declined"].pop(vid, None)
        ctx.storage.set(key, st)
        ctx.logger.info(f"{q.rfq_id} {vid}: owner decision -> ${q.total_usd} ({q.status})")
        await _forward(ctx, st, vid, q)

    @vendor_updates.on_message(Decline)
    async def on_vendor_decline(ctx: Context, sender: str, d: Decline):
        key, st, vid = _vendor_state(ctx, sender, d.rfq_id, d.version)
        if not st or not vid:
            return
        current = Quote.parse_raw(st["quotes"][vid]) if vid in st["quotes"] else None
        if current and current.status == "needs_approval":
            # The owner said no to the change: the earlier price stands.
            current = current.copy(update={"status": "quoted", "note": d.reason})
            st["quotes"][vid] = current.json()
        else:
            st["quotes"].pop(vid, None)
            st["declined"][vid] = d.reason
        ctx.storage.set(key, st)
        ctx.logger.info(f"{d.rfq_id} {vid}: owner decision -> declined ({d.reason})")
        await _forward(ctx, st, vid, current, d.reason)

    agent.include(proto, publish_manifest=True)
    agent.include(vendor_updates)
    return agent


if __name__ == "__main__":
    load_dotenv()
    observability.init("negotiator")
    make_negotiator(mailbox=True, publish_agent_details=True,
                    port=int(os.getenv("NEGOTIATOR_PORT", "8003"))).run()
