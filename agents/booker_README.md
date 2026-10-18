# Haggle Booker

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Booking agent for **Haggle**, a multi-agent group catering marketplace for Los Angeles.

When a user pays a booking deposit in ASI:One (Fetch.ai Payment Protocol, Stripe), the Haggle Concierge hands the
booking to this agent. The Booker:

1. verifies with Stripe that the deposit is paid, for the right request and the right amount
2. confirms the booking with the caterer's agent, and refunds the deposit if the caterer can't take it
3. sends the booking confirmation email

Every booking is idempotent on its request id: a repeated message never double-books or double-charges.

Talk to Haggle in ASI:One: `@haggle`.
