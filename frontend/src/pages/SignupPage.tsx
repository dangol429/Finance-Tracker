import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { InputField } from "@/components/ui/Field";
import { WalletIcon } from "@/components/ui/icons";
import styles from "./auth.module.css";

/** Mirrors the backend's `UserCreate.password` floor, so the user is told
 *  before a round trip rather than by a 422 afterwards. */
const MIN_PASSWORD_LENGTH = 8;

export function SignupPage(): JSX.Element {
  const { signup } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setPasswordError(null);

    // Client-side validation as a *courtesy*, never as the enforcement. The
    // server checks this too — it has to, since anything can POST to it — so
    // this exists only to answer faster and in the right place on the form.
    if (password.length < MIN_PASSWORD_LENGTH) {
      setPasswordError(`At least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }

    setSubmitting(true);
    try {
      await signup({ email, password });
      navigate("/", { replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create your account");
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

        <h1 className={styles.title}>Create your account</h1>
        <p className={styles.subtitle}>Track spending, spot the patterns.</p>

        <form className={styles.form} onSubmit={handleSubmit} noValidate>
          {error && <Alert>{error}</Alert>}

          <InputField
            label="Email"
            type="email"
            name="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
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
            onChange={(event) => {
              setPassword(event.target.value);
              // Clear the error as soon as the input could plausibly be valid.
              // Leaving a red field under a corrected value is the most common
              // way form validation feels hostile.
              if (passwordError && event.target.value.length >= MIN_PASSWORD_LENGTH) {
                setPasswordError(null);
              }
            }}
            autoComplete="new-password"
            placeholder="At least 8 characters"
            error={passwordError}
            hint="Length beats symbols — a passphrase is fine."
            required
          />

          <Button type="submit" variant="primary" loading={submitting} fullWidth>
            Create account
          </Button>
        </form>

        <p className={styles.footer}>
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
