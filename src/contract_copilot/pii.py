"""Personal data (C3): detect it at ingest, store a masked copy, serve the original only to users allowed to see it.

Detection runs on the whole contract (more context for the name model, and one numbering per contract):
  - Microsoft Presidio with spaCy en_core_web_lg: PERSON names, emails, phone/fax numbers.
  - Contract-specific recognizers: signature and notice fields ("Name:", "/s/", "By:", "Attention:") and street
    addresses (number + street suffix, P.O. boxes).
  - Filters for what a legal NER model gets wrong in contracts: company and law-firm names, defined terms
    ("Licensee"), job titles, all-caps headings.
  - Propagation: once a full name is found anywhere, every other occurrence of it in that contract is masked too.

Masking replaces each value with a typed placeholder that is stable within a contract ([PERSON_1] is the same person
everywhere in that contract), so answers stay readable. Each chunk keeps a map from masked to original offsets, so
citations made on masked text still point at the right characters of the original contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

PII_VERSION = 2  # v2: field labels cut from model-found names too ("Mario Cibelli Email")
TYPES = ("PERSON", "EMAIL", "PHONE", "ADDRESS")
PRIORITY = {"EMAIL": 0, "PHONE": 1, "ADDRESS": 2, "PERSON": 3}  # when spans overlap, the more specific type wins


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    type: str
    text: str


# ---------------------------------------------------------------- recognizers

_NAME = r"(?:[A-Z][a-zA-Z'\-]+|[A-Z]\.)(?:[ \t]+(?:[A-Z][a-zA-Z'\-]+|[A-Z]\.|[A-Z][a-z]?\.|de|van|von|da|del|la)){1,4}"
FIELD = re.compile(r"(?:\bName|\bAttn|\bAttention|/s/|\bBy|\bContact)[ \t]*[:.]?[ \t]*(?:/s/[ \t]*)?(?P<name>"
                   + _NAME + r")")
_SUFFIXES = ("Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Parkway|Pkwy|"
             "Highway|Hwy|Plaza|Circle|Cir|Square|Sq|Terrace|Trail|Turnpike|Pike|Center|Centre|Expressway|Freeway|"
             "Park|Broadway|Row|Walk|Crescent|Loop|Alley|Mall|Quay|Wharf")
_SUFFIX = _SUFFIXES + "|" + _SUFFIXES.upper()  # case-sensitive: "one clause shall in no way" is not an address
_NUMBER = r"(?:\b\d{1,6}(?:-?[A-Z]|[-/]\d{1,4})?|\b(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten)|\bNo\.[ \t]*\d{1,6})"
_UNIT = (r"(?:,?[ \t]+(?:(?i:Suite|Ste\.?|Floor|Fl\.?|Unit|Room|Rm\.?|Building|Bldg\.?)[ \t]*#?[\w\-]+"
         r"|#[ \t]*[\w\-]+|\d{1,3}(?:st|nd|rd|th)[ \t]+Floor|P\.?O\.?B\.?[ \t]*\d+))")
ADDRESS = re.compile(
    _NUMBER + r"[ \t]+(?:(?:[NSEW]|North|South|East|West|NE|NW|SE|SW)\.?[ \t]+)?"
    r"(?:Avenue[ \t]+of[ \t]+the[ \t]+[A-Z]\w+|(?:[A-Z0-9][\w'.\-]*[ \t]+){0,4}(?:" + _SUFFIX + r")\b\.?)"
    r"(?:,?[ \t]+(?:North|South|East|West|N\.?W\.?|N\.?E\.?|S\.?W\.?|S\.?E\.?)(?!\w))?" + _UNIT + r"{0,2}"
    r"|\b(?i:(?:P\.?[ \t]?O\.?|Post Office)[ \t]+Box[ \t]+\d+|P\.?O\.?B\.?[ \t]*\d+)")
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
PHONE = re.compile(r"(?<![\w$.])(?:\+?1[ .\-]?)?\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4}(?:[ \t]*(?:x|ext\.?)[ \t]*\d{1,5})?"
                   r"|\+[ \t]?\d{1,3}[ .\-]?(?:\(?\d{1,4}\)?[ .\-]?){2,4}\d{2,4}(?![\w])")

_ORG = (r"Inc|Incorporated|LLC|L\.L\.C|Corp|Corporation|Company|Co|Ltd|Limited|LP|L\.P|LLP|L\.L\.P|PLC|GmbH|AG|SA|"
        r"SAS|S\.A|N\.V|B\.V|Bank|Trust|Group|Holdings|Holdco|Partners|Associates|Capital|Fund|Foundation|"
        r"University|Hospital|Securities|Technology|Technologies|Communications|Systems|Pharmaceuticals|"
        r"International|Enterprises|Industries|Media|Entertainment|Software|Labs|Solutions|Services")
ORG_WORDS = re.compile(r"\b(?:" + _ORG + r"|Agreement|Exhibit|Schedule|Section|Article|Party|Parties|State|County|"
                       r"Court|Act|Code)\b\.?")
ORG_NAME = re.compile(r"((?:[A-Z][\w'.\-]* ){0,3}[A-Z][\w'.\-]*),? (?:\([A-Z]{2,3}\) )?(?i:"  # single spaces: not across columns
                      + _ORG + r")(?![\w\-])")
ORG_ALIAS = re.compile(r"(?i:" + _ORG + r")\.?(?:[ \t]*\([A-Z]{2,3}\))?,?[^\"“”()]{0,80}?\([ \t]*(?:the[ \t]+)?[\"“]"
                       r"([A-Z][\w .&'\-]{1,40})[\"”]")
TITLE_WORDS = {"president", "chief", "officer", "executive", "director", "manager", "vice", "secretary", "treasurer",
               "chairman", "counsel", "general", "senior", "title", "name", "date", "dated", "by", "its", "attorney",
               "authorized", "print", "printed", "signatory", "witness", "signature", "member", "partner", "ceo",
               "cfo", "coo", "cto", "evp", "svp", "vp", "head", "legal", "the", "and", "of", "for", "to", "at", "in",
               "on", "with", "esq", "department", "dept", "office", "team", "whereof", "notice", "notices",
               "address", "addresses", "contact", "approved"}
SUFFIX_RE = re.compile(r",?[ \t]+(?:Jr|Sr|II|III|IV|Esq|M\.D|Ph\.D)\.?(?![\w])")


@lru_cache(maxsize=1)
def _analyzer():
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}]}).create_engine()
    engine = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
    for m in nlp.nlp.values():
        m.max_length = 2_000_000
    return engine


@dataclass(frozen=True)
class Context:
    """What a contract itself says about which capitalized words are not people."""
    terms: frozenset[str]       # defined terms in quotes ("Licensee", "Effective Date")
    lower_words: frozenset[str]  # words the contract also uses in lowercase: ordinary words, not names
    orgs: frozenset[str]        # capitalized runs followed by Inc/LLC/Ltd/...: company names
    org_aliases: frozenset[str]  # short names defined right after a company name: Rogers Cable Inc. ("Rogers")

    @classmethod
    def of(cls, text: str) -> Context:
        terms = {t.strip().lower() for t in re.findall(r"[\"“]([A-Z][\w .&'\-]{1,60})[\"”]", text)}
        prose = re.sub(r"\S+@\S+|\b(?:https?://|www\.)\S+", " ", text)  # keith.gottfried@ is not prose
        lower = set(re.findall(r"(?<![\w.@])[a-z][a-z'\-]+\b", prose))
        orgs = {re.sub(r"[,\s]+", " ", m.group(1)).strip().lower() for m in ORG_NAME.finditer(text)}
        aliases = {m.group(1).strip().lower() for m in ORG_ALIAS.finditer(text)}
        return cls(frozenset(terms), frozenset(lower), frozenset(orgs), frozenset(aliases))


def _clean(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and not text[start].isalnum():
        start += 1
    while end > start and not (text[end - 1].isalnum() or text[end - 1] == "."):
        end -= 1
    if end - start > 2 and text[end - 1] == "." and text[end - 3] != " ":  # sentence period, not an initial
        end -= 1
    return start, end


def plausible_person(name: str, ctx: Context) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*\.?", name)
    alpha = [w.rstrip(".") for w in words]
    if not alpha or len(name) > 60 or "\n" in name.strip() or any(ch.isdigit() for ch in name):
        return False
    low = re.sub(r"\s+", " ", name).strip().lower()
    if ORG_WORDS.search(name) or low in ctx.terms or "&" in name:
        return False
    if any(o.startswith(low) or low.startswith(o) for o in ctx.orgs if len(o) > 3):  # "Didi Chuxing" Technology Co.
        return False
    real = [w for w in alpha if len(w) > 1 and w.lower() not in TITLE_WORDS]
    if not real or (len(words) == 1 and len(real[0]) < 3) or len(real) > 4 or alpha[0].lower() == "the":
        return False
    if len(real) == 1 and real[0].lower() in ctx.terms:
        return False
    # every word is one the contract also writes in lowercase ("Legal Department", "Office Phone"): not a name
    return not all(w.lower() in ctx.lower_words for w in real)


def detect(text: str) -> list[Span]:
    ctx = Context.of(text)
    found: list[Span] = []
    for r in _analyzer().analyze(text=text, language="en", entities=["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER"]):
        s, e = _clean(text, r.start, r.end)
        if e <= s:
            continue
        kind = {"EMAIL_ADDRESS": "EMAIL", "PHONE_NUMBER": "PHONE"}.get(r.entity_type, r.entity_type)
        if kind == "PERSON":
            trimmed = _trim_name(text, s, e)
            if not trimmed:
                continue
            s, e = trimmed
            words = text[s:e].split()
            # a lone word ("Esq", "Deliverables", "lien") is almost never a full name in a contract; surnames on
            # their own are caught by propagation from the full name
            if len(words) < 2 or not all(w[0].isupper() for w in words) or not plausible_person(text[s:e], ctx):
                continue
        elif kind == "PHONE" and len(re.sub(r"\D", "", text[s:e])) < 10:  # "16 8.4" is a section number
            continue
        found.append(Span(s, e, kind, text[s:e]))
    for rx, kind in ((EMAIL, "EMAIL"), (PHONE, "PHONE"), (ADDRESS, "ADDRESS")):
        for m in rx.finditer(text):
            if kind != "PHONE" or len(re.sub(r"\D", "", m.group())) >= 10:
                found.append(Span(m.start(), m.end(), kind, m.group()))
    for m in FIELD.finditer(text):
        trimmed = _trim_name(text, m.start("name"), m.end("name"))
        if trimmed and len(text[trimmed[0]:trimmed[1]].split()) >= 2 and plausible_person(text[slice(*trimmed)], ctx):
            found.append(Span(*trimmed, "PERSON", text[slice(*trimmed)]))
    found = [_with_suffix(text, sp) for sp in found]
    return merge(text, _propagate(text, found, ctx))


LABEL_CUT = re.compile(r"[ \t]{2,}|\n|,|[ \t]+(?:Title|Its|Date|Dated|Name|By|Address|Telephone|Phone|Fax|Email|"
                       r"E-mail|Print|Office|Mobile|Tel|Facsimile)\b")


def _trim_name(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Cut a candidate name where the next field or column starts ("Gene Jackson      Print", "Mario Cibelli Email",
    "Frank Magliochetti, CEO") and drop trailing title words. None if nothing name-like is left."""
    s, e = _clean(text, start, end)
    m = LABEL_CUT.search(text, s, e)
    if m:
        e = m.start()
    toks = text[s:e].split()
    while toks and toks[-1].lower().rstrip(".,") in TITLE_WORDS:
        toks.pop()
    if not toks:
        return None
    e = s + text[s:e].rfind(toks[-1]) + len(toks[-1])
    s, e = _clean(text, s, e)
    return (s, e) if e > s else None


def _with_suffix(text: str, sp: Span) -> Span:
    """Extend a name over a generational suffix: "George F. Hamel, Jr.", "Harold T. Hanley, III"."""
    if sp.type != "PERSON":
        return sp
    m = SUFFIX_RE.match(text, sp.end)
    return Span(sp.start, m.end(), sp.type, text[sp.start:m.end()]) if m else sp


def _propagate(text: str, spans: list[Span], ctx: Context) -> list[Span]:
    """Mask every other occurrence of each full name found, and the surname on its own ("Nantz shall assign",
    "Mr. Nantz") unless it starts a company name ("Nantz Communications") or is an ordinary word in this contract."""
    names = {s.text for s in spans if s.type == "PERSON" and len(s.text.split()) >= 2}
    extra = []
    for n in names:
        pattern = r"\b" + r"\s+".join(re.escape(w) for w in n.split()) + r"(?![\w])"
        extra += [Span(m.start(), m.end(), "PERSON", m.group()) for m in re.finditer(pattern, text, re.IGNORECASE)]
        parts = [w.rstrip(".,") for w in n.split()]
        while len(parts) > 1 and parts[-1].lower() in {"jr", "sr", "ii", "iii", "iv", "esq", "md", "phd"}:
            parts.pop()
        surname = parts[-1].capitalize() if parts[-1].isupper() else parts[-1]
        if len(surname) < 4 or surname.lower() in ctx.lower_words:  # a defined term ("Nantz") is an alias: mask
            continue
        low = surname.lower()
        if low in ctx.org_aliases or (low not in ctx.terms and any(o.startswith(low + " ") for o in ctx.orgs)):
            continue  # "Rogers" is short for Rogers Cable Communications Inc. here, not for a person
        for m in re.finditer(r"(?:\b(?:Mr|Ms|Mrs|Dr)\.?\s+)?\b" + re.escape(surname) + r"\b(?!['’]s? [A-Z])", text):
            after = text[m.end():m.end() + 40]
            if (re.match(r"[ \t]+[A-Z]", after) or re.match(r",?[ \t]+(?:" + _ORG + r")\b", after)) and \
                    not re.match(r"[ \t]+(?:and|or|shall|will|may|is)\b", after):
                continue  # "Nantz Communications", "Smith Barney": part of a longer capitalized name
            extra.append(Span(m.start(), m.end(), "PERSON", m.group()))
    return spans + extra


def merge(text: str, spans: list[Span]) -> list[Span]:
    """Union overlapping spans; the merged span takes the most specific type."""
    out: list[Span] = []
    for s in sorted(spans, key=lambda s: (s.start, -s.end)):
        if out and s.start < out[-1].end:
            last = out[-1]
            end = max(last.end, s.end)
            kind = min(last.type, s.type, key=PRIORITY.get)
            out[-1] = Span(last.start, end, kind, text[last.start:end])
        else:
            out.append(s)
    return out


# ---------------------------------------------------------------- masking


def _key(kind: str, value: str) -> str:
    v = re.sub(r"\s+", " ", value).strip().lower()
    return f"{kind}:{re.sub(r'[^0-9a-z@.]', '', v) if kind != 'PERSON' else v.replace('.', '')}"


def tokens(text: str, spans: list[Span]) -> list[str]:
    """Placeholder per span, numbered per contract and type in order of first appearance."""
    seen: dict[str, str] = {}
    counts: dict[str, int] = {}
    out = []
    for s in spans:
        k = _key(s.type, text[s.start:s.end])
        if k not in seen:
            counts[s.type] = counts.get(s.type, 0) + 1
            seen[k] = f"[{s.type}_{counts[s.type]}]"
        out.append(seen[k])
    return out


def mask_range(text: str, start: int, end: int, spans: list[Span], toks: list[str]) -> tuple[str, list[list[int]]]:
    """Masked text of text[start:end] and its offset map [[masked_start, masked_end, orig_start, orig_end], ...]
    (all relative to `start`). A span crossing the range edge is masked for the part inside."""
    parts, mapping, pos, out_len = [], [], start, 0
    for s, tok in zip(spans, toks):
        if s.end <= start or s.start >= end:
            continue
        a, b = max(s.start, start), min(s.end, end)
        parts.append(text[pos:a])
        out_len += a - pos
        parts.append(tok)
        mapping.append([out_len, out_len + len(tok), a - start, b - start])
        out_len += len(tok)
        pos = b
    parts.append(text[pos:end])
    return "".join(parts), mapping


def to_original(pos: int, mapping: list[list[int]], side: str = "start") -> int:
    """Map an offset in masked text back to the original text. Inside a placeholder, snap to its edge."""
    shift = 0
    for ms, me, os_, oe in mapping:
        if pos < ms:
            break
        if pos < me or (pos == me and side == "end"):
            return os_ if side == "start" else oe
        shift = oe - me
    return pos + shift


def mask_value_strings(text: str, spans: list[Span], toks: list[str]) -> str:
    """Mask a short derived string (a chunk heading) by replacing the contract's detected values."""
    for value, tok in sorted({(s.text or "", t) for s, t in zip(spans, toks) if s.text}, key=lambda x: -len(x[0])):
        if len(value) > 3:
            text = re.sub(re.escape(value), tok, text, flags=re.IGNORECASE)
    return text
