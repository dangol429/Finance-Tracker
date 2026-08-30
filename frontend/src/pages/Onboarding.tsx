import { useState } from "react";

import { useCreateAccount, useCreateCategory } from "@/api/queries";
import type { AccountType } from "@/api/types";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { InputField, SelectField } from "@/components/ui/Field";
import styles from "./pages.module.css";

const ACCOUNT_TYPES: { value: AccountType; label: string }[] = [
  { value: "checking", label: "Current / checking" },
  { value: "savings", label: "Savings" },
  { value: "credit_card", label: "Credit card" },
  { value: "cash", label: "Cash" },
  { value: "investment", label: "Investment" },
];

/**
 * Categories every new account gets, so the first transaction has somewhere to
 * go. Chosen to cover the common cases on both sides of the ledger without
 * pretending to be a complete taxonomy — the point is that the category
 * dropdown is not empty on day one, not that these are the right categories for
 * anyone in particular.
 */
const STARTER_CATEGORIES = [
  { name: "Groceries", type: "expense" as const },
  { name: "Rent", type: "expense" as const },
  { name: "Transport", type: "expense" as const },
  { name: "Eating out", type: "expense" as const },
  { name: "Utilities", type: "expense" as const },
  { name: "Salary", type: "income" as const },
];

/**
 * The first-run screen.
 *
 * Shown when the user owns no accounts, which is every newly registered user.
 * Without it the dashboard is four zeroes above three empty charts and a table
 * that cannot accept a row — technically correct and completely unhelpful, and
 * the exact moment someone decides an app is broken.
 *
 * The starter categories are created alongside the account rather than offered
 * as a second step. A category list is not a decision anyone wants to make
 * before they have entered a single transaction, and they can be added to later.
 */
export function Onboarding(): JSX.Element {
  const [name, setName] = useState("");
  const [type, setType] = useState<AccountType>("checking");
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const createAccount = useCreateAccount();
  const createCategory = useCreateCategory();

  async function handleSubmit(): Promise<void> {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Give the account a name.");
      return;
    }

    setError(null);
    setWorking(true);

    try {
      await createAccount.mutateAsync({ name: trimmed, type });

      // Sequential rather than `Promise.all`. Six requests at once is not a
      // meaningful saving here, and firing them in parallel makes a partial
      // failure much harder to describe — some categories exist, some do not,
      // in no particular order.
      for (const category of STARTER_CATEGORIES) {
        try {
          await createCategory.mutateAsync(category);
        } catch {
          // A 409 means a category by that name already exists, which is a fine
          // outcome for a convenience step. The account — the part that
          // actually unblocks the app — has already been created.
        }
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create the account");
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.onboarding}>
        <h2 className={styles.onboardingTitle}>Let's set up your first account</h2>
        <p className={styles.onboardingBody}>
          Every transaction belongs to an account — a current account, a card, or just
          cash. Name one and you can start recording. A handful of starter categories
          come with it, and you can change all of it later.
        </p>

        {error && <Alert>{error}</Alert>}

        <div className={styles.onboardingForm}>
          <InputField
            label="Account name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Everyday current account"
            onKeyDown={(event) => {
              if (event.key === "Enter") void handleSubmit();
            }}
            autoFocus
          />
          <SelectField
            label="Type"
            value={type}
            onChange={(event) => setType(event.target.value as AccountType)}
          >
            {ACCOUNT_TYPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </SelectField>
          <Button variant="primary" onClick={handleSubmit} loading={working}>
            Create account
          </Button>
        </div>

        <p className={styles.seedNote}>
          Already have history elsewhere? Once this exists you can bulk-import a bank
          statement CSV from the Transactions page — it parses the file, skips rows it
          cannot read unambiguously, and tells you which ones and why.
        </p>
      </div>
    </div>
  );
}
