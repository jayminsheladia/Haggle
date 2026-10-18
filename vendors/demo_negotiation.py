"""Local test: 2 fake-menu vendor agents + Negotiator + a stand-in Concierge in one Bureau.

Owner: Person C

Run:  python -m vendors.demo_negotiation
Expect: quotes from both vendors, ranked, then one counter round that lowers the top price.
"""
import json
from pathlib import Path

from uagents import Agent, Bureau, Context

from agents.negotiator import make_negotiator
from shared.models import RFQ, CounterRequest, CounterResult, NegotiationRequest, NegotiationResult, VendorRef
from vendors.template import make_vendor_agent

FIXTURES = Path(__file__).parent / "fixtures"
menus = [json.loads(p.read_text()) for p in sorted(FIXTURES.glob("demo_*.json"))]
vendors = [make_vendor_agent(m, seed=f"haggle-demo-vendor-{m['vendor_id']}") for m in menus]
negotiator = make_negotiator()
concierge = Agent(name="demo-concierge", seed="haggle-demo-concierge")


@concierge.on_event("startup")
async def start(ctx: Context):
    rfq = RFQ(rfq_id="rfq-demo-1", event_date="2026-10-23", event_time="18:00", location="Ackerman Union, UCLA",
              headcount=60, budget_usd=850, dietary={"vegetarian": 10, "halal": 3}, allergies=[], trace="demo")
    refs = [VendorRef(vendor_id=m["vendor_id"], name=m["name"], address=a.address, distance_mi=2.1,
                      claimed=m["claimed"], rating=r) for m, a, r in zip(menus, vendors, [4.6, 4.3])]
    await ctx.send(negotiator.address, NegotiationRequest(rfq=rfq, vendors=refs, trace="demo"))


@concierge.on_message(NegotiationResult)
async def on_result(ctx: Context, sender: str, msg: NegotiationResult):
    for r in msg.ranked:
        q = r.quote
        ctx.logger.info(f"#{r.rank} {q.vendor_name}: ${q.total_usd} (${q.per_head_usd}/head) "
                        f"fits_budget={r.fits_budget} score={r.score}")
    ctx.logger.info(f"declined: {msg.declined}")
    if msg.ranked:
        top = msg.ranked[0].quote
        await ctx.send(sender, CounterRequest(rfq_id=msg.rfq_id, vendor_id=top.vendor_id, trace="demo"))


@concierge.on_message(CounterResult)
async def on_counter(ctx: Context, sender: str, msg: CounterResult):
    q = msg.quote
    ctx.logger.info(f"COUNTER {msg.vendor_id}: ${q.total_usd if q else '-'} status={q.status if q else '-'} "
                    f"round={q.round if q else '-'} {msg.declined_reason}")


bureau = Bureau(port=8100)
for a in [*vendors, negotiator, concierge]:
    bureau.add(a)

if __name__ == "__main__":
    bureau.run()
