/**
 * Who is signed in, and the three operations that change that.
 *
 * **The state here is deliberately thin: a token, and whatever `/auth/me`
 * said.** It would be easy to also keep the decoded JWT claims, an `isExpired`
 * timer, the user's preferences. All of that is derived or belongs elsewhere,
 * and every extra field is another thing that can disagree with the server.
 *
 * **`status` is three values, not a boolean.** `loading | authenticated |
 * anonymous` — because on first paint the app genuinely does not know yet. A
 * token exists in storage but has not been verified, and collapsing that into
 * `isAuthenticated: false` makes the app flash the login page for a moment on
 * every refresh before redirecting back. That flash is the single most common
 * bug in hand-rolled SPA auth, and it is a modelling error rather than a timing
 * one: the fix is to represent "don't know yet" rather than to add a delay.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError, login as loginRequest, request, tokenStore } from "@/api/client";
import { useMe } from "@/api/queries";
import type { RegisterPayload, User } from "@/api/types";

type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  status: AuthStatus;
  user: User | null;
  login: (email: string, password: string) => Promise<void>;
  signup: (payload: RegisterPayload) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  // Read once on mount rather than on every render: `localStorage` is
  // synchronous and touching it in a render path is a needless main-thread hit.
  const [token, setToken] = useState<string | null>(() => tokenStore.get());
  const queryClient = useQueryClient();

  // Only asks the server who we are if there is a token worth asking about.
  const meQuery = useMe(Boolean(token));

  const status: AuthStatus = useMemo(() => {
    if (!token) return "anonymous";
    if (meQuery.isPending) return "loading";
    // A token that the server rejects is not a session. Treating a failed
    // `/auth/me` as "anonymous" rather than "authenticated with no user" is
    // what stops a dead token leaving the app in a half-signed-in state where
    // every subsequent request 401s.
    if (meQuery.isError) return "anonymous";
    return meQuery.data ? "authenticated" : "loading";
  }, [token, meQuery.isPending, meQuery.isError, meQuery.data]);

  const clearSession = useCallback(() => {
    tokenStore.clear();
    setToken(null);
    // Not just `invalidate`: the cache holds one user's transactions and
    // totals, and leaving it in place means the next person to sign in on this
    // machine sees the previous person's data render for a frame before it
    // refetches. `clear()` is the difference between a stale cache and a leak.
    queryClient.clear();
  }, [queryClient]);

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await loginRequest(email, password);
      tokenStore.set(result.access_token);
      setToken(result.access_token);
      // The token changed, so the cached "who am I" answer is about to be a
      // different person. Removing it means `useMe` refetches rather than
      // briefly serving the previous session's user.
      queryClient.removeQueries({ queryKey: ["me"] });
    },
    [queryClient],
  );

  const signup = useCallback(
    async (payload: RegisterPayload) => {
      await request<User>("/auth/register", { method: "POST", json: payload });
      // Register returns the user, not a token — the backend keeps minting
      // tokens to exactly one endpoint on purpose. Signing in immediately
      // afterwards is a product decision the client is allowed to make, and it
      // is why "sign up" feels like one step despite being two requests.
      await login(payload.email, payload.password);
    },
    [login],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user: meQuery.data ?? null,
      login,
      signup,
      logout: clearSession,
    }),
    [status, meQuery.data, login, signup, clearSession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    // A thrown error rather than a silent `null` return. Using this hook
    // outside the provider is a wiring mistake that would otherwise surface as
    // "cannot read property status of null" somewhere far from the cause.
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return context;
}

/** True when an error is the API saying "log in again". */
export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.isUnauthorized;
}
