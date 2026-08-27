import { useEffect, useState } from "react";
import { api, type PersonRow } from "../api";
import { introState, joinParts, label, titleCase } from "../labels";
import {
  Band,
  CompletenessBar,
  Empty,
  EnrichmentBlock,
  NotEnriched,
  PersonLine,
  PersonName,
  Score,
} from "../components";

/* One template for the header and every row, so columns line up down the
   whole table. Previously each row sized its own columns from its own
   content, so a row carrying a FIT badge pushed its completeness bar out of
   line with the rows above it. */
const TABLE_GRID =
  "grid w-full grid-cols-[minmax(0,1.5fr)_minmax(0,1.2fr)_10rem_9rem_5.5rem] items-center gap-4";

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
  // Only 40 of 257 people are applicants, so the fit column is empty on most
  // rows. Rather than hide it -- which makes the table change shape as filters
  // move, and removes a useful scanning signal -- every non-applicant gets an
  // explicit em-dash, and this filter gives a dense view on demand.
  const [applicantsOnly, setApplicantsOnly] = useState(false);

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

  // Applicant status is already on every row, so this filter needs no request.
  const visible = applicantsOnly ? rows.filter((p) => p.applicant) : rows;

  return (
    <div className="flex">
      <div className={`flex-1 px-8 py-10 ${selected ? "max-w-3xl" : "mx-auto max-w-6xl"}`}>
        <header className="mb-5">
          <h1 className="font-serif text-[32px]">People</h1>
          <p className="mt-1 text-[13px] text-clay">
            {visible.length === total
              ? `${total} canonical records. Merged duplicates are folded away.`
              : `${visible.length} of ${total} canonical records.`}
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
          <label className="flex items-center gap-2 text-[13px] text-clay">
            <input
              type="checkbox"
              checked={applicantsOnly}
              onChange={(e) => setApplicantsOnly(e.target.checked)}
              className="accent-oxblood"
            />
            applicants only
          </label>
        </div>

        <div className="card overflow-hidden">
          <div className={`${TABLE_GRID} border-b border-ink/10 px-4 py-2`}>
            {["name", "company", "persona", "completeness", "fit"].map((h) => (
              <div key={h} className="label">{h}</div>
            ))}
          </div>
          <div className="divide-y divide-ink/6">
            {visible.map((p) => (
              <button
                key={p.id}
                onClick={() => onSelect(p.id)}
                className={`${TABLE_GRID} px-4 py-2.5 text-left hover:bg-ink/[0.02] ${
                  selected === p.id ? "bg-oxblood/[0.04]" : ""
                }`}
              >
                <PersonLine name={p.full_name} detail={[p.title]} />
                <div className="truncate text-[13px]">{titleCase(p.company) || <span className="italic text-clay">no company</span>}</div>
                <div className="truncate text-[13px]">
                  {p.enrichment ? (
                    <span className="border-l border-oxblood/70 pl-2">
                      {label(p.enrichment.persona)}
                    </span>
                  ) : (
                    <span className="text-[11px] italic text-clay">not yet enriched</span>
                  )}
                </div>
                <CompletenessBar c={p.completeness} />
                <div className="text-right">
                  {p.applicant ? (
                    <span className="block">
                      <span className="block text-[13px] tabular-nums">
                        {Math.round(p.applicant.total)}
                        <span className="text-clay">/100</span>
                      </span>
                      <Band band={p.applicant.band} />
                    </span>
                  ) : (
                    <span className="text-[13px] text-clay" title="not an applicant">—</span>
                  )}
                </div>
              </button>
            ))}
            {visible.length === 0 && (
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
    /* The panel is a column: a header that does not move and a body that
       scrolls under it. Scrolling to the applicant score used to carry the
       person's name off the top, leaving five numbers with nobody attached. */
    <aside className="sticky top-0 flex h-screen w-[440px] shrink-0 flex-col overflow-hidden border-l border-ink/10 bg-white">
      <div className="shrink-0 border-b border-ink/8 bg-white px-7 pb-4 pt-8">
        <button onClick={onClose} className="mb-5 text-[12px] text-clay hover:text-ink">
          ← close
        </button>

        <PersonName name={person.full_name} />
        <div className="mt-1 text-[13px] text-clay">
          {joinParts([titleCase(person.title), titleCase(person.company)]) || "—"}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-8">
      <Block title="Source record" first>
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
          {/* Serif is for people's names. A score is not a person. */}
          <div className="mb-3 flex items-baseline gap-3">
            <span className="text-[28px] tabular-nums">{Math.round(applicant.total)}</span>
            <span className="text-[13px] text-clay">/ 100 fit</span>
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
              <div className="flex items-baseline justify-between gap-3">
                <PersonName name={s.other?.full_name ?? "—"} size="sm" />
                <Score value={s.score} label="match" align="right" />
              </div>
              <div className="text-[12px] text-clay">{titleCase(s.other?.company)}</div>
              {s.why && <p className="ai-field mt-1 text-[12px]">{s.why}</p>}
              <div className="mt-1 text-[11px] text-clay">{introState(s.status)}</div>
            </div>
          ))}
        </Block>
      )}
      </div>
    </aside>
  );
}

function Block({
  title,
  first = false,
  children,
}: {
  title: string;
  first?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className={first ? "pt-6" : "mt-7 border-t border-ink/8 pt-5"}>
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
