import styles from "./ui.module.css";

export function Spinner({ large = false }: { large?: boolean }): JSX.Element {
  return (
    <span
      className={`${styles.spinner} ${large ? styles.spinnerLg : ""}`}
      // Decorative: the surrounding text (or `aria-busy` on the parent) carries
      // the meaning. A spinner announced as "loading" by every screen reader on
      // every render is noise.
      aria-hidden="true"
    />
  );
}

export function FullPageSpinner({ label }: { label?: string }): JSX.Element {
  return (
    <div className={styles.fullPage} role="status" aria-live="polite">
      <Spinner large />
      {label && <span>{label}…</span>}
    </div>
  );
}
