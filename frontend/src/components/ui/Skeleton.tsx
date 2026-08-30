import styles from "./ui.module.css";

interface SkeletonProps {
  width?: string;
  height?: string;
  radius?: string;
  className?: string;
}

/**
 * A placeholder in the shape of the content that is loading.
 *
 * Sized by the caller rather than guessing, because the whole value of a
 * skeleton over a spinner is that the layout does not move when real data
 * arrives. A skeleton the wrong size is a spinner with extra steps.
 */
export function Skeleton({
  width = "100%",
  height = "1em",
  radius,
  className,
}: SkeletonProps): JSX.Element {
  return (
    <span
      className={`${styles.skeleton} ${className ?? ""}`}
      style={{ width, height, display: "block", borderRadius: radius }}
      aria-hidden="true"
    />
  );
}

/** A few lines of placeholder text, each slightly different in width so the
 *  block reads as prose rather than as a bar chart. */
export function SkeletonText({ lines = 3 }: { lines?: number }): JSX.Element {
  const widths = ["100%", "92%", "78%", "85%", "64%"];
  return (
    <span aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton
          key={index}
          className={styles.skeletonText}
          width={widths[index % widths.length]}
        />
      ))}
    </span>
  );
}
