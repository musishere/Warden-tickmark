"""Azure Document Intelligence (prebuilt-invoice) extractor with a record/replay cache.

Each PDF is sent to Azure once; the raw result is saved as data/cache/docint/<sha256>.json and
every later run parses that file. parse_result() works on the saved JSON only, so it is testable
offline.
"""
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from tickmark.config import settings
from tickmark.models import BBox, ExtractedField, ExtractedInvoice, Snip

MODEL_ID = "prebuilt-invoice"
DEFAULT_CACHE = Path("data/cache/docint")
POINTS_PER_UNIT = {"inch": 72.0}  # PDF pages are reported in inches

# Azure field -> (our field, how to read its value)
FIELD_MAP = {
    "VendorName": ("vendor", "string"),
    "InvoiceId": ("invoice_number", "string"),
    "InvoiceDate": ("invoice_date", "date"),
    "InvoiceTotal": ("total", "currency"),
    "SubTotal": ("subtotal", "currency"),
    "TotalTax": ("tax", "currency"),
}


class DocIntError(Exception):
    pass


class DocIntExtractor:
    def __init__(self, cache_dir: Path = DEFAULT_CACHE, endpoint: str | None = None,
                 key: str | None = None):
        self.cache_dir = Path(cache_dir)
        self.endpoint = endpoint if endpoint is not None else settings.docint_endpoint
        self.key = key if key is not None else settings.docint_key
        self._client = None

    def extract(self, pdf: Path) -> ExtractedInvoice:
        pdf = Path(pdf)
        data = pdf.read_bytes()
        cached = self.cache_dir / f"{hashlib.sha256(data).hexdigest()}.json"
        if cached.exists():
            result = json.loads(cached.read_text())
        else:
            result = self._analyze(data)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(result, indent=1))
        return parse_result(result, pdf.name)

    def _analyze(self, data: bytes) -> dict:
        if not (self.endpoint and self.key):
            raise DocIntError("Azure Document Intelligence is not configured: set "
                              "TICKMARK_DOCINT_ENDPOINT and TICKMARK_DOCINT_KEY in .env")
        if self._client is None:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.core.credentials import AzureKeyCredential

            self._client = DocumentIntelligenceClient(self.endpoint, AzureKeyCredential(self.key))
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

        poller = self._client.begin_analyze_document(MODEL_ID, AnalyzeDocumentRequest(bytes_source=data))
        return poller.result().as_dict()


def parse_result(result: dict, file: str) -> ExtractedInvoice:
    """Turn a saved AnalyzeResult (as_dict JSON) into an ExtractedInvoice."""
    raw = result
    result = result.get("analyzeResult", result)  # raw REST responses wrap the result
    meta = {"extractor": "azure-docint", "model_id": result.get("modelId") or MODEL_ID,
            "api_version": result.get("apiVersion"), "raw": raw}
    docs = result.get("documents") or []
    if not docs:
        return ExtractedInvoice(file=file, **meta)
    units = {p.get("pageNumber"): p.get("unit") for p in result.get("pages") or []}
    fields: dict[str, ExtractedField] = {}
    for azure_name, (name, kind) in FIELD_MAP.items():
        f = (docs[0].get("fields") or {}).get(azure_name)
        value = _value(f, kind) if f else None
        if value is None:
            continue
        fields[name] = ExtractedField(name=name, value=value, confidence=f.get("confidence"),
                                      snip=_snip(f, units))
    return ExtractedInvoice(file=file, fields=fields, **meta)


def _value(f: dict, kind: str) -> str | None:
    if kind == "string":
        v = f.get("valueString") or f.get("content")
        return " ".join(v.split()) if v else None
    if kind == "date":
        return f.get("valueDate")
    amount = (f.get("valueCurrency") or {}).get("amount")
    if amount is None:
        amount = f.get("valueNumber")
    return None if amount is None else f"{Decimal(str(amount)):f}"


def _snip(f: dict, units: dict) -> Snip | None:
    regions = f.get("boundingRegions") or []
    if not regions or not regions[0].get("polygon"):
        return None
    page, poly = regions[0]["pageNumber"], regions[0]["polygon"]
    scale = POINTS_PER_UNIT.get(units.get(page), 1.0)
    xs, ys = poly[0::2], poly[1::2]
    return Snip(page=page, text=f.get("content") or "",
                bbox=BBox(x0=min(xs) * scale, y0=min(ys) * scale, x1=max(xs) * scale, y1=max(ys) * scale))
