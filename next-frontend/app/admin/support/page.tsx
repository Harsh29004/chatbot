import type { Metadata } from "next";

import { AdminSupport } from "@/screens/AdminSupport";

export const metadata: Metadata = { title: "Support inbox", robots: { index: false } };

/** The staff support inbox. Client-only, for the same reason as `/admin`. */
export default function AdminSupportPage() {
  return <AdminSupport />;
}
