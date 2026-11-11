"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertTriangle, CalendarClock, Infinity as InfinityIcon, Loader2, RefreshCcw, UserRoundCheck } from "lucide-react";
import { useUser } from "@/components/nav";
import { Badge, Card, Empty, PageHeader, Stat } from "@/components/ui";
import { Masked } from "@/components/masked";
import { api, FIELD_LABELS, type Tone } from "@/lib/api";

type Ev = { present: boolean; value: string; status: string; start: number | null; end: number | null };
type Item = { contract_id: number; contract: string; headline: string; when: string | null; days_left: number | null; evidence: Record<string, Ev> };
type Radar = {
  as_of: string; horizon_days: number; contracts: number; unreviewed_items: number;
  sections: Record<"notice_deadlines" | "deadline_passed" | "evergreen" | "uncapped" | "expiring" | "needs_person", Item[]>;
};

const STATUS: Record<string, { tone: Tone; label: string }> = {
  auto_accepted: { tone: "brand", label: "auto-accepted" },
  needs_review: { tone: "warn", label: "unreviewed" },
  reviewed: { tone: "ok", label: "reviewed" },
};

function Evidence({ item }: { item: Item }) {
  return (
    <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {Object.entries(item.evidence).map(([f, ev]) => (
        <span key={f} className="inline-flex items-center gap-1.5">
          <span>{FIELD_LABELS[f] ?? f}: <span className="text-foreground"><Masked text={ev.value.slice(0, 80) || "present"} /></span></span>
          <Badge tone={STATUS[ev.status]?.tone ?? "neutral"}>{STATUS[ev.status]?.label ?? ev.status}</Badge>
          {ev.start !== null && <Link href={`/contracts/${item.contract_id}?hl=${ev.start}-${ev.end}`} className="text-accent hover:underline">clause</Link>}
        </span>
      ))}
    </div>
  );
}

function Section({ title, hint, icon, items, empty, limit = 25, urgent = false }: {
  title: string; hint: string; icon: React.ReactNode; items: Item[]; empty: string; limit?: number; urgent?: boolean;
}) {
  return (
    <section className="mt-8">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">{icon}{title}
          <span className="text-sm font-normal text-muted">({items.length})</span></h2>
        <p className="hidden text-xs text-faint sm:block">{hint}</p>
      </div>
      <Card>
        {items.slice(0, limit).map((it) => (
          <div key={`${it.contract_id}-${it.headline}`} className="border-b border-line px-4 py-3 last:border-0">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              {it.days_left !== null && (
                <span className={`tabular w-16 shrink-0 text-sm font-semibold ${urgent && it.days_left <= 14 ? "text-bad" : urgent && it.days_left <= 30 ? "text-warn" : "text-foreground"}`}>
                  {it.days_left >= 0 ? `${it.days_left} d` : `${-it.days_left} d ago`}
                </span>
              )}
              <Link href={`/contracts/${it.contract_id}`} className="min-w-0 flex-1 truncate text-sm font-medium hover:underline">{it.contract}</Link>
            </div>
            <p className="mt-0.5 text-sm"><Masked text={it.headline} /></p>
            <Evidence item={it} />
          </div>
        ))}
        {!items.length && <Empty>{empty}</Empty>}
        {items.length > limit && <p className="px-4 py-2 text-xs text-faint">+ {items.length - limit} more</p>}
      </Card>
    </section>
  );
}

export default function RadarPage() {
  const user = useUser();
  const [data, setData] = useState<Radar | null>(null);
  const [asOf, setAsOf] = useState("");
  const [days, setDays] = useState(90);
  const [error, setError] = useState("");

  useEffect(() => {
    setData(null); setError("");
    const q = new URLSearchParams({ days: String(days), ...(asOf ? { as_of: asOf } : {}) });
    api<Radar>(`/radar?${q}`).then(setData).catch((e) => setError((e as Error).message));
  }, [user, asOf, days]);

  const s = data?.sections;
  return (
    <div>
      <PageHeader eyebrow="Northline Distribution · Legal operations" title="Act this quarter"
        actions={
          <div className="flex items-center gap-2 text-sm">
            <label className="text-muted">as of</label>
            <input type="date" value={asOf || data?.as_of || ""} onChange={(e) => setAsOf(e.target.value)}
              className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm" />
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}
              className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm">
              {[30, 60, 90, 180].map((d) => <option key={d} value={d}>next {d} days</option>)}
            </select>
          </div>
        }>
        Decisions with a date on them, computed from the key terms extracted from every contract you can see:
        auto-renewals are rolled forward to the current term, and the deadline is the end of that term minus the
        notice period. Every row links to the clause it comes from and says whether a person has reviewed it.
      </PageHeader>

      {error && <p className="text-sm text-bad">{error}</p>}
      {!data && !error && <p className="flex items-center gap-2 text-sm text-muted"><Loader2 className="size-4 animate-spin" />Reading the portfolio…</p>}

      {data && s && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label={`Notice deadlines, next ${data.horizon_days} days`} value={s.notice_deadlines.length}
              tone={s.notice_deadlines.length ? "warn" : undefined} note="send non-renewal notice or it renews" />
            <Stat label="Uncapped liability" value={s.uncapped.length} tone={s.uncapped.length ? "bad" : undefined} note="exceptions to the liability cap" />
            <Stat label="Need a person" value={s.needs_person.length} note="can't be put on the calendar yet" />
            <Stat label="Rows on unreviewed values" value={data.unreviewed_items} note={`of ${data.contracts} contracts you can see`} />
          </div>

          <Section title="Notice deadlines" hint="last day to stop an automatic renewal" icon={<CalendarClock className="size-4" />}
            items={s.notice_deadlines} urgent empty="No notice deadline in this window." />
          <Section title="Renewing, notice window closed" hint="renews on this date unless ended for cause or by agreement"
            icon={<RefreshCcw className="size-4" />} items={s.deadline_passed} empty="Nothing renewing with a closed window." limit={10} />
          <Section title="Need a person" hint="renewal length unclear, or a date / notice period missing"
            icon={<UserRoundCheck className="size-4" />} items={s.needs_person} empty="Every renewal could be computed." limit={10} />
          <Section title="Uncapped liability" hint="what's excluded from the cap" icon={<AlertTriangle className="size-4" />}
            items={s.uncapped} empty="No uncapped liability found." limit={10} />
          <Section title="Evergreen" hint="continues until someone terminates" icon={<InfinityIcon className="size-4" />}
            items={s.evergreen} empty="No evergreen contracts." limit={10} />
          <Section title="Fixed terms ending in 12 months" hint="non-renewing contracts" icon={<CalendarClock className="size-4" />}
            items={s.expiring} empty="No fixed-term contract ends in the next 12 months." limit={10} />
        </>
      )}
    </div>
  );
}
