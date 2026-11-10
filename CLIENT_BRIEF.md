# Client brief (hypothetical client, public data)

**Client:** Northline Distribution, a mid-size distributor (the same fictional client as the Legacy Portal Agent).
**Who asked:** the legal-operations lead, on behalf of procurement, marketing and legal.
**Data:** 510 real commercial contracts from SEC EDGAR (CUAD v1, labeled by lawyers for 41 clause types, CC BY 4.0).
Ownership of each contract (business unit, region) is synthetic and documented in `src/contract_copilot/cuad.py`.

## The problem today
About 500 contracts sit in PDFs. Nobody can answer "which supply agreements auto-renew in the next 90 days with less
than 60 days' notice?" or "where is our liability uncapped?" without reading them. Regional managers should only see
their own region's contracts, and analysts shouldn't see personal data at all.

## What they're afraid of
- An answer that sounds right but isn't in the contract.
- A manager seeing a contract from another business unit or region through a search result or a quoted snippet.
- Personal data (names, emails, phone numbers, signatures) shown to people who shouldn't see it.

## Constraints (every decision in DECISIONS.md points to one of these)
- **C1** Every answer cites the exact clause; no citation, no answer ("I can't find it" is a valid answer).
- **C2** A user never sees text from a contract they can't access: not in answers, citations, search results or the
  model's context.
- **C3** Personal data is masked for roles without access to it.
- **C4** Cost per question under $0.02; p95 latency under 8 s.
- **C5** Every number in the README comes from the eval suite.

## Success =
Questions answered with verified citations; portfolio questions ("which contracts…") answered as exact sets;
0 permission leaks in adversarial tests; measured recall, faithfulness and cost.
