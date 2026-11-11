import { Fragment } from "react";

const PLACEHOLDER = /(\[(?:PERSON|EMAIL|PHONE|ADDRESS)_\d+\])/g;
const KIND: Record<string, string> = { PERSON: "person", EMAIL: "email", PHONE: "phone", ADDRESS: "address" };

/** Text with masked personal data shown as labelled pills instead of raw placeholders. */
export function Masked({ text }: { text: string }) {
  return (
    <>
      {text.split(PLACEHOLDER).map((part, i) => {
        const m = part.match(/^\[(PERSON|EMAIL|PHONE|ADDRESS)_(\d+)\]$/);
        if (!m) return <Fragment key={i}>{part}</Fragment>;
        return (
          <span key={i} title="Personal data, hidden for your role"
            className="mx-0.5 inline-flex items-center rounded bg-surface-2 px-1.5 py-px align-baseline font-mono text-[0.8em] text-muted ring-1 ring-line">
            {KIND[m[1]]} {m[2]}
          </span>
        );
      })}
    </>
  );
}
