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
