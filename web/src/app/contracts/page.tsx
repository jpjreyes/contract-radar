"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useUser } from "@/components/nav";
import { Badge, Card, Empty, PageHeader } from "@/components/ui";
import { api, type ContractRow } from "@/lib/api";

export default function ContractsPage() {
  const user = useUser();
  const [rows, setRows] = useState<ContractRow[] | null>(null);
  const [q, setQ] = useState("");
  useEffect(() => {
    setRows(null);
    api<ContractRow[]>("/contracts").then(setRows).catch(() => setRows([]));
  }, [user]);
  const shown = (rows ?? []).filter((r) => `${r.name} ${r.type}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div>
      <PageHeader eyebrow="What you can see" title={rows ? `${rows.length} contracts` : "Contracts"}>
        Row-level security in Postgres decides this list: switch the user at the top right and it changes.
      </PageHeader>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by name or type…"
        className="mb-4 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-line-strong" />
      <Card>
        <div className="hidden grid-cols-[1fr_9rem_10rem_8rem] gap-3 border-b border-line px-4 py-2 text-xs font-medium text-muted md:grid">
          <span>Contract</span><span>Type</span><span>Owner</span><span className="text-right">Fields to review</span>
        </div>
        {shown.slice(0, 300).map((r) => (
          <Link key={r.id} href={`/contracts/${r.id}`}
            className="grid gap-1 border-b border-line px-4 py-2.5 text-sm transition last:border-0 hover:bg-surface-2 md:grid-cols-[1fr_9rem_10rem_8rem] md:gap-3">
            <span className="truncate font-medium">{r.name}</span>
            <span className="truncate text-muted">{r.type}</span>
            <span className="truncate text-muted">{r.business_unit} · {r.region}</span>
            <span className="md:text-right">{r.to_review ? <Badge tone="warn">{r.to_review}</Badge> : <span className="text-faint">–</span>}</span>
          </Link>
        ))}
        {rows && !shown.length && <Empty>No contracts.</Empty>}
        {!rows && <Empty>Loading…</Empty>}
      </Card>
    </div>
  );
}
