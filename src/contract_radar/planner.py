"""Query planning for clause types: domain knowledge about where a clause lives and how contracts phrase it.

Two kinds of help, both measured in the retrieval eval (D6):
  front matter  fields that sit in the opening of a contract (title, parties, dates): the first chunks always join
                the candidate pool before reranking
  expansions    how contracts actually word a clause, added to the question for keyword and vector search
  back matter   free-text questions about who signed: signature blocks sit in the last chunks, which rarely share
                words with the question, so those chunks join the pool the same way
"""

from __future__ import annotations

import re

FRONT_MATTER = {"Document Name", "Parties", "Agreement Date", "Effective Date"}
FRONT_CHUNKS = 3
BACK_CHUNKS = 3
BACK_MATTER = re.compile(r"\b(?:sign(?:s|ed|er|ers|ature|atures|atory|atories)?|executed)\b", re.IGNORECASE)

EXPANSIONS = {
    "Document Name": "agreement title heading",
    "Parties": "by and between party company corporation inc llc hereinafter referred to as",
    "Agreement Date": "dated as of entered into this day of",
    "Effective Date": "effective as of effective date commencement",
    "Expiration Date": "term shall expire terminate on initial term ending",
    "Renewal Term": "automatically renew successive renewal term extend unless notice",
    "Notice Period To Terminate Renewal": "written notice days prior to expiration not to renew non-renewal",
    "Governing Law": "governed by construed in accordance with the laws of the state",
    "Minimum Commitment": "shall purchase not less than minimum order quantity minimum purchase obligation annual minimum",
    "Revenue/Profit Sharing": "royalty percentage of net sales revenue share profit share commission",
    "Price Restrictions": "price increase shall not exceed fixed price may not raise prices",
    "Most Favored Nation": "most favorable terms no less favorable than any other customer lower price matching",
    "Non-Compete": "shall not compete engage in any competing business during the term within territory",
    "Exclusivity": "exclusive sole and exclusive shall not appoint any other distributor",
    "No-Solicit Of Customers": "shall not solicit customers clients",
    "No-Solicit Of Employees": "shall not solicit hire employ employees personnel",
    "Volume Restriction": "maximum quantity volume limit not to exceed units",
    "Liquidated Damages": "liquidated damages penalty shall pay as damages",
    "Warranty Duration": "warranty period months from delivery warrants for a period",
    "Cap On Liability": "aggregate liability shall not exceed limited to amounts paid",
    "Uncapped Liability": "no limitation unlimited liability gross negligence willful misconduct indemnification excluded",
    "Termination For Convenience": "terminate for any reason or no reason without cause upon written notice",
    "Change Of Control": "change of control merger acquisition sale of all or substantially all assets",
    "Anti-Assignment": "may not assign transfer without prior written consent",
    "Audit Rights": "audit inspect books records",
    "Insurance": "maintain insurance coverage liability policy",
    "Ip Ownership Assignment": "intellectual property shall be owned by assigns all right title and interest",
    "Joint Ip Ownership": "jointly owned joint ownership",
    "License Grant": "grants a license right to use",
    "Non-Transferable License": "non-transferable non-sublicensable license",
    "Competitive Restriction Exception": "notwithstanding the foregoing exception shall not prevent",
    "Unlimited/All-You-Can-Eat-License": "unlimited number of copies users enterprise-wide",
}


def expand(category: str | None, question: str) -> str:
    extra = EXPANSIONS.get(category or "", "")
    return f"{question} {extra}".strip()


def is_front_matter(category: str | None) -> bool:
    return category in FRONT_MATTER


def is_back_matter(question: str) -> bool:
    return bool(BACK_MATTER.search(question))
