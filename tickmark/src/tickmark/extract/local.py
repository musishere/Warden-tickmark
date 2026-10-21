"""Read an invoice PDF from its own text layer, for free. Reads, decides nothing, never guesses.

1. File check: can this file be read from its text at all? A scanned or photographed page, text
   over a full-page image (a scanner's OCR), unreadable characters or rotated text -> needs_ocr
   (with why), and the router may send it to Azure. Thresholds are settings.
2. Values next to their labels: each meaning of the definition has the words printed next to its
   value ("Invoice No", "Total"). On every line the longest matching label wins, so "Total excl.
   VAT" is the net amount, not the total. The value is the rest of that block, else the next block
   on the line, else the line just below. Exactly one distinct value -> the field; more than one ->
   not read, with why (`unread`); none -> absent.
3. The text itself is kept (lines, letterhead), so a vendor or invoice number printed without a
   label can still be found by the comparison (tickmark.extract.text.found_in_text).

Line items are not read here: that table needs Azure.
"""
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

import pdfplumber

from tickmark.config import settings
from tickmark.definitions import INVOICE_VOUCHING, Definition
from tickmark.fields.sheet import header_name
from tickmark.fields.types import (
    column_decimal,
    parse_amount,
    parse_currency,
    parse_date,
    parse_identifier,
)
from tickmark.log import get
from tickmark.models import BBox, ExtractedField, ExtractedInvoice, Snip, TextLine

log = get("read")
SYMBOLS = {"€": "EUR", "£": "GBP"}  # '$' is left out: dollars of which country?
DATE_FORMATS = ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y")


def parse_pdf_date(text: str) -> date:
    """ISO, dd.mm.yyyy, or a month written in English words; dd/mm vs mm/dd is refused (ambiguous)."""
    try:
        return parse_date(text)
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text.strip().rstrip(","), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"not a date: {text!r}")


def _norm(word: str) -> list[str]:
    return header_name(word).split()


class _Block:
    """Words of one line that belong together (no wide gap between them)."""

    def __init__(self, page: int, words: list[dict]):
        self.page, self.words = page, words
        self.tokens = [(t, i) for i, w in enumerate(words) for t in _norm(w["text"])]

    @property
    def text(self) -> str:
        return " ".join(w["text"] for w in self.words)

    def bbox(self, words: list[dict] | None = None) -> BBox:
        ws = words or self.words
        return BBox(x0=min(w["x0"] for w in ws), y0=min(w["top"] for w in ws),
                    x1=max(w["x1"] for w in ws), y1=max(w["bottom"] for w in ws))


def _rows(page_no: int, words: list[dict]) -> list[list[_Block]]:
    """Words grouped into lines (same baseline), each line split into blocks at wide gaps."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    rows = []
    for line in lines:
        line.sort(key=lambda w: w["x0"])
        blocks, current = [], [line[0]]
        for w in line[1:]:
            if w["x0"] - current[-1]["x1"] > settings.pdf_word_gap:
                blocks.append(_Block(page_no, current))
                current = []
            current.append(w)
        blocks.append(_Block(page_no, current))
        rows.append(blocks)
    return rows


def check_page(page, n: int) -> str | None:
    """Why this page cannot be read from its own text, or None. A blank page is fine."""
    chars = [c for c in page.chars if c["text"].strip()]
    area = float(page.width * page.height) or 1.0
    cover = sum(max(0.0, (min(i["x1"], page.width) - max(i["x0"], 0)) * (min(i["bottom"], page.height) -
                                                                          max(i["top"], 0))) for i in page.images)
    if len(chars) < settings.pdf_min_chars:
        if page.images or page.curves:
            return f"page {n}: no text layer (a scan, a photo or text drawn as shapes)"
        return None
    if cover / area >= settings.pdf_image_cover:
        return f"page {n}: text over a full-page image (a scanner's OCR, which may be wrong)"
    bad = sum(1 for c in chars if "(cid:" in c["text"] or "�" in c["text"]
              or unicodedata.category(c["text"][0]) in ("Cc", "Co", "Cn"))
    if bad / len(chars) > settings.pdf_bad_chars:
        return f"page {n}: unreadable characters ({bad} of {len(chars)}; the font has no text map)"
    rotated = sum(1 for c in chars if not c.get("upright", True))
    if rotated / len(chars) > settings.pdf_rotated:
        return f"page {n}: rotated text ({rotated} of {len(chars)} characters)"
    return None


class LocalExtractor:
    def __init__(self, definition: Definition = INVOICE_VOUCHING):
        self.definition = definition

    def extract(self, pdf: Path) -> ExtractedInvoice:
        pdf = Path(pdf)
        try:
            with pdfplumber.open(pdf) as doc:
                needs_ocr = [why for n, p in enumerate(doc.pages, 1) if (why := check_page(p, n))]
                pages = [(n, p.height, p.extract_words()) for n, p in enumerate(doc.pages, 1)]
        except Exception as e:  # noqa: BLE001 - encrypted or broken: not readable here, say why
            log.warning("%s: cannot open (%s)", pdf.name, e.__class__.__name__)
            return ExtractedInvoice(file=pdf.name, extractor="local", needs_ocr=[f"cannot open: {e.__class__.__name__}"],
                                    raw={"reader": "local", "error": str(e)})
        raw = {"reader": "local", "needs_ocr": needs_ocr,
               "pages": [[{k: w[k] for k in ("text", "x0", "x1", "top", "bottom")} for w in words]
                         for _, _, words in pages]}
        if needs_ocr:
            log.info("%s: cannot be read from its own text: %s", pdf.name, "; ".join(needs_ocr))
            return ExtractedInvoice(file=pdf.name, extractor="local", needs_ocr=needs_ocr, raw=raw)
        inv = self.from_words(ExtractedInvoice(file=pdf.name, extractor="local", raw=raw), pages)
        log.info("%s: read from its own text: %s", pdf.name,
                 ", ".join(f"{k}={f.value!r} (p{f.snip.page})" for k, f in inv.fields.items()) or "no labelled value")
        for name, why in inv.unread.items():
            log.info("%s: %s not read: %s", pdf.name, name, why)
        if inv.letterhead:
            log.debug("%s: letterhead %s", pdf.name, [ln.text for ln in inv.lines[:inv.letterhead]])
        return inv

    def from_words(self, inv: ExtractedInvoice, pages: list[tuple[int, float, list[dict]]]) -> ExtractedInvoice:
        """Find the values and keep the text, from words with positions ({text, x0, x1, top, bottom}
        per page) - this PDF's own, or the words Azure's OCR read on a scan."""
        rows = [(n, height, _rows(n, words)) for n, height, words in pages if words]
        self._find_values(inv, [r for _, _, page_rows in rows for r in page_rows])
        inv.lines = [TextLine(page=b.page, text=b.text, bbox=b.bbox()) for _, _, page_rows in rows
                     for r in page_rows for b in r]
        inv.letterhead = self._letterhead(rows, inv.lines)
        return inv

    # --- values next to labels ---------------------------------------------------------------------

    def _specs(self):
        return [s for s in self.definition.compared if s.pdf_labels]

    def _find_values(self, inv: ExtractedInvoice, rows: list[list[_Block]]) -> None:
        numbers = [w["text"] for r in rows for b in r for w in b.words if re.search(r"\d[.,]\d", w["text"])]
        decimal = column_decimal(numbers)
        labels = sorted(((tuple(label.split()), s) for s in self._specs() for label in s.pdf_labels),
                        key=lambda ls: -len(ls[0]))
        found: dict[str, list[tuple[str, Snip]]] = {}
        currencies: list[tuple[str, Snip]] = []
        for i, row in enumerate(rows):
            for bi, block in enumerate(row):
                for spec, start, end in self._labels_in(block, labels):
                    value = self._value(spec.type, row, bi, block, start, end, rows[i + 1] if i + 1 < len(rows) else [],
                                        decimal)
                    if value is not None:
                        found.setdefault(spec.evidence_field, []).append(value)
                    if spec.evidence_field == "total":
                        currencies += self._currency(row, block, end)
        for ev, values in found.items():
            distinct = {v for v, _ in values}
            if len(distinct) == 1:
                value, snip = values[0]
                inv.fields[ev] = ExtractedField(name=ev, value=value, confidence=1.0, snip=snip)
            else:
                inv.unread[ev] = "ambiguous: " + " / ".join(f"{v} (page {s.page})" for v, s in values)
        codes = {c for c, _ in currencies}
        if len(codes) == 1 and "currency" not in inv.fields:
            code, snip = currencies[0]
            inv.fields["currency"] = ExtractedField(name="currency", value=code, confidence=1.0, snip=snip)
        elif len(codes) > 1:
            inv.unread["currency"] = "ambiguous: " + " / ".join(sorted(codes))

    @staticmethod
    def _labels_in(block: _Block, labels) -> list:
        """(spec, first token, past-last token) per label in this block, longest first, no overlaps."""
        tokens = [t for t, _ in block.tokens]
        taken: set[int] = set()
        out = []
        for words, spec in labels:
            n = len(words)
            for start in range(len(tokens) - n + 1):
                span = set(range(start, start + n))
                if tuple(tokens[start:start + n]) == words and not span & taken:
                    taken |= span
                    out.append((spec, start, start + n))
        return sorted(out, key=lambda x: x[1])

    def _value(self, type_: str, row, bi: int, block: _Block, start: int, end: int, below, decimal):
        """The value printed after a label: rest of its block, the next block on the line, or the line below.
        An amount must be the only one after its label on that line: a row like 'VAT  198,00  19  37,62'
        (base, rate, tax) is a table, and picking one of its numbers would be a guess."""
        after = block.words[block.tokens[end - 1][1] + 1:] + [w for b in row[bi + 1:] for w in b.words]
        if type_ == "money":
            amounts = {v[0] for w in after if re.search(r"\d[.,]\d", w["text"])
                       and (v := self._parse("money", [w], decimal))}
            if len(amounts) > 1:
                return None
        candidates = [block.words[block.tokens[end - 1][1] + 1:]]  # the words after the label's last word
        if bi + 1 < len(row):
            candidates.append(row[bi + 1].words)
        x0 = block.words[block.tokens[start][1]]["x0"]
        candidates.append([w for b in below for w in b.words if w["x0"] >= x0 - 5][:4])
        for words in candidates:
            if words and (v := self._parse(type_, words, decimal)) is not None:
                text, used = v
                return text, Snip(page=block.page, bbox=block.bbox(used), text=" ".join(w["text"] for w in used))
        return None

    @staticmethod
    def _parse(type_: str, words: list[dict], decimal):
        texts = [w["text"].strip(":#") for w in words]
        if type_ == "money":
            for i in range(len(texts) - 1, -1, -1):  # amounts stand on the right
                try:
                    return f"{parse_amount(texts[i], 'amount', decimal):f}", [words[i]]
                except ValueError:
                    pass
            return None
        if type_ == "date":
            for i in range(len(texts)):
                for n in (1, 2, 3):
                    try:
                        return parse_pdf_date(" ".join(texts[i:i + n])).isoformat(), words[i:i + n]
                    except ValueError:
                        pass
            return None
        if type_ == "identifier":
            for i, t in enumerate(texts):
                if t and re.search(r"\d", t):
                    return parse_identifier(t), [words[i]]
            return None
        if type_ == "currency":
            for i, t in enumerate(texts):
                try:
                    return parse_currency(SYMBOLS.get(t, t)), [words[i]]
                except ValueError:
                    pass
            return None
        text = " ".join(t for t in texts if t)
        return (text, words) if text else None

    @staticmethod
    def _currency(row, block: _Block, end: int) -> list[tuple[str, Snip]]:
        """Currency on the total's line: a code in brackets or next to the amount, or € / £."""
        words = block.words[block.tokens[end - 1][1] + 1:]
        words += [w for b in row[row.index(block) + 1:] for w in b.words]
        out = []
        for w in words:
            t = w["text"]
            code = next((c for s, c in SYMBOLS.items() if s in t), None)
            if code is None and (m := re.fullmatch(r"\(?([A-Z]{3})\)?", t)):
                code = m[1]
            if code:
                out.append((code, Snip(page=block.page, bbox=block.bbox([w]), text=t)))
        return out

    # --- letterhead --------------------------------------------------------------------------------

    def _letterhead(self, rows, lines: list[TextLine]) -> int:
        """How many of `lines` (page 1 first) are the issuer's letterhead: those above the first
        customer label ('Bill to') or the first amount (items and totals start there), and never
        below the top share of page 1 (a setting)."""
        if not rows:
            return 0
        customer = [tuple(label.split()) for s in self.definition.fields if s.name == "customer"
                    for label in s.pdf_labels]
        page_no, height, _ = rows[0]
        stop = height * settings.pdf_letterhead_share
        for ln in lines:
            tokens = _norm(ln.text)
            if ln.page == page_no and (re.search(r"\d[.,]\d{2}\b", ln.text) or any(
                    tuple(tokens[j:j + len(c)]) == c for c in customer for j in range(len(tokens)))):
                stop = min(stop, ln.bbox.y0 - 1)  # the whole line it is on ends the letterhead
        return next((i for i, ln in enumerate(lines) if ln.page != page_no or ln.bbox.y1 > stop), len(lines))

