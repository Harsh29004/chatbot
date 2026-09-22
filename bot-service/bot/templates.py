"""
The template catalogue — ten ready-made bots a customer can pick from.

A template is not a personality. It is a *scope contract*: it names the
subject the bot is allowed to talk about, the words it uses to refuse
everything else, and how sure it has to be before it answers at all.

The customer's uploaded sheet supplies the facts; the template supplies the
boundary around them. Together they answer the only two questions that
matter: "what may this bot say?" and "what does it say when it doesn't know?"

Thresholds are deliberately not uniform. A wrong answer about a haircut price
costs an apology; a wrong answer about a drug interaction or a loan penalty
costs considerably more, so those templates demand a closer match before they
will speak and fall silent sooner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The decline line every template ends with. Kept separate so the "we won't
# guess" promise reads identically across the whole catalogue.
_HANDOFF = "Try rephrasing, or contact our team and a person will help you directly."


@dataclass(frozen=True)
class BotTemplate:
    """One ready-made bot configuration."""

    id: str
    name: str
    category: str
    icon: str
    tagline: str
    description: str

    # What the bot is allowed to be about — quoted back to the user on refusal.
    scope_label: str
    decline_message: str
    near_match_suffix: str

    # How close a match has to be before the bot will answer.
    strong_threshold: float
    near_threshold: float

    # Sheet-building help
    starter_categories: tuple[str, ...]
    sample_questions: tuple[str, ...]
    starter_rows: tuple[tuple[str, str, str, str], ...]

    # Things this vertical must never appear to *do*, only explain.
    extra_action_patterns: tuple[str, ...] = ()


def _decline(scope: str) -> str:
    return f"I can only answer questions about {scope}. {_HANDOFF}"


def _nudge(who: str) -> str:
    return (
        f"\n\nIf that doesn't fully answer your question, {who} can help you "
        "with the specifics."
    )


TEMPLATES: tuple[BotTemplate, ...] = (
    # ---------------------------------------------------------------- 01
    BotTemplate(
        id="ecommerce",
        name="E-commerce Support",
        category="Retail",
        icon="🛍️",
        tagline="Orders, delivery, returns and refunds",
        description=(
            "For online stores. Handles the four questions that make up most "
            "support volume — where is my order, how do I return it, when do I "
            "get my money back, and what does delivery cost."
        ),
        scope_label="orders, delivery, returns, refunds and payments for this store",
        decline_message=_decline(
            "orders, delivery, returns, refunds and payments for this store"
        ),
        near_match_suffix=_nudge("our support team"),
        strong_threshold=0.84,
        near_threshold=0.60,
        starter_categories=("Orders", "Shipping", "Returns", "Refunds", "Payments", "Account"),
        sample_questions=(
            "Where is my order?",
            "How do I return an item?",
            "How long do refunds take?",
            "Do you deliver to my pin code?",
        ),
        starter_rows=(
            (
                "Where is my order?",
                "track my order; order status; when will it arrive",
                "Orders",
                "Open My Orders and tap the order to see live tracking. Tracking usually "
                "updates within 24 hours of dispatch.",
            ),
            (
                "How do I return an item?",
                "start a return; send it back; exchange an item",
                "Returns",
                "Go to My Orders, select the item and tap Return. Returns are accepted "
                "within 7 days of delivery if the item is unused and in original packaging.",
            ),
            (
                "How long do refunds take?",
                "when will I get my money; refund time",
                "Refunds",
                "Refunds are issued once the returned item passes inspection, and reach "
                "your original payment method in 5–7 working days.",
            ),
        ),
        extra_action_patterns=(
            r"\b(place|make)\s+(an?\s+)?order\s+for\s+me\b",
            r"\bapply\s+(a\s+)?(coupon|discount)\s+(for|to)\s+(me|my)\b",
        ),
    ),
    # ---------------------------------------------------------------- 02
    BotTemplate(
        id="restaurant",
        name="Restaurant & Food Delivery",
        category="Food",
        icon="🍽️",
        tagline="Menu, timings, delivery areas and table bookings",
        description=(
            "For restaurants, cloud kitchens and delivery brands. Answers the "
            "menu, hours, delivery-radius and reservation questions that "
            "otherwise tie up a phone line during service."
        ),
        scope_label="our menu, opening hours, delivery areas, orders and table bookings",
        decline_message=_decline(
            "our menu, opening hours, delivery areas, orders and table bookings"
        ),
        near_match_suffix=_nudge("the restaurant"),
        strong_threshold=0.84,
        near_threshold=0.60,
        starter_categories=("Menu", "Timings", "Delivery", "Reservations", "Offers", "Dietary"),
        sample_questions=(
            "What are your opening hours?",
            "Do you have vegetarian options?",
            "Do you deliver to my area?",
            "How do I book a table?",
        ),
        starter_rows=(
            (
                "What are your opening hours?",
                "when do you open; timings; are you open now",
                "Timings",
                "We are open 11:00 AM to 11:00 PM every day, with last orders taken at "
                "10:30 PM.",
            ),
            (
                "Do you have vegetarian options?",
                "veg food; pure veg; vegan dishes",
                "Dietary",
                "Yes — the menu has a dedicated vegetarian section, and every dish is "
                "marked veg or non-veg. Jain and vegan preparations are available on request.",
            ),
            (
                "How do I book a table?",
                "reservation; reserve a table; book seats",
                "Reservations",
                "Tables can be reserved from the Reservations page on our website, or by "
                "calling the outlet directly. We hold reserved tables for 15 minutes.",
            ),
        ),
        extra_action_patterns=(
            r"\b(book|reserve)\s+(a\s+)?table\s+for\s+me\b",
            r"\border\s+(me|my)\s+",
        ),
    ),
    # ---------------------------------------------------------------- 03
    BotTemplate(
        id="clinic",
        name="Clinic & Healthcare",
        category="Health",
        icon="🩺",
        tagline="Timings, services, appointments and insurance",
        description=(
            "For clinics, dental practices and diagnostic labs. Deliberately "
            "the strictest template in the catalogue: it answers logistics — "
            "hours, fees, preparation, insurance — and refuses anything that "
            "edges toward clinical advice."
        ),
        scope_label=(
            "our clinic's timings, services, appointments, fees and insurance"
        ),
        decline_message=(
            "I can only answer questions about our clinic's timings, services, "
            "appointments, fees and insurance. I can't give medical advice — "
            "please speak to a doctor or call the clinic directly."
        ),
        near_match_suffix=(
            "\n\nFor anything about your own health or treatment, please speak to "
            "a doctor at the clinic."
        ),
        # High stakes: answer only on a very close match, stay silent otherwise.
        strong_threshold=0.90,
        near_threshold=0.72,
        starter_categories=(
            "Timings", "Appointments", "Services", "Fees", "Insurance", "Reports", "Preparation",
        ),
        sample_questions=(
            "What are your consultation timings?",
            "How do I book an appointment?",
            "Do you accept my insurance?",
            "Do I need to fast before a blood test?",
        ),
        starter_rows=(
            (
                "What are your consultation timings?",
                "clinic hours; when is the doctor available; opd timing",
                "Timings",
                "OPD consultations run 9:00 AM to 1:00 PM and 5:00 PM to 8:00 PM, Monday "
                "to Saturday. The clinic is closed on Sundays and public holidays.",
            ),
            (
                "How do I book an appointment?",
                "take appointment; schedule a visit; booking",
                "Appointments",
                "Appointments can be booked from the Appointments page on our website or "
                "by calling the front desk. Walk-ins are seen subject to availability.",
            ),
            (
                "Do I need to fast before a blood test?",
                "fasting for blood test; empty stomach test",
                "Preparation",
                "Fasting requirements depend on the specific test. Your test instructions "
                "are printed on the requisition slip — please follow those, and call the "
                "lab if anything is unclear.",
            ),
        ),
        extra_action_patterns=(
            r"\b(what|which)\s+(medicine|tablet|dose|dosage)\s+(should|can|do)\s+I\b",
            r"\b(diagnose|prescribe)\b",
            r"\bis\s+it\s+(safe|dangerous)\s+(for\s+me\s+)?to\s+take\b",
            r"\bbook\s+(me\s+)?an?\s+appointment\b",
        ),
    ),
    # ---------------------------------------------------------------- 04
    BotTemplate(
        id="realestate",
        name="Real Estate & Property",
        category="Property",
        icon="🏢",
        tagline="Listings, site visits, pricing and paperwork",
        description=(
            "For builders, brokers and property portals. Covers project "
            "details, availability, site visits, payment plans and the "
            "documentation buyers always ask about."
        ),
        scope_label="our properties, pricing, site visits, payment plans and documentation",
        decline_message=_decline(
            "our properties, pricing, site visits, payment plans and documentation"
        ),
        near_match_suffix=_nudge("our sales team"),
        strong_threshold=0.87,
        near_threshold=0.65,
        starter_categories=(
            "Projects", "Pricing", "Site Visits", "Payment Plans", "Documentation", "Possession",
        ),
        sample_questions=(
            "What is the price per square foot?",
            "How do I book a site visit?",
            "What is the possession date?",
            "Which banks have approved this project?",
        ),
        starter_rows=(
            (
                "How do I book a site visit?",
                "site visit; visit the property; see the flat",
                "Site Visits",
                "Site visits run daily between 10:00 AM and 6:00 PM. Request a slot from "
                "the project page and our sales team will confirm the time with you.",
            ),
            (
                "What is the possession date?",
                "when is handover; ready to move; completion date",
                "Possession",
                "The committed possession date for each tower is listed on its project "
                "page and in the allotment letter. Our sales team can confirm the current "
                "construction status for a specific unit.",
            ),
            (
                "Which documents do I need to book a unit?",
                "paperwork; documents required; kyc for booking",
                "Documentation",
                "Booking requires PAN, Aadhaar, recent photographs and address proof for "
                "every applicant, plus the booking amount receipt.",
            ),
        ),
        extra_action_patterns=(
            r"\bblock\s+(a\s+)?(unit|flat|apartment)\s+for\s+me\b",
            r"\bnegotiate\s+(the\s+)?price\b",
        ),
    ),
    # ---------------------------------------------------------------- 05
    BotTemplate(
        id="education",
        name="Coaching & EdTech",
        category="Education",
        icon="🎓",
        tagline="Courses, fees, batches, admissions and results",
        description=(
            "For coaching institutes, online course sellers and schools. "
            "Answers the admissions-season questions — syllabus, batch "
            "timings, fees, refunds and certificates — at any hour."
        ),
        scope_label="our courses, batches, fees, admissions and certificates",
        decline_message=_decline(
            "our courses, batches, fees, admissions and certificates"
        ),
        near_match_suffix=_nudge("the admissions desk"),
        strong_threshold=0.85,
        near_threshold=0.62,
        starter_categories=(
            "Courses", "Admissions", "Fees", "Batches", "Faculty", "Certificates", "Refunds",
        ),
        sample_questions=(
            "What is the fee for this course?",
            "When does the next batch start?",
            "Is there a demo class?",
            "Do I get a certificate?",
        ),
        starter_rows=(
            (
                "When does the next batch start?",
                "batch timing; new batch; when do classes begin",
                "Batches",
                "New batches begin on the first Monday of every month. Weekday batches run "
                "6:00–8:00 PM and weekend batches 10:00 AM–1:00 PM.",
            ),
            (
                "Is there a demo class?",
                "trial class; free class; sample lecture",
                "Admissions",
                "Yes — one free demo class is available for every course. Register from the "
                "course page and you will receive the joining link by email.",
            ),
            (
                "Do I get a certificate?",
                "certification; completion certificate",
                "Certificates",
                "A completion certificate is issued after you finish the coursework and "
                "score at least 60% in the final assessment.",
            ),
        ),
    ),
    # ---------------------------------------------------------------- 06
    BotTemplate(
        id="saas",
        name="SaaS & App Support",
        category="Software",
        icon="💻",
        tagline="Features, billing, integrations and troubleshooting",
        description=(
            "For software products. Deflects the repetitive tier-1 tickets — "
            "password resets, plan limits, integration setup — so your team "
            "only sees the tickets that need a human."
        ),
        scope_label="our product's features, plans, billing, integrations and setup",
        decline_message=_decline(
            "our product's features, plans, billing, integrations and setup"
        ),
        near_match_suffix=_nudge("our support team"),
        strong_threshold=0.85,
        near_threshold=0.60,
        starter_categories=(
            "Getting Started", "Features", "Billing", "Plans", "Integrations",
            "Troubleshooting", "Security",
        ),
        sample_questions=(
            "How do I reset my password?",
            "What is included in the free plan?",
            "How do I connect it to Slack?",
            "Where can I download my invoice?",
        ),
        starter_rows=(
            (
                "How do I reset my password?",
                "forgot password; change password; cannot log in",
                "Getting Started",
                "Use Forgot Password on the sign-in screen. A reset link valid for 30 "
                "minutes is sent to your registered email.",
            ),
            (
                "What is included in the free plan?",
                "free tier limits; trial features",
                "Plans",
                "The free plan includes one project, up to three team members and 500 API "
                "calls per month. Paid plans lift all three limits.",
            ),
            (
                "Where can I download my invoice?",
                "get invoice; billing receipt; gst invoice",
                "Billing",
                "Invoices are under Settings → Billing → Invoices, and can be downloaded "
                "as PDF for any past billing period.",
            ),
        ),
    ),
    # ---------------------------------------------------------------- 07
    BotTemplate(
        id="travel",
        name="Travel & Hotel Booking",
        category="Travel",
        icon="✈️",
        tagline="Bookings, check-in, cancellations and baggage",
        description=(
            "For hotels, tour operators and travel agencies. Covers the "
            "policy questions guests ask before and during a trip — check-in "
            "times, cancellation windows, inclusions and documentation."
        ),
        scope_label="our bookings, check-in, cancellation policy, packages and amenities",
        decline_message=_decline(
            "our bookings, check-in, cancellation policy, packages and amenities"
        ),
        near_match_suffix=_nudge("our reservations desk"),
        strong_threshold=0.85,
        near_threshold=0.62,
        starter_categories=(
            "Bookings", "Check-in", "Cancellation", "Packages", "Amenities", "Payments",
        ),
        sample_questions=(
            "What is your check-in time?",
            "What is the cancellation policy?",
            "Is breakfast included?",
            "Do you allow pets?",
        ),
        starter_rows=(
            (
                "What is your check-in time?",
                "checkin; when can I check in; early check in",
                "Check-in",
                "Check-in is from 2:00 PM and check-out is by 11:00 AM. Early check-in is "
                "subject to availability on the day.",
            ),
            (
                "What is the cancellation policy?",
                "cancel booking; refund on cancellation",
                "Cancellation",
                "Cancellations made more than 48 hours before check-in are fully "
                "refundable. Within 48 hours, one night's tariff is charged.",
            ),
            (
                "Is breakfast included?",
                "complimentary breakfast; meal plan",
                "Amenities",
                "Breakfast is included on all Bed-and-Breakfast rates. Room-only rates do "
                "not include meals, and breakfast can be added at the desk.",
            ),
        ),
        extra_action_patterns=(
            r"\b(book|reserve)\s+(a\s+)?(room|flight|ticket)\s+for\s+me\b",
        ),
    ),
    # ---------------------------------------------------------------- 08
    BotTemplate(
        id="fintech",
        name="Banking & Fintech",
        category="Finance",
        icon="🏦",
        tagline="Accounts, KYC, charges, limits and statements",
        description=(
            "For lenders, wallets, NBFCs and neobanks. The strictest template "
            "alongside healthcare: it explains published policy, charges and "
            "processes, and refuses anything resembling financial advice or "
            "account-specific action."
        ),
        scope_label="our products, charges, KYC, limits, statements and account processes",
        decline_message=(
            "I can only answer questions about our products, charges, KYC, limits "
            "and account processes. I can't advise on your personal finances or act "
            "on your account — please contact our support team for that."
        ),
        near_match_suffix=(
            "\n\nFor anything specific to your own account, please contact our "
            "support team, who can verify your identity first."
        ),
        strong_threshold=0.90,
        near_threshold=0.72,
        starter_categories=(
            "Accounts", "KYC", "Charges", "Limits", "Statements", "Cards", "Loans", "Security",
        ),
        sample_questions=(
            "What documents are needed for KYC?",
            "What are the transaction limits?",
            "How do I download my statement?",
            "What charges apply on late payment?",
        ),
        starter_rows=(
            (
                "What documents are needed for KYC?",
                "kyc documents; verification papers; id proof",
                "KYC",
                "KYC requires PAN and one address proof — Aadhaar, passport, voter ID or "
                "driving licence. Verification usually completes within 24 hours of upload.",
            ),
            (
                "What are the transaction limits?",
                "daily limit; maximum transfer; upi limit",
                "Limits",
                "Fully-KYC accounts can transfer up to ₹1,00,000 per day. Minimum-KYC "
                "accounts are capped at ₹10,000 per month.",
            ),
            (
                "How do I download my statement?",
                "account statement; transaction history; passbook",
                "Statements",
                "Statements are under Account → Statements. Any period in the last 12 "
                "months can be downloaded as PDF or CSV.",
            ),
        ),
        extra_action_patterns=(
            r"\b(should|shall)\s+I\s+(invest|buy|sell|borrow)\b",
            r"\bwhich\s+(fund|stock|scheme|policy)\s+(should|is best)\b",
            r"\b(transfer|send)\s+(money|funds|₹|rs\.?\s*\d)",
            r"\b(increase|raise)\s+my\s+(limit|credit)\b",
        ),
    ),
    # ---------------------------------------------------------------- 09
    BotTemplate(
        id="logistics",
        name="Logistics & Courier",
        category="Logistics",
        icon="📦",
        tagline="Tracking, delivery windows, charges and claims",
        description=(
            "For courier companies and delivery fleets. Handles the tracking "
            "and policy questions that flood a support line, and routes "
            "genuine exceptions — damage, loss, disputes — to a person."
        ),
        scope_label="shipments, tracking, delivery timelines, charges and claims",
        decline_message=_decline(
            "shipments, tracking, delivery timelines, charges and claims"
        ),
        near_match_suffix=_nudge("our support team"),
        strong_threshold=0.85,
        near_threshold=0.60,
        starter_categories=(
            "Tracking", "Delivery", "Charges", "Pickup", "Claims", "Serviceability",
        ),
        sample_questions=(
            "How do I track my shipment?",
            "What if I miss the delivery?",
            "How are shipping charges calculated?",
            "What do I do if my parcel is damaged?",
        ),
        starter_rows=(
            (
                "How do I track my shipment?",
                "tracking number; where is my parcel; track consignment",
                "Tracking",
                "Enter your AWB number on the Track page. Status updates appear within a "
                "few hours of each scan.",
            ),
            (
                "What if I miss the delivery?",
                "missed delivery; nobody was home; redelivery",
                "Delivery",
                "We attempt delivery three times on consecutive working days. After the "
                "third attempt the parcel is held at the nearest hub for seven days.",
            ),
            (
                "What do I do if my parcel is damaged?",
                "damaged package; broken item; claim",
                "Claims",
                "Raise a claim within 48 hours of delivery with photographs of the "
                "packaging and contents. Claims are assessed within five working days.",
            ),
        ),
    ),
    # ---------------------------------------------------------------- 10
    BotTemplate(
        id="services",
        name="Salon, Spa & Local Services",
        category="Services",
        icon="💇",
        tagline="Services, prices, timings and appointments",
        description=(
            "For salons, spas, gyms, repair shops and other appointment-led "
            "local businesses. Answers the price-and-timing questions that "
            "arrive by message at every hour of the day."
        ),
        scope_label="our services, prices, timings, appointments and offers",
        decline_message=_decline("our services, prices, timings, appointments and offers"),
        near_match_suffix=_nudge("the front desk"),
        strong_threshold=0.84,
        near_threshold=0.60,
        starter_categories=(
            "Services", "Pricing", "Timings", "Appointments", "Offers", "Policies",
        ),
        sample_questions=(
            "How much does a haircut cost?",
            "Do you take walk-ins?",
            "What are your timings?",
            "Do you have any offers running?",
        ),
        starter_rows=(
            (
                "How much does a haircut cost?",
                "haircut price; rate for hair cutting; charges",
                "Pricing",
                "A haircut starts at ₹400 for men and ₹700 for women. The full price list "
                "is on the Services page.",
            ),
            (
                "Do you take walk-ins?",
                "without appointment; walk in; can I come directly",
                "Appointments",
                "Walk-ins are welcome subject to availability. Appointments are "
                "recommended on weekends, when waiting times are longest.",
            ),
            (
                "What are your timings?",
                "opening hours; when do you open; closing time",
                "Timings",
                "We are open 10:00 AM to 8:00 PM, Tuesday to Sunday. The salon is closed "
                "on Mondays.",
            ),
        ),
        extra_action_patterns=(
            r"\bbook\s+(me\s+)?(an?\s+)?(appointment|slot)\b",
            r"\bcancel\s+my\s+appointment\b",
        ),
    ),
)

TEMPLATES_BY_ID: dict[str, BotTemplate] = {t.id: t for t in TEMPLATES}

DEFAULT_TEMPLATE_ID = "ecommerce"

# The columns an uploaded sheet may contain. Only Question and Answer are
# required — demanding four columns turns a five-minute setup into an
# afternoon, and the other two only improve matching.
REQUIRED_SHEET_COLUMNS = ("Question", "Answer")
OPTIONAL_SHEET_COLUMNS = ("Alt_Phrasings", "Category")


def get_template(template_id: str) -> BotTemplate | None:
    return TEMPLATES_BY_ID.get(template_id)


def template_public_dict(template: BotTemplate) -> dict[str, Any]:
    """The shape the template picker renders."""
    return {
        "id": template.id,
        "name": template.name,
        "category": template.category,
        "icon": template.icon,
        "tagline": template.tagline,
        "description": template.description,
        "scope_label": template.scope_label,
        "decline_message": template.decline_message,
        "sample_questions": list(template.sample_questions),
        "starter_categories": list(template.starter_categories),
        "strong_threshold": template.strong_threshold,
        "near_threshold": template.near_threshold,
        "strictness": (
            "strict" if template.strong_threshold >= 0.90
            else "balanced" if template.strong_threshold >= 0.86
            else "open"
        ),
    }


def list_templates() -> list[dict[str, Any]]:
    return [template_public_dict(t) for t in TEMPLATES]


def starter_sheet_rows(template: BotTemplate) -> list[list[str]]:
    """Header plus example rows, ready to be written as CSV."""
    header = ["Question", "Alt_Phrasings", "Category", "Answer"]
    return [header] + [list(row) for row in template.starter_rows]
