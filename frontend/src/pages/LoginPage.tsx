import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { InputField } from "@/components/ui/Field";
import { WalletIcon } from "@/components/ui/icons";
import styles from "./auth.module.css";

interface LocationState {
  from?: { pathname: string };
}

export function LoginPage(): JSX.Element {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Where the guard sent them from, so a deep link survives the detour through
  // this page. Falls back to the dashboard for a direct visit.
  const destination = (location.state as LocationState | null)?.from?.pathname ?? "/";

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      await login(email, password);
      // `replace`, so the back button from the dashboard does not return to a
      // login page that immediately bounces forward again.
      navigate(destination, { replace: true });
    } catch (caught) {
      // The API answers "Incorrect email or password" for both a wrong password
      // and an unknown account — deliberately, so the endpoint is not an
      // account-enumeration oracle. Showing its message verbatim keeps that
      // property; inventing a more "helpful" one here would undo it.
      setError(caught instanceof Error ? caught.message : "Could not sign in");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.card}>
        <div className={styles.brand}>
          <span className={styles.brandMark}>
            <WalletIcon size={17} />
          </span>
          Finance Tracker
        </div>

        <h1 className={styles.title}>Welcome back</h1>
        <p className={styles.subtitle}>Sign in to see where your money went.</p>

        {/* A real <form>, not a div with a click handler. That is what makes
            Enter submit, what lets password managers recognise and fill the
            fields, and what gives the browser its own validation pass. */}
        <form className={styles.form} onSubmit={handleSubmit} noValidate>
          {error && <Alert>{error}</Alert>}

          <InputField
            label="Email"
            type="email"
            name="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            // `username` rather than `email`: it is the token password managers
            // look for to offer a saved credential pair.
            autoComplete="username"
            placeholder="you@example.com"
            required
            autoFocus
          />

          <InputField
            label="Password"
            type="password"
            name="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            placeholder="••••••••"
            required
          />

          <Button type="submit" variant="primary" loading={submitting} fullWidth>
            Sign in
          </Button>
        </form>

        <p className={styles.footer}>
          No account yet? <Link to="/signup">Create one</Link>
        </p>
      </div>
    </div>
  );
}
