/**
 * Next.js configuration.
 *
 * Two things matter here, and both exist because auth is an httpOnly cookie.
 *
 * **The API is reached same-origin.** The browser talks to `/api/...` and
 * `/v1/...` on :3000, and Next rewrites those to the Express server. That is
 * what the Vite proxy did, and it is what keeps `SameSite=Lax` sufficient: the
 * cookie is first-party, so there is no cross-site posting to defend against
 * and no CORS preflight on every request. Getting rid of the rewrite would mean
 * `SameSite=None; Secure` and a real CSRF story.
 *
 * **Server components reach the API directly.** They run on the server, where
 * a relative URL has no origin to resolve against, so `lib/api-server.ts` uses
 * `INTERNAL_API_ORIGIN` and forwards the cookie by hand. The rewrite below is
 * for the browser only.
 */

/** @type {import("next").NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // three.js ships untranspiled ESM that older bundler targets choke on.
  transpilePackages: ["three"],

  async rewrites() {
    const api = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";
    return [
      { source: "/api/:path*", destination: `${api}/api/:path*` },
      // The dashboard shows copy-pasteable snippets that call /v1, and the
      // preview widget calls it from this origin. Proxying it keeps the
      // development experience identical to production behind one domain.
      { source: "/v1/:path*", destination: `${api}/v1/:path*` },
      { source: "/widget/:path*", destination: `${api}/widget/:path*` },
      { source: "/health", destination: `${api}/health` },
    ];
  },

  // The landing page is the only route worth indexing; everything else is
  // behind a sign-in. Kept explicit so a future route does not inherit it.
  async headers() {
    return [
      {
        source: "/(dashboard|assistant|support|admin|checkout)/:path*",
        headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow" }],
      },
    ];
  },
};

export default nextConfig;
