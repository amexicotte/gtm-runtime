"""Local, token-free extraction of bounded sections from executed contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
import re

import pdfplumber


MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PAGES = 100
MAX_SECTION_CHARS = 7_000
MAX_TOTAL_CHARS = 20_000


@dataclass(frozen=True)
class ContractSection:
    name: str
    pages: tuple[int, ...]
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


SECTION_PATTERNS = {
    "fees": re.compile(
        r"^(?:fees?(?:\s*(?:,|and|&)\s*(?:expenses|payment(?: terms)?|"
        r"invoicing))*|compensation|pricing)$", re.I),
    "payment_terms": re.compile(
        r"^(?:payment terms|invoicing and payment|billing and payment)$", re.I),
    "scope_services": re.compile(
        r"^(?:scope(?: of (?:work|services))?|scope and services|services|"
        r"services and deliverables|description of services|statement of work)$", re.I),
    "term_renewal_termination": re.compile(
        r"^(?:term|renewal|termination|term (?:and|&) (?:renewal|termination)|"
        r"renewal (?:and|&) termination|termination (?:and|&) renewal|"
        r"term, renewal and termination)$", re.I),
}
HEADING_PREFIX = re.compile(
    r"^(?:(?:section|article|schedule|exhibit)\s+)?"
    r"(?:[A-Z]|\d+)(?:\.\d+)*[.):\-]?\s+", re.I)
MONEY_WORDS = re.compile(
    r"(?:\$|usd|fee|rate|retainer|invoice|payment|hour|month)", re.I)


def extract_contract_sections(pdf_bytes: bytes) -> dict[str, ContractSection]:
    """Read the PDF locally and return only approved sections for reasoning."""
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("DocuSign document is not a PDF")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValueError("DocuSign PDF exceeds the 25 MB safety cap")
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            if not pdf.pages or len(pdf.pages) > MAX_PAGES:
                raise ValueError("DocuSign PDF page count is outside the safety cap")
            pages = [_page_text(page) for page in pdf.pages]
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("DocuSign PDF could not be parsed") from error
    if not any(value.strip() for value in pages):
        raise ValueError(
            "DocuSign PDF has no searchable text; OCR review is required")
    return sections_from_page_text(pages)


def sections_from_page_text(pages: list[str]) -> dict[str, ContractSection]:
    """Pure section locator used by fixtures and the production PDF reader."""
    lines: list[tuple[int, str]] = []
    for page_number, text in enumerate(pages, start=1):
        for line in str(text or "").splitlines():
            line = re.sub(r"[ \t]+", " ", line).strip()
            if line:
                lines.append((page_number, line))
    if not lines:
        raise ValueError("DocuSign PDF has no searchable text")

    headings = [(index, page, _heading_key(line))
                for index, (page, line) in enumerate(lines)
                if _is_heading(line)]
    found: dict[str, ContractSection] = {}
    first_page = "\n".join(
        line for page, line in lines if page == 1)[:2_500].strip()
    if first_page:
        found["parties_effective_date"] = ContractSection(
            "parties_effective_date", (1,), f"[Page 1]\n{first_page}")

    for name in SECTION_PATTERNS:
        candidates = []
        for position, (line_index, page, key) in enumerate(headings):
            if key != name:
                continue
            next_index = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
            excerpt_rows = lines[line_index:next_index]
            excerpt = _render_rows(excerpt_rows)[:MAX_SECTION_CHARS].strip()
            if excerpt:
                score = len(excerpt) + (5_000 if name == "fees" and MONEY_WORDS.search(excerpt) else 0)
                candidates.append((score, excerpt_rows, excerpt))
        if candidates:
            _, rows, excerpt = max(candidates, key=lambda value: value[0])
            found[name] = ContractSection(
                name, tuple(dict.fromkeys(page for page, _ in rows)), excerpt)

    if "fees" not in found:
        raise ValueError("executed SOW has no unambiguous Fees section heading")
    _enforce_total_cap(found)
    return found


def reasoning_excerpt(sections: dict[str, ContractSection]) -> str:
    ordered = [
        "fees", "payment_terms", "scope_services", "term_renewal_termination",
        "parties_effective_date",
    ]
    blocks = []
    for name in ordered:
        section = sections.get(name)
        if section:
            blocks.append(f"=== {name} ===\n{section.text}")
    value = "\n\n".join(blocks)
    if len(value) > MAX_TOTAL_CHARS:
        raise ValueError("bounded contract excerpts exceed the reasoning cap")
    return value


def _page_text(page) -> str:
    # layout=True retains table columns better than a plain reading-order dump.
    return str(page.extract_text(
        x_tolerance=2, y_tolerance=3, layout=True) or "").strip()


def _heading_key(line: str) -> str:
    if len(line) > 100 or len(line.split()) > 12:
        return ""
    value = HEADING_PREFIX.sub("", line.strip()).strip(" .:;-–—")
    for key, pattern in SECTION_PATTERNS.items():
        if pattern.fullmatch(value):
            return key
    return ""


def _is_heading(line: str) -> bool:
    if _heading_key(line):
        return True
    if len(line) > 100 or len(line.split()) > 12:
        return False
    if HEADING_PREFIX.match(line):
        return True
    letters = re.sub(r"[^A-Za-z]", "", line)
    return bool(letters) and line.upper() == line and len(line.split()) <= 8


def _render_rows(rows: list[tuple[int, str]]) -> str:
    blocks = []
    prior_page = None
    for page, line in rows:
        if page != prior_page:
            blocks.append(f"[Page {page}]")
            prior_page = page
        blocks.append(line)
    return "\n".join(blocks)


def _enforce_total_cap(sections: dict[str, ContractSection]) -> None:
    total = sum(len(value.text) for value in sections.values())
    if total <= MAX_TOTAL_CHARS:
        return
    # Fees are mandatory. Trim optional excerpts deterministically, preserving
    # their headings and page markers instead of widening the model input.
    remaining = MAX_TOTAL_CHARS - len(sections["fees"].text)
    if remaining < 0:
        raise ValueError("Fees section exceeds the reasoning cap")
    for name in ("payment_terms", "scope_services", "term_renewal_termination",
                 "parties_effective_date"):
        section = sections.get(name)
        if not section:
            continue
        text = section.text[:max(0, remaining)].rstrip()
        if text:
            sections[name] = ContractSection(name, section.pages, text)
        else:
            sections.pop(name, None)
        remaining -= len(text)
