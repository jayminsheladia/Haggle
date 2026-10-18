"""Sentry init + trace propagation helpers. Every agent calls init on startup.

Owner: Person C
"""
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import sentry_sdk


def init(service: str) -> None:
    """Init Sentry for this agent. No-op without SENTRY_DSN, so local runs still work."""
    dsn = os.getenv("SENTRY_DSN", "")
    if dsn:
        sentry_sdk.init(dsn=dsn, traces_sample_rate=1.0)
    sentry_sdk.set_tag("service", service)


def new_trace() -> str:
    return uuid.uuid4().hex


def tag(trace: str, rfq_id: str = "", vendor_id: str = "") -> None:
    """Attach the message's trace/rfq/vendor to the current Sentry scope."""
    sentry_sdk.set_tag("trace", trace)
    if rfq_id:
        sentry_sdk.set_tag("rfq_id", rfq_id)
    if vendor_id:
        sentry_sdk.set_tag("vendor_id", vendor_id)


@contextmanager
def span(op: str, description: str, trace: str = "", **tags: str) -> Iterator[None]:
    """A Sentry span around one step. Sentry problems are swallowed: they never block a request."""
    try:
        cm = sentry_sdk.start_span(op=op, name=description)
        cm.__enter__()
        if trace:
            cm.set_tag("trace", trace)
        for k, v in tags.items():
            cm.set_tag(k, v)
    except Exception:
        cm = None
    try:
        yield
    finally:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass
