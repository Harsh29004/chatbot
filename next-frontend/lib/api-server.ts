import "server-only";

import { cookies } from "next/headers";

import type {
  AssistantMessage,
  AssistantStatus,
  AssistantThread,
  Bot,
  BotTemplate,
  Customer,
  Dashboard,
  Gaps,
  Pricing,
  ReferralSummary,
  Subscription,
  SupportMessage,
  WidgetTheme,
} from "./types";

/**
 * The server-side API client, for React Server Components.
 *
 * Why this exists at all
 * ----------------------
 * The Vite app had one client, and it ran in the browser: `fetch("/api/...")`
 * with `credentials: "include"`, and the browser attached the session cookie.
 * A Server Component has neither of those. A relative URL has no origin to
 * resolve against, and there is no browser to attach anything — so both have to
 * be supplied by hand:
 *
 * * the origin comes from `INTERNAL_API_ORIGIN`, which points at the Express
 *   server directly rather than back through Next's own rewrite (going out and
 *   back in would be a pointless second hop, and in a container the public URL
 *   may not even resolve from inside);
 * * the cookie comes from `next/headers`, forwarded from the request being
 *   rendered.
 *
 * Everything here is `cache: "no-store"`. These are per-user, per-request reads
 * behind a session cookie — caching them is how one customer's dashboard ends
 * up rendered for another.
 */

const INTERNAL_API_ORIGIN =
  process.env.INTERNAL_API_ORIGIN ?? process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  readonly status: number;

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

async function serverFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const cookieStore = await cookies();
  const jar = cookieStore
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");

  let response: Response;
  try {
    response = await fetch(`${INTERNAL_API_ORIGIN}/api${path}`, {
      ...init,
      headers: {
        ...(jar ? { cookie: jar } : {}),
        ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
      cache: "no-store",
    });
  } catch {
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

/**
 * Fetch, but a 401 becomes `null` rather than a throw.
 *
 * Most pages want "the signed-in customer, or nobody" — the layout renders a
 * sign-in link for the second case. A throw would mean a try/catch around every
 * one of them.
 */
async function optional<T>(path: string): Promise<T | null> {
  try {
    return await serverFetch<T>(path);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      return null;
    }
    throw error;
  }
}

// ---------------------------------------------------------------------------
// Public reads — no session needed, so these are the ones worth caching
// ---------------------------------------------------------------------------

/**
 * Pricing and the template catalogue are the same for everybody and change
 * when an admin edits them, not per request. A short revalidate keeps the
 * landing page fast without pinning a stale price for long.
 */
async function publicFetch<T>(path: string, revalidateSeconds = 60): Promise<T> {
  const response = await fetch(`${INTERNAL_API_ORIGIN}/api${path}`, {
    next: { revalidate: revalidateSeconds },
  });
  if (!response.ok) {
    throw new ApiError(response.status, `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export const serverApi = {
  // --- public ---
  pricing: () => publicFetch<Pricing>("/billing/pricing"),
  templates: () => publicFetch<BotTemplate[]>("/templates"),
  widgetThemes: () => publicFetch<WidgetTheme[]>("/widget/themes"),
  authProviders: () =>
    publicFetch<{ password: boolean; firebase: boolean }>("/auth/providers", 300),

  // --- session-scoped ---
  me: () => optional<Customer>("/auth/me"),
  dashboard: () => optional<Dashboard>("/dashboard"),
  subscription: () => optional<Subscription>("/billing/subscription"),
  bot: () => optional<Bot>("/bot"),
  gaps: (days = 30) => optional<Gaps>(`/bot/gaps?days=${days}`),
  referrals: () => optional<ReferralSummary>("/referrals"),

  assistantStatus: () => optional<AssistantStatus>("/assistant/status"),
  assistantThreads: () => optional<AssistantThread[]>("/assistant/threads"),
  assistantThread: (id: string) =>
    optional<{ thread: AssistantThread; messages: AssistantMessage[] }>(
      `/assistant/threads/${id}`,
    ),

  supportMessages: () =>
    optional<{ messages: SupportMessage[]; unread: number }>("/support/messages?after_id="),
};

/**
 * Is anyone signed in?
 *
 * Cheaper than a full `me()` for the layout, which only needs to decide between
 * a "Dashboard" link and a "Sign in" one. Still a round trip — the cookie is
 * opaque and only the API can say whether it is live.
 */
export async function currentCustomer(): Promise<Customer | null> {
  return serverApi.me();
}
