/**
 * Account, billing, and dashboard endpoints for the self-serve platform.
 *
 * Port of `backend/billing/router.py`.
 *
 * Auth is a session cookie (httpOnly, SameSite=Lax) rather than a token in
 * localStorage: a stored token is readable by any script that gets injected
 * into the page, and an httpOnly cookie is not. The SPA talks to this API
 * through a same-origin path (Next.js rewrites /api in development), so Lax is
 * sufficient and no CSRF-prone cross-site posting is required.
 *
 * FastAPI's `Depends(current_customer)` becomes {@link requireCustomer}
 * middleware, which attaches the resolved customer to the request. Everything
 * else — statuses, messages, ordering of checks — is unchanged.
 */

import { Router, type Request, type Response } from "express";

import * as config from "../config.js";
import {
  countActiveKeysForEmail,
  generateApiKey,
  getUsageStats,
  getUserByEmail,
  listKeysForEmail,
  revokeKeyForEmail,
} from "../shared/apiKeys.js";
import {
  asyncHandler,
  badRequest,
  clientAddress,
  conflict,
  forbidden,
  notFound,
  parseBody,
  paymentRequired,
  serviceUnavailable,
  tooManyRequests,
  unauthorized,
  HTTPException,
} from "../shared/http.js";
import { logger } from "../shared/logger.js";
import * as rateLimits from "../shared/rateLimits.js";
import type { Doc } from "../shared/mongo.js";

import * as db from "./db.js";
import * as entitlements from "./entitlements.js";
import * as firebaseAuth from "./firebaseAuth.js";
import * as identity from "./identity.js";
import * as referrals from "./referrals.js";
import { getProvider, manualActivationAllowed } from "./payments.js";
import { PLANS, getPlan, listPlans } from "./plans.js";
import {
  CREATED_KEY_MESSAGE,
  checkoutSchema,
  createKeySchema,
  firebaseAuthSchema,
  loginSchema,
  referralInviteSchema,
  signupSchema,
  type CustomerResponse,
  type SubscriptionResponse,
} from "./schemas.js";
import { generateSessionToken, hashPassword, verifyPassword } from "./security.js";

export const router = Router();

export const SESSION_COOKIE = config.SESSION_COOKIE_NAME;
const COOKIE_SECURE =
  (process.env.BILLING_COOKIE_SECURE ?? String(config.COOKIE_SECURE)).toLowerCase() ===
  "true";
const MAX_KEYS_PER_ACCOUNT = Number.parseInt(process.env.MAX_KEYS_PER_ACCOUNT ?? "10", 10);
const PUBLIC_BASE_URL = process.env.PUBLIC_BASE_URL ?? "http://localhost:3000";

export const REFERRAL_COOKIE = "nexora_ref";

// ---------------------------------------------------------------------------
// Login throttling
// ---------------------------------------------------------------------------
// Failed attempts are counted in MongoDB (shared/rateLimits.ts), so the lockout
// survives a restart and every worker enforces the same limit.

const LOGIN_BUCKET = "login_failure";
const THROTTLE_WINDOW_SECONDS = 15 * 60;
const THROTTLE_MAX_ATTEMPTS = 8;

function throttleKey(email: string, req: Request): string {
  return `${email.toLowerCase()}|${clientAddress(req)}`;
}

async function checkThrottle(key: string): Promise<void> {
  const attempts = await rateLimits.count(LOGIN_BUCKET, key, THROTTLE_WINDOW_SECONDS);
  if (attempts >= THROTTLE_MAX_ATTEMPTS) {
    throw tooManyRequests("Too many failed sign-in attempts. Try again in 15 minutes.");
  }
}

async function recordFailure(key: string): Promise<void> {
  await rateLimits.record(LOGIN_BUCKET, key, THROTTLE_WINDOW_SECONDS);
}

// ---------------------------------------------------------------------------
// Signup throttling
// ---------------------------------------------------------------------------
// Canonical email stops one *mailbox* opening many accounts. It cannot stop one
// *person* with several real mailboxes, and the cheap version of that is a
// handful of signups from one machine in one sitting. This caps that, counted
// in MongoDB so the cap holds across restarts and workers.

const SIGNUP_BUCKET = "signup";
const SIGNUP_WINDOW_SECONDS = 24 * 60 * 60;

async function checkSignupRate(req: Request): Promise<void> {
  if (config.MAX_SIGNUPS_PER_IP_PER_DAY <= 0) return; // explicitly disabled

  const ip = clientAddress(req);
  const used = await rateLimits.count(SIGNUP_BUCKET, ip, SIGNUP_WINDOW_SECONDS);
  if (used >= config.MAX_SIGNUPS_PER_IP_PER_DAY) {
    logger.warn(`Signup rate limit hit from ${ip}.`);
    throw tooManyRequests(
      "Too many accounts have been created from this network today. " +
        "If you need another one, contact support.",
    );
  }
}

async function recordSignup(req: Request): Promise<void> {
  await rateLimits.record(SIGNUP_BUCKET, clientAddress(req), SIGNUP_WINDOW_SECONDS);
}

// ---------------------------------------------------------------------------
// Session middleware (FastAPI's `Depends(current_customer)`)
// ---------------------------------------------------------------------------

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      customer?: Doc;
    }
  }
}

/** Resolve the session cookie to a customer, or 401. */
export const requireCustomer = asyncHandler(async (req, _res, next) => {
  const token = req.cookies?.[SESSION_COOKIE] ?? "";
  const customer = await db.getSessionCustomer(token);
  if (customer === null) {
    throw unauthorized("Not signed in.");
  }
  req.customer = customer;
  next();
});

/** The customer resolved by {@link requireCustomer}. */
export function customerOf(req: Request): Doc {
  if (!req.customer) {
    throw new Error("No customer on this request — requireCustomer is missing.");
  }
  return req.customer;
}

function setSessionCookie(res: Response, token: string): void {
  res.cookie(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: COOKIE_SECURE,
    maxAge: db.SESSION_TTL_DAYS * 24 * 3600 * 1000, // Express takes milliseconds
    path: "/",
  });
}

function customerPublic(customer: Doc): CustomerResponse {
  return {
    id: customer.id,
    email: customer.email,
    name: customer.name,
    created_at: customer.created_at,
  };
}

async function subscriptionView(customerId: string): Promise<SubscriptionResponse> {
  await entitlements.sync();
  const sub = await db.getCurrentSubscription(customerId);
  const entitled = db.isEntitled(sub);

  if (sub === null) {
    return { status: "none", is_entitled: false, can_start_trial: true };
  }

  const plan = getPlan(sub.plan_id);
  return {
    plan_id: sub.plan_id,
    plan_name: plan ? plan.name : sub.plan_id,
    status: sub.status,
    is_entitled: entitled,
    current_period_end: sub.current_period_end,
    daily_credits: plan ? plan.daily_credits : null,
    can_start_trial: !(await db.hasUsedTrial(customerId)) && !entitled,
  };
}

// Grant/revoke live in `entitlements.ts` — see that module for why.

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

/**
 * Create an account and start the free trial.
 *
 * One account per *mailbox*, not per string: `you+1@gmail.com` and
 * `y.o.u@gmail.com` are the address that already signed up, and are refused as
 * such. See `identity.ts` for why that is worth doing.
 */
router.post(
  "/auth/signup",
  asyncHandler(async (req, res) => {
    const body = parseBody(signupSchema, req.body);
    const email = body.email.toLowerCase().trim();

    try {
      identity.screenSignupEmail(email);
    } catch (error) {
      if (error instanceof identity.EmailPolicyError) throw badRequest(error.message);
      throw error;
    }

    // Deliberately the same message whichever alias they tried, and phrased so
    // it reads as "you already have one", not "we saw through your alias".
    if ((await db.getCustomerForMailbox(email)) !== null) {
      throw conflict("An account with that email already exists.");
    }

    await checkSignupRate(req);

    // PBKDF2 at 600k iterations is deliberately slow. Python moved it to a
    // thread; Node's crypto.pbkdf2 is already async and runs on the libuv
    // threadpool, so the event loop is free either way.
    const pwHash = await hashPassword(body.password);

    let customer: Doc;
    try {
      customer = await db.createCustomer(email, body.name, pwHash);
    } catch (error) {
      if (error instanceof db.DuplicateAccountError) {
        // Two requests raced past the check above; the index caught it.
        throw conflict(error.message);
      }
      throw error;
    }

    await recordSignup(req);

    // Everyone starts on the trial — no card, no checkout.
    const trial = PLANS.trial;
    await db.startSubscription(customer.id, trial, db.STATUS_TRIALING, "none");
    await entitlements.grant(email, body.name, trial.daily_credits);

    // After the allowance is granted, so the bonus credits land on an account
    // that exists. A bad code is not an error: the signup still succeeds.
    await referrals.attach(customer, body.referral_code);

    const token = generateSessionToken();
    await db.createSession(customer.id, token);
    setSessionCookie(res, token);

    res.status(201).json(customerPublic(customer));
  }),
);

/**
 * Which sign-in methods this server actually supports.
 *
 * Public, and deliberately so: the sign-in page needs it before anyone is
 * authenticated. It leaks nothing — whether a Google button exists is visible
 * from the button.
 */
router.get(
  "/auth/providers",
  asyncHandler(async (_req, res) => {
    res.json({ password: true, firebase: firebaseAuth.isConfigured() });
  }),
);

// ---------------------------------------------------------------------------
// Firebase sign-in
// ---------------------------------------------------------------------------
// Firebase runs the sign-in; this endpoint is where its result becomes a
// session on this API. One exchange, one cookie, and from then on the request
// is indistinguishable from a password login. See `firebaseAuth.ts` for why the
// ID token is not simply trusted per-request.

/**
 * Exchange a verified Firebase ID token for a session cookie.
 *
 * Handles both Firebase sign-in methods — Google and email+password — the same
 * way, because by the time the token is verified the difference no longer
 * matters: Firebase has said who this is, and what is left is finding or
 * creating the account that belongs to them.
 */
router.post(
  "/auth/firebase",
  asyncHandler(async (req, res) => {
    const body = parseBody(firebaseAuthSchema, req.body);

    if (!firebaseAuth.isConfigured()) {
      throw serviceUnavailable("Firebase sign-in is not configured on this server.");
    }

    let fbIdentity: firebaseAuth.FirebaseIdentity;
    try {
      const claims = await firebaseAuth.verifyIdToken(body.id_token);
      fbIdentity = firebaseAuth.identityFromClaims(claims);
    } catch (error) {
      if (error instanceof firebaseAuth.EmailNotVerifiedError) {
        // 403, not 401: the credential is good, the mailbox is the problem. The
        // page uses this to offer "resend verification email" instead of
        // sending the user back to a form that will keep succeeding.
        throw forbidden(error.message);
      }
      if (error instanceof firebaseAuth.FirebaseAuthError) {
        throw unauthorized(error.message);
      }
      throw error;
    }

    // A brand-new account here is a signup, and signups are rate limited per IP
    // for the same reason the password path limits them: distinct real
    // mailboxes, one person, one afternoon. Checked before creation, and only
    // when this really is a new account.
    const isNew =
      (await db.getCustomerByFirebaseUid(fbIdentity.firebase_uid)) === null &&
      (await db.getCustomerForMailbox(fbIdentity.email)) === null &&
      (fbIdentity.google_sub === null ||
        (await db.getCustomerByGoogleSub(fbIdentity.google_sub)) === null);

    if (isNew) {
      try {
        identity.screenSignupEmail(fbIdentity.email);
      } catch (error) {
        if (error instanceof identity.EmailPolicyError) throw badRequest(error.message);
        throw error;
      }
      await checkSignupRate(req);
    }

    const customer = await resolveFirebaseCustomer(
      fbIdentity,
      body.referral_code || (req.cookies?.[REFERRAL_COOKIE] ?? ""),
    );

    if (customer === null) {
      throw conflict(
        "That email already has an account signed in a different way. " +
          "Sign in with your password instead.",
      );
    }

    if (!customer.is_active) {
      throw forbidden("This account has been disabled.");
    }

    if (isNew) await recordSignup(req);

    const token = generateSessionToken();
    await db.createSession(customer.id, token);
    setSessionCookie(res, token);
    res.clearCookie(REFERRAL_COOKIE, { path: "/" });

    res.json(customerPublic(customer));
  }),
);

/**
 * Find or create the account for a verified Firebase identity.
 *
 * Four cases, in this order — each one narrower than the last:
 *
 * 1. **Known Firebase uid** — the same Firebase user as last time. Email may
 *    have changed since; the uid is what does not.
 * 2. **Known Google `sub`** — this person signed up through the older
 *    server-side OAuth flow and is now arriving via Firebase Google. Same
 *    human, same Google account, different wrapper around it. Linked, not
 *    duplicated. Without this case they would collide on the mailbox and be
 *    turned away from their own account.
 * 3. **Known email** — an existing account, linked now. Only reachable because
 *    the unverified case was already refused: somebody proved they control that
 *    mailbox, so it is the same person. Their password, if they have one, keeps
 *    working.
 * 4. **Neither** — a new account, with no local password, trial started.
 */
async function resolveFirebaseCustomer(
  fbIdentity: firebaseAuth.FirebaseIdentity,
  referralCode = "",
): Promise<Doc | null> {
  const existing = await db.getCustomerByFirebaseUid(fbIdentity.firebase_uid);
  if (existing !== null) return existing;

  const googleSub = fbIdentity.google_sub;
  if (googleSub) {
    const byGoogle = await db.getCustomerByGoogleSub(googleSub);
    if (byGoogle !== null) {
      return db.linkFirebaseAccount(
        byGoogle.id,
        fbIdentity.firebase_uid,
        fbIdentity.sign_in_provider,
      );
    }
  }

  // Matched on the mailbox, so an account registered as "you@gmail.com" is
  // found even when the token reports "y.o.u@gmail.com".
  const byEmail = await db.getCustomerForMailbox(fbIdentity.email);
  if (byEmail !== null) {
    return db.linkFirebaseAccount(
      byEmail.id,
      fbIdentity.firebase_uid,
      fbIdentity.sign_in_provider,
    );
  }

  let customer: Doc;
  try {
    customer = await db.createCustomer(fbIdentity.email, fbIdentity.name, db.NO_PASSWORD, {
      googleSub,
      firebaseUid: fbIdentity.firebase_uid,
      firebaseProvider: fbIdentity.sign_in_provider,
      authProvider: db.PROVIDER_FIREBASE,
    });
  } catch (error) {
    if (error instanceof db.DuplicateAccountError) {
      // The mailbox was taken between the lookup above and this insert.
      // Refusing is right: linking on a losing race is how one person ends up
      // inside another's account.
      return null;
    }
    throw error;
  }

  await startTrial(customer, fbIdentity.name, referralCode);
  return customer;
}

// ---------------------------------------------------------------------------
// Shared sign-up helpers
// ---------------------------------------------------------------------------

/** Everyone starts on the trial, however they signed up. */
async function startTrial(customer: Doc, name: string, referralCode = ""): Promise<void> {
  const trial = PLANS.trial;
  await db.startSubscription(customer.id, trial, db.STATUS_TRIALING, "none");
  await entitlements.grant(customer.email, name, trial.daily_credits);
  await referrals.attach(customer, referralCode);
}

/** Exchange email + password for a session cookie. */
router.post(
  "/auth/login",
  asyncHandler(async (req, res) => {
    const body = parseBody(loginSchema, req.body);
    const email = body.email.toLowerCase().trim();
    const key = throttleKey(email, req);
    await checkThrottle(key);

    const customer = await db.getCustomerByEmail(email);

    // Same error and roughly the same work either way, so this doesn't become
    // an oracle for which emails are registered.
    const pwOk =
      customer !== null ? await verifyPassword(body.password, customer.password_hash) : false;

    if (customer === null || !pwOk) {
      await recordFailure(key);
      throw unauthorized("Incorrect email or password.");
    }

    if (!customer.is_active) {
      throw forbidden("This account has been disabled.");
    }

    const token = generateSessionToken();
    await db.createSession(customer.id, token);
    setSessionCookie(res, token);
    await rateLimits.clear(LOGIN_BUCKET, key);

    res.json(customerPublic(customer));
  }),
);

/** Revoke the current session server-side and clear the cookie. */
router.post(
  "/auth/logout",
  asyncHandler(async (req, res) => {
    const token = req.cookies?.[SESSION_COOKIE] ?? "";

    if (token) {
      // Resolved before the session is revoked, because afterwards there is no
      // way back from the cookie to the account.
      const customer = await db.getSessionCustomer(token);
      await db.revokeSession(token);

      // The browser also clears its own Firebase session, but that is the
      // browser's word for it. Revoking the refresh tokens server-side means a
      // page that kept one cannot quietly mint a fresh ID token and open a new
      // session the user believes they closed. Best effort by design — see
      // firebaseAuth.revokeRefreshTokens.
      if (customer?.firebase_uid) {
        await firebaseAuth.revokeRefreshTokens(customer.firebase_uid);
      }
    }

    res.clearCookie(SESSION_COOKIE, { path: "/" });
    res.json({ status: "signed out" });
  }),
);

router.get(
  "/auth/me",
  requireCustomer,
  asyncHandler(async (req, res) => {
    res.json(customerPublic(customerOf(req)));
  }),
);

// ---------------------------------------------------------------------------
// Billing
// ---------------------------------------------------------------------------

/** Public pricing. The pricing page renders exactly this — no hardcoded prices. */
router.get(
  "/billing/pricing",
  asyncHandler(async (_req, res) => {
    res.json(listPlans());
  }),
);

router.get(
  "/billing/subscription",
  requireCustomer,
  asyncHandler(async (req, res) => {
    res.json(await subscriptionView(customerOf(req).id));
  }),
);

/**
 * Start a hosted checkout for a paid plan.
 *
 * We create the session with the provider and hand back their URL — the
 * customer enters payment details on the provider's page, never ours.
 */
router.post(
  "/billing/checkout",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const body = parseBody(checkoutSchema, req.body);
    const customer = customerOf(req);

    const plan = getPlan(body.plan_id);
    if (plan === null || plan.price_cents === 0) {
      throw badRequest("Unknown plan.");
    }

    const provider = getProvider();
    let session;
    try {
      session = provider.createCheckout({
        customer,
        plan,
        successUrl: `${PUBLIC_BASE_URL}/checkout/return`,
        cancelUrl: `${PUBLIC_BASE_URL}/pricing`,
      });
    } catch (error) {
      // The unwired providers throw to say so, rather than silently granting
      // access. 503 is the honest status: the feature exists but is not
      // available on this deployment.
      if (error instanceof Error && !(error instanceof HTTPException)) {
        throw serviceUnavailable(error.message);
      }
      throw error;
    }

    const sub = await db.startSubscription(
      customer.id,
      plan,
      db.STATUS_PENDING,
      session.provider,
      session.reference,
    );
    await db.createInvoice(
      customer.id,
      sub.id,
      plan,
      process.env.BILLING_CURRENCY ?? "USD",
      "open",
      session.provider,
      session.reference,
    );

    res.json({
      checkout_url: session.url,
      reference: session.reference,
      provider: session.provider,
      requires_manual_confirmation: session.requires_manual_confirmation,
      message: session.requires_manual_confirmation
        ? "Development checkout — no payment is taken and the plan stays " +
          "pending until it is confirmed."
        : "Redirecting to secure checkout.",
    });
  }),
);

/**
 * Activate a pending subscription **without payment** — development only.
 *
 * Refused unless BILLING_PROVIDER=manual *and* BILLING_ALLOW_MANUAL=true, so it
 * cannot become a free-access hole in production. Real activation happens in
 * the provider webhook.
 */
router.post(
  "/billing/confirm",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const customer = customerOf(req);

    if (!manualActivationAllowed()) {
      throw forbidden(
        "Manual activation is disabled. Payments are confirmed by the provider webhook.",
      );
    }

    const sub = await db.getCurrentSubscription(customer.id);
    if (sub === null || sub.status !== db.STATUS_PENDING) {
      throw badRequest("No pending subscription to confirm.");
    }

    const plan = getPlan(sub.plan_id);
    if (plan === null) {
      throw badRequest("Unknown plan on subscription.");
    }

    await db.startSubscription(
      customer.id,
      plan,
      db.STATUS_ACTIVE,
      sub.provider,
      sub.provider_ref,
    );
    await db.setSubscriptionStatus(sub.id, db.STATUS_EXPIRED); // supersede the pending row
    await entitlements.grant(customer.email, customer.name, plan.daily_credits);

    // Checkout opened an invoice; activation is what settles it. Leaving it
    // open made "how much have we billed?" unanswerable and left the referral
    // payout with no payment to key off.
    const paidInvoices = await db.markOpenInvoicesPaid(customer.id, sub.id);
    await referrals.rewardPayment(customer.id, paidInvoices);

    logger.warn(
      `Manual (unpaid) activation of ${plan.id} for customer_id=${customer.id} — dev mode only.`,
    );

    res.json(await subscriptionView(customer.id));
  }),
);

/**
 * Cancel the subscription. Access continues until the paid period ends — they
 * paid for it, so we don't cut it short.
 */
router.post(
  "/billing/cancel",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const customer = customerOf(req);

    const sub = await db.getCurrentSubscription(customer.id);
    if (sub === null || !db.isEntitled(sub)) {
      throw badRequest("No active subscription to cancel.");
    }

    await db.setSubscriptionStatus(sub.id, db.STATUS_CANCELED);
    res.json(await subscriptionView(customer.id));
  }),
);

router.get(
  "/billing/invoices",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const invoices = await db.listInvoices(customerOf(req).id);
    res.json(
      invoices.map((inv) => ({
        id: inv.id,
        plan_id: inv.plan_id,
        amount_cents: inv.amount_cents,
        currency: inv.currency,
        status: inv.status,
        issued_at: inv.issued_at,
        paid_at: inv.paid_at ?? null,
      })),
    );
  }),
);

/**
 * Provider payment callback.
 *
 * Deliberately inert until a provider is wired up: an unverified webhook is an
 * unauthenticated "make me a paying customer" endpoint. Before handling any
 * event here, verify the provider's signature header against the webhook secret
 * and reject anything that fails. `server.ts` already captures the raw body on
 * this path, which is what a signature check needs.
 *
 * When you do wire one up, a successful payment must do the same three things
 * `/billing/confirm` does — activate the subscription, grant the entitlement,
 * then:
 *
 *     const paid = await db.markOpenInvoicesPaid(customerId, subscriptionId);
 *     await referrals.rewardPayment(customerId, paid);
 *
 * Both are safe to call twice: the reward is keyed on the invoice, and
 * providers deliver webhooks more than once as a matter of course.
 */
router.post(
  "/billing/webhook",
  asyncHandler(async (_req, _res) => {
    logger.info("Ignoring unverified billing webhook — no provider configured.");
    throw new HTTPException(501, "No payment provider is configured for webhooks yet.");
  }),
);

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

async function requireEntitlement(customer: Doc): Promise<void> {
  await entitlements.sync();
  const sub = await db.getCurrentSubscription(customer.id);
  if (!db.isEntitled(sub)) {
    throw paymentRequired("An active plan is required to issue API keys.");
  }
}

/** Everything the dashboard screen needs, in one round trip. */
router.get(
  "/dashboard",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const customer = customerOf(req);
    const email = customer.email;
    const subView = await subscriptionView(customer.id);

    const user = await getUserByEmail(email);
    const usage = user
      ? await getUsageStats(user.id, user.daily_credit_limit)
      : {
          credits_remaining: 0,
          credits_used_today: 0,
          credits_daily_limit: 0,
          total_queries_all_time: 0,
          last_7_days: [],
        };

    res.json({
      customer: customerPublic(customer),
      subscription: subView,
      keys: await listKeysForEmail(email),
      usage,
    });
  }),
);

router.get(
  "/dashboard/keys",
  requireCustomer,
  asyncHandler(async (req, res) => {
    res.json(await listKeysForEmail(customerOf(req).email));
  }),
);

/**
 * Issue a new API key for this account.
 *
 * All of an account's keys share one daily credit pool, so extra keys are for
 * separating environments, not for buying more capacity.
 */
router.post(
  "/dashboard/keys",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const body = parseBody(createKeySchema, req.body);
    const customer = customerOf(req);

    await requireEntitlement(customer);

    const email = customer.email;
    if ((await countActiveKeysForEmail(email)) >= MAX_KEYS_PER_ACCOUNT) {
      throw conflict(
        `You already have ${MAX_KEYS_PER_ACCOUNT} active keys. ` +
          "Revoke one before creating another.",
      );
    }

    const result = await generateApiKey(email, customer.name, body.label);
    res.status(201).json({
      api_key: result.api_key,
      key_id: result.key_id,
      key_prefix: result.key_prefix,
      message: CREATED_KEY_MESSAGE,
    });
  }),
);

/** Revoke one of *your own* keys. Takes effect on the next request. */
router.delete(
  "/dashboard/keys/:keyId",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const keyId = req.params.keyId;
    if (!(await revokeKeyForEmail(keyId, customerOf(req).email))) {
      throw notFound("No such key on this account.");
    }
    res.json({ status: "revoked", key_id: keyId });
  }),
);

// ---------------------------------------------------------------------------
// Referrals
// ---------------------------------------------------------------------------

/**
 * This account's referral code, who used it, and what it has earned.
 *
 * The referred customers' addresses come back masked — the referrer knows who
 * they invited, but this screen gets shared and screenshotted.
 */
router.get(
  "/referrals",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const summary = await referrals.summaryFor(customerOf(req).id);
    summary.link = `${PUBLIC_BASE_URL}/signup?ref=${summary.code}`;
    res.json(summary);
  }),
);

/**
 * Record an invite so a friend who signs up with that address is credited even
 * if they never click a link carrying the code.
 *
 * Inviting yourself is refused here rather than at signup, because at signup it
 * would be a silent no-op and the person would never learn why they were not
 * credited.
 */
router.post(
  "/referrals/invites",
  requireCustomer,
  asyncHandler(async (req, res) => {
    const body = parseBody(referralInviteSchema, req.body);
    const customer = customerOf(req);

    const address = body.email.toLowerCase().trim();
    if (address === customer.email.toLowerCase()) {
      throw badRequest("You cannot refer yourself.");
    }
    if ((await db.getCustomerByEmail(address)) !== null) {
      throw conflict("That email already has an account.");
    }

    await referrals.invite(customer.id, address);
    res.status(201).json(await referrals.summaryFor(customer.id));
  }),
);
