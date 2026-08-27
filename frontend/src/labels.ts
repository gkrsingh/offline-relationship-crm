/** Enum values, in the words a person would use.
 *
 *  The API speaks in closed enums on purpose -- `series_a` is a stable key that
 *  the matcher and the rubric can both rely on. But `series_a.replace("_"," ")`
 *  puts "series a" on the screen, which reads as a debug value leaking through.
 *  This is the one place the translation happens.
 */
const LABELS: Record<string, string> = {
  // persona
  founder: "Founder",
  operator: "Operator",
  investor: "Investor",
  service_provider: "Service provider",
  ic: "Individual contributor",

  // seniority
  c_level: "C-level",
  vp: "VP",
  director: "Director",
  head_or_lead: "Head / Lead",
  senior_ic: "Senior IC",
  mid: "Mid-level",
  junior: "Junior",

  // company stage
  pre_seed: "Pre-seed",
  seed: "Seed",
  series_a: "Series A",
  series_b: "Series B",
  growth: "Growth-stage",
  public: "Public",
  not_applicable: "Not a startup role",

  // sector
  b2b_saas: "B2B SaaS",
  fintech: "Fintech",
  healthtech: "Healthtech",
  climate: "Climate tech",
  devtools: "Developer tools",
  ecommerce: "E-commerce",
  marketplace: "Marketplaces",
  ai_infra: "AI infrastructure",
  cybersecurity: "Cybersecurity",
  logistics: "Logistics",
  edtech: "Edtech",
  consumer: "Consumer apps",
  investing: "Investing",
  services: "Services",
  other: "Other",

  // geography
  india: "India",
  south_east_asia: "South-East Asia",
  middle_east: "Middle East",
  europe: "Europe",
  north_america: "North America",
  latin_america: "Latin America",
  africa: "Africa",
  apac: "APAC",

  // record source -- these come straight off the source data, so they are the
  // one place a raw key reaches the page unless it is translated here
  airtable_export: "Airtable export",
  event_signup: "Event signup",
  referral: "Referral",
  linkedin_import: "LinkedIn import",
  newsletter_signup: "Newsletter signup",
  portfolio_intro: "Portfolio introduction",
  applicant_form: "Membership application",

  // scoring components
  persona_fit: "Persona fit",
  seniority: "Seniority",
  company_stage: "Company stage",
  referral_signal: "Referral",
  profile_signal: "Profile signal",
};

/** `unknown` is handled by the caller, which says "not stated in the record" --
 *  a different and more honest sentence than any label could be. */
export function label(value: string | null | undefined): string {
  if (!value) return "";
  return LABELS[value] ?? value.replace(/_/g, " ");
}

/** Provenance an operator can read.
 *
 *  "groq/openai/gpt-oss-120b" is the most engineer-facing string on the page,
 *  and it is attached to the thing a reader is most entitled to be sceptical
 *  about. Keeping the provenance matters; making them parse a model slug does
 *  not. Falls back to the raw value so a model we have not named still shows.
 */
const MODELS: Record<string, string> = {
  "openai/gpt-oss-120b": "GPT-OSS 120B",
  "gpt-oss-120b": "GPT-OSS 120B",
  "gpt-oss-20b": "GPT-OSS 20B",
  "gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite",
  "gemini-3.5-flash": "Gemini 3.5 Flash",
  "gemini-2.0-flash": "Gemini 2.0 Flash",
  "llama-3.3-70b-versatile": "Llama 3.3 70B",
};

export function classifiedBy(model: string | null | undefined): string {
  if (!model) return "";
  return `Classified by ${MODELS[model] ?? model}`;
}

// ---------------------------------------------------------------------------
// Pipeline internals, in prose
// ---------------------------------------------------------------------------

/** Blocking-key codes. `nl` is not a word an operator should have to learn. */
const BLOCKING_KEYS: Record<string, string> = {
  li: "LinkedIn slug",
  em: "email",
  co: "company + surname",
  nl: "surname + city",
  nm: "first name",
};

/** How a pair was settled. */
const METHODS: Record<string, string> = {
  exact_email: "identical email",
  exact_linkedin: "identical LinkedIn",
  fuzzy_auto: "fuzzy name and company match",
  llm_adjudication: "reviewed by the model",
  escalated_not_adjudicated: "escalated, not yet reviewed",
};

const VERDICTS: Record<string, string> = {
  same_person: "same person",
  different_people: "different people",
  insufficient_evidence: "not enough evidence to say",
};

const STATUSES: Record<string, string> = {
  suggested: "suggested",
  approved: "approved",
  dismissed: "dismissed",
  auto_merged: "merged automatically",
  pending: "awaiting a decision",
  rejected: "rejected",
};

/** "li, nl, nm" -> "LinkedIn slug, surname and city and first name". */
export function blockingKeys(codes: string[] | null | undefined): string {
  // Comma-separated, no final "and": these are key names, not a natural list,
  // and several contain "+" already. "surname + city and first name" reads as
  // one key rather than two.
  const names = (codes ?? []).map((c) => BLOCKING_KEYS[c] ?? c);
  return names.length ? names.join(", ") : "no shared key";
}

export const method = (m: string | null | undefined) =>
  m ? METHODS[m] ?? label(m) : "";
export const verdict = (v: string | null | undefined) =>
  v ? VERDICTS[v] ?? label(v) : "";
export const status = (s: string | null | undefined) =>
  s ? STATUSES[s] ?? label(s) : "";

// ---------------------------------------------------------------------------
// Numbers and strings
// ---------------------------------------------------------------------------

/** Every score in this product is a 0-100 integer, whatever scale it arrived on.
 *
 *  The pipeline speaks in whatever each stage found natural -- dedupe in 0-100,
 *  the intro engine in 0-1, the rubric in points out of 100. Showing all three
 *  raw made the reader convert between scales to compare two numbers on the
 *  same screen. They are all confidence in the same sense, so they all read the
 *  same way, and each one carries a label saying which scale it is on. */
export function score100(value: number | null | undefined): string {
  if (value == null) return "—";
  return String(Math.round(value <= 1 ? value * 100 : value));
}

/** Join only the parts that exist. Guards against "Amara Dasgupta · CEO ·". */
export function joinParts(
  parts: (string | null | undefined)[],
  separator = " · ",
): string {
  return parts.map((p) => p?.trim()).filter(Boolean).join(separator);
}

/** Cached intro reasons open two ways -- "They should meet because X needs…"
 *  and "X needs…". Normalising at display time costs nothing; regenerating
 *  265 drafts to fix a sentence opening would cost quota for no gain. */
export function introReason(text: string | null | undefined): string {
  if (!text) return "";
  const trimmed = text.trim().replace(/^they should meet because\s+/i, "");
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

/** Truncate on a word boundary. Cutting mid-word looks like a rendering fault
 *  rather than a deliberate summary. */
export function truncateWords(text: string, max = 120): string {
  if (!text || text.length <= max) return text ?? "";
  const cut = text.slice(0, max);
  const boundary = cut.lastIndexOf(" ");
  return `${cut.slice(0, boundary > max * 0.5 ? boundary : max).replace(/[,;:.\s]+$/, "")}…`;
}

/** Deterministic-stage reasons are written for a log, not a reader.
 *
 *  Stage 1 and stage 2 record why they decided in the shortest form that is
 *  unambiguous to the pipeline -- "name 100.0, company 100.0, title agrees:
 *  True". That is the right thing to store and the wrong thing to show, so it
 *  is rewritten here. The model's own stage-3 reasons are already prose and
 *  pass through untouched. */
export function matchReason(text: string | null | undefined): string {
  if (!text) return "";
  const t = text.trim();

  if (/^identical normalized email$/i.test(t)) return "Identical email address";
  if (/^identical linkedin slug$/i.test(t)) return "Identical LinkedIn profile";

  const fuzzy = t.match(
    /^name ([\d.]+), company ([\d.]+|None|null), title agrees: (True|False|None)$/i,
  );
  if (fuzzy) {
    const [, name, company, title] = fuzzy;
    const describe = (v: string) => {
      const n = Number(v);
      if (!Number.isFinite(n)) return null;
      if (n >= 99) return "exactly";
      if (n >= 90) return "almost exactly";
      return "loosely";
    };
    const parts: string[] = [];
    const nameWord = describe(name);
    if (nameWord) parts.push(`names match ${nameWord}`);
    const companyWord = describe(company);
    parts.push(companyWord ? `company matches ${companyWord}` : "no company to compare");
    if (title === "True") parts.push("same role");
    else if (title === "False") parts.push("different roles");
    const joined = parts.join(", ");
    return joined.charAt(0).toUpperCase() + joined.slice(1);
  }

  return t;
}

/** Job titles are full of acronyms that are not shouting.
 *
 *  Without this list "COO" becomes "Coo" and "FOUNDER & CPO" becomes "Founder &
 *  Cpo", which is worse than leaving the caps alone. Only tokens that are
 *  genuinely written in caps by the people who hold the title are listed. */
const ACRONYMS = new Set([
  "AI", "API", "APAC", "AVP", "BD", "CEO", "CFO", "CIO", "CISO", "CMO", "COO",
  "CPO", "CRO", "CTO", "CX", "EMEA", "EU", "EVP", "GM", "GP", "HR", "IC", "IPO",
  "IT", "KPI", "LP", "MD", "ML", "NLP", "OKR", "PE", "PR", "QA", "R&D", "ROI",
  "SDR", "SEO", "SRE", "SVP", "UI", "UK", "US", "USA", "UX", "VC", "VP",
]);

/** Re-case shouting source data for DISPLAY only.
 *
 *  "UMA KUMAR" and "INVESTMENT DIRECTOR" are in the records because the source
 *  data is noisy, and that noise is the problem this product exists to solve --
 *  so the stored value is never touched, and anywhere a screen is showing the
 *  source record as evidence (the duplicate compare card, the Source record
 *  block) it still shows it raw.
 *
 *  Only all-caps runs are altered. A string that already has any lowercase in
 *  it is left exactly as written, so "Co-founder & CEO", "GM, India" and
 *  "VP Sales" survive untouched -- and inside a string that IS all caps, known
 *  acronyms stay as they are. */
export function titleCase(text: string | null | undefined): string {
  if (!text) return "";
  const trimmed = text.trim().replace(/\s+/g, " ");
  if (/[a-z]/.test(trimmed)) return trimmed;
  return trimmed.replace(/[^\s]+/g, (word) => {
    // Punctuation travels with the word: "CPO," must still match "CPO".
    const core = word.replace(/^[^A-Za-z&]+|[^A-Za-z&]+$/g, "");
    if (ACRONYMS.has(core)) return word;
    return word
      .toLowerCase()
      .replace(/(^|[\-/&(.])([a-z])/g, (_m, pre, ch) => pre + ch.toUpperCase());
  });
}

/** Why a merge happened, in a sentence rather than a stage name.
 *
 *  The merge log records the stage that decided ("stage1_exact"), which is the
 *  right thing to store and the wrong thing to show. No per-pair method is kept
 *  on the group, so stage 1 is described as the pair of identifiers it matches
 *  on rather than claiming one of them specifically. */
export function mergeReason(decidedBy: string | null | undefined): string {
  switch (decidedBy) {
    case "stage1_exact":
      return "matched on an identical email or LinkedIn profile";
    case "stage2_fuzzy":
      return "matched on name and company";
    case "llm":
      return "settled by model adjudication";
    case "human":
      return "merged by you";
    default:
      return decidedBy ? label(decidedBy) : "";
  }
}

/** How an introduction's state reads inside a panel, where "status: suggested"
 *  is a field dump and the operator only wants to know whose turn it is. */
export function introState(s: string | null | undefined): string {
  switch (s) {
    case "suggested":
      return "Awaiting your approval";
    case "approved":
      return "Approved";
    case "dismissed":
      return "Dismissed";
    default:
      return status(s);
  }
}
