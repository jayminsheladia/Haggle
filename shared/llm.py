"""ASI:One LLM client (OpenAI SDK, base_url=https://api.asi1.ai/v1, model="asi1").

The LLM only parses text, extracts menus, writes friendly notes and picks a
negotiation strategy. It never sets prices, floors, budgets or payments.

Owner: Person A
"""

import json
import os
import re

from openai import AsyncOpenAI

BASE_URL = "https://api.asi1.ai/v1"
MODEL = "asi1"

_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=BASE_URL, api_key=os.environ["ASI1_API_KEY"], timeout=30)
    return _client


def extract_json(text: str) -> dict | None:
    """Parse a JSON object from an LLM reply, tolerating code fences or extra prose."""
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        pass
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


async def chat_json(system: str, user: str, max_tokens: int = 800) -> dict | None:
    """One LLM call that must return a JSON object. Returns None if it doesn't."""
    r = await client().chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,
        max_tokens=max_tokens,
    )
    return extract_json(r.choices[0].message.content or "")
