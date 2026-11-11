"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, use, useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, unwrap, type User } from "@/lib/api/client";
import { session } from "@/lib/auth/session";

type AuthState =
  | { status: "loading"; user: null }
  | { status: "anonymous"; user: null }
  | { status: "authenticated"; user: User };

type RegisterInput = { email: string; password: string; full_name: string };

type AuthContextValue = AuthState & {
  login: (email: string, password: string) => Promise<User>;
  register: (input: RegisterInput) => Promise<User>;
  logout: () => Promise<void>;
  setUser: (user: User) => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const fetchCurrentUser = () => unwrap(api.GET("/api/v1/users/me"));

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<AuthState>({ status: "loading", user: null });

  const signOutLocally = useCallback(() => {
    session.clear();
    queryClient.clear();
    setState({ status: "anonymous", user: null });
  }, [queryClient]);

  // Restore the session on load: the refresh cookie (if any) yields a new access token.
  useEffect(() => {
    let active = true;
    session
      .refresh()
      .then((ok) => (ok ? fetchCurrentUser() : null))
      .then((user) => {
        if (active) setState(user ? { status: "authenticated", user } : { status: "anonymous", user: null });
      })
      .catch(() => {
        if (active) setState({ status: "anonymous", user: null });
      });
    const unsubscribe = session.onExpired(signOutLocally);
    return () => {
      active = false;
      unsubscribe();
    };
  }, [signOutLocally]);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await unwrap(api.POST("/api/v1/auth/login", { body: { email, password } }));
    session.setTokens(tokens.access_token, tokens.expires_in);
    const user = await fetchCurrentUser();
    setState({ status: "authenticated", user });
    return user;
  }, []);

  const register = useCallback(
    async (input: RegisterInput) => {
      await unwrap(api.POST("/api/v1/auth/register", { body: input }));
      return login(input.email, input.password);
    },
    [login],
  );

  const logout = useCallback(async () => {
    try {
      await unwrap(api.POST("/api/v1/auth/logout"));
    } finally {
      signOutLocally();
    }
  }, [signOutLocally]);

  const setUser = useCallback((user: User) => setState({ status: "authenticated", user }), []);

  const value = useMemo(
    () => ({ ...state, login, register, logout, setUser }),
    [state, login, register, logout, setUser],
  );
  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthContextValue {
  const context = use(AuthContext);
  if (!context) throw new Error("useAuth must be used within <AuthProvider>");
  return context;
}

/** For components rendered only behind <AuthGuard>. */
export function useCurrentUser(): User {
  const { user } = useAuth();
  if (!user) throw new Error("useCurrentUser must be used behind <AuthGuard>");
  return user;
}
