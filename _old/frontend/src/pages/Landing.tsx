import { Suspense, lazy } from "react";
import { useNavigate } from "react-router-dom";

import { CodeBlock, Page, Tagline } from "../components/Chrome";
import { PricingSection } from "../components/Pricing";
import { TemplatePicker } from "../components/TemplatePicker";
import { EVENTS, track } from "../lib/analytics";

// three.js is ~600kb. It should never block the headline from rendering.
const HeroScene = lazy(() => import("../three/HeroScene"));

// Swap the host for your own once the domain is live.
const REQUEST_SAMPLE = [
  [{ text: "curl -X POST https://api.nexora.app/v1/ask \\" }],
  [{ text: '  -H ' }, { text: '"X-Api-Key: nxk_live_9f3c…"', tone: "str" as const }, { text: " \\" }],
  [{ text: '  -H ' }, { text: '"Content-Type: application/json"', tone: "str" as const }, { text: " \\" }],
  [{ text: "  -d '{" }, { text: '"message"', tone: "key" as const }, { text: ': ' }, { text: '"How do I cancel an order?"', tone: "str" as const }, { text: ", " }, { text: '"session_id"', tone: "key" as const }, { text: ': ' }, { text: '"u_1182"', tone: "str" as const }, { text: "}'" }],
  [],
  [{ text: "# 200 OK", tone: "cmt" as const }],
  [{ text: "{" }],
  [{ text: "  " }, { text: '"response"', tone: "key" as const }, { text: ": " }, { text: '"Open My Orders, select the order and choose Cancel…"', tone: "str" as const }, { text: "," }],
  [{ text: "  " }, { text: '"mode"', tone: "key" as const }, { text: ": " }, { text: '"strong"', tone: "str" as const }, { text: "," }],
  [{ text: "  " }, { text: '"matched_question"', tone: "key" as const }, { text: ": " }, { text: '"How do I cancel an order?"', tone: "str" as const }, { text: "," }],
  [{ text: "  " }, { text: '"confidence"', tone: "key" as const }, { text: ": 0.98" }],
  [{ text: "}" }],
];

/* The strip under the hero.
 *
 * n8n-style landing pages put customer logos here. We do not have customers to
 * name yet, and inventing them is the one thing this product is about not
 * doing — so this names the sectors the templates cover and links to them.
 * Same visual job, nothing claimed that is not true. */
const TRUST = [
  {
    label: "E-commerce",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
        <path d="M3 4h2l2.5 11h10L20 7H6" />
        <circle cx="9" cy="19" r="1.3" />
        <circle cx="17" cy="19" r="1.3" />
      </svg>
    ),
  },
  {
    label: "Healthcare",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
        <path d="M12 5v14M5 12h14" />
      </svg>
    ),
  },
  {
    label: "Fintech",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
        <path d="M3 9l9-5 9 5M5 9v9m14-9v9M3 19h18M9 9v9m6-9v9" />
      </svg>
    ),
  },
  {
    label: "SaaS",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
        <path d="M7 17a4 4 0 010-8 5.5 5.5 0 0110.5 1.5A3.5 3.5 0 0117 17z" />
      </svg>
    ),
  },
  {
    label: "Logistics",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
        <path d="M3 7h11v10H3zM14 10h4l3 3v4h-7" />
        <circle cx="7" cy="18" r="1.4" />
        <circle cx="17.5" cy="18" r="1.4" />
      </svg>
    ),
  },
];

const STEPS = [
  {
    n: "01",
    title: "Upload your sheet",
    body: "One Excel file: question, alternate phrasings, category, and the answer you have already approved. That sheet is the whole knowledge base.",
  },
  {
    n: "02",
    title: "We index it",
    body: "Every question and phrasing is embedded and stored in a vector index scoped to your account. Re-upload any time and it re-indexes in seconds.",
  },
  {
    n: "03",
    title: "Point your app at the API",
    body: "One POST per question with your API key. You get the answer, the question it matched, and a confidence score you can log or act on.",
  },
];

const FAQS = [
  {
    q: "Does it make up answers?",
    a: "No. By default there is no generation step at all: the bot embeds the question, finds the closest entry in your sheet, and returns that answer verbatim. Below the confidence threshold it declines and points the user to a human instead of guessing. You can optionally let a model running on our own servers reword close matches so they read more naturally — even then it only ever rephrases your rows, every answer is checked against them before it is sent, and anything that does not match falls back to your exact wording.",
  },
  {
    q: "What exactly is a credit?",
    a: "One answered question, priced by length: up to 200 characters costs 1 credit, up to 500 costs 2, up to 1000 costs 3, and longer costs 5. Credits reset at midnight IST.",
  },
  {
    q: "Can I create more keys to get more credits?",
    a: "You can create more keys, but no. Credits are pooled on the account, so ten keys and one key have exactly the same daily allowance. Multiple keys are for separating staging from production, or revoking one integration without breaking the others.",
  },
  {
    q: "What happens when I run out?",
    a: "The API returns 402 with the number of credits remaining and the reset time, so your app can fall back to a support form rather than showing an error.",
  },
  {
    q: "Where does my data go?",
    a: "Your FAQ sheet is indexed into a collection scoped to your account and is never mixed with anyone else's. Questions that fail to match are logged so you can see the gaps in your own sheet.",
  },
];

export function Landing() {
  const navigate = useNavigate();

  return (
    <Page>
      {/* ---------------------------------------------------------------- */}
      <section className="hero">
        <div className="wrap">
          <div className="hero-grid">
            <div className="hero-copy fade-up">
              <p className="eyebrow">Nexora · AI chat bot</p>
              <h1 className="h-hero">
                Answers from your sheet.
                <br />
                <span className="line-2">Never invented.</span>
              </h1>

              <div className="mt-5">
                <Tagline />
              </div>

              <div className="hero-actions">
                <button
                  className="btn btn-primary"
                  onClick={() => {
                    // `cta_position` is the whole point of tracking these
                    // separately: two buttons with the same destination
                    // convert very differently, and merging them hides
                    // whether anyone reads as far as the second one.
                    track(EVENTS.LANDING_CTA_CLICK, {
                      cta: "start_trial",
                      cta_position: "hero",
                    });
                    navigate("/signup");
                  }}
                >
                  Start free for 14 days
                </button>
                <a
                  href="#how"
                  className="btn btn-secondary"
                  onClick={() =>
                    track(EVENTS.LANDING_CTA_CLICK, {
                      cta: "how_it_works",
                      cta_position: "hero",
                    })
                  }
                >
                  See how it works
                </a>
              </div>

              <p className="lede mt-6">
                Upload the FAQ you already wrote. Get an API key. Your app starts
                answering support questions in one POST request — using your
                approved wording, or saying nothing at all.
              </p>

              <div className="hero-meta">
                <div className="hero-meta-item">
                  <span className="hero-meta-value">18ms</span>
                  <span className="tiny">typical answer</span>
                </div>
                <div className="hero-meta-item">
                  <span className="hero-meta-value">0</span>
                  <span className="tiny">hallucinated answers</span>
                </div>
                <div className="hero-meta-item">
                  <span className="hero-meta-value">1</span>
                  <span className="tiny">endpoint to integrate</span>
                </div>
              </div>
            </div>

            <div className="hero-canvas">
              <Suspense fallback={null}>
                <HeroScene />
              </Suspense>
            </div>
          </div>

          <div className="trust">
            <p className="trust-label">Grounded FAQ bots for teams in</p>
            <div className="trust-marks">
              {TRUST.map((mark) => (
                <a href="#templates" className="trust-mark" key={mark.label}>
                  {mark.icon}
                  {mark.label}
                </a>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section className="section" id="how">
        <div className="wrap">
          <p className="eyebrow eyebrow-muted">How it works</p>
          <h2 className="h-section">Three steps, then you are done.</h2>

          <div className="grid-3 mt-6">
            {STEPS.map((step) => (
              <article className="card" key={step.n}>
                <div className="step-num">{step.n}</div>
                <h3 className="h-card">{step.title}</h3>
                <p className="small mt-3">{step.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section className="section" id="templates">
        <div className="wrap">
          <p className="eyebrow eyebrow-muted">Templates</p>
          <h2 className="h-section">Ten bots. Pick the one you already are.</h2>
          <p className="lede mt-4">
            A template decides what your bot is allowed to talk about and the
            exact words it uses to refuse everything else. Your sheet fills in
            the facts. Healthcare and finance templates are deliberately
            stricter — they stay quiet unless the match is close.
          </p>

          <div className="mt-6">
            <TemplatePicker readOnly />
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section className="section-tight" id="docs">
        <div className="wrap">
          <div className="grid-2 gap-5" style={{ alignItems: "center" }}>
            <div>
              <p className="eyebrow eyebrow-muted">The integration</p>
              <h2 className="h-section">One endpoint. One header.</h2>
              <p className="lede mt-4">
                Send the question and a session id. You get back the answer, which
                FAQ entry it matched, and how confident the match was — so you can
                log the weak ones and fix your sheet.
              </p>
              <ul className="feature-list mt-5">
                <li>
                  <span>
                    <code className="mono accent">mode: strong</code> — verbatim answer,
                    similarity above 0.85
                  </span>
                </li>
                <li>
                  <span>
                    <code className="mono accent">mode: near</code> — answer plus a nudge
                    to contact support
                  </span>
                </li>
                <li>
                  <span>
                    <code className="mono accent">mode: decline</code> — out of scope, a
                    fixed message, no guessing
                  </span>
                </li>
              </ul>
            </div>
            <CodeBlock title="ask the customer bot" code={REQUEST_SAMPLE} />
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section className="section" id="pricing">
        <div className="wrap">
          <div className="center">
            <p className="eyebrow eyebrow-muted">Pricing</p>
            <h2 className="h-section">One plan. Pick how you pay.</h2>
            <p className="lede wrap-center mt-4" style={{ textAlign: "center" }}>
              No per-seat pricing, no usage cliff you find out about in an invoice.
            </p>
          </div>

          <div className="mt-6">
            <PricingSection
              onChoose={() => navigate("/signup")}
              ctaLabel="Get started"
            />
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section className="section-tight" id="faq">
        <div className="wrap wrap-narrow">
          <p className="eyebrow eyebrow-muted">Questions</p>
          <h2 className="h-section mb-5">Before you sign up.</h2>
          {FAQS.map((faq) => (
            <div className="faq-item" key={faq.q}>
              <h3 className="faq-q">{faq.q}</h3>
              <p className="small">{faq.a}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section className="section-tight">
        <div className="wrap">
          <div className="card center" style={{ padding: "var(--s8) var(--s5)" }}>
            <h2 className="h-section">Put it in front of your users this week.</h2>
            <p className="lede wrap-center mt-4" style={{ textAlign: "center" }}>
              Fourteen days free. No card. If your sheet is ready, the first
              answer is about ten minutes away.
            </p>
            <div className="mt-5">
              <button
                className="btn btn-primary"
                onClick={() => {
                  track(EVENTS.LANDING_CTA_CLICK, {
                    cta: "create_account",
                    cta_position: "footer",
                  });
                  navigate("/signup");
                }}
              >
                Create your account
              </button>
            </div>
          </div>
        </div>
      </section>
    </Page>
  );
}
