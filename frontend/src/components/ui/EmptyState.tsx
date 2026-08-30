import type { ReactNode } from "react";

import styles from "./ui.module.css";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  body?: string;
  action?: ReactNode;
}

/**
 * What a screen says when there is nothing to show.
 *
 * Worth a component because the alternative — rendering nothing, or the words
 * "No data" — leaves the user unable to tell three very different situations
 * apart: they are new and have not added anything yet, their filters excluded
 * everything, or something failed. Each needs a different sentence and a
 * different next action, so every caller passes its own copy rather than
 * sharing a generic one.
 */
export function EmptyState({ icon, title, body, action }: EmptyStateProps): JSX.Element {
  return (
    <div className={styles.empty}>
      {icon && <div className={styles.emptyIcon}>{icon}</div>}
      <p className={styles.emptyTitle}>{title}</p>
      {body && <p className={styles.emptyBody}>{body}</p>}
      {action && <div className={styles.emptyAction}>{action}</div>}
    </div>
  );
}
