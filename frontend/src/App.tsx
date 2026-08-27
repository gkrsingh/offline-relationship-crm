import { useEffect, useState } from "react";
import { api } from "./api";
import { Duplicates } from "./views/Duplicates";
import { Introductions } from "./views/Introductions";
import { People } from "./views/People";
import { Review } from "./views/Review";

const NAV = [
  ["review", "Today"],
  ["duplicates", "Duplicates"],
  ["introductions", "Introductions"],
  ["people", "People"],
] as const;

export function App() {
  const [view, setView] = useState<string>("review");
  const [person, setPerson] = useState<string | null>(null);
  const [health, setHealth] = useState<any>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
  }, [view]);

  const go = (next: string, arg?: string) => {
    if (next === "person") {
      setPerson(arg ?? null);
      setView("people");
      return;
    }
    if (next === "applicants") {
      setView("people");
      return;
    }
    setView(next);
  };

  const coverage = health?.coverage;

  return (
    <div className="min-h-screen">
      <nav className="sticky top-0 z-20 border-b border-ink/10 bg-paper/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-baseline gap-8 px-8 py-3">
          <button onClick={() => go("review")} className="font-serif text-[19px] text-oxblood">
            Offline
          </button>
          <div className="flex gap-6">
            {NAV.map(([key, label]) => (
              <button
                key={key}
                onClick={() => go(key)}
                className={`text-[13px] transition-colors ${
                  view === key
                    ? "text-ink border-b border-oxblood pb-0.5"
                    : "text-clay hover:text-ink"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {coverage && (
            <div className="ml-auto text-[11px] text-clay" title="AI enrichment coverage">
              enriched {coverage.enriched}/{coverage.canonical} · drafts{" "}
              {coverage.intros_with_copy}/{health.counts.introductions}
            </div>
          )}
        </div>
      </nav>

      {view === "review" && <Review go={go} />}
      {view === "duplicates" && <Duplicates />}
      {view === "introductions" && <Introductions />}
      {view === "people" && <People selected={person} onSelect={setPerson} />}
    </div>
  );
}
