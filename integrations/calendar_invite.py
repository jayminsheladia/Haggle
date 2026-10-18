""".ics calendar invites.

Owner: Person D
"""

import base64
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LA = ZoneInfo("America/Los_Angeles")
EVENT_HOURS = 2  # catering window shown on the invite


def _ics_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def booking_invite(rfq_id: str, title: str, description: str, location: str, event_date: str, event_time: str) -> str:
    """An iCalendar (RFC 5545) event for the booking, in UTC."""
    start_local = datetime.fromisoformat(f"{event_date}T{event_time or '12:00'}").replace(tzinfo=LA)
    start = start_local.astimezone(timezone.utc)
    end = start + timedelta(hours=EVENT_HOURS)
    fmt = "%Y%m%dT%H%M%SZ"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Haggle//Booking//EN", "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{rfq_id}@haggle",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}",
        f"DTSTART:{start.strftime(fmt)}",
        f"DTEND:{end.strftime(fmt)}",
        f"SUMMARY:{_ics_text(title)}",
        f"DESCRIPTION:{_ics_text(description)}",
        f"LOCATION:{_ics_text(location)}",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def as_attachment(ics: str, filename: str = "haggle-booking.ics") -> dict:
    return {"filename": filename, "content": base64.b64encode(ics.encode()).decode()}
