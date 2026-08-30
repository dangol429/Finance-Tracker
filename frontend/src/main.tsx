import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import { ApiError, tokenStore } from "./api/client";
import { AuthProvider } from "./auth/AuthContext";
import { applyTheme, resolveInitialTheme } from "./hooks/useTheme";
import "./styles/global.css";

// Before the first render, not in an effect. A theme applied after mount
// arrives one paint too late: the page renders dark, then flips. See
// `resolveInitialTheme`.
applyTheme(resolveInitialTheme());

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A dashboard is read far more often than the data behind it changes.
      // Thirty seconds means moving between pages serves from cache instead of
      // re-fetching identical rows, while anything genuinely new still arrives
      // quickly.
      staleTime: 30_000,

      // Refetching every time the window regains focus is TanStack's default
      // and it is wrong for this app: alt-tabbing back to a dashboard should
      // not make every chart flicker. `staleTime` already covers the case where
      // the data is actually old.
      refetchOnWindowFocus: false,

      retry: (failureCount, error) => {
        // Never retry a 4xx. A 401 will 401 again, and a 422 is a bug in the
        // request — retrying either just delays the error the user needs to
        // see. Server and network errors are worth two more attempts.
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          return false;
        }
        return failureCount < 2;
      },
    },
    mutations: {
      // Writes are never retried automatically. `POST /transactions` is not
      // idempotent, so a retry after a response that was merely slow to arrive
      // records the transaction twice — and a duplicate in a financial ledger
      // is worse than an error message.
      retry: false,
    },
  },
});

// A 401 anywhere means the session is over. Handling it centrally rather than
// in each hook is what stops one expired token producing eight separate error
// toasts as every query on the dashboard fails in turn.
queryClient.getQueryCache().config.onError = (error) => {
  if (error instanceof ApiError && error.isUnauthorized) {
    tokenStore.clear();
  }
};

const container = document.getElementById("root");
if (!container) throw new Error("#root is missing from index.html");

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        {/* AuthProvider is inside QueryClientProvider because it uses a query
            (`/auth/me`) to resolve the session, and outside BrowserRouter's
            routes so the guards can read it. */}
        <AuthProvider>
          <App />
        </AuthProvider>
      </QueryClientProvider>
    </BrowserRouter>
  </StrictMode>,
);
