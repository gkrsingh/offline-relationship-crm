import { useEffect, useState } from "react";
import { api } from "../api";
import { Band, CompletenessBar, Empty, PersonName } from "../components";

/** The landing page is a work queue, not a dashboard.
 *
 *  A dashboard tells you how things are going. A queue tells you what to do
 *  next, and every row here is a thing an operator can act on -- so each one
 *  routes straight into the screen where the action happens. There is no
 *  "total records" tile, because nobody has ever done anything about one. */
export function Review({ go }: { go: (view: string, arg?: string) => void }) {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    api.queue().then(setData).catch(() => setData({ error: true }));
  }, []);

  if (!data) return <Empty>Loading the queue…</Empty>;
  if (data.error) return <Empty>Could not reach the API.</Empty>;

  const coverage = data.coverage;
  const pct = Math.round((coverage.enriched / Math.max(1, coverage.canonical)) * 100);

  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      <header className="mb-10">
        <h1 className="font-serif text-[38px] leading-none">Today</h1>
        <p className="mt-2 text-[14px] text-clay">
          Four things are waiting. Everything here is a decision only a person
          can make.
        </p>
        {pct < 100 && (
          <p className="mt-3 text-[12px] text-review">
            AI enrichment covers {coverage.enriched} of {coverage.canonical} people
            ({pct}%). Records the backfill has not reached are marked, never guessed.
          </p>
        )}
      </header>

      <Section
        title="Needs your decision"
        count={data.duplicates.count}
        blurb={
          `The pipeline resolved ${data.duplicates.auto_resolved} pairs into ` +
          `${data.duplicates.merged_clusters} merged records without asking. ` +
          (data.duplicates.count === 0
            ? "Nothing is genuinely ambiguous right now."
            : `${data.duplicates.count} it will not settle on its own — ` +
              "either it abstained, or two records disagree on a field it refuses to pick between.")
        }
        cta={data.duplicates.count ? "Review" : "See what merged"}
        onGo={() => go("duplicates")}
      >
        {data.duplicates.items.map((d: any) => (
          <Row key={d.id} onClick={() => go("duplicates")}>
            <span className="font-serif text-[16px]">
              {d.person_a_id} · {d.person_b_id}
            </span>
            <span className="text-[13px] text-clay">{d.reason}</span>
            <span className="text-[12px] tabular-nums text-clay">
              {d.score?.toFixed?.(1)}
            </span>
          </Row>
        ))}
      </Section>

      <Section
        title="Incomplete records"
        count={data.incomplete.count}
        blurb={`${data.incomplete.blocked} cannot be acted on at all — no email or no company.`}
        cta="Open people"
        onGo={() => go("people")}
      >
        {data.incomplete.items.map((p: any) => (
          <Row key={p.id} onClick={() => go("person", p.id)}>
            <PersonName name={p.full_name} size="sm" />
            <span className="text-[13px] text-clay">{p.completeness.summary}</span>
            <CompletenessBar c={p.completeness} />
          </Row>
        ))}
      </Section>

      <Section
        title="Applicants needing review"
        count={data.applicants.needs_review}
        blurb={`${data.applicants.strong} scored strong, ${data.applicants.count} total. The score is arithmetic; the decision is not.`}
        cta="Open applicants"
        onGo={() => go("applicants")}
      >
        {data.applicants.items.map((a: any) => (
          <Row key={a.person_id} onClick={() => go("person", a.person_id)}>
            <PersonName name={a.full_name} size="sm" />
            <span className="text-[13px] text-clay">
              {a.title} · {a.company}
            </span>
            <span className="flex items-center gap-3">
              <span className="text-[13px] tabular-nums">{Math.round(a.total)}</span>
              <Band band={a.band} />
            </span>
          </Row>
        ))}
      </Section>

      <Section
        title="Suggested introductions"
        count={data.introductions.count}
        blurb={
          data.introductions.with_copy < data.introductions.count
            ? `${data.introductions.with_copy} have drafted copy; the rest are still being written.`
            : "Every suggestion needs your approval. Nothing sends itself."
        }
        cta="Open introductions"
        onGo={() => go("introductions")}
      />
    </div>
  );
}

function Section({
  title,
  count,
  blurb,
  cta,
  onGo,
  children,
}: {
  title: string;
  count: number;
  blurb: string;
  cta: string;
  onGo: () => void;
  children?: React.ReactNode;
}) {
  return (
    <section className="mb-9">
      <div className="mb-3 flex items-baseline justify-between border-b border-ink/10 pb-2">
        <div className="flex items-baseline gap-3">
          <h2 className="font-serif text-[24px]">{title}</h2>
          <span className="text-[13px] tabular-nums text-oxblood">{count}</span>
        </div>
        <button className="btn-ghost" onClick={onGo}>
          {cta} →
        </button>
      </div>
      <p className="mb-3 text-[13px] text-clay">{blurb}</p>
      {children && <div className="divide-y divide-ink/6">{children}</div>}
    </section>
  );
}

function Row({
  children,
  onClick,
}: {
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="grid w-full grid-cols-[minmax(150px,1fr)_2fr_auto] items-center gap-4 py-2.5 text-left hover:bg-ink/[0.02]"
    >
      {children}
    </button>
  );
}
