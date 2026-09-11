"""
Nexora AI — Gradio demo for Hugging Face Spaces.

A self-contained retrieval-grounded FAQ chatbot demo. Each template comes with
pre-loaded starter FAQ data, and the bot answers using the same three-tier
pipeline (strong / near / decline) that the production system uses.

No MongoDB or external services required — embeddings and vectors are computed
in-memory on startup.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

import chromadb
import gradio as gr
from sentence_transformers import SentenceTransformer

# ZeroGPU support — available on HF Spaces, graceful fallback locally
try:
    import spaces
except ImportError:
    # Running locally without ZeroGPU — create a no-op decorator
    class _Spaces:
        @staticmethod
        def GPU(fn=None, **kwargs):
            return fn if fn else lambda f: f
    spaces = _Spaces()

# ---------------------------------------------------------------------------
# Embedding model (loaded once at startup)
# ---------------------------------------------------------------------------
print("Loading embedding model…")
_model = SentenceTransformer("all-MiniLM-L6-v2")
print("Model loaded.")


def _embed(text: str) -> list[float]:
    return _model.encode(text, normalize_embeddings=True).tolist()


# ZeroGPU requires at least one @spaces.GPU function to exist in the app, but
# the retrieval path must NOT use it: every decorated call pays a GPU
# allocation round-trip (tens of seconds) and MiniLM embeds a short query on
# CPU in milliseconds. This placeholder exists only to satisfy the detector.
@spaces.GPU(duration=1)
def _zerogpu_placeholder():
    return "ok"


# ---------------------------------------------------------------------------
# Templates — the ten verticals, with demo FAQ data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Template:
    id: str
    name: str
    icon: str
    scope_label: str
    decline_message: str
    near_suffix: str
    strong_threshold: float
    near_threshold: float
    faqs: tuple[tuple[str, str, str], ...]  # (question, alt_phrasings, answer)


_HANDOFF = "Try rephrasing, or contact the team and a person will help you directly."


def _decline(scope: str) -> str:
    return f"I can only answer questions about {scope}. {_HANDOFF}"


def _nudge(who: str) -> str:
    return (
        f"\n\nIf that doesn't fully answer your question, {who} can help you "
        "with the specifics."
    )


TEMPLATES: dict[str, Template] = {}

_raw_templates = [
    Template(
        id="ecommerce", name="E-commerce Support", icon="🛍️",
        scope_label="orders, delivery, returns, refunds and payments",
        decline_message=_decline("orders, delivery, returns, refunds and payments"),
        near_suffix=_nudge("our support team"),
        strong_threshold=0.84, near_threshold=0.60,
        faqs=(
            ("Where is my order?", "track my order; order status; when will it arrive",
             "Open My Orders and tap the order to see live tracking. Tracking usually updates within 24 hours of dispatch."),
            ("How do I return an item?", "start a return; send it back; exchange an item",
             "Go to My Orders, select the item and tap Return. Returns are accepted within 7 days of delivery if the item is unused and in original packaging."),
            ("How long do refunds take?", "when will I get my money; refund time",
             "Refunds are issued once the returned item passes inspection, and reach your original payment method in 5–7 working days."),
            ("What is the delivery cost?", "shipping fee; delivery charges; free delivery",
             "Standard delivery is free on orders above ₹499. For orders below, a flat ₹49 delivery charge applies. Express delivery costs ₹99 regardless of order value."),
            ("How do I cancel an order?", "cancel order; order cancellation; revoke order",
             "Open My Orders, select the order and choose Cancel Order. If the order has already shipped, you can refuse delivery or request a return instead."),
            ("What payment methods do you accept?", "payment options; upi; credit card",
             "We accept UPI, credit and debit cards, net banking, wallets and cash on delivery. EMI options are available on orders above ₹3,000."),
            ("Can I change my delivery address?", "update address; wrong address",
             "Address changes are possible until the order is dispatched. Go to My Orders → Order Details → Edit Address."),
            ("How do I apply a coupon?", "discount code; promo code; coupon code",
             "Enter the coupon code in the 'Apply Coupon' field on the checkout page. Only one coupon can be used per order."),
        ),
    ),
    Template(
        id="restaurant", name="Restaurant & Food Delivery", icon="🍽️",
        scope_label="our menu, opening hours, delivery areas and bookings",
        decline_message=_decline("our menu, opening hours, delivery areas and bookings"),
        near_suffix=_nudge("the restaurant"),
        strong_threshold=0.84, near_threshold=0.60,
        faqs=(
            ("What are your opening hours?", "when do you open; timings; are you open now",
             "We are open 11:00 AM to 11:00 PM every day, with last orders taken at 10:30 PM."),
            ("Do you have vegetarian options?", "veg food; pure veg; vegan dishes",
             "Yes — the menu has a dedicated vegetarian section, and every dish is marked veg or non-veg. Jain and vegan preparations are available on request."),
            ("How do I book a table?", "reservation; reserve a table; book seats",
             "Tables can be reserved from the Reservations page on our website, or by calling the outlet directly. We hold reserved tables for 15 minutes."),
            ("Do you deliver to my area?", "delivery zones; delivery radius; home delivery",
             "We deliver within a 10 km radius of the outlet. Enter your pin code on the order page to check availability."),
            ("What is the minimum order for delivery?", "minimum order; min order amount",
             "The minimum order for free delivery is ₹300. Below that, a ₹30 delivery charge applies."),
            ("Do you cater for events?", "catering; party orders; bulk orders",
             "Yes, we offer catering for events of 20 or more guests. Contact us at least 48 hours in advance with the guest count and menu preferences."),
        ),
    ),
    Template(
        id="clinic", name="Clinic & Healthcare", icon="🩺",
        scope_label="clinic timings, services, appointments, fees and insurance",
        decline_message="I can only answer questions about our clinic's timings, services, appointments, fees and insurance. I can't give medical advice — please speak to a doctor or call the clinic directly.",
        near_suffix="\n\nFor anything about your own health or treatment, please speak to a doctor at the clinic.",
        strong_threshold=0.90, near_threshold=0.72,
        faqs=(
            ("What are your consultation timings?", "clinic hours; when is the doctor available; opd timing",
             "OPD consultations run 9:00 AM to 1:00 PM and 5:00 PM to 8:00 PM, Monday to Saturday. The clinic is closed on Sundays and public holidays."),
            ("How do I book an appointment?", "take appointment; schedule a visit; booking",
             "Appointments can be booked from the Appointments page on our website or by calling the front desk. Walk-ins are seen subject to availability."),
            ("Do I need to fast before a blood test?", "fasting for blood test; empty stomach test",
             "Fasting requirements depend on the specific test. Your test instructions are printed on the requisition slip — please follow those, and call the lab if anything is unclear."),
            ("Do you accept my insurance?", "insurance accepted; cashless; empanelled hospitals",
             "We accept cashless claims from most major insurers. Please bring your insurance card and a valid ID when you visit."),
            ("What is the consultation fee?", "doctor fee; opd charges; visit cost",
             "General consultation costs ₹500, and specialist consultations cost ₹800–₹1,200 depending on the department."),
        ),
    ),
    Template(
        id="education", name="Coaching & EdTech", icon="🎓",
        scope_label="courses, batches, fees, admissions and certificates",
        decline_message=_decline("our courses, batches, fees, admissions and certificates"),
        near_suffix=_nudge("the admissions desk"),
        strong_threshold=0.85, near_threshold=0.62,
        faqs=(
            ("When does the next batch start?", "batch timing; new batch; when do classes begin",
             "New batches begin on the first Monday of every month. Weekday batches run 6:00–8:00 PM and weekend batches 10:00 AM–1:00 PM."),
            ("Is there a demo class?", "trial class; free class; sample lecture",
             "Yes — one free demo class is available for every course. Register from the course page and you will receive the joining link by email."),
            ("Do I get a certificate?", "certification; completion certificate",
             "A completion certificate is issued after you finish the coursework and score at least 60% in the final assessment."),
            ("What is the fee for this course?", "course price; tuition; fee structure",
             "Fees vary by course and duration. The full fee structure is listed on each course page, and EMI options are available for courses above ₹10,000."),
            ("Can I get a refund if I drop out?", "refund policy; cancel enrollment",
             "Refunds are available within 7 days of enrollment if you've attended fewer than two classes. After that, no refund is issued."),
        ),
    ),
    Template(
        id="saas", name="SaaS & App Support", icon="💻",
        scope_label="features, plans, billing, integrations and setup",
        decline_message=_decline("our product's features, plans, billing, integrations and setup"),
        near_suffix=_nudge("our support team"),
        strong_threshold=0.85, near_threshold=0.60,
        faqs=(
            ("How do I reset my password?", "forgot password; change password; cannot log in",
             "Use Forgot Password on the sign-in screen. A reset link valid for 30 minutes is sent to your registered email."),
            ("What is included in the free plan?", "free tier limits; trial features",
             "The free plan includes one project, up to three team members and 500 API calls per month. Paid plans lift all three limits."),
            ("Where can I download my invoice?", "get invoice; billing receipt; gst invoice",
             "Invoices are under Settings → Billing → Invoices, and can be downloaded as PDF for any past billing period."),
            ("How do I connect it to Slack?", "slack integration; integrate with slack",
             "Go to Settings → Integrations → Slack and click Connect. You'll be asked to authorize the app in your Slack workspace."),
            ("What happens when my trial ends?", "trial expiry; trial period; after trial",
             "When your 14-day trial ends, your account switches to the free plan. Your data is preserved, but usage limits apply."),
        ),
    ),
    Template(
        id="travel", name="Travel & Hotel Booking", icon="✈️",
        scope_label="bookings, check-in, cancellation, packages and amenities",
        decline_message=_decline("our bookings, check-in, cancellation, packages and amenities"),
        near_suffix=_nudge("our reservations desk"),
        strong_threshold=0.85, near_threshold=0.62,
        faqs=(
            ("What is your check-in time?", "checkin; when can I check in; early check in",
             "Check-in is from 2:00 PM and check-out is by 11:00 AM. Early check-in is subject to availability on the day."),
            ("What is the cancellation policy?", "cancel booking; refund on cancellation",
             "Cancellations made more than 48 hours before check-in are fully refundable. Within 48 hours, one night's tariff is charged."),
            ("Is breakfast included?", "complimentary breakfast; meal plan",
             "Breakfast is included with all room bookings. The buffet is served 7:00–10:30 AM in the ground-floor restaurant."),
            ("Do you have airport pickup?", "airport transfer; pick up from airport",
             "Airport pickup can be arranged at ₹1,200 per trip. Please share your flight details at least 24 hours in advance."),
            ("Do you allow pets?", "pet friendly; bring dog; pet policy",
             "We are pet-friendly for dogs under 15 kg. A pet deposit of ₹2,000 is required at check-in and refunded on check-out."),
        ),
    ),
    Template(
        id="banking", name="Banking & Fintech", icon="🏦",
        scope_label="charges, KYC, limits, statements and account services",
        decline_message="I can only answer questions about our charges, KYC, limits, statements and account services. I cannot advise on specific investments or approve any transactions — please contact a branch or call our helpline.",
        near_suffix="\n\nFor anything about a specific transaction or account action, please contact the branch or call our helpline.",
        strong_threshold=0.90, near_threshold=0.72,
        faqs=(
            ("What are the charges for an NEFT transfer?", "neft fee; transfer charges",
             "NEFT transfers up to ₹10 lakh are free through internet and mobile banking. Branch transfers carry a ₹2–₹25 fee depending on the amount."),
            ("What documents are needed for KYC?", "kyc documents; identity proof; address proof",
             "KYC requires one photo ID (Aadhaar, PAN, passport or voter ID) and one address proof (Aadhaar, utility bill or bank statement not older than 3 months)."),
            ("What is the daily UPI transfer limit?", "upi limit; maximum upi",
             "The daily UPI transfer limit is ₹1 lakh per bank account, across all UPI apps. Individual transactions are capped at ₹1 lakh."),
            ("How do I download my account statement?", "bank statement; transaction history",
             "Log in to internet banking → Accounts → Statement → select the date range and format (PDF or CSV) → Download."),
            ("How do I report a lost debit card?", "block card; lost card; stolen card",
             "Call our 24/7 helpline or use the mobile app → Cards → Block Card to instantly block it. A replacement card will be dispatched within 5 working days."),
        ),
    ),
    Template(
        id="logistics", name="Logistics & Courier", icon="📦",
        scope_label="tracking, delivery, packaging and claims",
        decline_message=_decline("tracking, delivery, packaging and claims"),
        near_suffix=_nudge("our support team"),
        strong_threshold=0.85, near_threshold=0.60,
        faqs=(
            ("How do I track my shipment?", "tracking; where is my parcel; delivery status",
             "Enter your tracking number on the Track page of our website or app. You'll see live status and an estimated delivery date."),
            ("What is the delivery time?", "how long does delivery take; eta; estimated delivery",
             "Standard delivery takes 3–5 business days. Express delivery takes 1–2 business days and is available for metro areas."),
            ("What if my package is damaged?", "damaged shipment; broken item; damage claim",
             "Report damage within 48 hours of delivery through the app with photos of the packaging and item. We process claims within 5 working days."),
            ("What items are not allowed for shipping?", "prohibited items; restricted goods; cannot ship",
             "Hazardous materials, flammable liquids, live animals, perishable food and items worth over ₹50,000 without declared value are not accepted."),
            ("How do I schedule a pickup?", "pickup; collect parcel; schedule collection",
             "Schedule a pickup from the app → Ship → Schedule Pickup. Pickups are available between 10:00 AM and 6:00 PM, Monday to Saturday."),
        ),
    ),
    Template(
        id="salon", name="Salon, Spa & Local Services", icon="💇",
        scope_label="services, pricing, timings and bookings",
        decline_message=_decline("our services, pricing, timings and bookings"),
        near_suffix=_nudge("the salon"),
        strong_threshold=0.84, near_threshold=0.58,
        faqs=(
            ("What are your timings?", "opening hours; salon hours; when do you open",
             "We are open 10:00 AM to 8:00 PM, Tuesday to Sunday. Closed on Mondays."),
            ("How do I book an appointment?", "appointment; book a slot; schedule visit",
             "Book from our website, call us, or walk in. Appointments are recommended for weekends to avoid waiting."),
            ("Do you offer bridal packages?", "bridal makeup; wedding package; bridal services",
             "Yes — our bridal packages include a trial session, day-of makeup and hairstyling. Book at least two weeks in advance."),
            ("What is the price for a haircut?", "haircut cost; hair cutting charge",
             "Haircuts start at ₹300 for men and ₹500 for women. Premium stylist cuts are ₹800–₹1,200."),
            ("Do you use products I can trust?", "products used; brands; organic products",
             "We use salon-grade products from L'Oréal Professional, Schwarzkopf and Kérastase. Organic and vegan options are available on request."),
        ),
    ),
    Template(
        id="realestate", name="Real Estate & Property", icon="🏢",
        scope_label="properties, pricing, site visits, payment plans and documentation",
        decline_message=_decline("our properties, pricing, site visits, payment plans and documentation"),
        near_suffix=_nudge("our sales team"),
        strong_threshold=0.87, near_threshold=0.65,
        faqs=(
            ("How do I book a site visit?", "site visit; visit the property; see the flat",
             "Site visits run daily between 10:00 AM and 6:00 PM. Request a slot from the project page and our sales team will confirm the time with you."),
            ("What is the possession date?", "when is handover; ready to move; completion date",
             "The committed possession date for each tower is listed on its project page and in the allotment letter."),
            ("Which documents do I need to book a unit?", "paperwork; documents required; kyc for booking",
             "Booking requires PAN, Aadhaar, recent photographs and address proof for every applicant, plus the booking amount receipt."),
            ("What is the price per square foot?", "price; rate; cost per sqft",
             "Prices vary by project, floor and facing. Current rates are listed on the project page, and our sales team can share a detailed cost sheet."),
            ("Which banks have approved this project?", "bank loan; home loan; approved banks",
             "All major banks and housing finance companies have approved this project. Pre-approved home loan assistance is available at the site office."),
        ),
    ),
]

for t in _raw_templates:
    TEMPLATES[t.id] = t


# ---------------------------------------------------------------------------
# Guardrails — injection + action-intent detection
# ---------------------------------------------------------------------------

_INJECTION_RE = re.compile(
    "|".join(f"(?:{p})" for p in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(the\s+)?(above|previous|prior)",
        r"forget\s+(all\s+)?(your\s+)?instructions",
        r"you\s+are\s+now", r"act\s+as\b",
        r"pretend\s+(to\s+be|you\s+are)",
        r"system\s*prompt", r"reveal\s+(your\s+)?instructions",
        r"override\s+(your\s+)?rules", r"jailbreak", r"DAN\s+mode",
    ]),
    re.IGNORECASE,
)

_ACTION_RE = re.compile(
    "|".join(f"(?:{p})" for p in [
        r"\b(refund|reimburse)\s+(me|my|the|this)\b",
        r"\b(cancel|delete|remove)\s+(my|the|this)\s+(booking|order|account|subscription)",
        r"\bchange\s+(my|the)\s+(phone|number|email|password|address|name|account)",
        r"\bdo\s+(this|it|that)\s+for\s+me\b",
        r"\bprocess\s+(my|the|a)\s+(refund|cancellation|change|update)",
        r"\bplease\s+(refund|cancel|delete|change|update|modify)\b",
        r"\bblock\s+(my|the|this)\s+(account|card)\b",
    ]),
    re.IGNORECASE,
)

_INSTRUCTIONAL_RE = re.compile(
    r"^\s*(?:how\s+(?:do|can|should|would)\s+I|how\s+to|what\s+happens?\s+if)\b",
    re.IGNORECASE,
)


def _detect_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text))


def _detect_action(text: str) -> bool:
    if _INSTRUCTIONAL_RE.match(text):
        return False
    return bool(_ACTION_RE.search(text))


# ---------------------------------------------------------------------------
# Vector store — one ChromaDB collection per template, in-memory
# ---------------------------------------------------------------------------

_chroma = chromadb.Client()  # ephemeral, in-memory
_collections: dict[str, chromadb.Collection] = {}


def _init_collections():
    """Index every template's FAQ data into its own ChromaDB collection."""
    for tid, tmpl in TEMPLATES.items():
        col = _chroma.get_or_create_collection(
            name=f"demo_{tid}",
            metadata={"hnsw:space": "cosine"},
        )
        docs, embeddings, ids, metas = [], [], [], []
        idx = 0
        for question, alts_str, answer in tmpl.faqs:
            # Index the canonical question
            docs.append(answer)
            embeddings.append(_embed(question))
            ids.append(f"{tid}_{idx}")
            metas.append({"question": question})
            idx += 1
            # Index each alternate phrasing as its own vector
            for alt in alts_str.split(";"):
                alt = alt.strip()
                if alt:
                    docs.append(answer)
                    embeddings.append(_embed(alt))
                    ids.append(f"{tid}_{idx}")
                    metas.append({"question": question})
                    idx += 1
        col.add(documents=docs, embeddings=embeddings, ids=ids, metadatas=metas)
        _collections[tid] = col


print("Indexing FAQ data…")
_init_collections()
print("Ready.")


# ---------------------------------------------------------------------------
# The answer pipeline — retrieve and route on confidence
# ---------------------------------------------------------------------------

def _ask(template_id: str, query: str) -> tuple[str, str, float]:
    """
    Run the retrieval pipeline for a query.

    Returns (answer, mode, confidence).
    """
    tmpl = TEMPLATES[template_id]
    col = _collections[template_id]

    # Guardrails
    if _detect_injection(query):
        return tmpl.decline_message, "decline (injection detected)", 0.0

    if _detect_action(query):
        return (
            tmpl.decline_message,
            "decline (action request — the bot explains, it never *does*)",
            0.0,
        )

    # Embed and retrieve
    q_emb = _embed(query)
    results = col.query(query_embeddings=[q_emb], n_results=3)

    if not results["documents"] or not results["documents"][0]:
        return tmpl.decline_message, "decline", 0.0

    # ChromaDB cosine distance: 0 = identical, 2 = opposite
    # Convert to similarity: 1 - (distance / 2)
    distance = results["distances"][0][0]
    confidence = 1.0 - (distance / 2.0)
    best_answer = results["documents"][0][0]
    matched_q = results["metadatas"][0][0].get("question", "")

    if confidence >= tmpl.strong_threshold:
        return best_answer, "strong ✅", confidence
    elif confidence >= tmpl.near_threshold:
        return best_answer + tmpl.near_suffix, "near ⚠️", confidence
    else:
        return tmpl.decline_message, "decline ❌", confidence


# ---------------------------------------------------------------------------
# Gradio interface
# ---------------------------------------------------------------------------

# Current template state per session (using Gradio state)

def chat_fn(message: str, history: list, template_id: str):
    """Handle a chat message."""
    if not template_id:
        return "Please select a template first."

    answer, mode, confidence = _ask(template_id, message)

    # Build a rich response with metadata
    conf_bar = "█" * int(confidence * 20) + "░" * (20 - int(confidence * 20))
    meta = f"\n\n---\n*`{mode}` · confidence `{conf_bar}` {confidence:.0%}*"

    return answer + meta


def get_template_info(template_id: str) -> str:
    """Return info about the selected template."""
    if not template_id:
        return "Select a template to get started."

    tmpl = TEMPLATES.get(template_id)
    if not tmpl:
        return "Template not found."

    faq_list = "\n".join(f"  • {q}" for q, _, _ in tmpl.faqs)
    return (
        f"## {tmpl.icon} {tmpl.name}\n\n"
        f"**Scope:** {tmpl.scope_label}\n\n"
        f"**Thresholds:** strong ≥ {tmpl.strong_threshold:.0%}, "
        f"near ≥ {tmpl.near_threshold:.0%}\n\n"
        f"**Loaded FAQs:**\n{faq_list}\n\n"
        f"*Try asking any of these questions in your own words — "
        f"or ask something off-topic to see the decline.*"
    )


# Template choices for dropdown
_choices = [(f"{t.icon} {t.name}", t.id) for t in TEMPLATES.values()]


# Build the Gradio app
with gr.Blocks(
    title="Nexora AI",
    theme=gr.themes.Soft(
        primary_hue="indigo",
        secondary_hue="purple",
        neutral_hue="slate",
    ),
    css="""
    .main-header { text-align: center; margin-bottom: 0.5em; }
    .main-header h1 { 
        font-size: 2.5em; 
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 800;
    }
    .main-header p { color: #94a3b8; font-size: 1.1em; }
    .info-box { 
        border: 1px solid #334155; 
        border-radius: 12px; 
        padding: 1.2em; 
        background: rgba(30, 41, 59, 0.5);
    }
    footer { display: none !important; }
    """,
) as demo:

    gr.HTML("""
    <div class="main-header">
        <h1>🤖 Nexora AI</h1>
        <p>Retrieval-grounded FAQ chatbots — the bot cannot state a fact the customer didn't write down.</p>
    </div>
    """)

    with gr.Row():
        with gr.Column(scale=1):
            template_dd = gr.Dropdown(
                choices=_choices,
                value="ecommerce",
                label="🎯 Select Industry Template",
                info="Each template has its own scope, thresholds and decline wording.",
            )
            template_info = gr.Markdown(
                value=get_template_info("ecommerce"),
                elem_classes=["info-box"],
            )

            gr.Markdown("""
### How it works

1. **Pick a template** — it decides what the bot may talk about
2. **Ask a question** — try the loaded FAQs or go off-topic
3. **See the pipeline** — each response shows the match mode and confidence

| Mode | Meaning |
|------|---------|
| `strong ✅` | High-confidence verbatim answer |
| `near ⚠️` | Partial match + handoff nudge |
| `decline ❌` | Out of scope — the bot stays silent |

*In the full product, customers upload their own FAQ sheet and get an API key.*
            """)

        with gr.Column(scale=2):
            chatbot = gr.Chatbot(
                height=520,
                label="Chat with the demo bot",
                type="messages",
                avatar_images=(None, "https://em-content.zobj.net/source/twitter/376/robot_1f916.png"),
                show_copy_button=True,
            )
            msg = gr.Textbox(
                placeholder="Ask the bot a question…",
                label="Your message",
                show_label=False,
                scale=4,
            )
            with gr.Row():
                send_btn = gr.Button("Send", variant="primary", scale=1)
                clear_btn = gr.ClearButton([msg, chatbot], value="Clear", scale=1)

    # Wire up events
    template_dd.change(
        fn=get_template_info,
        inputs=template_dd,
        outputs=template_info,
    )

    # Also clear chat when template changes
    template_dd.change(
        fn=lambda: ([], ""),
        inputs=None,
        outputs=[chatbot, msg],
    )

    def respond(message, history, template_id):
        if not message.strip():
            return history, ""
        bot_response = chat_fn(message, history, template_id)
        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": bot_response},
        ]
        return history, ""

    msg.submit(respond, [msg, chatbot, template_dd], [chatbot, msg])
    send_btn.click(respond, [msg, chatbot, template_dd], [chatbot, msg])


demo.launch(ssr_mode=False)
