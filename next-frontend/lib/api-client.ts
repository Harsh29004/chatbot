"use client";

import type {
  AdminAiUsage,
  AdminAudit,
  AdminHealth,
  AdminOverview,
  AdminReferrals,
  AdminSubscriptions,
  AdminTemplate,
  AdminUsage,
  AdminUserDetail,
  AdminUserPage,
  Answer,
  AssistantMessage,
  AssistantStatus,
  AssistantThread,
  Bot,
  BotTemplate,
  Checkout,
  CreatedKey,
  Customer,
  Dashboard,
  Gaps,
  Pricing,
  ReferralSummary,
  SheetUpload,
  Subscription,
  SupportConversation,
  SupportMessage,
  WidgetTheme,
} from "./types";

/**
 * The browser API client.
 *
 * This is the Vite app's `lib/api.ts` with the type declarations lifted out to
 * `types.ts` and the two Vite-only things replaced. Everything a user *does* —
 * submitting a form, uploading a sheet, sending a message — still goes through
 * here, because a Server Component cannot respond to a click.
 *
 * Requests go to `/api/...` on this origin, which `next.config.mjs` rewrites to
 * the Express server. Keeping it same-origin is what lets the session cookie
 * stay `SameSite=Lax` and first-party.
 */

/**
 * Where customers' servers and installed widgets send requests, for the
 * snippets the dashboard shows.
 *
 * `import.meta.env` was Vite's; Next exposes browser-visible variables through
 * `process.env.NEXT_PUBLIC_*`, which is inlined at build time. The fallback to
 * `window.location.origin` is unchanged — in production the API is behind the
 * same domain.
 */
export const PUBLIC_API_ORIGIN: string =
  process.env.NEXT_PUBLIC_PUBLIC_API_ORIGIN ??
  (typeof window === "undefined" ? "" : window.location.origin);

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** The API puts validation problems in `detail`, sometimes as a list. */
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

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
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
    throw new ApiError(response.status, readDetail(payload, `Request failed (${response.status})`));
  }

  return payload as T;
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  pricing: () => request<Pricing>("/billing/pricing"),

  signup: (email: string, password: string, name: string, referralCode = "") =>
    post<Customer>("/auth/signup", { email, password, name, referral_code: referralCode }),

  login: (email: string, password: string) =>
    post<Customer>("/auth/login", { email, password }),

  /**
   * Trade a Firebase ID token for the session cookie.
   *
   * The one place a Firebase credential is sent to our API. Everything after
   * this call is authenticated by the httpOnly cookie the response sets, the
   * same one a password login produces — so the rest of this client needs to
   * know nothing about Firebase.
   *
   * A 403 here means the email is not verified yet; the sign-in page turns that
   * into "check your inbox" plus a re-send button rather than a dead end.
   */
  firebaseExchange: (idToken: string, referralCode = "") =>
    post<Customer>("/auth/firebase", { id_token: idToken, referral_code: referralCode }),

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

  widgetThemes: () => request<WidgetTheme[]>("/widget/themes"),

  widgetPackageUrl: (themeId: string) => `/api/widget/themes/${themeId}/package`,

  starterSheetUrl: (templateId: string) => `/api/templates/${templateId}/starter-sheet`,

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

      // Events are separated by a blank line. Anything after the last one is a
      // partial event, so it stays in the buffer until the rest arrives.
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

  authProviders: () => request<{ password: boolean; firebase: boolean }>("/auth/providers"),

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
/* Staff inbox                                                                */
/* -------------------------------------------------------------------------- */

/**
 * The staff side is header-authenticated, not cookie-authenticated, so it
 * cannot go through `request` above — that one sends credentials and no key.
 *
 * The key is held by the caller and passed in on every call rather than stored
 * in this module. It lives in sessionStorage, which is cleared when the tab
 * closes: this is the key that opens every customer's conversation, and it
 * should not outlive the sitting. That is also why the admin screens are client
 * components rather than server-rendered — the credential is deliberately kept
 * out of the request the server sees.
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
        ? "Your admin session has ended. Sign in again."
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

/** Serialise query parameters, dropping the empty ones rather than sending "". */
function qs(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

/** Staff sign-in: trades a username and password for a session token. */
export async function adminLogin(
  username: string,
  password: string,
): Promise<{ token: string; expires_at: number }> {
  let response: Response;
  try {
    response = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Is the API running?");
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, readDetail(payload, "Could not sign in."));
  }
  return payload as { token: string; expires_at: number };
}

export const adminPanelApi = {
  overview: (key: string, days = 30) =>
    adminRequest<AdminOverview>(`/admin/overview${qs({ days })}`, key),

  health: (key: string) => adminRequest<AdminHealth>("/admin/health", key),

  users: (
    key: string,
    params: {
      q?: string;
      status_filter?: string;
      days?: number;
      sort?: string;
      limit?: number;
      offset?: number;
    } = {},
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
    params: {
      days?: number;
      email?: string;
      endpoint?: string;
      role?: string;
      limit?: number;
    } = {},
  ) => adminRequest<AdminAudit>(`/admin/audit${qs(params)}`, key),

  aiUsage: (key: string, days = 30) =>
    adminRequest<AdminAiUsage>(`/admin/ai-usage${qs({ days })}`, key),

  templates: (key: string) =>
    adminRequest<{ templates: AdminTemplate[]; editable_fields: string[] }>(
      "/admin/templates",
      key,
    ),

  updateTemplate: (key: string, templateId: string, changes: Record<string, unknown>) =>
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

export type * from "./types";
