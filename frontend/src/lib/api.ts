/**
 * Thin fetch wrapper for the platform API.
 *
 * Every call sends credentials so the httpOnly session cookie rides along.
 * Errors come back as a thrown ApiError carrying the server's message, so
 * screens can render what actually went wrong instead of "Something failed".
 */

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** FastAPI puts validation problems in `detail`, sometimes as a list. */
function readDetail(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string };
      if (first?.msg) return first.msg;
    }
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: string }).message;
      if (message) return message;
    }
  }
  return fallback;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    // FormData must set its own Content-Type — it carries the multipart
    // boundary, and overriding it makes the upload unparseable server-side.
    const isFormData = options.body instanceof FormData;
    response = await fetch(`/api${path}`, {
      credentials: "include",
      headers:
        options.body !== undefined && !isFormData
          ? { "Content-Type": "application/json", ...options.headers }
          : options.headers,
      ...options,
    });
  } catch {
    // Network-level failure: the API isn't reachable at all.
    throw new ApiError(0, "Can't reach the server. Is the API running?");
  }

  if (response.status === 204) return undefined as T;

  const raw = await response.text();
  const payload = raw ? JSON.parse(raw) : null;

  if (!response.ok) {
    throw new ApiError(
      response.status,
      readDetail(payload, `Request failed (${response.status})`),
    );
  }

  return payload as T;
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

// ---------------------------------------------------------------------------
// Types (mirror apps/billing/schemas.py)
// ---------------------------------------------------------------------------

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

/* -------------------------------------------------------------------------- */
/* Assistant                                                                  */
/* -------------------------------------------------------------------------- */

/* -------------------------------------------------------------------------- */
/* Support chat                                                               */
/* -------------------------------------------------------------------------- */

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

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  pricing: () => request<Pricing>("/billing/pricing"),

  signup: (email: string, password: string, name: string, referralCode = "") =>
    post<Customer>("/auth/signup", {
      email,
      password,
      name,
      referral_code: referralCode,
    }),

  login: (email: string, password: string) =>
    post<Customer>("/auth/login", { email, password }),

  logout: () => post<{ status: string }>("/auth/logout"),

  me: () => request<Customer>("/auth/me"),

  referrals: () => request<ReferralSummary>("/referrals"),

  inviteToReferral: (email: string) =>
    post<ReferralSummary>("/referrals/invites", { email }),

  dashboard: () => request<Dashboard>("/dashboard"),

  createKey: (label: string) => post<CreatedKey>("/dashboard/keys", { label }),

  revokeKey: (id: string) =>
    request<{ status: string }>(`/dashboard/keys/${id}`, { method: "DELETE" }),

  checkout: (planId: "monthly" | "yearly") =>
    post<Checkout>("/billing/checkout", { plan_id: planId }),

  confirmManual: () => post<Subscription>("/billing/confirm"),

  cancel: () => post<Subscription>("/billing/cancel"),

  // --- templates & bot ---

  templates: () => request<BotTemplate[]>("/templates"),

  starterSheetUrl: (templateId: string) =>
    `/api/templates/${templateId}/starter-sheet`,

  bot: () => request<Bot>("/bot"),

  selectTemplate: (templateId: string, name = "") =>
    request<Bot>("/bot", {
      method: "PUT",
      body: JSON.stringify({ template_id: templateId, name }),
    }),

  /** Multipart upload — no Content-Type header, the browser sets the boundary. */
  uploadSheet: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<SheetUpload>("/bot/sheet", { method: "POST", body: form });
  },

  preview: (message: string) =>
    post<Answer>("/bot/preview", { message, session_id: "dashboard-preview" }),

  gaps: (days = 30) => request<Gaps>(`/bot/gaps?days=${days}`),

  setAnsweringMode: (enabled: boolean) =>
    request<Bot>("/bot/answering", {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),

  // --- assistant ---

  assistantStatus: () => request<AssistantStatus>("/assistant/status"),

  assistantThreads: () => request<AssistantThread[]>("/assistant/threads"),

  createAssistantThread: () =>
    request<AssistantThread>("/assistant/threads", {
      method: "POST",
      body: JSON.stringify({ title: "New chat" }),
    }),

  assistantThread: (id: string) =>
    request<{ thread: AssistantThread; messages: AssistantMessage[] }>(
      `/assistant/threads/${id}`,
    ),

  deleteAssistantThread: (id: string) =>
    request<void>(`/assistant/threads/${id}`, { method: "DELETE" }),

  /**
   * Send a message and consume the server-sent event stream.
   *
   * Not EventSource: that is GET-only and cannot carry a body, and we need to
   * POST the message. So this reads the response stream by hand and splits on
   * the blank line that terminates an SSE event.
   *
   * `onDelta` is called for every fragment as it arrives. The returned promise
   * settles when the stream ends; `signal` aborts an answer mid-flight.
   */
  sendAssistantMessage: async (
    threadId: string,
    message: string,
    onDelta: (text: string) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch(`/api/assistant/threads/${threadId}/messages`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
      signal,
    });

    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try {
        const payload = await response.json();
        detail = readDetail(payload, detail);
      } catch {
        /* a non-JSON error body is still an error; keep the status text */
      }
      throw new ApiError(response.status, detail);
    }
    if (!response.body) throw new ApiError(500, "The server sent no response body.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Events are separated by a blank line. Anything after the last one is
      // a partial event, so it stays in the buffer until the rest arrives.
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";

      for (const event of events) {
        const line = event.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        let payload: { delta?: string; error?: string; done?: boolean };
        try {
          payload = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (payload.error) throw new ApiError(500, payload.error);
        if (payload.delta) onDelta(payload.delta);
      }
    }
  },

  authProviders: () => request<{ password: boolean; google: boolean }>("/auth/providers"),

  // --- support chat (customer side, session cookie) ---

  supportMessages: (afterId = "") =>
    request<{ messages: SupportMessage[]; unread: number }>(
      `/support/messages?after_id=${afterId}`,
    ),

  sendSupportMessage: (body: string) =>
    request<SupportMessage>("/support/messages", {
      method: "POST",
      body: JSON.stringify({ body }),
    }),

  markSupportRead: () => request<{ ok: boolean }>("/support/read", { method: "POST" }),
};

/* -------------------------------------------------------------------------- */
/* Referrals                                                                  */
/* -------------------------------------------------------------------------- */

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

/* -------------------------------------------------------------------------- */
/* Staff inbox                                                                */
/* -------------------------------------------------------------------------- */

/**
 * The staff side is header-authenticated, not cookie-authenticated, so it
 * cannot go through `request` above — that one sends credentials and no key.
 *
 * The key is held by the caller and passed in on every call rather than stored
 * in this module. It lives in sessionStorage, which is cleared when the tab
 * closes: this is the key that opens every customer's conversation, and it
 * should not outlive the sitting.
 */
async function adminRequest<T>(
  path: string,
  key: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "X-Admin-Key": key,
      ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    let detail =
      response.status === 403
        ? "That admin key was not accepted."
        : `Request failed (${response.status})`;
    try {
      detail = readDetail(await response.json(), detail);
    } catch {
      /* keep the status-derived message */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const adminApi = {
  inbox: (key: string) =>
    adminRequest<{ conversations: SupportConversation[]; total_unread: number }>(
      "/support/admin/conversations",
      key,
    ),

  conversation: (key: string, id: string, afterId = "") =>
    adminRequest<{ conversation: SupportConversation; messages: SupportMessage[] }>(
      `/support/admin/conversations/${id}?after_id=${afterId}`,
      key,
    ),

  reply: (key: string, id: string, body: string) =>
    adminRequest<SupportMessage>(`/support/admin/conversations/${id}/messages`, key, {
      method: "POST",
      body: JSON.stringify({ body }),
    }),

  markRead: (key: string, id: string) =>
    adminRequest<{ ok: boolean; total_unread: number }>(
      `/support/admin/conversations/${id}/read`,
      key,
      { method: "POST" },
    ),
};


/* -------------------------------------------------------------------------- */
/* Admin panel                                                                */
/* -------------------------------------------------------------------------- */

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

/** Serialise query parameters, dropping the empty ones rather than sending "". */
function qs(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

export const adminPanelApi = {
  overview: (key: string, days = 30) =>
    adminRequest<AdminOverview>(`/admin/overview${qs({ days })}`, key),

  health: (key: string) => adminRequest<AdminHealth>("/admin/health", key),

  users: (
    key: string,
    params: { q?: string; status_filter?: string; days?: number; sort?: string; limit?: number; offset?: number } = {},
  ) => adminRequest<AdminUserPage>(`/admin/users${qs(params)}`, key),

  user: (key: string, customerId: string, days = 30) =>
    adminRequest<AdminUserDetail>(`/admin/users/${customerId}${qs({ days })}`, key),

  grantCredits: (key: string, customerId: string, credits: number, note: string) =>
    adminRequest<unknown>(`/admin/users/${customerId}/credits`, key, {
      method: "POST",
      body: JSON.stringify({ credits, note }),
    }),

  setLimit: (key: string, customerId: string, dailyCreditLimit: number | null) =>
    adminRequest<unknown>(`/admin/users/${customerId}/limit`, key, {
      method: "POST",
      body: JSON.stringify({ daily_credit_limit: dailyCreditLimit }),
    }),

  setActive: (key: string, customerId: string, isActive: boolean) =>
    adminRequest<unknown>(`/admin/users/${customerId}/active`, key, {
      method: "POST",
      body: JSON.stringify({ is_active: isActive }),
    }),

  revokeKey: (key: string, keyId: string) =>
    adminRequest<unknown>(`/admin/keys/${keyId}`, key, { method: "DELETE" }),

  subscriptions: (key: string, days = 90) =>
    adminRequest<AdminSubscriptions>(`/admin/subscriptions${qs({ days })}`, key),

  usage: (key: string, days = 30) =>
    adminRequest<AdminUsage>(`/admin/usage${qs({ days })}`, key),

  audit: (
    key: string,
    params: { days?: number; email?: string; endpoint?: string; role?: string; limit?: number } = {},
  ) => adminRequest<AdminAudit>(`/admin/audit${qs(params)}`, key),

  aiUsage: (key: string, days = 30) =>
    adminRequest<AdminAiUsage>(`/admin/ai-usage${qs({ days })}`, key),

  templates: (key: string) =>
    adminRequest<{ templates: AdminTemplate[]; editable_fields: string[] }>(
      "/admin/templates",
      key,
    ),

  updateTemplate: (
    key: string,
    templateId: string,
    changes: Record<string, unknown>,
  ) =>
    adminRequest<AdminTemplate>(`/admin/templates/${templateId}`, key, {
      method: "PATCH",
      body: JSON.stringify(changes),
    }),

  resetTemplate: (key: string, templateId: string) =>
    adminRequest<AdminTemplate>(`/admin/templates/${templateId}/reset`, key, {
      method: "POST",
    }),

  referrals: (key: string) => adminRequest<AdminReferrals>("/admin/referrals", key),
};
