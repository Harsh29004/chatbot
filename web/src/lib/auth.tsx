import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Navigate, useLocation } from "react-router-dom";

import { api, type Customer } from "./api";

interface AuthState {
  customer: Customer | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, name: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [loading, setLoading] = useState(true);

  // One "who am I" call on boot. A 401 here is the normal signed-out case,
  // not an error worth surfacing.
  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (!cancelled) setCustomer(me);
      })
      .catch(() => {
        if (!cancelled) setCustomer(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    setCustomer(await api.login(email, password));
  }, []);

  const signUp = useCallback(
    async (email: string, password: string, name: string) => {
      setCustomer(await api.signup(email, password, name));
    },
    [],
  );

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      // Clear locally even if the network call failed — the user asked to
      // leave, so the UI should reflect that immediately.
      setCustomer(null);
    }
  }, []);

  const value = useMemo(
    () => ({ customer, loading, signIn, signUp, signOut }),
    [customer, loading, signIn, signUp, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

/** Gate for signed-in-only routes. Sends people to sign-in and back again. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { customer, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="auth-page">
        <div className="spinner" />
      </div>
    );
  }

  if (!customer) {
    return <Navigate to="/signin" state={{ from: location.pathname }} replace />;
  }

  return <>{children}</>;
}
