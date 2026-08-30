/**
 * Route guards — the two halves of "who may see this page".
 *
 * Written as layout routes wrapping an `<Outlet />` rather than as a wrapper
 * around each page element. That means the guard is declared once per *group*
 * of routes in the router tree, so adding a page inside the protected block is
 * a route entry rather than a route entry plus remembering the wrapper. The
 * failure mode it removes is the important one: a new page that is unprotected
 * because someone forgot a step, which looks fine in every test that logs in
 * first.
 */

import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "./AuthContext";
import { FullPageSpinner } from "@/components/ui/Spinner";

/** Requires a session. Sends anonymous visitors to /login. */
export function RequireAuth(): JSX.Element {
  const { status } = useAuth();
  const location = useLocation();

  // The reason `status` has three values. Rendering the redirect here would
  // bounce a signed-in user to the login page for one frame on every hard
  // refresh, because the token has not been verified yet.
  if (status === "loading") return <FullPageSpinner label="Signing you in" />;

  if (status === "anonymous") {
    // `state.from` is what makes "deep link → login → land where you meant to
    // go" work, and `replace` keeps the guarded URL out of history so the back
    // button after logging out doesn't return to a page that redirects again.
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}

/**
 * The mirror image: /login and /signup should not be reachable while signed in.
 *
 * Without this, a signed-in user following a bookmarked /login link gets a form
 * that, on submit, logs them in as possibly someone else — and more mundanely,
 * the back button after signing in lands on the login page again, which feels
 * like the sign-in did not take.
 */
export function RequireAnonymous(): JSX.Element {
  const { status } = useAuth();

  if (status === "loading") return <FullPageSpinner label="Loading" />;
  if (status === "authenticated") return <Navigate to="/" replace />;

  return <Outlet />;
}
