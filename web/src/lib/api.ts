"use client";

import { accessToken } from "./auth";

export type Tone = "ok" | "warn" | "bad" | "neutral" | "brand" | "accent";

export const CLIENT = "Northline Distribution";
const KEY = "cc-user";
export const DEFAULT_USER = "legal_admin";

export type User = { name: string; description: string; pii_access: boolean; can_review: boolean; auth?: string };

export type ContractRow = {
  id: number; name: string; type: string; region: string; business_unit: string;
  pii: Record<string, number>; to_review: number;
};

export type Chunk = { id: number; start: number; end: number; text: string; masked: boolean; masks: number[][] };

export type FieldRow = {
  name: string; present: boolean; value: string; quote: string; start: number | null; end: number | null;
  confidence: string; issues: string[]; status: "auto_accepted" | "needs_review" | "reviewed"; reviewed_by: string | null;
};

export type ContractDetail = {
  id: number; name: string; type: string; region: string; business_unit: string; chunks: Chunk[]; fields: FieldRow[];
};

export type Citation = { source: string; chunk_id: number; contract: string; contract_id: number; quote: string; start: number; end: number };
export type AskResult = {
  answerable: boolean; clause_present: boolean | null; audit_id: number; seconds: number;
  pii_revealed_chunks: number[]; claims: { text: string; citations: Citation[] }[]; replayed?: boolean;
};

export type Condition = { field: string; op: string; value: string };
export type PortfolioResult = {
  conditions: Condition[]; unsupported: string; unreviewed: number; audit_id: number; replayed?: boolean;
  matches: { contract_id: number; contract: string;
    evidence: Record<string, { value: string; quote: string; start: number | null; end: number | null; status: string }> }[];
};

export type ReviewItem = {
  contract_id: number; contract: string; field: string; present: boolean; value: string;
  confidence: string; issues: string[]; quote: string;
};

export function currentUser(): string {
  if (typeof window === "undefined") return DEFAULT_USER;
  try {
    return window.localStorage.getItem(KEY) || DEFAULT_USER;
  } catch {
    return DEFAULT_USER;
  }
}

export function setCurrentUser(name: string) {
  try {
    window.localStorage.setItem(KEY, name);
  } catch {
    /* private mode: the switch still applies for this page view */
  }
  window.dispatchEvent(new CustomEvent("cc-user", { detail: name }));
}

export async function api<T>(path: string, init: RequestInit = {}, as?: string): Promise<T> {
  const token = as ? null : await accessToken();  // Entra mode: a bearer token; demo mode: null → the X-User switcher
  const identity: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : { "X-User": as ?? currentUser() };
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...identity, ...(init.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const FIELD_LABELS: Record<string, string> = {
  parties: "Parties",
  agreement_date: "Agreement date",
  effective_date: "Effective date",
  expiration_date: "Expiration date",
  renewal_term: "Renewal term",
  notice_to_terminate_renewal_days: "Notice to stop renewal (days)",
  governing_law: "Governing law",
  termination_for_convenience: "Termination for convenience",
  exclusivity: "Exclusivity",
  non_compete: "Non-compete",
  cap_on_liability: "Cap on liability",
  uncapped_liability: "Uncapped liability",
  minimum_commitment: "Minimum commitment",
};

export function prettyName(name: string): string {
  return name.replace(/_/g, " ").replace(/\s+/g, " ").trim();
}
