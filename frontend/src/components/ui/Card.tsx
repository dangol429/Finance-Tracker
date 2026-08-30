import type { ReactNode } from "react";

import styles from "./ui.module.css";

interface CardProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  /** Removes body padding — for a table that should meet the card's edges. */
  flush?: boolean;
  className?: string;
  children: ReactNode;
}

export function Card({
  title,
  subtitle,
  action,
  flush = false,
  className,
  children,
}: CardProps): JSX.Element {
  return (
    <section className={`${styles.card} ${className ?? ""}`}>
      {(title || action) && (
        <header className={styles.cardHeader}>
          <div>
            {title && <h2 className={styles.cardTitle}>{title}</h2>}
            {subtitle && <p className={styles.cardSubtitle}>{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className={flush ? styles.cardBodyFlush : styles.cardBody}>{children}</div>
    </section>
  );
}
