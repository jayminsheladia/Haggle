"""Send one DiscoveryRequest to the running Discovery agent and print the vendors it returns.

Start the agent first (python -m agents.discovery), then from the repo root:
    python -m scripts.test_discovery "Ackerman Union, UCLA"

Owner: Person B
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from uagents.communication import send_sync_message
from uagents.crypto import Identity
from uagents.resolver import RulesBasedResolver

from shared.models import DiscoveryRequest, DiscoveryResult

load_dotenv()

DISCOVERY_ENDPOINT = "http://127.0.0.1:8002/submit"


async def main(location: str) -> int:
    address = Identity.from_seed(os.environ["DISCOVERY_SEED"], 0).address
    result = await send_sync_message(
        address,
        DiscoveryRequest(rfq_id="rfq_test", location=location),
        response_type=DiscoveryResult,
        resolver=RulesBasedResolver({address: DISCOVERY_ENDPOINT}),
        timeout=30,
    )
    if not isinstance(result, DiscoveryResult):
        print(f"No DiscoveryResult from {address}: {result}")
        return 1

    print(f"Near {result.resolved_location} ({result.lat}, {result.lng}): {len(result.vendors)} vendors")
    if result.error:
        print(f"Error: {result.error}")
    for v in result.vendors:
        where = v.agent_address or f"{v.distance_mi} mi · {v.address}"
        print(f"- [{v.source}] {v.name} · {v.rating} ({v.rating_count}) · {v.phone} · {where}")
        print(f"    place_id={v.place_id} photo={'yes' if v.photo_url else 'no'}")
    return 0 if result.vendors else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "Ackerman Union, UCLA")))
