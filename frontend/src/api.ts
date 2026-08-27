export type Person = {
  id: string;
  full_name: string | null;
  email: string | null;
  linkedin_url: string | null;
  company: string | null;
  title: string | null;
  location: string | null;
  bio: string | null;
  source: string | null;
  needs: string[];
  offers: string[];
  created_at: string | null;
  merged_into: string | null;
};

export type Evidence = { field: string; quote: string; supports: string };

/** null means the backfill has not reached this person -- NOT that the model
 *  looked and found nothing. Those are different facts and the UI says so. */
export type Enrichment = {
  persona: string;
  seniority: string;
  company_stage: string;
  sector: string;
  geography: string;
  needs: string[];
  offers: string[];
  confidence: number;
  low_confidence: boolean;
  evidence: Evidence[];
  evidence_verified: number;
  evidence_total: number;
  model: string;
  provider: string;
} | null;

export type Completeness = {
  score: number;
  missing: string[];
  blocked: boolean;
  blocked_reason: string | null;
  summary: string;
};

export type PersonRow = Person & {
  enrichment: Enrichment;
  completeness: Completeness;
  applicant: { total: number; band: string } | null;
};

export type DuplicatePair = {
  id: number;
  person_a_id: string;
  person_b_id: string;
  stage: string;
  method: string;
  score: number | null;
  verdict: string;
  confidence: number | null;
  reason: string | null;
  review_state: string;
  blocking_keys: string[];
  decision?: string | null;
  conflicts: { field: string; values: string[]; detail: string }[];
  a: PersonRow;
  b: PersonRow;
};

export type MergeGroup = {
  id: number;
  canonical_person_id: string;
  decided_by: string;
  created_at: string;
  source_record_ids: string[];
  records: Person[];
  resolved: Record<string, unknown>;
};

export type Introduction = {
  id: number;
  person_a_id: string;
  person_b_id: string;
  score: number;
  complementarity: number | null;
  similarity: number | null;
  matched_need: string | null;
  matched_offer: string | null;
  why: string | null;
  a_gets: string | null;
  b_gets: string | null;
  draft_message: string | null;
  status: string;
  has_copy: boolean;
  a: Person & { enrichment: Enrichment };
  b: Person & { enrichment: Enrichment };
};

export type ScoreSignal = {
  name: string;
  points: number;
  out_of: number;
  signal: string;
  basis: string;
};

export type Applicant = {
  person_id: string;
  full_name: string | null;
  company: string | null;
  title: string | null;
  location: string | null;
  total: number;
  band: string;
  signals: ScoreSignal[];
  explanation: string | null;
  bullets: string[];
  explanation_kind: string | null;
  enrichment_missing: boolean;
};

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

export const api = {
  queue: () => get<any>("/api/queue"),
  duplicates: (status = "pending") =>
    get<{ pairs: DuplicatePair[]; remaining: number; auto_resolved: number }>(
      `/api/duplicates?status=${status}`,
    ),
  merges: () => get<{ merges: MergeGroup[]; reverted: number }>("/api/merges"),
  undoMerge: (id: number) => post<any>(`/api/merges/${id}/undo`, {}),
  decideDuplicate: (id: number, decision: string) =>
    post<{ remaining: number }>(`/api/duplicates/${id}/decision`, { decision }),
  introductions: (status = "suggested") =>
    get<{ introductions: Introduction[]; counts: Record<string, number> }>(
      `/api/introductions?status=${status}`,
    ),
  decideIntro: (id: number, decision: string) =>
    post<{ status: string }>(`/api/introductions/${id}/decision`, { decision }),
  people: (params: Record<string, string>) =>
    get<{ people: PersonRow[]; total: number }>(
      `/api/people?${new URLSearchParams(params).toString()}`,
    ),
  person: (id: string) => get<any>(`/api/people/${id}`),
  applicants: () => get<{ applicants: Applicant[] }>("/api/applicants"),
  health: () => get<any>("/api/health"),
};
