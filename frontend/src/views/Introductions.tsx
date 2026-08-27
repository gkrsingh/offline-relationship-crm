import { useEffect, useState } from "react";
import { api, type Introduction } from "../api";
import { CopyButton, Empty, PersonName } from "../components";
import { label } from "../labels";

/** The product screen.
 *
 *  A card is one pair and one decision. The matched need and offer sit above
 *  the prose because they are the reason the pair exists -- the model's
 *  sentence is a rendering of that match, not the evidence for it. If the copy
 *  has not been written yet the card still works: the match is the substance. */
export function Introductions() {
  const [items, setItems] = useState<Introduction[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [status, setStatus] = useState("suggested");
  const [busy, setBusy] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = (s: string) => {
    setLoaded(false);
    api.introductions(s).then((d) => {
      setItems(d.introductions);
      setCounts(d.counts);
      setLoaded(true);
    });
  };

  useEffect(() => load(status), [status]);

  const decide = async (id: number, decision: string) => {
    setBusy(id);
    try {
      await api.decideIntro(id, decision);
      setItems((prev) => prev.filter((i) => i.id !== id));
      setCounts((c) => ({ ...c, suggested: Math.max(0, (c.suggested ?? 1) - 1) }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-8 py-10">
      <header className="mb-6">
        <h1 className="font-serif text-[32px]">Introductions</h1>
        <p className="mt-1 text-[14px] text-clay">
          Matched on what one person needs and another offers. Nothing sends
          itself — every card below is a proposal.
        </p>
        <div className="mt-4 flex gap-2">
          {["suggested", "approved", "dismissed"].map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`btn ${
                status === s
                  ? "border-oxblood text-oxblood"
                  : "border-transparent text-clay hover:text-ink"
              }`}
            >
              {s} {counts[s] != null && <span className="tabular-nums">({counts[s]})</span>}
            </button>
          ))}
        </div>
      </header>

      {!loaded && <Empty>Loading…</Empty>}
      {loaded && items.length === 0 && (
        <Empty>Nothing {status === "suggested" ? "waiting" : `marked ${status}`}.</Empty>
      )}

      <div className="space-y-5">
        {items.map((intro) => (
          <article key={intro.id} className="card p-6">
            <div className="mb-4 flex items-start justify-between gap-6">
              <Side person={intro.a} role="A" />
              <div className="mt-3 shrink-0 text-center">
                <div className="font-serif text-[20px] text-oxblood">↕</div>
                <div className="mt-1 text-[11px] tabular-nums text-clay">
                  {intro.score.toFixed(2)}
                </div>
              </div>
              <Side person={intro.b} role="B" align="right" />
            </div>

            <div className="mb-4 grid grid-cols-2 gap-4 border-y border-ink/8 py-3">
              <div>
                <div className="label">A needs</div>
                <div className="text-[13px]">{intro.matched_need || "—"}</div>
              </div>
              <div>
                <div className="label">B offers</div>
                <div className="text-[13px]">{intro.matched_offer || "—"}</div>
              </div>
            </div>

            {intro.has_copy ? (
              <div className="space-y-3">
                <p className="ai-field text-[14px] leading-relaxed">{intro.why}</p>
                <div className="grid grid-cols-2 gap-4">
                  <div className="ai-field">
                    <div className="label">what A gets</div>
                    <div className="text-[13px]">{intro.a_gets}</div>
                  </div>
                  <div className="ai-field">
                    <div className="label">what B gets</div>
                    <div className="text-[13px]">{intro.b_gets}</div>
                  </div>
                </div>
                <div className="ai-field">
                  <div className="label mb-1">draft introduction</div>
                  <p className="whitespace-pre-wrap rounded-sm bg-ink/[0.025] p-3 text-[13px] leading-relaxed">
                    {intro.draft_message}
                  </p>
                </div>
              </div>
            ) : (
              <p className="ai-field text-[13px] italic text-clay">
                The draft has not been written yet — the copy backfill is still
                running. The match above is complete and reviewable now.
              </p>
            )}

            <div className="mt-5 flex items-center gap-3">
              <button
                className="btn-approve"
                disabled={busy === intro.id}
                onClick={() => decide(intro.id, "approve")}
              >
                Approve
              </button>
              <button
                className="btn-ghost"
                disabled={busy === intro.id}
                onClick={() => decide(intro.id, "dismiss")}
              >
                Dismiss
              </button>
              <button
                className="btn-ghost"
                disabled={busy === intro.id}
                onClick={() => decide(intro.id, "block")}
              >
                Never suggest this pair
              </button>
              {intro.has_copy && <div className="ml-auto"><CopyButton text={intro.draft_message!} /></div>}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function Side({
  person,
  role,
  align = "left",
}: {
  person: Introduction["a"];
  role: string;
  align?: "left" | "right";
}) {
  return (
    <div className={`flex-1 ${align === "right" ? "text-right" : ""}`}>
      <div className="label mb-1">{role}</div>
      <PersonName name={person.full_name} size="sm" />
      <div className="mt-1 text-[13px] text-clay">
        {[person.title, person.company].filter(Boolean).join(" · ") || "—"}
      </div>
      {person.enrichment ? (
        <div className={`mt-1 text-[11px] text-clay ${align === "right" ? "" : ""}`}>
          <span className="border-l border-oxblood/70 pl-2">
            {label(person.enrichment.persona)}
          </span>
        </div>
      ) : (
        <div className="mt-1 text-[11px] italic text-clay">not yet enriched</div>
      )}
    </div>
  );
}
