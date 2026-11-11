"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, FileText, Layers, Loader2, Quote, ShieldAlert } from "lucide-react";
import { useUser } from "@/components/nav";
import { Badge, Button, Card, Empty, PageHeader } from "@/components/ui";
import { api, FIELD_LABELS, type AskResult, type ContractRow, type PortfolioResult } from "@/lib/api";
import { Masked } from "@/components/masked";

const PORTFOLIO_EXAMPLES = [
  "Which contracts auto-renew and need 60 days or less notice to stop it?",
  "Which Delaware-law contracts have uncapped liability?",
  "Which distributor agreements have exclusivity?",
  "Which contracts have no cap on liability at all?",
];
const CONTRACT_EXAMPLES = [
  "Who signed this agreement?",
  "Is there a cap on liability, and what is excluded from it?",
  "Can either party terminate for convenience?",
  "Where should notices be sent?",
];
const OPS: Record<string, string> = {
  present: "has", absent: "has no", contains: "mentions", lte: "≤", gte: "≥", before: "before", after: "on or after",
};

export default function AskPage() {
  const user = useUser();
  const [mode, setMode] = useState<"portfolio" | "contract">("portfolio");
  const [contracts, setContracts] = useState<ContractRow[]>([]);
  const [contractId, setContractId] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [single, setSingle] = useState<AskResult | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioResult | null>(null);

  useEffect(() => {
    setSingle(null); setPortfolio(null); setContractId(null);
    api<ContractRow[]>("/contracts").then(setContracts).catch(() => setContracts([]));
  }, [user]);

  async function submit(q = question) {
    if (!q.trim() || (mode === "contract" && contractId === null)) return;
    setQuestion(q); setBusy(true); setError(""); setSingle(null); setPortfolio(null);
    try {
      if (mode === "portfolio") setPortfolio(await api<PortfolioResult>("/portfolio", { method: "POST", body: JSON.stringify({ question: q }) }));
      else setSingle(await api<AskResult>("/ask", { method: "POST", body: JSON.stringify({ question: q, contract_id: contractId }) }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const shown = contracts.filter((c) => c.name.toLowerCase().includes(filter.toLowerCase())).slice(0, 60);
  const chosen = contracts.find((c) => c.id === contractId);

  return (
    <div>
      <PageHeader eyebrow="Northline Distribution · Legal operations" title="Ask the contract portfolio">
        Portfolio questions run as filters over key terms extracted from every contract you can see. Questions about
        one contract are answered from its text, and every claim carries a quote checked against the source.
      </PageHeader>

      <Card className="p-4 sm:p-5">
        <div className="mb-4 inline-flex rounded-lg border border-line bg-surface-2 p-0.5 text-sm">
          {([["portfolio", "Whole portfolio", Layers], ["contract", "One contract", FileText]] as const).map(([m, label, Icon]) => (
            <button key={m} onClick={() => { setMode(m); setSingle(null); setPortfolio(null); }}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition ${mode === m ? "bg-surface font-medium shadow-card" : "text-muted hover:text-foreground"}`}>
              <Icon className="size-4" />{label}
            </button>
          ))}
        </div>

        {mode === "contract" && (
          <div className="mb-4">
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={`Find one of your ${contracts.length} contracts…`}
              className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-line-strong" />
            {chosen ? (
              <p className="mt-2 text-sm">Asking about <span className="font-medium">{chosen.name}</span>{" "}
                <button className="text-accent hover:underline" onClick={() => setContractId(null)}>change</button></p>
            ) : (
              <div className="mt-2 max-h-48 overflow-y-auto rounded-lg border border-line">
                {shown.map((c) => (
                  <button key={c.id} onClick={() => setContractId(c.id)}
                    className="block w-full truncate border-b border-line px-3 py-1.5 text-left text-sm last:border-0 hover:bg-surface-2">
                    {c.name} <span className="text-faint">· {c.type}</span>
                  </button>
                ))}
                {!shown.length && <Empty>No contract you can see matches.</Empty>}
              </div>
            )}
          </div>
        )}

        <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="flex gap-2">
          <input value={question} onChange={(e) => setQuestion(e.target.value)}
            placeholder={mode === "portfolio" ? "Which contracts…?" : "Ask about this contract…"}
            className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-[15px] outline-none focus:border-line-strong" />
          <Button disabled={busy || (mode === "contract" && contractId === null)}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <ArrowRight className="size-4" />}Ask
          </Button>
        </form>
        <div className="mt-3 flex flex-wrap gap-2">
          {(mode === "portfolio" ? PORTFOLIO_EXAMPLES : CONTRACT_EXAMPLES).map((q) => (
            <button key={q} onClick={() => submit(q)} disabled={busy || (mode === "contract" && contractId === null)}
              className="rounded-full border border-line px-3 py-1 text-xs text-muted transition hover:border-line-strong hover:text-foreground disabled:opacity-40">
              {q}
            </button>
          ))}
        </div>
      </Card>

      {error && <p className="mt-4 text-sm text-bad">{error}</p>}
      {busy && <p className="mt-6 flex items-center gap-2 text-sm text-muted"><Loader2 className="size-4 animate-spin" />
        {mode === "portfolio" ? "Planning the filter and running it over your contracts…" : "Retrieving clauses and checking every quote…"}</p>}

      {portfolio && (
        <section className="mt-6 space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted">Filter:</span>
            {portfolio.conditions.length ? portfolio.conditions.map((c, i) => (
              <Badge key={i} tone="brand">
                {c.op === "present" ? `has ${(FIELD_LABELS[c.field] ?? c.field).toLowerCase()}`
                  : c.op === "absent" ? `no ${(FIELD_LABELS[c.field] ?? c.field).toLowerCase()}`
                  : `${FIELD_LABELS[c.field] ?? "Contract type"} ${OPS[c.op]} ${c.value}`}
              </Badge>
            )) : <span className="text-warn">{portfolio.unsupported || "This question can't be answered from the extracted terms."}</span>}
          </div>
          <p className="text-sm">
            <span className="text-lg font-semibold">{portfolio.matches.length}</span> contracts match
            {portfolio.unreviewed > 0 && <span className="text-warn"> · {portfolio.unreviewed} rest on values nobody has reviewed yet</span>}
            <span className="text-faint"> · audit #{portfolio.audit_id}</span>
          </p>
          <Card>
            {portfolio.matches.slice(0, 100).map((m) => (
              <div key={m.contract_id} className="border-b border-line px-4 py-3 last:border-0">
                <Link href={`/contracts/${m.contract_id}`} className="text-sm font-medium hover:underline">{m.contract}</Link>
                <div className="mt-1.5 space-y-1">
                  {Object.entries(m.evidence).map(([f, ev]) => (
                    <div key={f} className="flex flex-wrap items-baseline gap-2 text-xs">
                      <span className="text-muted">{FIELD_LABELS[f] ?? f}:</span>
                      <span>{ev.value || "present"}</span>
                      {ev.status === "needs_review" ? <Badge tone="warn">unreviewed</Badge> : ev.status === "reviewed" ? <Badge tone="ok">reviewed</Badge> : null}
                      {ev.start !== null && (
                        <Link href={`/contracts/${m.contract_id}?hl=${ev.start}-${ev.end}`} className="text-accent hover:underline">source</Link>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {!portfolio.matches.length && <Empty>No contract you can see matches this filter.</Empty>}
          </Card>
        </section>
      )}

      {single && (
        <section className="mt-6 space-y-3">
          <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
            {single.answerable ? <Badge tone="ok" dot>answered from the contract</Badge> : <Badge tone="warn" dot>not found in this contract</Badge>}
            {single.pii_revealed_chunks.length > 0 && <Badge tone="accent"><ShieldAlert className="size-3" />personal data shown ({single.pii_revealed_chunks.length})</Badge>}
            <span className="text-faint">{single.seconds}s · audit #{single.audit_id}</span>
          </p>
          {!single.answerable && <Card className="p-4 text-sm text-muted">I can&apos;t find that in this contract. Nothing is shown unless a quote from the contract supports it.</Card>}
          {single.claims.map((c, i) => (
            <Card key={i} className="p-4">
              <p className="text-[15px] leading-relaxed"><Masked text={c.text} /></p>
              <div className="mt-3 space-y-2">
                {c.citations.map((ct, j) => (
                  <Link key={j} href={`/contracts/${ct.contract_id}?hl=${ct.start}-${ct.end}`}
                    className="flex gap-2 rounded-lg bg-surface-2 px-3 py-2 text-sm text-muted transition hover:text-foreground">
                    <Quote className="mt-0.5 size-3.5 shrink-0" />
                    <span className="line-clamp-3"><Masked text={ct.quote} /></span>
                  </Link>
                ))}
              </div>
            </Card>
          ))}
        </section>
      )}
    </div>
  );
}
