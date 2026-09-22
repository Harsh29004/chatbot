# Nexora AI — Next.js frontend

Next.js 15 with the App Router, migrated from the Vite + React Router build in
`../frontend`. Same screens, same styling, same behaviour — rendered on the
server.

## Running it

```bash
npm install
npm run dev        # http://localhost:3000
```

The API has to be running on :8000 (`../node-backend`). `next.config.mjs`
rewrites `/api`, `/v1`, `/widget` and `/health` to it, so the browser only ever
talks to one origin — which is what keeps the session cookie first-party and
`SameSite=Lax` sufficient.

Copy `.env.example` to `.env.local` and fill in the Firebase values.

## Verifying it

```bash
# terminal 1 — the API, against a throwaway in-memory MongoDB
cd ../node-backend && node run-e2e-backend.mjs

# terminal 2
npm run build && npm start

# terminal 3
node e2e-check.mjs
```

29 checks. They assert the thing that is actually new: that the *server-rendered
HTML* already contains the data and the access decisions, rather than the page
assembling itself after JavaScript boots.

## What changed, and why

### Routing

| Vite | Next |
|---|---|
| `src/main.tsx` | `app/layout.tsx` + `components/AppShell.tsx` |
| `<Routes>` in `App.tsx` | the `app/` directory |
| `src/pages/*` | `screens/*`, rendered by `app/*/page.tsx` |
| `<Link to>` | `<Link href>` |
| `useNavigate()` | `useRouter()` |
| `useLocation()` | `usePathname()` / `useSearchParams()` |
| `<Navigate to>` | `redirect()` (server) or `router.replace()` (client) |
| `index.html` | `metadata` in `layout.tsx` |
| `VITE_*` | `NEXT_PUBLIC_*` |

The old `pages/` components kept their names and their code; they moved to
`screens/` and each gained optional `initial*` props. A thin Server Component in
`app/` does the fetching and passes them down.

### Where the data comes from now

`lib/api.ts` became three files, because there are now two places code runs:

- **`lib/types.ts`** — the payload shapes. Lifted out verbatim; both clients
  describe the same API and a type only one could see would be the start of a
  drift.
- **`lib/api-server.ts`** — for Server Components. Reads the session cookie with
  `next/headers` and calls the API directly on `INTERNAL_API_ORIGIN`, because a
  relative URL has no origin to resolve against on the server and there is no
  browser to attach the cookie.
- **`lib/api-client.ts`** — the original browser client, unchanged in substance.
  Every mutation still goes through it; a Server Component cannot respond to a
  click.

### What server rendering actually bought

**The landing page has content in it.** Prices and all ten templates are in the
HTML a crawler or a first-time visitor receives. The Vite build shipped an empty
page and filled it in two round trips after the bundle booted.

**Auth is decided before the response starts.** `lib/guard.ts` calls
`requireCustomer()` in each protected `page.tsx`. A signed-out visitor gets a
307 and nothing else — no bundle, no flash of a dashboard shell, no protected
markup reaching a browser that should not have it. `<RequireAuth>` still exists
as a client-side fallback, but only for the session ending *while the tab is
open*: the server cannot re-check a page it has already sent.

**No auth flicker.** The root layout resolves the cookie and hands the answer to
`AuthProvider` as `initialCustomer`, so the first painted frame knows who is
signed in. The header used to flash "Sign in" on every load.

**A 404 is a 404.** The SPA fallback sent every unknown path to the landing page
with a 200. The server can tell a typo from a route.

**Fonts are self-hosted.** `next/font` fetches them at build time. The Vite
build had a render-blocking request to `fonts.googleapis.com` on every visit.

### What stayed client-rendered, deliberately

**`/admin` and `/admin/support`.** They authenticate with `X-Admin-Key`, held in
`sessionStorage` so it dies with the tab. That credential opens every customer's
data, and keeping it out of the request the server sees is the point — a
server-rendered version would have to accept it as a cookie, which would make it
survive the tab closing and ride along on every request to every route.

**The chat widget and support button.** Mounted in `AppShell`, outside the page,
so they survive navigation instead of remounting and dropping the conversation —
the same reason the old `App.tsx` kept them outside `<Routes>`.

## One real fix

`lib/markdown.ts` called `DOMPurify.addHook` at module scope. That was fine in a
build that only ever ran in a browser; under the App Router even a `"use client"`
module executes once on the server, where DOMPurify has no DOM and no `addHook`.
It now installs the hook on first use and, with no DOM available, returns
**escaped plain text** rather than unsanitised HTML. Failing closed matters here:
falling back to raw `marked` output would have shipped exactly the unsanitised
markup that module exists to prevent, in the server-rendered copy, before any
client code could correct it.
