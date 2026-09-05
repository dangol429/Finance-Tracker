import { useState } from "react";
import type { FormEvent } from "react";

import { useAccounts, useAskAi } from "@/api/queries";
import { ApiError } from "@/api/client";
import { Evidence } from "@/components/ai/Evidence";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { BareInput } from "@/components/ui/Field";
import { SendIcon, SparkleIcon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/Skeleton";
import { Onboarding } from "./Onboarding";
import aiStyles from "@/components/ai/ai.module.css";
import styles from "./pages.module.css";

/**
 * Ask a question about your own spending, in plain English.
 *
 * The screen is deliberately plain: a box, an answer, and the queries behind
 * it. Two decisions are worth explaining.
 *
 * **It is not a chat.** There is no thread, no history, no follow-up. Each
 * question is answered independently, because the server treats it that way —
 * `POST /ai/query` carries one question and no prior turns. Rendering a
 * transcript would imply a memory the API does not have, and the first "and
 * what about May?" would come back answered as if May had never been mentioned.
 * A single question and a single answer is the honest shape of the endpoint it
 * is built on.
 *
 * **The evidence is part of the answer, not a debug panel.** Every figure the
 * model states comes from a SQL aggregate, and `<Evidence>` shows which one.
 * That is what makes the difference between a feature a user can rely on for a
 * number they care about and a party trick.
 *
 * The example questions are not decoration either. The single hardest thing
 * about a free-text box is knowing what it will accept, and three concrete
 * examples answer that faster than any placeholder text.
 */

const EXAMPLES = [
  "How much did I spend on groceries last month?",
  "What was my biggest expense this year?",
  "How does my spending this month compare to last month?",
];

export function AskPage(): JSX.Element {
  const [question, setQuestion] = useState("");
  const accounts = useAccounts();
  const ask = useAskAi();

  // Kept separate from `question` so the answer stays labelled with the
  // question that produced it. Without this, typing a new question would
  // silently re-caption the answer still on screen with text that did not
  // produce it — the kind of small lie that makes a feature feel untrustworthy.
  const [asked, setAsked] = useState<string | null>(null);

  function submit(event: FormEvent): void {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || ask.isPending) return;
    setAsked(trimmed);
    ask.mutate(trimmed);
  }

  function askExample(example: string): void {
    setQuestion(example);
    setAsked(example);
    ask.mutate(example);
  }

  if (accounts.isLoading) {
    return <div className={styles.page} />;
  }

  // Nothing to ask about yet. The generic empty state would say "no results",
  // which is true and useless; a brand-new user needs to be sent to the thing
  // that has to happen first.
  if (accounts.data && accounts.data.length === 0) {
    return <Onboarding />;
  }

  // A 503 means the server has no API key configured. That is a deployment
  // fact rather than something the user did, so it gets its own message
  // instead of the raw error text — and the question box is left disabled
  // rather than removed, so the feature's existence is still discoverable.
  const notConfigured = ask.error instanceof ApiError && ask.error.status === 503;

  return (
    <div className={styles.page}>
      <Card
        title="Ask about your spending"
        subtitle="Answered from your own transactions — every figure comes from a query you can inspect."
      >
        <form className={aiStyles.askForm} onSubmit={submit}>
          <BareInput
            className={aiStyles.askInput}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="How much did I spend on food in June?"
            maxLength={500}
            aria-label="Your question"
            disabled={ask.isPending}
          />
          <Button
            type="submit"
            variant="primary"
            loading={ask.isPending}
            disabled={!question.trim()}
          >
            <SendIcon size={15} />
            Ask
          </Button>
        </form>

        <div className={aiStyles.suggestions}>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              className={aiStyles.suggestionChip}
              onClick={() => askExample(example)}
              disabled={ask.isPending}
            >
              {example}
            </button>
          ))}
        </div>
      </Card>

      {notConfigured && (
        <Alert variant="info">
          This server has no AI provider configured, so questions cannot be answered. Set{" "}
          <code>ANTHROPIC_API_KEY</code> on the API and restart it.
        </Alert>
      )}

      {ask.error && !notConfigured && <Alert>{ask.error.message}</Alert>}

      {ask.isPending && (
        <Card title={asked ?? "Thinking"}>
          {/* Skeleton lines rather than a spinner: the shape of what is coming
              is known — two or three lines of prose — so reserving that space
              means the page does not jump when the answer lands. */}
          <Skeleton height="1em" width="94%" />
          <Skeleton height="1em" width="80%" />
        </Card>
      )}

      {ask.data && !ask.isPending && (
        <Card
          title={ask.data.question}
          action={
            <span className={aiStyles.aiMark}>
              <SparkleIcon size={12} />
              AI
            </span>
          }
        >
          <p className={aiStyles.answer}>{ask.data.answer}</p>

          <Evidence steps={ask.data.evidence} />

          {ask.data.evidence.length === 0 && (
            // No tool ran, so no figure in this answer came from the ledger.
            // Saying so is the honest complement to the evidence panel: the
            // absence of a receipt should be as visible as its presence.
            <p className={aiStyles.answerMeta}>
              This answer used no transaction data.
            </p>
          )}

          <div className={aiStyles.answerMeta}>
            <span>{ask.data.model}</span>
            <span>
              {ask.data.usage.input_tokens.toLocaleString()} in ·{" "}
              {ask.data.usage.output_tokens.toLocaleString()} out
            </span>
          </div>
        </Card>
      )}

      {!ask.data && !ask.isPending && !ask.error && (
        <EmptyState
          icon={<SparkleIcon size={22} />}
          title="Ask a question to get started"
          body="Questions are answered from your recorded transactions. Nothing is estimated — if the data isn't there, the answer says so."
        />
      )}
    </div>
  );
}
