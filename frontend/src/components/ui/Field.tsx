import { useId } from "react";
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

import styles from "./ui.module.css";

interface FieldWrapperProps {
  label: string;
  error?: string | null;
  hint?: string;
  children: (props: { id: string; describedBy: string | undefined }) => ReactNode;
}

/**
 * Label, control, error and hint, wired together by id.
 *
 * `useId` rather than a hand-passed prop or a module counter: the association
 * between a label and its input is what lets a screen reader announce the field
 * and what makes clicking the label focus the control. Generating ids by hand
 * is the step that gets skipped, and nothing visibly breaks when it does —
 * which is exactly why it should not be a step.
 */
function FieldWrapper({ label, error, hint, children }: FieldWrapperProps): JSX.Element {
  const id = useId();
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  const describedBy = error ? errorId : hint ? hintId : undefined;

  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={id}>
        {label}
      </label>
      {children({ id, describedBy })}
      {error && (
        // role="alert" so the message is announced when it appears, rather than
        // only being found if the user happens to navigate back to it.
        <span className={styles.errorText} id={errorId} role="alert">
          {error}
        </span>
      )}
      {!error && hint && (
        <span className={styles.hint} id={hintId}>
          {hint}
        </span>
      )}
    </div>
  );
}

interface InputFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  error?: string | null;
  hint?: string;
}

export function InputField({
  label,
  error,
  hint,
  className,
  ...rest
}: InputFieldProps): JSX.Element {
  return (
    <FieldWrapper label={label} error={error} hint={hint}>
      {({ id, describedBy }) => (
        <input
          id={id}
          className={`${styles.input} ${error ? styles.inputError : ""} ${className ?? ""}`}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          {...rest}
        />
      )}
    </FieldWrapper>
  );
}

interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "id"> {
  label: string;
  error?: string | null;
  hint?: string;
  children: ReactNode;
}

export function SelectField({
  label,
  error,
  hint,
  className,
  children,
  ...rest
}: SelectFieldProps): JSX.Element {
  return (
    <FieldWrapper label={label} error={error} hint={hint}>
      {({ id, describedBy }) => (
        <select
          id={id}
          className={`${styles.select} ${error ? styles.inputError : ""} ${className ?? ""}`}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          {...rest}
        >
          {children}
        </select>
      )}
    </FieldWrapper>
  );
}

/** A bare input for use inside a table row, where the column header is the label. */
export function BareInput({
  className,
  invalid,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }): JSX.Element {
  return (
    <input
      className={`${styles.input} ${invalid ? styles.inputError : ""} ${className ?? ""}`}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
}

export function BareSelect({
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement>): JSX.Element {
  return (
    <select className={`${styles.select} ${className ?? ""}`} {...rest}>
      {children}
    </select>
  );
}
