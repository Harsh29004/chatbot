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
  id: number;
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
  id: number;
  key_prefix: string;
  label: string;
  created_at: string;
  is_active: number;
}

export interface Usage {
  credits_remaining: number;
  credits_used_today: number;
  credits_daily_limit: number;
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
  key_id: number;
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
  id: number;
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
  mode: "strong" | "near" | "decline";
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

  signup: (email: string, password: string, name: string) =>
    post<Customer>("/auth/signup", { email, password, name }),

  login: (email: string, password: string) =>
    post<Customer>("/auth/login", { email, password }),

  logout: () => post<{ status: string }>("/auth/logout"),

  me: () => request<Customer>("/auth/me"),

  dashboard: () => request<Dashboard>("/dashboard"),

  createKey: (label: string) => post<CreatedKey>("/dashboard/keys", { label }),

  revokeKey: (id: number) =>
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
};
