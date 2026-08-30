import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import styles from "./ui.module.css";

type Variant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "sm" | "md";
  /** Shows a spinner alongside the label and disables the button. */
  loading?: boolean;
  iconOnly?: boolean;
  fullWidth?: boolean;
  children?: ReactNode;
}

/**
 * `forwardRef` because callers need the DOM node, not the component.
 *
 * `ConfirmDialog` focuses its confirm button on open, which is what makes the
 * dialog usable by keyboard. Without ref forwarding that `ref` prop is silently
 * dropped — React warns in development and the focus simply never happens, so
 * the bug is easy to ship and invisible to anyone using a mouse.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    loading = false,
    iconOnly = false,
    fullWidth = false,
    disabled,
    className,
    children,
    ...rest
  },
  ref,
) {
  const classes = [
    styles.button,
    styles[variant],
    size === "sm" && styles.sizeSm,
    iconOnly && styles.iconOnly,
    fullWidth && styles.fullWidth,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      ref={ref}
      // `type="button"` by default. HTML's default is `submit`, so a button
      // placed inside a form for any other purpose — cancel, toggle, delete —
      // silently submits it. That is the most common bug in hand-written forms,
      // and it is fixed once here rather than remembered at every call site.
      type="button"
      className={classes}
      disabled={disabled || loading}
      // Tells assistive technology the control is busy, which the spinner
      // conveys to everyone else.
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <span className={styles.spinner} aria-hidden="true" />}
      {children}
    </button>
  );
});
