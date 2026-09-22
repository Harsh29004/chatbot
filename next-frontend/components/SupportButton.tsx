"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** Pages where a "contact support" button would be in the way or pointless. */
function hidden(pathname: string): boolean {
  return (
    pathname.startsWith("/support") ||
    pathname.startsWith("/admin") ||
    pathname.startsWith("/signin") ||
    pathname.startsWith("/signup") ||
    pathname.startsWith("/checkout")
  );
}

/**
 * The way to reach a person: a button pinned to the bottom of every page that
 * opens /support, where the customer writes to the team. Their messages land
 * in the staff inbox at /admin/support.
 *
 * Bottom-left, because bottom-right belongs to the assistant bubble. Signed-out
 * visitors see it too; /support asks them to sign in first and brings them back.
 */
export function SupportButton() {
  const pathname = usePathname();
  if (hidden(pathname)) return null;

  return (
    <Link href="/support" className="support-fab" aria-label="Contact support">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <path
          fill="currentColor"
          d="M12 3a8 8 0 0 0-8 8v4a3 3 0 0 0 3 3h1v-7H6a6 6 0 0 1 12 0h-2v7h1.2A4.8 4.8 0 0 1 13 20.9V20h-2v3h2a6.8 6.8 0 0 0 6.7-5.6A3 3 0 0 0 20 15v-4a8 8 0 0 0-8-8Z"
        />
      </svg>
      <span>Support</span>
    </Link>
  );
}
