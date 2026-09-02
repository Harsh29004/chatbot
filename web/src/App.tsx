import { useEffect } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { AuthProvider, RequireAuth } from "./lib/auth";
import { SignIn, SignUp } from "./pages/Auth";
import { CheckoutReturn } from "./pages/CheckoutReturn";
import { Dashboard } from "./pages/Dashboard";
import { Landing } from "./pages/Landing";

/**
 * Router-level scroll behaviour: jump to an #anchor when there is one,
 * otherwise start new pages at the top. Without this, navigating from the
 * bottom of the landing page lands you at the bottom of the next one.
 */
function ScrollBehaviour() {
  const { pathname, hash } = useLocation();

  useEffect(() => {
    if (hash) {
      const target = document.querySelector(hash);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
    }
    window.scrollTo(0, 0);
  }, [pathname, hash]);

  return null;
}

export default function App() {
  return (
    <AuthProvider>
      <ScrollBehaviour />
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/signin" element={<SignIn />} />
        <Route path="/signup" element={<SignUp />} />
        <Route
          path="/dashboard"
          element={
            <RequireAuth>
              <Dashboard />
            </RequireAuth>
          }
        />
        <Route
          path="/checkout/return"
          element={
            <RequireAuth>
              <CheckoutReturn />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Landing />} />
      </Routes>
    </AuthProvider>
  );
}
