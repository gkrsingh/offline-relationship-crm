import { useState } from "react";
import type { Completeness, Enrichment, Evidence } from "./api";
import { classifiedBy, joinParts, label as pretty, score100, titleCase } from "./labels";

/** Confidence as a hairline whose length is the score. Not a coloured badge:
 *  a badge says "high/low" categorically, a rule shows you the actual quantity
 *  and takes no visual weight away from the person's name. */
export function ConfidenceRule({ value, width = 64 }: { value: number; width?: number }) {
  return (
    <span className="inline-flex items-center gap-1.5 align-middle" title={`confidence ${value.toFixed(2)}`}>
      <span className="block h-px bg-ink/12" style={{ width }}>
        <span
          className="block h-px bg-oxblood"
          style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }}
        />
      </span>
      <span className="text-[10px] tabular-nums text-clay">{value.toFixed(2)}</span>
    </span>
  );
}

/** An AI-derived value. Carries the oxblood hairline, and its evidence quote
 *  on hover. If we cannot show what the model read, we do not show the value
 *  as though it were established. */
export function AiValue({
  label,
  value,
  evidence,
}: {
  label: string;
  value: string | null | undefined;
  evidence?: Evidence[];
}) {
  const support = evidence?.filter((e) => e.supports === label.toLowerCase().replace(/ /g, "_"));
  const quote = support?.[0];
  const unknown = !value || value === "unknown";
  return (
    <div className="ai-field group relative">
      <div className="label">{label}</div>
      <div className={unknown ? "text-clay italic text-[13px]" : "text-[14px]"}>
        {unknown ? "not stated in the record" : pretty(value)}
      </div>
      {quote && (
        <div className="pointer-events-none absolute left-3 top-full z-30 mt-1 hidden w-72 rounded-sm border border-ink/12 bg-white p-2.5 shadow-lg group-hover:block">
          <div className="label mb-1">read from {quote.field}</div>
          <div className="text-[12px] italic text-ink/80">“{quote.quote}”</div>
        </div>
      )}
    </div>
  );
}

/** The honest empty state. A record the backfill has not reached says so, in
 *  its own words, rather than rendering blanks that read as a broken screen. */
export function NotEnriched({ compact = false }: { compact?: boolean }) {
  return (
    <div
      className={`ai-field ${compact ? "py-1" : "py-2"} text-[13px] text-clay italic`}
      title="AI enrichment runs as a background backfill; this record is queued."
    >
      Not yet enriched — queued for the next backfill pass.
    </div>
  );
}

export function CompletenessBar({ c }: { c: Completeness }) {
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <span className="block h-px w-24 bg-ink/12">
          <span
            className="block h-px"
            style={{
              width: `${c.score * 100}%`,
              background: c.blocked ? "#B45309" : "#1A1614",
            }}
          />
        </span>
        <span className="text-[11px] tabular-nums text-clay">
          {Math.round(c.score * 100)}%
        </span>
      </div>
      {c.blocked_reason && (
        <div className="mt-1 text-[12px] text-review">{c.blocked_reason}</div>
      )}
    </div>
  );
}

/** A person's name, in the serif.
 *
 *  Shouting source data ("UMA KUMAR") is re-cased for display, because the caps
 *  are an artefact of the export and not how anyone writes their name. Pass
 *  `raw` wherever the screen is showing the record AS evidence -- the duplicate
 *  compare card -- since there the messy original is the point. The stored
 *  value is never altered either way. */
export function PersonName({
  name,
  size = "lg",
  raw = false,
}: {
  name: string | null;
  size?: "sm" | "lg";
  raw?: boolean;
}) {
  const shown = raw ? name?.trim() : titleCase(name);
  return (
    <span
      className={`font-serif ${size === "lg" ? "text-[22px]" : "text-[17px]"} leading-tight`}
    >
      {shown || <span className="text-clay italic">unnamed record</span>}
    </span>
  );
}

export function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn-ghost"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          /* clipboard unavailable; the draft is selectable above */
        }
        setDone(true);
        setTimeout(() => setDone(false), 1600);
      }}
    >
      {done ? "Copied" : "Copy draft"}
    </button>
  );
}

export function Band({ band }: { band: string }) {
  const tone =
    band === "strong"
      ? "text-approve border-approve/40"
      : band === "review"
        ? "text-review border-review/40"
        : "text-clay border-clay/40";
  return (
    <span className={`border-b pb-px text-[11px] uppercase tracking-[0.12em] ${tone}`}>
      {band}
    </span>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="card p-8 text-center text-[14px] text-clay">{children}</div>
  );
}

export function EnrichmentBlock({ e }: { e: Enrichment }) {
  if (!e) return <NotEnriched />;
  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <div className="label">AI enrichment</div>
        <ConfidenceRule value={e.confidence} />
      </div>
      <div className="grid grid-cols-2 gap-x-5 gap-y-3">
        <AiValue label="persona" value={e.persona} evidence={e.evidence} />
        <AiValue label="seniority" value={e.seniority} evidence={e.evidence} />
        <AiValue label="company stage" value={e.company_stage} evidence={e.evidence} />
        <AiValue label="sector" value={e.sector} evidence={e.evidence} />
        <AiValue label="geography" value={e.geography} evidence={e.evidence} />
      </div>
      <div className="mt-3 text-[11px] text-clay">
        {classifiedBy(e.model)} · {e.evidence_verified}/{e.evidence_total} evidence
        quotes verified against the record
      </div>
    </div>
  );
}

/** One button, three ranks. `rank` is the only knob. */
export function Button({
  rank = "secondary",
  children,
  ...rest
}: {
  rank?: "primary" | "secondary" | "quiet";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={`btn-${rank}`} {...rest}>
      {children}
    </button>
  );
}

/** Every score on every screen: a 0-100 integer, and a label saying which
 *  scale it is on. A bare "0.56" on an introduction card tells a reader
 *  nothing about whether that is good. */
export function Score({
  value,
  label: what,
  align = "left",
}: {
  value: number | null | undefined;
  label: string;
  align?: "left" | "right";
}) {
  return (
    <span className={align === "right" ? "block text-right" : "block"}>
      <span className="block text-[13px] tabular-nums">
        {score100(value)}
        <span className="text-clay">/100</span>
      </span>
      <span className="label block">{what}</span>
    </span>
  );
}

/** A person, named. Serif is reserved for the name; the id is metadata and is
 *  never set in the serif, because an id is not a person. */
export function PersonLine({
  name,
  id,
  detail,
  size = "sm",
  raw = false,
}: {
  name: string | null;
  id?: string | null;
  detail?: (string | null | undefined)[];
  size?: "sm" | "lg";
  raw?: boolean;
}) {
  const sub = joinParts((detail ?? []).map((d) => (raw ? d : titleCase(d))));
  return (
    <span className="block min-w-0">
      <PersonName name={name} size={size} raw={raw} />
      {(sub || id) && (
        <span className="mt-0.5 block truncate text-[11px] text-clay">
          {joinParts([sub, id])}
        </span>
      )}
    </span>
  );
}

/** The row template every list on every screen shares.
 *
 *  These were flex rows, so each one sized its own columns from its own
 *  content and nothing lined up between them -- one reason string started
 *  fifty pixels right of the one above it, and the completeness bars sat at
 *  different x positions down the page. A fixed grid template fixes the
 *  columns for the whole list regardless of what any single row contains. */
export const ROW_GRID =
  "grid w-full grid-cols-[minmax(0,15rem)_minmax(0,1fr)_9rem] items-center gap-4";

export function ListRow({
  children,
  onClick,
}: {
  children: React.ReactNode;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`${ROW_GRID} px-1 py-2.5 text-left hover:bg-ink/[0.02]`}
    >
      {children}
    </button>
  );
}
