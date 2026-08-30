import { Navigate, Route, Routes } from "react-router-dom";

import { RequireAnonymous, RequireAuth } from "@/auth/ProtectedRoute";
import { AppShell } from "@/components/layout/AppShell";
import { DashboardPage } from "@/pages/DashboardPage";
import { LoginPage } from "@/pages/LoginPage";
import { SignupPage } from "@/pages/SignupPage";
import { TransactionsPage } from "@/pages/TransactionsPage";

/**
 * The route tree.
 *
 * Guards are *layout routes* wrapping groups rather than wrappers around each
 * element, so protection is a property of where a route sits in the tree. That
 * makes the dangerous mistake — adding a page and forgetting to protect it —
 * structurally hard: a new `<Route>` inside the `RequireAuth` block is
 * protected by virtue of being there.
 */
export function App(): JSX.Element {
  return (
    <Routes>
      <Route element={<RequireAnonymous />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
      </Route>

      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/transactions" element={<TransactionsPage />} />
        </Route>
      </Route>

      {/* Anything else goes home rather than to a 404 page. This app has two
          screens; a dedicated not-found page would be more ceremony than the
          situation deserves, and an unknown URL here is nearly always a stale
          link rather than a typo worth explaining. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
