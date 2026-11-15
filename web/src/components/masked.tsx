import { Fragment } from "react";

const PLACEHOLDER = /(\[(?:PERSON|EMAIL|PHONE|ADDRESS)_\d+\])/g;
const KIND: Record<string, string> = { PERSON: "name", EMAIL: "email", PHONE: "phone number", ADDRESS: "address" };
const WIDTH: Record<string, string> = { PERSON: "5.5em", EMAIL: "8em", PHONE: "6em", ADDRESS: "10em" };

/** Text with masked personal data drawn as redaction bars (screen readers hear what kind of value is hidden). */
export function Masked({ text }: { text: string }) {
  return (
    <>
      {text.split(PLACEHOLDER).map((part, i) => {
        const m = part.match(/^\[(PERSON|EMAIL|PHONE|ADDRESS)_(\d+)\]$/);
        if (!m) return <Fragment key={i}>{part}</Fragment>;
        const label = `${KIND[m[1]]} ${m[2]}, hidden for your role`;
        return (
          <span key={i} title={label} className="redact" style={{ width: WIDTH[m[1]] }}>
            <span className="sr-only">[{label}]</span>
          </span>
        );
      })}
    </>
  );
}
