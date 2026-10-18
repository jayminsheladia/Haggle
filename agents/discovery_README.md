# Haggle Discovery — find caterers near any LA venue

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Discovery is the search service behind Haggle, the group catering marketplace for Los Angeles. Give it a venue and it returns real caterers nearby, plus any vendor agents on Agentverse that can quote and negotiate through Haggle.

## What it does

- Turns a venue, address or neighborhood ("Ackerman Union, UCLA", "Westwood") into coordinates
- Finds real caterers within a radius using Google Places: name, address, phone, rating, photo and distance
- Finds vendor agents on Agentverse that speak Haggle's negotiation protocol
- If one source fails, still returns what the other found

## Chat with it

Message **@haggle-discovery** in ASI:One with a venue or neighborhood in Los Angeles. It replies with the closest caterers, their rating, phone number and a Google Maps link. To get quotes, negotiate and book, chat with **@haggle**.

## Example prompts

- "Caterers near Ackerman Union, UCLA"
- "Find catering in Santa Monica"
- "Who caters near 405 Hilgard Ave?"
- "Catering around Downtown LA for 60 people"

## Protocol

`HaggleDiscovery` v0.1.0 — send a `DiscoveryRequest` (`rfq_id`, `category`, `location` or `lat`/`lng`, `radius_mi`, `limit`) and get back a `DiscoveryResult` with a list of vendors.

## Keywords

catering, caterers, event vendors, vendor search, restaurant catering, Los Angeles, LA, UCLA, Westwood, Santa Monica, Haggle
