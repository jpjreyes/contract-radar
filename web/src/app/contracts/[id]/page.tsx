"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { Masked } from "@/components/masked";
import { useUser } from "@/components/nav";
import { Badge, Card, Empty, PageHeader } from "@/components/ui";
import { api, FIELD_LABELS, type Chunk, type ContractDetail, type FieldRow } from "@/lib/api";

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
  const textRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const p = search.get("hl")?.split("-").map(Number);
    setHl(p && p.length === 2 && p.every(Number.isFinite) ? [p[0], p[1]] : null);
  }, [search]);
  useEffect(() => {
    setDoc(null); setError("");
    api<ContractDetail>(`/contracts/${id}`).then(setDoc).catch((e) => setError((e as Error).message));
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
      <PageHeader eyebrow={`${doc.type} · ${doc.business_unit} · ${doc.region}`} title={doc.name}>
        {masked ? "Personal data in this contract is masked for your role." : "You can see this contract's personal data."}
      </PageHeader>
      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <Card className="max-h-[75vh] overflow-y-auto p-5">
          <div ref={textRef} className="whitespace-pre-wrap font-serif text-[14px] leading-relaxed">
            {doc.chunks.map((c) => <div key={c.id}><ChunkText chunk={c} hl={hl} /></div>)}
          </div>
        </Card>
        <div className="space-y-2">
          <h2 className="text-sm font-medium text-muted">Key terms</h2>
          {doc.fields.map((f) => (
            <button key={f.name} disabled={f.start === null} onClick={() => f.start !== null && setHl([f.start, f.end ?? f.start])}
              className="block w-full rounded-xl border border-line bg-surface p-3 text-left shadow-card transition enabled:hover:border-line-strong">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-muted">{FIELD_LABELS[f.name] ?? f.name}</span>
                {statusBadge(f)}
              </div>
              <p className="mt-1 text-sm">{f.present ? (f.value ? <Masked text={f.value} /> : "present") : <span className="text-faint">not in this contract</span>}</p>
              {f.issues.length > 0 && <p className="mt-1 text-xs text-warn">{f.issues.join(" · ")}</p>}
            </button>
          ))}
          {!doc.fields.length && <p className="text-sm text-faint">No key terms extracted yet.</p>}
        </div>
      </div>
    </div>
  );
}

export default function ContractPage() {
  return <Suspense fallback={<Empty>Loading…</Empty>}><Viewer /></Suspense>;
}
