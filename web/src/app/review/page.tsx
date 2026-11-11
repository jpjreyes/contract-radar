"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Check, Pencil, X } from "lucide-react";
import { Masked } from "@/components/masked";
import { useUser } from "@/components/nav";
import { Badge, Button, Card, Empty, PageHeader } from "@/components/ui";
import { api, FIELD_LABELS, type ReviewItem, type User } from "@/lib/api";

type Item = ReviewItem & { start: number | null; end: number | null };

export default function ReviewPage() {
  const user = useUser();
  const [items, setItems] = useState<Item[] | null>(null);
  const [me, setMe] = useState<User | null>(null);
  const [editing, setEditing] = useState<string>("");
  const [draft, setDraft] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    api<Item[]>("/review?limit=60").then(setItems).catch(() => setItems([]));
  }, []);
  useEffect(() => {
    setItems(null); setMsg("");
    load();
    api<User[]>("/users").then((us) => setMe(us.find((u) => u.name === user) ?? null));
  }, [user, load]);

  async function decide(it: Item, decision: "accept" | "edit" | "reject", value?: string) {
    setMsg("");
    try {
      await api("/review", { method: "POST", body: JSON.stringify({ contract_id: it.contract_id, field: it.field, decision, value }) });
      setEditing("");
      setMsg(`Saved: ${FIELD_LABELS[it.field]} on ${it.contract.slice(0, 40)} (${decision}). Written to the audit log.`);
      load();
    } catch (e) {
      setMsg(`Not saved: ${(e as Error).message}`);
    }
  }

  const key = (it: Item) => `${it.contract_id}:${it.field}`;
  return (
    <div>
      <PageHeader eyebrow="Human in the loop" title="Review queue">
        Extracted terms the system wasn&apos;t sure about: a failed check, or a field that isn&apos;t trusted to auto-accept.
        {me && !me.can_review && <span className="text-warn"> You&apos;re read-only: {user} can see the queue but not change it.</span>}
      </PageHeader>
      {msg && <p className="mb-3 text-sm text-muted">{msg}</p>}
      <Card>
        {(items ?? []).map((it) => (
          <div key={key(it)} className="border-b border-line px-4 py-3 last:border-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">{FIELD_LABELS[it.field] ?? it.field}</span>
              <Badge>{it.confidence}</Badge>
              {it.issues.map((s) => <Badge key={s} tone="warn">{s}</Badge>)}
              <Link href={`/contracts/${it.contract_id}${it.start !== null ? `?hl=${it.start}-${it.end}` : ""}`}
                className="ml-auto truncate text-xs text-accent hover:underline">{it.contract.slice(0, 60)}</Link>
            </div>
            <p className="mt-1 text-sm">{it.present ? (it.value ? <Masked text={it.value} /> : "present") : <span className="text-faint">extracted as: not in this contract</span>}</p>
            {it.quote && <p className="mt-1 line-clamp-2 text-xs text-muted">“<Masked text={it.quote} />”</p>}
            {me?.can_review && (
              editing === key(it) ? (
                <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); decide(it, "edit", draft); }}>
                  <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
                    className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm outline-none" />
                  <Button>Save</Button>
                  <Button type="button" variant="ghost" onClick={() => setEditing("")}>Cancel</Button>
                </form>
              ) : (
                <div className="mt-2 flex gap-2">
                  <Button variant="secondary" onClick={() => decide(it, "accept")}><Check className="size-4" />Accept</Button>
                  <Button variant="secondary" onClick={() => { setEditing(key(it)); setDraft(it.value); }}><Pencil className="size-4" />Edit</Button>
                  <Button variant="danger" onClick={() => decide(it, "reject")}><X className="size-4" />Not in contract</Button>
                </div>
              )
            )}
          </div>
        ))}
        {items && !items.length && <Empty>Nothing waiting for review.</Empty>}
        {!items && <Empty>Loading…</Empty>}
      </Card>
    </div>
  );
}
