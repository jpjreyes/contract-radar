import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Compare } from "@/components/compare";

const NUMBERS = [
  ["0", "permission leaks in 2,129 returned chunks, every retrieval path"],
  ["0", "personal-data values shown to users without access, in 4,841 served texts"],
  ["89%", "of lawyer-labeled clauses found in the top 8 results (415 unseen contracts)"],
  ["97.9%", "of auto-accepted key terms correct; doubtful ones go to a person"],
];

const PLACES = [
  { href: "/radar", title: "Act this quarter", text: "Non-renewal notice deadlines and risky terms with dates on them, each linked to its clause." },
  { href: "/ask", title: "Ask", text: "“Which contracts…?” across the portfolio, or anything about one contract. Every claim carries a checked quote." },
  { href: "/contracts", title: "Contracts", text: "Read any contract you're allowed to see, with masked personal data and extracted key terms." },
];

export default function Overview() {
  return (
    <div className="space-y-14">
      <Compare />

      <section>
        <h2 className="text-2xl">Measured, not claimed</h2>
        <div className="mt-4 grid gap-px overflow-hidden rounded-sm border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
          {NUMBERS.map(([v, l]) => (
            <div key={l} className="bg-surface p-4">
              <p className="font-serif text-3xl">{v}</p>
              <p className="mt-1.5 text-xs leading-relaxed text-muted">{l}</p>
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-faint">From the evals in the repository; how each number was produced is written up with it.</p>
      </section>

      <section>
        <h2 className="text-2xl">Look around</h2>
        <p className="mt-1 text-sm text-muted">Switch user at the top right at any time: every page re-runs as that person.</p>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          {PLACES.map((p) => (
            <Link key={p.href} href={p.href} className="group rounded-sm border border-line bg-surface p-4 shadow-card transition hover:border-line-strong">
              <p className="flex items-center justify-between font-serif text-lg">{p.title}<ArrowRight className="size-4 text-faint transition group-hover:translate-x-0.5 group-hover:text-accent" /></p>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">{p.text}</p>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
