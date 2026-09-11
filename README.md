---
title: Nexora AI
emoji: 🤖
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 5.45.0
python_version: "3.10"
app_file: app.py
pinned: false
---

# Nexora AI

Retrieval-grounded FAQ chatbots, sold as a service.

A customer signs up, picks one of ten templates, uploads their FAQ sheet, and
gets an API key. Their bot answers from that sheet and refuses anything the
sheet and template don't cover.

**The bot cannot state a fact the customer didn't write down.**

## Live Demo

This Space runs an interactive demo of the Nexora AI retrieval pipeline:

1. **Pick a template** — each one defines what the bot is allowed to talk about
2. **Ask a question** — try the pre-loaded FAQs or ask something off-topic
3. **See the result** — every response shows match mode (strong/near/decline) and confidence

## Templates

| # | Template | Covers | Strictness |
|---|----------|--------|------------|
| 1 | 🛍️ E-commerce Support | Orders, delivery, returns, refunds | open |
| 2 | 🍽️ Restaurant & Food Delivery | Menu, timings, delivery areas, bookings | open |
| 3 | 🩺 Clinic & Healthcare | Timings, appointments, fees, insurance | **strict** |
| 4 | 🏢 Real Estate & Property | Listings, site visits, payment plans | balanced |
| 5 | 🎓 Coaching & EdTech | Courses, fees, batches, admissions | open |
| 6 | 💻 SaaS & App Support | Features, billing, integrations | open |
| 7 | ✈️ Travel & Hotel Booking | Bookings, check-in, cancellation | open |
| 8 | 🏦 Banking & Fintech | Charges, KYC, limits, statements | **strict** |
| 9 | 📦 Logistics & Courier | Tracking, delivery, claims | open |
| 10 | 💇 Salon, Spa & Local Services | Services, prices, timings | open |
