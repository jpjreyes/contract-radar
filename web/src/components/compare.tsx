"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Check, EyeOff, Eye, Loader2, Lock, Quote } from "lucide-react";
import { Masked } from "./masked";
import { api, prettyName, type AskResult, type ContractRow, type PortfolioResult, type User } from "@/lib/api";

// Two questions whose answers were recorded for every demo user, so the comparison is instant and costs nothing.
const QUESTIONS = [
  { kind: "contract" as const, contractId: 4, text: "Who signed this agreement?" },
  { kind: "portfolio" as const, text: "Which contracts auto-renew and need 60 days or less notice to stop it?" },
];
const LEFT = "legal_admin";
const RIGHT_CHOICES = ["analyst", "maria", "kenji"];

type Side = { user: string; contracts: ContractRow[] | null; ask?: AskResult | null; portfolio?: PortfolioResult | null; error?: string };

async function load(user: string, q: (typeof QUESTIONS)[number], contracts: ContractRow[]): Promise<Partial<Side>> {
  if (q.kind === "portfolio") {
    return { portfolio: await api<PortfolioResult>("/portfolio", { method: "POST", body: JSON.stringify({ question: q.text }) }, user) };
  }
  if (!contracts.some((c) => c.id === q.contractId)) return { ask: null }; // not visible to this user: nothing to ask
  return { ask: await api<AskResult>("/ask", { method: "POST", body: JSON.stringify({ question: q.text, contract_id: q.contractId }) }, user) };
}

export function Compare() {
  const [qi, setQi] = useState(0);
  const [right, setRight] = useState("analyst");
  const [users, setUsers] = useState<User[]>([]);
  const [lists, setLists] = useState<Record<string, ContractRow[]>>({});
  const [sides, setSides] = useState<Record<string, Side>>({});
  const q = QUESTIONS[qi];

  useEffect(() => {
    api<User[]>("/users", {}, LEFT).then(setUsers).catch(() => setUsers([]));
  }, []);

  useEffect(() => {
    let live = true;
    for (const u of [LEFT, right]) {
      setSides((s) => ({ ...s, [u]: { user: u, contracts: lists[u] ?? null } }));
      (async () => {
        try {
          const contracts = lists[u] ?? (await api<ContractRow[]>("/contracts", {}, u));
          if (!lists[u]) setLists((l) => ({ ...l, [u]: contracts }));
          const res = await load(u, q, contracts);
          if (live) setSides((s) => ({ ...s, [u]: { user: u, contracts, ...res } }));
        } catch (e) {
          if (live) setSides((s) => ({ ...s, [u]: { user: u, contracts: null, error: (e as Error).message } }));
        }
      })();
    }
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qi, right]);

  const contractName = prettyName(lists[LEFT]?.find((c) => c.id === QUESTIONS[0].contractId)?.name ?? "");

  return (
    <section>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-2xl">
          <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.14em] text-accent">Same question · two people</p>
          <h1 className="text-3xl leading-tight sm:text-[2.6rem]">Every answer depends on who&apos;s asking.</h1>
          <p className="mt-3 text-[15px] leading-relaxed text-muted">
            Permissions and personal-data masking are enforced in the database, before any text reaches the model. So the
            same question gets a different, still fully cited, answer for each person. Pick a question and a colleague.
          </p>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        {QUESTIONS.map((x, i) => (
          <button key={x.text} onClick={() => setQi(i)}
            className={`rounded-sm border px-3 py-1.5 text-left text-sm transition ${i === qi ? "border-foreground bg-foreground text-background" : "border-line-strong bg-surface text-muted hover:text-foreground"}`}>
            {x.kind === "contract" ? <>&ldquo;{x.text}&rdquo; <span className="opacity-60">· one contract</span></> : <>&ldquo;{x.text}&rdquo;</>}
          </button>
        ))}
      </div>
      {q.kind === "contract" && contractName && (
        <p className="mt-2 truncate font-mono text-[11px] text-faint">contract: {contractName}</p>
      )}

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        {[LEFT, right].map((u, col) => (
          <Column key={col} side={sides[u]} user={users.find((x) => x.name === u)} q={q}
            picker={col === 1 ? (
              <select value={right} onChange={(e) => setRight(e.target.value)} aria-label="Compare with"
                className="rounded-sm border border-line-strong bg-surface px-2 py-1 font-mono text-xs outline-none">
                {RIGHT_CHOICES.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            ) : <span className="font-mono text-xs">{u}</span>} />
        ))}
      </div>
      <p className="mt-3 text-xs text-faint">
        These two answers were recorded from the live system, so they load instantly.{" "}
        <Link href="/ask" className="text-accent hover:underline">Ask your own question</Link> as any user; it runs live.
      </p>
    </section>
  );
}

function Column({ side, user, q, picker }: { side?: Side; user?: User; q: (typeof QUESTIONS)[number]; picker: React.ReactNode }) {
  const n = side?.contracts?.length;
  return (
    <div className="sheet flex flex-col rounded-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line py-3 pl-12 pr-4">
        <div className="min-w-0">
          {picker}
          <p className="truncate text-xs text-muted">{user?.description ?? " "}</p>
        </div>
        <div className="flex flex-wrap gap-1.5 font-mono text-[11px]">
          <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-muted">{n ?? "…"} contracts</span>
          {user && (user.pii_access
            ? <span className="flex items-center gap-1 rounded-sm bg-surface-2 px-1.5 py-0.5 text-muted"><Eye className="size-3" />personal data</span>
            : <span className="flex items-center gap-1 rounded-sm bg-foreground px-1.5 py-0.5 text-background"><EyeOff className="size-3" />masked</span>)}
        </div>
      </div>
      <div className="min-h-56 flex-1 py-4 pl-12 pr-4 text-[15px] leading-relaxed">
        {!side || (side.ask === undefined && side.portfolio === undefined && !side.error) ? (
          <p className="flex items-center gap-2 text-sm text-faint"><Loader2 className="size-4 animate-spin" />Asking as {side?.user}…</p>
        ) : side.error ? (
          <p className="text-sm text-bad">{side.error}</p>
        ) : q.kind === "contract" && side.ask === null ? (
          <div className="ink-in flex gap-3 text-sm text-muted">
            <Lock className="mt-0.5 size-4 shrink-0" />
            <p>This contract isn&apos;t one of the {n} {side.user} can see. Row-level security in Postgres filters it out,
              so there is nothing to search, quote or leak, and the model is never called.</p>
          </div>
        ) : side.ask ? (
          <div className="ink-in space-y-3">
            {side.ask.claims.map((c, i) => <p key={i}><Masked text={c.text} /></p>)}
            {side.ask.claims[0]?.citations[0] && (
              <Link href={`/contracts/${side.ask.claims[0].citations[0].contract_id}?hl=${side.ask.claims[0].citations[0].start}-${side.ask.claims[0].citations[0].end}`}
                className="flex gap-2 border-l-2 border-accent bg-surface-2 px-3 py-2 text-[13px] text-muted hover:text-foreground">
                <Quote className="mt-0.5 size-3.5 shrink-0" />
                <span className="line-clamp-3 font-serif"><Masked text={side.ask.claims[0].citations[0].quote} /></span>
              </Link>
            )}
            <p className="flex items-center gap-1.5 font-mono text-[11px] text-ok"><Check className="size-3.5" />every quote checked against the contract</p>
          </div>
        ) : side.portfolio ? (
          <div className="ink-in">
            <p><span className="font-serif text-4xl">{side.portfolio.matches.length}</span> <span className="text-muted">contracts match</span></p>
            <ul className="mt-3 space-y-1.5 text-[13px]">
              {side.portfolio.matches.slice(0, 5).map((m) => (
                <li key={m.contract_id} className="truncate">
                  <Link href={`/contracts/${m.contract_id}`} className="hover:underline">{prettyName(m.contract)}</Link>
                </li>
              ))}
            </ul>
            {side.portfolio.matches.length > 5 && <p className="mt-1.5 font-mono text-[11px] text-faint">+ {side.portfolio.matches.length - 5} more</p>}
          </div>
        ) : null}
      </div>
    </div>
  );
}
