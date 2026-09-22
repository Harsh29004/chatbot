import type { Metadata, Viewport } from "next";
import { Exo_2, Inter, JetBrains_Mono, Michroma } from "next/font/google";

import { AppShell } from "@/components/AppShell";
import { currentCustomer } from "@/lib/api-server";

import "./globals.css";

/**
 * The root layout — a Server Component.
 *
 * It does one thing the Vite app could not: resolve the session cookie *before*
 * rendering. `currentCustomer()` reads the cookie server-side and hands the
 * answer to the client providers, so the first painted frame already knows who
 * is signed in. The old app shipped a bundle, booted React, fired `/auth/me`,
 * and only then could decide whether to show "Dashboard" or "Sign in" — which
 * is why the header used to flicker.
 *
 * Everything stateful lives in `<AppShell>`, a Client Component. Keeping the
 * boundary here rather than at the page level means the providers mount once
 * and survive navigation, which is what the persistent chat widget needs.
 */

// next/font self-hosts these at build time. The Vite app loaded them from
// fonts.googleapis.com on every visit, which is a render-blocking request to a
// third party and a layout shift while it resolves. The CSS variables keep the
// same names the stylesheet already uses.
const exo2 = Exo_2({
  subsets: ["latin"],
  weight: ["500", "600", "700", "800"],
  variable: "--font-display",
  display: "swap",
});

const michroma = Michroma({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-brand",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap",
});

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-body",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Nexora AI — AI chat bots that answer from your sheet",
    template: "%s · Nexora AI",
  },
  description:
    "Upload your FAQ sheet, get an API key, ship a support bot that only " +
    "answers from your own approved answers.",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  themeColor: "#030509",
  width: "device-width",
  initialScale: 1,
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // One read, on the server, for the whole tree.
  const customer = await currentCustomer();

  return (
    <html
      lang="en"
      className={`${exo2.variable} ${michroma.variable} ${jetbrainsMono.variable} ${inter.variable}`}
    >
      <body>
        <AppShell initialCustomer={customer}>{children}</AppShell>
      </body>
    </html>
  );
}
