# Haggle Follow-up

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Follow-up agent for **Haggle**, a multi-agent group catering marketplace for Los Angeles.

After a booking is confirmed, the Haggle Concierge hands the event to this agent. The Follow-up agent:

1. sends a reminder 24 hours before the event (in ASI:One and by text)
2. checks in when the event ends: how did it go, and are there leftovers?
3. matches leftover food to Los Angeles food rescue partners (food banks, campus pantries, prepared-food
   rescue programs) by food type, food safety, pickup or drop-off, opening hours and distance
4. sends the rescue request and shows the address, directions and an estimate of the meals saved

Food safety comes first: prepared food that wasn't kept hot or cold is never offered for donation.
Every step is idempotent per booking: one reminder, one check-in, one rescue request.

Talk to Haggle in ASI:One: `@haggle`.
