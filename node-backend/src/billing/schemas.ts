/**
 * Zod schemas for the account, billing, and dashboard endpoints.
 *
 * Port of `backend/billing/schemas.py`.
 *
 * Pydantic did two jobs in the Python build: it validated incoming request
 * bodies, and it declared the *response* shape so FastAPI could both serialise
 * and document it. Zod covers the first job directly. For the second, the
 * response models become TypeScript interfaces — the compiler checks the shape
 * at build time, which is where the cost of getting it wrong belongs, and
 * nothing has to run at request time to reshape an object that is already
 * correct.
 *
 * `EmailStr` becomes `z.string().email()`, which applies the same basic
 * syntactic check. Pydantic's `email-validator` additionally normalises and can
 * check deliverability; neither was relied on here, and the real address policy
 * lives in `identity.ts` where it always did.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export const signupSchema = z.object({
  email: z.string().email(),
  name: z.string().max(120).default(""),
  // Length beats character-class rules; 8 is the floor.
  password: z.string().min(8).max(200),
  // Optional and never validated here: a mistyped code costs the signup
  // nothing, it just goes unattributed.
  referral_code: z.string().max(32).default(""),
});
export type SignupRequest = z.infer<typeof signupSchema>;

export const referralInviteSchema = z.object({
  email: z.string().email(),
});
export type ReferralInviteRequest = z.infer<typeof referralInviteSchema>;

export const loginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1).max(200),
});
export type LoginRequest = z.infer<typeof loginSchema>;

/**
 * The one-time exchange: a Firebase ID token for a session cookie.
 *
 * `max` is generous because an ID token carrying several provider identities
 * and custom claims runs long, but it is bounded — an unbounded string here is
 * a free way to make the server do RSA work on megabytes.
 */
export const firebaseAuthSchema = z.object({
  id_token: z.string().min(1).max(8192),
  // Only read when this exchange ends up *creating* an account; a returning
  // user cannot be referred again. Unvalidated on purpose: a mistyped code
  // costs the sign-in nothing, it just goes unattributed.
  referral_code: z.string().max(32).default(""),
});
export type FirebaseAuthRequest = z.infer<typeof firebaseAuthSchema>;

export interface CustomerResponse {
  id: string;
  email: string;
  name: string;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Billing
// ---------------------------------------------------------------------------

export interface PlanResponse {
  id: string;
  name: string;
  price_cents: number;
  price_display: string;
  currency: string;
  interval: string;
  daily_credits: number;
  tagline: string;
}

export interface PricingResponse {
  currency: string;
  plans: PlanResponse[];
  yearly_savings_cents: number;
  yearly_savings_display: string;
  yearly_discount_percent: number;
  yearly_effective_monthly_display: string;
  trial_days: number;
}

export interface SubscriptionResponse {
  plan_id?: string | null;
  plan_name?: string | null;
  status: string;
  is_entitled: boolean;
  current_period_end?: string | null;
  daily_credits?: number | null;
  can_start_trial: boolean;
}

export const checkoutSchema = z.object({
  plan_id: z.enum(["monthly", "yearly"]),
});
export type CheckoutRequest = z.infer<typeof checkoutSchema>;

export interface CheckoutResponse {
  checkout_url: string;
  reference: string;
  provider: string;
  requires_manual_confirmation: boolean;
  message: string;
}

export interface InvoiceResponse {
  id: string;
  plan_id: string;
  amount_cents: number;
  currency: string;
  status: string;
  issued_at: string;
  paid_at?: string | null;
}

// ---------------------------------------------------------------------------
// Dashboard — API keys
// ---------------------------------------------------------------------------

export const createKeySchema = z.object({
  label: z.string().max(60).default(""),
});
export type CreateKeyRequest = z.infer<typeof createKeySchema>;

export interface CreatedKeyResponse {
  api_key: string;
  key_id: string;
  key_prefix: string;
  message: string;
}

/** The default `message` Pydantic supplied for {@link CreatedKeyResponse}. */
export const CREATED_KEY_MESSAGE =
  "Copy this key now — it is not stored and cannot be shown again.";

export interface DashboardKey {
  id: string;
  key_prefix: string;
  label: string;
  created_at: string;
  is_active: number;
}

export interface DashboardResponse {
  customer: CustomerResponse;
  subscription: SubscriptionResponse;
  keys: DashboardKey[];
  usage: Record<string, unknown>;
}
