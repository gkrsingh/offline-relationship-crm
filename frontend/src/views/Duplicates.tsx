import { useCallback, useEffect, useState } from "react";
import { api, type DuplicatePair, type MergeGroup, type PersonRow } from "../api";
import {
  Button,
  ConfidenceRule,
  Empty,
  NotEnriched,
  PersonName,
  Score,
} from "../components";
import {
  blockingKeys,
  joinParts,
  label as pretty,
  matchReason,
  mergeReason,
  method as methodName,
  verdict as verdictName,
} from "../labels";

/* Header and rows share one template so the merge log lines up column by
   column instead of every row sizing itself. */
const LOG_GRID =
  "grid w-full grid-cols-[minmax(0,1fr)_minmax(0,14rem)_5rem] items-center gap-4";

const FIELDS: [keyof PersonRow, string][] = [
  ["full_name", "name"],
  ["email", "email"],
  ["linkedin_url", "linkedin"],
  ["company", "company"],
  ["title", "title"],
  ["location", "location"],
  ["bio", "bio"],
  ["source", "source"],
  ["created_at", "first seen"],
];

function norm(v: unknown): string {
  return String(v ?? "").trim().toLowerCase().replace(/\s+/g, " ");
}

/** Two screens, one route, and the split is the argument.
 *
 *  Most duplicates are not decisions. An identical email is an identical email,
 *  and putting forty-seven of those in front of a person as "confirmations"
 *  buries the three that genuinely need judgment. So the queue holds only what
 *  the pipeline refused to settle, and everything it merged goes to a log that
 *  can be undone. Reversible, not approved. */
export function Duplicates() {
  const [tab, setTab] = useState<"decide" | "merged">("decide");
  const [pairs, setPairs] = useState<DuplicatePair[]>([]);
  const [index, setIndex] = useState(0);
  const [remaining, setRemaining] = useState(0);
  const [autoResolved, setAutoResolved] = useState(0);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    api.duplicates("pending").then((d) => {
      setPairs(d.pairs);
      setRemaining(d.remaining);
      setAutoResolved(d.auto_resolved);
      setLoaded(true);
    });
  }, []);

  const pair = pairs[index];

  const decide = useCallback(
    async (decision: "merge" | "keep_both" | "not_sure") => {
      if (!pair || busy || tab !== "decide") return;
      setBusy(true);
      try {
        const res = await api.decideDuplicate(pair.id, decision);
        setRemaining(res.remaining);
        setIndex((i) => i + 1);
      } finally {
        setBusy(false);
      }
    },
    [pair, busy, tab],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "m") decide("merge");
      if (k === "k") decide("keep_both");
      if (k === "s") decide("not_sure");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [decide]);

  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      <header className="mb-6">
        <h1 className="font-serif text-[34px] leading-tight">
          The pipeline resolved{" "}
          <span className="text-oxblood">{autoResolved}</span> pairs on its own.
        </h1>
        <p className="mt-2 max-w-2xl text-[14px] text-clay">
          {remaining === 0 ? (
            <>Nothing is left that it refuses to settle. That is the result, not an
            empty screen — every merge below is still reversible.</>
          ) : (
            <>
              <span className="text-ink">{remaining}</span>{" "}
              {remaining === 1 ? "pair needs" : "pairs need"} a human. Either the
              adjudicator abstained, or survivorship found two values it will not
              pick between.
            </>
          )}
        </p>
        <div className="mt-4 flex gap-5">
          {(
            [
              ["decide", `Needs your decision (${remaining})`],
              ["merged", "Recently merged"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`text-[13px] ${
                tab === key
                  ? "border-b border-oxblood pb-0.5 text-ink"
                  : "text-clay hover:text-ink"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      {tab === "merged" ? (
        <MergeLog />
      ) : !loaded ? (
        <Empty>Loading…</Empty>
      ) : !pair ? (
        <Empty>
          <div className="font-serif text-[24px] text-ink">Queue clear</div>
          <div className="mt-2">
            Everything ambiguous has been decided. The merge log is one tab over.
          </div>
        </Empty>
      ) : (
        <>
          <div className="card overflow-hidden">
            {pair.conflicts.length > 0 && (
              <div className="border-b border-review/25 bg-review/8 px-6 py-3">
                <div className="label text-review">held on a field conflict</div>
                {pair.conflicts.map((conflict, i) => (
                  <div key={i} className="mt-1 text-[13px]">
                    <span className="font-medium">{conflict.field}</span>:{" "}
                    {conflict.values.join("  vs  ")}
                    <span className="text-clay"> — {conflict.detail}</span>
                  </div>
                ))}
                <div className="mt-1 text-[12px] text-clay">
                  The pipeline believes these are the same person but will not
                  choose which value survives.
                </div>
              </div>
            )}

            <div className="grid grid-cols-2 divide-x divide-ink/8">
              <PersonColumn person={pair.a} other={pair.b} />
              <PersonColumn person={pair.b} other={pair.a} />
            </div>

            <div className="border-t border-ink/8 bg-ink/[0.015] px-6 py-5">
              <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-2">
                <span className="label">
                  {pair.stage === "llm" ? "Model verdict" : "Pipeline verdict"}
                </span>
                <span className="text-[13px]">{verdictName(pair.verdict)}</span>
                {pair.confidence != null && (
                  <ConfidenceRule value={pair.confidence} />
                )}
                <span className="ml-auto">
                  <Score value={pair.score} label="match score" align="right" />
                </span>
              </div>
              {pair.reason && (
                <p className="ai-field text-[13px] leading-relaxed text-ink/80">
                  {matchReason(pair.reason)}
                </p>
              )}
              <div className="mt-2 text-[12px] text-clay">
                Surfaced by {blockingKeys(pair.blocking_keys)} · matched on{" "}
                {methodName(pair.method)}
              </div>
            </div>
          </div>

          <div className="mt-5 flex items-center gap-3">
            <Button rank="primary" disabled={busy} onClick={() => decide("merge")}>
              Merge <span className="kbd ml-1.5">M</span>
            </Button>
            <Button rank="secondary" disabled={busy} onClick={() => decide("keep_both")}>
              Keep both <span className="kbd ml-1.5">K</span>
            </Button>
            <Button rank="quiet" disabled={busy} onClick={() => decide("not_sure")}>
              Not sure <span className="kbd ml-1.5">S</span>
            </Button>
            <span className="ml-auto text-[12px] text-clay">
              Source rows are never deleted — any merge can be undone.
            </span>
          </div>
        </>
      )}
    </div>
  );
}

function MergeLog() {
  const [merges, setMerges] = useState<MergeGroup[]>([]);
  const [reverted, setReverted] = useState(0);
  const [busy, setBusy] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = () =>
    api.merges().then((d) => {
      setMerges(d.merges);
      setReverted(d.reverted);
      setLoaded(true);
    });

  useEffect(() => {
    load();
  }, []);

  const undo = async (id: number) => {
    setBusy(id);
    try {
      await api.undoMerge(id);
      setMerges((m) => m.filter((g) => g.id !== id));
      setReverted((r) => r + 1);
    } finally {
      setBusy(null);
    }
  };

  if (!loaded) return <Empty>Loading…</Empty>;
  if (merges.length === 0) return <Empty>Nothing has been merged.</Empty>;

  return (
    <div>
      <p className="mb-3 text-[13px] text-clay">
        {merges.length} merged records{reverted > 0 && `, ${reverted} already undone`}.
        Each one folded two or more rows into a canonical record; the originals are
        untouched underneath.
      </p>
      <div className="card divide-y divide-ink/6">
        {merges.map((g) => (
          <div key={g.id} className={`${LOG_GRID} px-4 py-3`}>
            {/* The row used to read "p-0006 + p-0073 → p-0006 · decided by
                stage 1", which is the log line the pipeline writes, not a
                sentence. The person leads, then what happened in words, and
                the ids stay as small metadata underneath -- never in serif. */}
            <span className="block min-w-0">
              <PersonName name={(g.resolved.full_name as string) ?? null} size="sm" />
              <span className="mt-0.5 block truncate text-[12px] text-clay">
                {g.source_record_ids.length} records folded into one ·{" "}
                {mergeReason(g.decided_by)}
              </span>
              <span className="mt-0.5 block truncate text-[11px] text-clay/80">
                {g.source_record_ids.join(" + ")} → {g.canonical_person_id}
              </span>
            </span>
            <div className="truncate text-[12px] text-clay">
              {(g.resolved.email as string) || "no email"}
            </div>
            <div className="text-right">
              <Button rank="secondary" disabled={busy === g.id} onClick={() => undo(g.id)}>
                Undo
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PersonColumn({ person, other }: { person: PersonRow; other: PersonRow }) {
  return (
    <div className="px-6 py-5">
      {/* Raw on purpose. This screen exists to show the two records as they
          arrived; re-casing the name here would hide the very noise the
          operator is being asked to judge. */}
      <PersonName name={person.full_name} raw />
      <div className="mb-4 mt-1 text-[11px] text-clay">
        {joinParts([person.title, person.company, person.id])}
      </div>

      <dl className="space-y-2.5">
        {FIELDS.map(([key, label]) => {
          const raw = person[key] as string | null;
          const theirsRaw = other[key] as string | null;
          // `source` is enum-backed: airtable_export, newsletter_signup.
          // Comparison still runs on the raw values; only display is mapped.
          const mine = key === "source" ? pretty(raw) || raw : raw;
          const theirs = key === "source" ? pretty(theirsRaw) || theirsRaw : theirsRaw;
          const both = Boolean(norm(mine) && norm(theirs));
          const same = both && norm(mine) === norm(theirs);
          const conflict = both && !same;

          return (
            <div key={String(key)}>
              <dt className={`label ${same ? "opacity-35" : ""}`}>{label}</dt>
              <dd
                className={[
                  "text-[13px] break-words",
                  same ? "text-ink/30" : "",
                  conflict ? "bg-review/8 -mx-1 px-1 text-ink" : "",
                  !mine ? "italic text-clay" : "",
                ].join(" ")}
              >
                {mine || "missing"}
              </dd>
            </div>
          );
        })}
      </dl>

      <div className="mt-5 border-t border-ink/8 pt-4">
        {person.enrichment ? (
          <div className="ai-field">
            <div className="label mb-1">persona</div>
            <div className="text-[13px]">
              {pretty(person.enrichment.persona)}
            </div>
          </div>
        ) : (
          <NotEnriched compact />
        )}
      </div>
    </div>
  );
}
