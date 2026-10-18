"""Sentry init + trace propagation helpers. Every agent calls init on startup.

Owner: Person C
"""
import os
import uuid

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
