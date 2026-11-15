import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Compare } from "@/components/compare";

const POINTS = [
  ["Cited", "Every claim quotes the clause it comes from, and code checks the quote against the contract."],
  ["Permissioned", "Each person sees only their contracts; personal data stays redacted unless their role allows it."],
  ["Measured", "0 permission leaks, 89% of lawyer-labeled clauses found, 97.9% of auto-accepted terms correct."],
];

export default function Overview() {
  return (
    <div>
      <section className="mx-auto max-w-4xl py-10 text-center sm:py-16">
        <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-accent">For legal operations teams</p>
        <h1 className="mt-5 text-balance text-4xl leading-[1.08] sm:text-[3.6rem]">Ask your contracts.<br />Get answers a lawyer can check.</h1>
        <p className="mx-auto mt-6 max-w-xl text-[17px] leading-relaxed text-muted">
          A working demo on 510 real commercial contracts: questions in plain English, deadlines you can act on, and
          the same rules a legal team would set on who sees what.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link href="/ask" className="inline-flex items-center gap-2 rounded-sm bg-foreground px-5 py-2.5 text-sm font-medium text-background hover:opacity-90">
            Ask a question <ArrowRight className="size-4" />
          </Link>
          <Link href="/radar" className="inline-flex items-center gap-2 rounded-sm border border-line-strong bg-surface px-5 py-2.5 text-sm font-medium hover:border-foreground">
            What&apos;s due this quarter
          </Link>
        </div>
      </section>

      <section className="grid gap-px border-y border-line bg-line sm:grid-cols-3">
        {POINTS.map(([t, d]) => (
          <div key={t} className="bg-background px-2 py-6 sm:px-6">
            <p className="font-serif text-xl">{t}</p>
            <p className="mt-1.5 text-sm leading-relaxed text-muted">{d}</p>
          </div>
        ))}
      </section>

      <div className="mt-16">
        <Compare />
      </div>
    </div>
  );
}
