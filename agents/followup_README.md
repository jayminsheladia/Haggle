# Haggle Follow-up

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Follow-up agent for **Haggle**, a multi-agent group catering marketplace for Los Angeles.

After a catering booking is confirmed, this agent runs on its own schedule, with no user message needed:

- **Reminder** the day before the event.
- **Check-in** after the event: "How did it go? Any leftover food?" plus a quick rating.
- **Leftover food rescue:** if there is leftover food that was kept at a safe temperature, it finds nearby food
  recovery organizations that are open now and accept that kind of food, sends the chosen one a pickup or
  drop-off request, and reports the impact (for example "~24 lbs rescued ≈ 20 meals", an estimate).

Safety rails: food is only matched to organizations that accept its type, food that wasn't kept hot or
refrigerated is never routed for donation, and the organization always makes the final call.

Talk to Haggle in ASI:One: `@haggle`.
