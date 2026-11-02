import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { API_BASE_URL, apiFetch, errorMessage, setCsrfToken } from "../api/http";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [expiresAt, setExpiresAt] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sessionError, setSessionError] = useState("");

  const clearSession = useCallback(() => {
    setCsrfToken(null);
    setUser(null);
    setExpiresAt(null);
    setSessionError("");
    setLoading(false);
  }, []);

  useEffect(() => {
    let active = true;
    const onExpired = () => {
      if (active) clearSession();
    };
    window.addEventListener("pms:auth-expired", onExpired);

    apiFetch(`${API_BASE_URL}/auth/me`)
      .then(async (response) => {
        if (response.status === 401) return null;
        if (!response.ok) {
          throw new Error(await errorMessage(response, "Could not check your session."));
        }
        return response.json();
      })
      .then((session) => {
        if (!active) return;
        setCsrfToken(session?.csrf_token || null);
        setUser(session?.user || null);
        setExpiresAt(session?.expires_at || null);
        setLoading(false);
      })
      .catch((error) => {
        if (active) {
          clearSession();
          setSessionError(error.message || "Could not check your session.");
        }
      });

    return () => {
      active = false;
      window.removeEventListener("pms:auth-expired", onExpired);
    };
  }, [clearSession]);

  useEffect(() => {
    if (!expiresAt) return undefined;
    const remaining = Date.parse(expiresAt) - Date.now();
    const timeout = window.setTimeout(clearSession, Math.max(0, remaining));
    return () => window.clearTimeout(timeout);
  }, [clearSession, expiresAt]);

  const login = useCallback(async (username, password) => {
    const response = await apiFetch(`${API_BASE_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
      throw new Error(await errorMessage(response, "Could not sign in."));
    }
    const session = await response.json();
    setCsrfToken(session.csrf_token);
    setUser(session.user);
    setExpiresAt(session.expires_at);
    setSessionError("");
  }, []);

  const logout = useCallback(async () => {
    const response = await apiFetch(`${API_BASE_URL}/auth/logout`, { method: "POST" });
    if (!response.ok) {
      throw new Error(await errorMessage(response, "Could not sign out."));
    }
    clearSession();
  }, [clearSession]);

  const value = useMemo(
    () => ({ user, loading, sessionError, login, logout }),
    [user, loading, sessionError, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used within AuthProvider.");
  return auth;
}
