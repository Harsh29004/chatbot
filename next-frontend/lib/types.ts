/**
 * The shapes the API returns.
 *
 * Lifted verbatim out of the Vite app's `lib/api.ts`, which is where they lived
 * next to the fetch calls. They move here because in the App Router there are
 * now *two* clients — one that runs on the server (`api-server.ts`) and one in
 * the browser (`api-client.ts`) — and both describe the same payloads. A type
 * that only one of them could see would be the start of a drift.
 *
 * These mirror `node-backend/src/billing/schemas.ts` and the response bodies
 * the routers build. Nothing here runs; it is erased at build time.
 */

export interface Customer {
  id: string;
  email: string;
  name: string;
  created_at: string;
}

export interface Plan {
  id: string;
  name: string;
  price_cents: number;
  price_display: string;
  currency: string;
  interval: string;
  daily_credits: number;
  tagline: string;
}

export interface Pricing {
  currency: string;
  plans: Plan[];
  yearly_savings_cents: number;
  yearly_savings_display: string;
  yearly_discount_percent: number;
  yearly_effective_monthly_display: string;
  trial_days: number;
}

export interface Subscription {
  plan_id: string | null;
  plan_name: string | null;
  status: string;
  is_entitled: boolean;
  current_period_end: string | null;
  daily_credits: number | null;
  can_start_trial: boolean;
}

export interface ApiKey {
  id: string;
  key_prefix: string;
  label: string;
  created_at: string;
  is_active: number;
}

export interface Usage {
  /** Today's allowance plus the bonus balance — what can actually be spent. */
  credits_remaining: number;
  credits_used_today: number;
  credits_daily_limit: number;
  /** Referral and goodwill credits. They don't reset at midnight. */
  bonus_credits?: number;
  total_queries_all_time?: number;
  resets_at?: string;
  last_7_days?: { usage_date: string; credits_used: number }[];
}

export interface Dashboard {
  customer: Customer;
  subscription: Subscription;
  keys: ApiKey[];
  usage: Usage;
}

export interface CreatedKey {
  api_key: string;
  key_id: string;
  key_prefix: string;
  message: string;
}

export interface Checkout {
  checkout_url: string;
  reference: string;
  provider: string;
  requires_manual_confirmation: boolean;
  message: string;
}

export interface BotTemplate {
  id: string;
  name: string;
  category: string;
  icon: string;
  tagline: string;
  description: string;
  scope_label: string;
  decline_message: string;
  sample_questions: string[];
  starter_categories: string[];
  strong_threshold: number;
  near_threshold: number;
  strictness: "strict" | "balanced" | "open";
}

export interface WidgetTheme {
  id: string;
  name: string;
  tagline: string;
  description: string;
  best_for: string[];
  layout: "bubble" | "drawer";
  header_style: "solid" | "gradient" | "glass";
  launcher_icon: string;
  primary: string;
  primary_2: string;
  on_primary: string;
  background: string;
  surface: string;
  text: string;
  muted: string;
  border: string;
  user_bubble: string;
  user_text: string;
  bot_bubble: string;
  bot_text: string;
  font_family: string;
  radius: number;
  dark: boolean;
  title: string;
  greeting: string;
  placeholder: string;
}

export interface Bot {
  id: string;
  name: string;
  template_id: string;
  template: BotTemplate | null;
  status: "draft" | "ready";
  doc_count: number;
  sheet_filename: string;
  sheet_uploaded_at: string | null;
  categories: string[];
  required_columns: string[];
  optional_columns: string[];
  /** File extensions the importer accepts, e.g. `.csv`, `.jsonl`. */
  accepted_formats?: string[];
  /** Owner opted this bot in to grounded rewording. */
  llm_enabled: boolean;
  /** A local model is actually reachable right now. */
  llm_available: boolean;
}

export interface SupportMessage {
  id: string;
  sender: "customer" | "staff";
  body: string;
  created_at: string;
}

export interface SupportConversation {
  id: string;
  customer_id: string;
  customer_email: string;
  customer_name: string;
  last_message_at: string;
  last_message_preview: string;
  unread: number;
}

export interface AssistantThread {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface AssistantMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface AssistantStatus {
  enabled: boolean;
  available: boolean;
  model: string | null;
  messages_today: number;
  daily_limit: number;
}

export interface SheetUpload {
  documents_indexed: number;
  skipped_rows: number;
  warnings: string[];
  categories: string[];
  bot: Bot;
}

export interface Answer {
  response: string;
  mode: "strong" | "near" | "grounded" | "decline";
  matched_question: string | null;
  confidence: number;
}

export interface Gap {
  question: string;
  times_asked: number;
  last_asked: string;
  best_score: number;
  verdict: "nearly" | "missing";
}

export interface Gaps {
  gaps: Gap[];
  days: number;
  flagged_inputs: number;
}

export interface ReferredCustomer {
  /** Masked — the referrer knows who they invited, screenshots don't need to. */
  email: string;
  name: string;
  joined_at: string;
  source: "code" | "invite";
  credits_earned: number;
  payments_rewarded: number;
}

export interface ReferralSummary {
  code: string;
  link: string;
  credits_earned: number;
  referrer_signup_credits: number;
  referred_signup_credits: number;
  topup_credits: number;
  referred: ReferredCustomer[];
  invites: { email: string; created_at: string; claimed_at: string | null }[];
  joined_via: string | null;
}

export interface AdminOverview {
  window_days: number;
  generated_at: string;
  customers: { total: number; active: number; new_in_window: number; via_google: number };
  subscriptions: {
    /** Can use the product now — trials included. */
    entitled: number;
    /** On a paid plan. The number that means revenue. */
    paying: number;
    on_trial: number;
    by_status: Record<string, number>;
    by_plan: Record<string, number>;
    mrr_cents: number;
    mrr_display: string;
    currency: string;
  };
  revenue: {
    all_time_cents: number;
    all_time_display: string;
    in_window_cents: number;
    in_window_display: string;
    invoices_paid: number;
  };
  usage: {
    requests_in_window: number;
    credits_in_window: number;
    active_accounts: number;
    credits_today: number;
  };
  bots: { total: number; ready: number; draft: number; rewording_on: number; indexed_rows: number };
  keys: { total: number; active: number };
  bonus_credits: { granted_all_time: number; outstanding: number };
  referrals: Record<string, number>;
}

export interface AdminUser {
  customer_id: string;
  user_id: string | null;
  email: string;
  name: string;
  created_at: string;
  is_active: number;
  auth_provider: string;
  role: string | null;
  daily_credit_limit: number | null;
  effective_daily_limit: number;
  bonus_credits: number;
  credits_today: number;
  credits_in_window: number;
  requests_in_window: number;
  last_request_at: string | null;
  active_keys: number;
  bot_id: string | null;
  template_id: string | null;
  bot_status: string | null;
  bot_doc_count: number | null;
  bot_llm_enabled: number | null;
  plan_id: string | null;
  subscription_status: string | null;
  current_period_end: string | null;
  is_entitled: boolean;
  referred_count: number;
  referred_by_email: string | null;
}

export interface AdminUserPage {
  total: number;
  limit: number;
  offset: number;
  window_days: number;
  users: AdminUser[];
}

export interface AdminUserDetail {
  account: AdminUser;
  subscriptions: Record<string, unknown>[];
  invoices: Record<string, unknown>[];
  keys: { id: string; key_prefix: string; label: string; created_at: string; is_active: number }[];
  credit_grants: {
    id: string;
    amount: number;
    remaining: number;
    reason: string;
    note: string;
    created_at: string;
  }[];
  daily_usage: { usage_date: string; credits_used: number }[];
  recent_requests: {
    id: string;
    endpoint: string;
    message_len: number;
    credit_cost: number;
    timestamp: string;
    key_prefix: string | null;
  }[];
  referrals: ReferralSummary;
}

export interface AdminSubscriptions {
  window_days: number;
  counts: {
    customers_with_a_subscription: number;
    entitled: number;
    paying: number;
    by_status: Record<string, number>;
  };
  by_plan: { plan_id: string; count: number; entitled: number; mrr_cents: number }[];
  mrr_cents: number;
  mrr_display: string;
  revenue_by_month: { month: string; invoices: number; amount_cents: number; amount_display: string }[];
  started_in_window: Record<string, number>;
  churn_in_window: { canceled: number; expired: number };
  trial_conversion: { trialled: number; converted: number; percent: number };
  subscriptions: {
    id: string;
    customer_id: string;
    email: string;
    name: string;
    plan_id: string;
    status: string;
    current_period_end: string;
    is_entitled: boolean;
    monthly_value_cents: number;
  }[];
}

export interface AdminUsage {
  window_days: number;
  totals: { requests: number; credits: number; active_accounts: number };
  per_day: { date: string; credits: number; accounts: number; requests: number }[];
  top_accounts: { user_id: string; email: string; name: string; role: string; credits: number; requests: number }[];
  by_endpoint: { endpoint: string; requests: number; credits: number; accounts: number; avg_message_len: number }[];
}

export interface AdminAudit {
  window_days: number;
  total: number;
  limit: number;
  offset: number;
  summary: { requests: number; credits: number; accounts: number; keys_used: number };
  entries: {
    id: string;
    timestamp: string;
    endpoint: string;
    message_len: number;
    credit_cost: number;
    owner_email: string | null;
    role: string | null;
    key_prefix: string | null;
    key_label: string | null;
    key_is_active: number | null;
  }[];
  flagged_inputs: {
    bot_type: string;
    query_text: string;
    top_match_score: number | null;
    session_id: string | null;
    timestamp: string;
  }[];
  owner_keys: {
    id: string;
    key_prefix: string;
    label: string;
    created_at: string;
    is_active: number;
    owner_email: string;
    requests: number;
  }[];
}

export interface AdminAiUsage {
  window_days: number;
  by_plan: {
    plan_id: string;
    accounts: number;
    entitled_accounts: number;
    requests: number;
    credits: number;
    bots_with_rewording: number;
    credits_per_account: number;
  }[];
  rewording: { bots: number; enabled: number; enabled_and_ready: number };
  retrieval: { unanswered: number; bots_affected: number; flagged_inputs: number };
  top_unanswered: { query_text: string; bot_type: string; times_asked: number; best_score: number }[];
  by_template: {
    template_id: string;
    bots: number;
    ready: number;
    rewording_on: number;
    indexed_rows: number;
    requests: number;
  }[];
}

export interface AdminTemplate extends BotTemplate {
  enabled: boolean;
  overridden_fields: string[];
  updated_at: string | null;
  defaults: Record<string, string | number>;
  bots: number;
  bots_ready: number;
  bots_rewording_on: number;
}

export interface AdminReferrals {
  totals: Record<string, number>;
  rewards: {
    referrer_signup_credits: number;
    referred_signup_credits: number;
    topup_credits: number;
  };
  leaderboard: {
    customer_id: string;
    email: string;
    name: string;
    referred_count: number;
    credits_earned: number;
  }[];
  recent_rewards: {
    id: string;
    kind: string;
    role: string;
    credits: number;
    created_at: string;
    invoice_id: string | null;
    email: string;
  }[];
}

export interface AdminHealth {
  llm_available: boolean;
  llm_model: string | null;
  plans: Pricing;
  referral_rewards: {
    referrer_signup_credits: number;
    referred_signup_credits: number;
    topup_credits: number;
  };
}
