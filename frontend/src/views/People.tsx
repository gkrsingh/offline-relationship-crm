import { useEffect, useState } from "react";
import { api, type PersonRow } from "../api";
import { label } from "../labels";
import {
  Band,
  CompletenessBar,
  Empty,
  EnrichmentBlock,
  NotEnriched,
  PersonName,
} from "../components";

const PERSONAS = ["", "founder", "operator", "investor", "service_provider", "ic", "unknown"];

export function People({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const [rows, setRows] = useState<PersonRow[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [persona, setPersona] = useState("");
  const [incomplete, setIncomplete] = useState(false);

  useEffect(() => {
    const params: Record<string, string> = {};
    if (q) params.q = q;
    if (persona) params.persona = persona;
    if (incomplete) params.incomplete = "true";
    const t = setTimeout(() => {
      api.people(params).then((d) => {
        setRows(d.people);
        setTotal(d.total);
      });
    }, 150);
    return () => clearTimeout(t);
  }, [q, persona, incomplete]);

  return (
    <div className="flex">
      <div className={`flex-1 px-8 py-10 ${selected ? "max-w-3xl" : "mx-auto max-w-6xl"}`}>
        <header className="mb-5">
          <h1 className="font-serif text-[32px]">People</h1>
          <p className="mt-1 text-[13px] text-clay">
            {total} canonical records. Merged duplicates are folded away.
          </p>
        </header>

        <div className="mb-4 flex flex-wrap items-center gap-3">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search name, company, title, email…"
            className="w-72 border-b border-ink/20 bg-transparent py-1.5 text-[14px] outline-none placeholder:text-clay/70 focus:border-oxblood"
          />
          <select
            value={persona}
            onChange={(e) => setPersona(e.target.value)}
            className="border-b border-ink/20 bg-transparent py-1.5 text-[13px] outline-none focus:border-oxblood"
          >
            {PERSONAS.map((p) => (
              <option key={p} value={p}>
                {p ? label(p) : "all personas"}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-[13px] text-clay">
            <input
              type="checkbox"
              checked={incomplete}
              onChange={(e) => setIncomplete(e.target.checked)}
              className="accent-oxblood"
            />
            incomplete only
          </label>
        </div>

        <div className="card overflow-hidden">
          <div className="grid grid-cols-[1.4fr_1.2fr_0.9fr_0.9fr_auto] gap-4 border-b border-ink/10 px-4 py-2">
            {["name", "company", "persona", "completeness", "fit"].map((h) => (
              <div key={h} className="label">{h}</div>
            ))}
          </div>
          <div className="divide-y divide-ink/6">
            {rows.map((p) => (
              <button
                key={p.id}
                onClick={() => onSelect(p.id)}
                className={`grid w-full grid-cols-[1.4fr_1.2fr_0.9fr_0.9fr_auto] items-center gap-4 px-4 py-2.5 text-left hover:bg-ink/[0.02] ${
                  selected === p.id ? "bg-oxblood/[0.04]" : ""
                }`}
              >
                <div>
                  <PersonName name={p.full_name} size="sm" />
                  <div className="text-[11px] text-clay">{p.title || "—"}</div>
                </div>
                <div className="text-[13px]">{p.company || <span className="italic text-clay">no company</span>}</div>
                <div className="text-[13px]">
                  {p.enrichment ? (
                    <span className="border-l border-oxblood/70 pl-2">
                      {label(p.enrichment.persona)}
                    </span>
                  ) : (
                    <span className="text-[11px] italic text-clay">not yet enriched</span>
                  )}
                </div>
                <CompletenessBar c={p.completeness} />
                <div>{p.applicant ? <Band band={p.applicant.band} /> : null}</div>
              </button>
            ))}
            {rows.length === 0 && (
              <div className="p-8 text-center text-[14px] text-clay">
                Nothing matches that filter.
              </div>
            )}
          </div>
        </div>
      </div>

      {selected && <Detail id={selected} onClose={() => onSelect(null)} />}
    </div>
  );
}

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    setData(null);
    api.person(id).then(setData);
  }, [id]);

  if (!data) return <aside className="w-[440px] border-l border-ink/10 p-8">Loading…</aside>;

  const { person, enrichment, completeness, applicant, suggestions } = data;

  return (
    <aside className="sticky top-0 h-screen w-[440px] shrink-0 overflow-y-auto border-l border-ink/10 bg-white px-7 py-8">
      <button onClick={onClose} className="mb-5 text-[12px] text-clay hover:text-ink">
        ← close
      </button>

      <PersonName name={person.full_name} />
      <div className="mt-1 text-[13px] text-clay">
        {[person.title, person.company].filter(Boolean).join(" · ") || "—"}
      </div>

      <Block title="Source record">
        <Field label="email" value={person.email} />
        <Field label="linkedin" value={person.linkedin_url} />
        <Field label="location" value={person.location} />
        <Field label="source" value={label(person.source)} />
        <Field label="first seen" value={person.created_at} />
        {person.bio && (
          <div className="mt-2">
            <div className="label">bio</div>
            <p className="text-[13px] leading-relaxed">{person.bio}</p>
          </div>
        )}
      </Block>

      <Block title="">
        {enrichment ? <EnrichmentBlock e={enrichment} /> : <NotEnriched />}
      </Block>

      <Block title="Completeness">
        <CompletenessBar c={completeness} />
        <div className="mt-1 text-[13px] text-clay">{completeness.summary}</div>
      </Block>

      {applicant && (
        <Block title="Applicant fit">
          <div className="mb-3 flex items-baseline gap-3">
            <span className="font-serif text-[28px]">{Math.round(applicant.total)}</span>
            <span className="text-[13px] text-clay">/ 100</span>
            <Band band={applicant.band} />
          </div>

          {applicant.signals.map((s: any) => (
            <div key={s.name} className="mb-2.5">
              <div className="flex items-baseline justify-between">
                <span className="text-[12px]">{label(s.name)}</span>
                <span className="text-[11px] tabular-nums text-clay">
                  {s.points} / {s.out_of}
                </span>
              </div>
              <span className="mt-1 block h-px w-full bg-ink/10">
                <span
                  className="block h-px bg-ink/60"
                  style={{ width: `${(s.points / s.out_of) * 100}%` }}
                />
              </span>
              <div className="mt-1 text-[11px] text-clay">{s.signal}</div>
            </div>
          ))}

          {applicant.signals.some((s: any) => s.basis?.includes("unknown")) && (
            <p className="mt-3 rounded-sm bg-review/8 p-2.5 text-[12px] text-review">
              This score is limited by a thin record — some components fell back to
              “unknown” because the source data does not say. It reflects what we
              know, not a judgment about the person.
            </p>
          )}

          {applicant.explanation && (
            <div className="ai-field mt-4">
              <div className="label mb-1">
                {applicant.explanation_kind === "why_not" ? "why not" : "why"}
              </div>
              <p className="text-[13px] leading-relaxed">{applicant.explanation}</p>
              <ul className="mt-2 space-y-1">
                {applicant.bullets.map((b: string, i: number) => (
                  <li key={i} className="text-[12px] text-ink/75">— {b}</li>
                ))}
              </ul>
            </div>
          )}
        </Block>
      )}

      {suggestions?.length > 0 && (
        <Block title="Suggested introductions">
          {suggestions.map((s: any) => (
            <div key={s.id} className="mb-3 border-b border-ink/6 pb-3 last:border-0">
              <div className="flex items-baseline justify-between">
                <PersonName name={s.other?.full_name ?? "—"} size="sm" />
                <span className="text-[11px] tabular-nums text-clay">
                  {s.score.toFixed(2)}
                </span>
              </div>
              <div className="text-[12px] text-clay">{s.other?.company}</div>
              {s.why && <p className="ai-field mt-1 text-[12px]">{s.why}</p>}
              <div className="mt-1 text-[11px] text-clay">status: {s.status}</div>
            </div>
          ))}
        </Block>
      )}
    </aside>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-7 border-t border-ink/8 pt-5">
      {title && <div className="label mb-3">{title}</div>}
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="mb-1.5">
      <div className="label">{label}</div>
      <div className={`text-[13px] break-words ${!value ? "italic text-clay" : ""}`}>
        {value || "missing"}
      </div>
    </div>
  );
}
