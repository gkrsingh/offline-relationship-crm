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
