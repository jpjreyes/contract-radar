"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Check, Download, Loader2, Pencil, X } from "lucide-react";
import { Masked } from "@/components/masked";
import { useUser } from "@/components/nav";
import { Badge, Card, Empty, PageHeader } from "@/components/ui";
import { api, API_BASE, authHeaders, FIELD_LABELS, prettyName, type Chunk, type ContractDetail, type FieldRow, type User } from "@/lib/api";

/** Original-text offset (relative to the chunk) -> offset in the text shown (masked chunks have placeholders). */
function toShown(pos: number, chunk: Chunk): number {
  let shift = 0;
  for (const [ms, me, os, oe] of chunk.masks) {
    if (pos < os) break;
    if (pos < oe) return ms;
    shift = me - oe;
  }
  return pos + shift;
}

function ChunkText({ chunk, hl }: { chunk: Chunk; hl: [number, number] | null }) {
  if (!hl || hl[1] <= chunk.start || hl[0] >= chunk.end) return <Masked text={chunk.text} />;
  const a = toShown(Math.max(hl[0], chunk.start) - chunk.start, chunk);
  const b = toShown(Math.min(hl[1], chunk.end) - chunk.start, chunk);
  return (
    <>
      <Masked text={chunk.text.slice(0, a)} />
      <mark id="hl" className="rounded bg-warn-soft px-0.5 text-foreground ring-1 ring-warn/40"><Masked text={chunk.text.slice(a, Math.max(b, a + 1))} /></mark>
      <Masked text={chunk.text.slice(Math.max(b, a + 1))} />
    </>
  );
}

function statusBadge(f: FieldRow) {
  if (f.status === "reviewed") return <Badge tone="ok">reviewed{f.reviewed_by ? ` by ${f.reviewed_by}` : ""}</Badge>;
  if (f.status === "auto_accepted") return <Badge tone="brand">auto-accepted</Badge>;
  return <Badge tone="warn">needs review</Badge>;
}

function Viewer() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const user = useUser();
  const [doc, setDoc] = useState<ContractDetail | null>(null);
  const [error, setError] = useState("");
  const [hl, setHl] = useState<[number, number] | null>(null);
  const [me, setMe] = useState<User | null>(null);
  const textRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const p = search.get("hl")?.split("-").map(Number);
    setHl(p && p.length === 2 && p.every(Number.isFinite) ? [p[0], p[1]] : null);
  }, [search]);
  const reload = () => api<ContractDetail>(`/contracts/${id}`).then(setDoc).catch((e) => setError((e as Error).message));
  useEffect(() => {
    setDoc(null); setError("");
    reload();
    api<User>("/me").then(setMe).catch(() => setMe(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, user]);
  useEffect(() => {
    if (doc && hl) document.getElementById("hl")?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [doc, hl]);

  const masked = useMemo(() => doc?.chunks.some((c) => c.masked) ?? false, [doc]);

  if (error) return <Card className="p-6 text-sm text-muted">This contract doesn&apos;t exist or isn&apos;t visible to {user}.</Card>;
  if (!doc) return <Empty>Loading…</Empty>;
  return (
    <div>
      <Link href="/contracts" className="mb-4 inline-flex items-center gap-1 text-sm text-muted hover:text-foreground"><ArrowLeft className="size-4" />Contracts</Link>
      <PageHeader eyebrow={`${doc.type} · ${doc.business_unit} · ${doc.region}`} title={<span className="block break-words text-xl leading-snug sm:text-2xl">{prettyName(doc.name)}</span>}
        actions={<PdfButton id={doc.id} masked={masked} />}>
        {masked ? "Personal data in this contract is masked for your role." : "You can see this contract's personal data."}
      </PageHeader>
      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <Card className="max-h-[75vh] overflow-y-auto p-5">
          <div ref={textRef} className="whitespace-pre-wrap font-serif text-[14px] leading-relaxed">
            {doc.chunks.map((c) => <div key={c.id}><ChunkText chunk={c} hl={hl} /></div>)}
          </div>
        </Card>
        <div className="space-y-2">
          <h2 className="text-lg">Key terms</h2>
          <p className="text-xs text-muted">{me?.can_review ? "Click a term to see its clause; confirm or correct it right here." : "Click a term to see its clause."}</p>
          {doc.fields.map((f) => (
            <FieldCard key={f.name} f={f} contractId={doc.id} canReview={!!me?.can_review}
              onShow={() => f.start !== null && setHl([f.start, f.end ?? f.start])} onSaved={reload} />
          ))}
          {!doc.fields.length && <p className="text-sm text-faint">No key terms extracted yet.</p>}
        </div>
      </div>
    </div>
  );
}

function PdfButton({ id, masked }: { id: number; masked: boolean }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function download() {
    setBusy(true); setErr("");
    try {
      const res = await fetch(`${API_BASE}/contracts/${id}/pdf`, { headers: await authHeaders(), cache: "no-store" });
      if (!res.ok) throw new Error(`${res.status}`);
      const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? `contract-${id}.pdf`;
      const url = URL.createObjectURL(await res.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: name });
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setErr("Download failed; try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="text-right">
      <button onClick={download} disabled={busy} title={masked ? "Your copy keeps the redactions" : undefined}
        className="inline-flex items-center gap-2 rounded-sm border border-line-strong bg-surface px-3.5 py-2 text-sm font-medium hover:border-foreground disabled:opacity-50">
        {busy ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}Download PDF
      </button>
      <p className="mt-1 text-[11px] text-faint">{masked ? "redacted for your role · logged" : "logged in the audit trail"}</p>
      {err && <p className="text-xs text-bad">{err}</p>}
    </div>
  );
}

function FieldCard({ f, contractId, canReview, onShow, onSaved }: {
  f: FieldRow; contractId: number; canReview: boolean; onShow: () => void; onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(f.value);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  async function decide(decision: "accept" | "edit" | "reject", value?: string) {
    setBusy(true); setMsg("");
    try {
      await api("/review", { method: "POST", body: JSON.stringify({ contract_id: contractId, field: f.name, decision, value }) });
      setEditing(false);
      onSaved();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const pending = f.status === "needs_review";
  return (
    <div className={`rounded-sm border bg-surface p-3 shadow-card ${pending && canReview ? "border-warn/50" : "border-line"}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted">{FIELD_LABELS[f.name] ?? f.name}</span>
        {statusBadge(f)}
      </div>
      {editing ? (
        <form className="mt-2 space-y-2" onSubmit={(e) => { e.preventDefault(); decide("edit", draft); }}>
          <textarea autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} rows={3}
            className="w-full rounded-sm border border-line-strong bg-surface px-2 py-1.5 text-sm outline-none focus:border-foreground" />
          <div className="flex gap-2">
            <button disabled={busy || !draft.trim()} className="rounded-sm bg-foreground px-2.5 py-1 text-xs font-medium text-background disabled:opacity-40">Save</button>
            <button type="button" onClick={() => setEditing(false)} className="px-2 py-1 text-xs text-muted hover:text-foreground">Cancel</button>
          </div>
        </form>
      ) : (
        <button onClick={onShow} disabled={f.start === null} className="mt-1 block w-full text-left text-sm enabled:hover:underline enabled:hover:decoration-line-strong">
          {f.present ? (f.value ? <Masked text={f.value} /> : "present") : <span className="text-faint">not in this contract</span>}
        </button>
      )}
      {f.issues.length > 0 && <p className="mt-1 text-xs text-warn">{f.issues.join(" · ")}</p>}
      {canReview && !editing && (
        <div className="mt-2 flex flex-wrap gap-1 border-t border-line pt-2 text-xs">
          <button disabled={busy} onClick={() => decide("accept")} className="flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-ok hover:bg-ok-soft disabled:opacity-40">
            <Check className="size-3.5" />{f.status === "reviewed" ? "Confirm again" : "Confirm"}
          </button>
          <button disabled={busy} onClick={() => { setDraft(f.value); setEditing(true); }} className="flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-muted hover:bg-surface-2 hover:text-foreground disabled:opacity-40">
            <Pencil className="size-3.5" />Edit
          </button>
          {f.present && (
            <button disabled={busy} onClick={() => decide("reject")} className="flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-bad hover:bg-bad-soft disabled:opacity-40">
              <X className="size-3.5" />Not in contract
            </button>
          )}
        </div>
      )}
      {msg && <p className="mt-1 text-xs text-bad">Not saved: {msg}</p>}
    </div>
  );
}

export default function ContractPage() {
  return <Suspense fallback={<Empty>Loading…</Empty>}><Viewer /></Suspense>;
}
