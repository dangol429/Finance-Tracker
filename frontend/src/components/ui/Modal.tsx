import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

import { Button } from "./Button";
import styles from "./ui.module.css";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * A confirmation dialog, shown before anything irreversible.
 *
 * Three behaviours separate a real dialog from a styled div, and a user notices
 * each one by its absence:
 *
 *   - **Escape closes it.** Nothing is more irritating than a modal with no
 *     visible way out once you have already decided against it.
 *   - **Focus moves into it on open.** Otherwise a keyboard user's focus is
 *     still behind the overlay, tabbing through content they cannot see.
 *   - **Clicking the backdrop cancels, clicking inside does not.** The event
 *     target has to be compared against the backdrop itself, or every click on
 *     the dialog body closes it.
 *
 * A full focus *trap* (cycling Tab within the dialog) is deliberately left out.
 * Doing it properly takes real care, and this dialog holds two buttons and
 * nothing else focusable — so the gap is small, and naming it here is more
 * useful than a half-implementation that looks like it works.
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = "Confirm",
  destructive = false,
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps): JSX.Element | null {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;

    confirmRef.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKeyDown);

    // The page behind must not scroll while a modal is open. On a phone it is
    // otherwise possible to scroll the content away and be left looking at a
    // dialog floating over nothing.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className={styles.modalBackdrop}
      onClick={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div className={styles.modal} role="dialog" aria-modal="true" aria-label={title}>
        <div className={styles.modalBody}>
          <p className={styles.modalTitle}>{title}</p>
          <div className={styles.modalText}>{body}</div>
          <div className={styles.modalActions}>
            <Button variant="ghost" onClick={onCancel} disabled={loading}>
              Cancel
            </Button>
            <Button
              ref={confirmRef}
              variant={destructive ? "danger" : "primary"}
              onClick={onConfirm}
              loading={loading}
            >
              {confirmLabel}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
