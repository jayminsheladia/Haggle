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
MAX_ROUNDS = 2
DEFAULT_DISCOUNT = 0.10  # ask for 10% off when there is no budget gap to close


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


def pick_target(quote: Quote, budget_usd: float) -> float:
    """Counter target: the budget if the quote is over it, else ask for DEFAULT_DISCOUNT off."""
    if budget_usd > 0 and quote.total_usd > budget_usd:
        return round(budget_usd, 2)
    return round(quote.total_usd * (1 - DEFAULT_DISCOUNT), 2)


def _ranked_from_state(st: dict) -> List[RankedQuote]:
    quotes = [Quote.parse_raw(q) for q in st["quotes"].values()]
    return rank_quotes(quotes, st["rfq"]["budget_usd"], {v: r["rating"] for v, r in st["vendors"].items()})


def make_negotiator(**agent_kwargs) -> Agent:
    agent = Agent(name="haggle-negotiator", seed=os.getenv("NEGOTIATOR_SEED", "haggle-negotiator-dev-seed"),
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
                    st["quotes"][v.vendor_id] = reply.json()
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
        observability.tag(msg.trace, msg.rfq_id, msg.vendor_id)
        version = ctx.storage.get(f"neg:{msg.rfq_id}:latest")
        key = f"neg:{msg.rfq_id}:v{version}"
        st = ctx.storage.get(key) if version else None

        def result(quote=None, reason=""):
            return CounterResult(rfq_id=msg.rfq_id, vendor_id=msg.vendor_id, quote=quote,
                                 declined_reason=reason, ranked=_ranked_from_state(st) if st else [],
                                 trace=msg.trace)

        if not st or msg.vendor_id not in st["quotes"]:
            await ctx.send(sender, result(reason="no open quote from this vendor"))
            return
        current = Quote.parse_raw(st["quotes"][msg.vendor_id])
        if current.estimate:
            await ctx.send(sender, result(current, "unclaimed vendor: estimate only, no negotiation"))
            return
        if current.round >= MAX_ROUNDS or current.status == "accepted":
            await ctx.send(sender, result(current, "negotiation finished"))
            return

        target = msg.target_usd or pick_target(current, st["rfq"]["budget_usd"])
        counter = Counter(rfq_id=msg.rfq_id, vendor_id=msg.vendor_id, target_usd=target,
                          round=current.round + 1, trace=msg.trace)
        reply, _ = await ctx.send_and_receive(st["vendors"][msg.vendor_id]["address"], counter,
                                              response_type={Quote, Decline}, timeout=QUOTE_TIMEOUT_S)
        if isinstance(reply, Quote):
            st["quotes"][msg.vendor_id] = reply.json()
            ctx.storage.set(key, st)
            ctx.logger.info(f"{msg.rfq_id} {msg.vendor_id}: ${current.total_usd} -> ${reply.total_usd} "
                            f"({reply.status}, target ${target})")
            await ctx.send(sender, result(reply))
        else:
            reason = reply.reason if isinstance(reply, Decline) else f"no reply in {QUOTE_TIMEOUT_S}s"
            await ctx.send(sender, result(current, reason))

    agent.include(proto, publish_manifest=True)
    return agent


if __name__ == "__main__":
    load_dotenv()
    observability.init("negotiator")
    make_negotiator(mailbox=True, publish_agent_details=True,
                    port=int(os.getenv("NEGOTIATOR_PORT", "8003"))).run()
