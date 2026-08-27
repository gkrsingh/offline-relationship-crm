import { useCallback, useEffect, useState } from "react";
import { api, type DuplicatePair, type MergeGroup, type PersonRow } from "../api";
import { ConfidenceRule, Empty, NotEnriched, PersonName } from "../components";

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
              <div className="mb-2 flex items-center gap-3">
                <span className="label">
                  {pair.stage === "llm" ? "Model verdict" : "Pipeline verdict"}
                </span>
                <span className="text-[13px]">
                  {pair.verdict.replace(/_/g, " ")}
                </span>
                {pair.confidence != null && (
                  <ConfidenceRule value={pair.confidence} />
                )}
              </div>
              {pair.reason && (
                <p className="ai-field text-[13px] leading-relaxed text-ink/80">
                  {pair.reason}
                </p>
              )}
              <div className="mt-2 text-[11px] text-clay">
                fuzzy score {pair.score?.toFixed(2)} · surfaced by{" "}
                {pair.blocking_keys.join(", ") || "—"} · method {pair.method}
              </div>
            </div>
          </div>

          <div className="mt-5 flex items-center gap-3">
            <button className="btn-primary" disabled={busy} onClick={() => decide("merge")}>
              Merge <span className="kbd ml-1.5">M</span>
            </button>
            <button className="btn-ghost" disabled={busy} onClick={() => decide("keep_both")}>
              Keep both <span className="kbd ml-1.5">K</span>
            </button>
            <button className="btn-ghost" disabled={busy} onClick={() => decide("not_sure")}>
              Not sure <span className="kbd ml-1.5">S</span>
            </button>
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

  const label: Record<string, string> = {
    stage1_exact: "exact identifier",
    stage2_fuzzy: "fuzzy score",
    llm: "model adjudication",
    human: "operator",
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
          <div key={g.id} className="flex items-center gap-4 px-4 py-3">
            <div className="min-w-0 flex-1">
              <PersonName
                name={(g.resolved.full_name as string) ?? g.canonical_person_id}
                size="sm"
              />
              <div className="mt-0.5 text-[12px] text-clay">
                {g.source_record_ids.join(" + ")} → {g.canonical_person_id} ·
                decided by {label[g.decided_by] ?? g.decided_by}
              </div>
            </div>
            <div className="text-[12px] text-clay">
              {(g.resolved.email as string) || "no email"}
            </div>
            <button
              className="btn-ghost"
              disabled={busy === g.id}
              onClick={() => undo(g.id)}
            >
              Undo
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function PersonColumn({ person, other }: { person: PersonRow; other: PersonRow }) {
  return (
    <div className="px-6 py-5">
      <PersonName name={person.full_name} />
      <div className="mb-4 mt-1 text-[11px] text-clay">{person.id}</div>

      <dl className="space-y-2.5">
        {FIELDS.map(([key, label]) => {
          const mine = person[key] as string | null;
          const theirs = other[key] as string | null;
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
              {person.enrichment.persona.replace(/_/g, " ")}
            </div>
          </div>
        ) : (
          <NotEnriched compact />
        )}
      </div>
    </div>
  );
}
