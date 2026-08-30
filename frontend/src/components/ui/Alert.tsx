import type { ReactNode } from "react";

import styles from "./ui.module.css";

interface AlertProps {
  variant?: "error" | "success" | "info";
  children: ReactNode;
}

export function Alert({ variant = "error", children }: AlertProps): JSX.Element {
  const variantClass =
    variant === "success"
      ? styles.alertSuccess
      : variant === "info"
        ? styles.alertInfo
        : styles.alertError;

  return (
    <div
      className={`${styles.alert} ${variantClass}`}
      // Errors interrupt; confirmations wait their turn. An assertive live
      // region on every success toast is the kind of thing that makes screen
      // readers exhausting to use.
      role={variant === "error" ? "alert" : "status"}
    >
      {children}
    </div>
  );
}

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "income" | "expense" | "neutral";
  children: ReactNode;
}): JSX.Element {
  const toneClass =
    tone === "income"
      ? styles.badgeIncome
      : tone === "expense"
        ? styles.badgeExpense
        : styles.badgeNeutral;
  return <span className={`${styles.badge} ${toneClass}`}>{children}</span>;
}
