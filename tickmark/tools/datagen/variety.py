"""Kinds of invoices Tickmark had not seen, with answers read by a person from the visible page.

    python -m tools.datagen.variety invoices   # data/real/variety/{originals,scans,photos}/invoices + manifest.json
    python -m tools.datagen.variety sample     # labels/*.json (typed by a person) -> each run's sample.xlsx + truth.json

Sources (downloaded into data/external, never committed):
  hq-invoice-images  30 invoice images (3 layouts), Hugging Face Shubhal829/high-quality-invoice-images-for-ocr,
                     ODbL 1.0: turned into image-only PDFs (nothing to read but pixels)
  zugferd-corpus     German / French e-invoice samples not used by data/real/facturx, and one real invoice
  derived            'scan' and 'photo' copies of some of the above: tilted, blurred, noisy, unevenly lit

The labels are what the printed page says, typed by a person: never the embedded XML, never Tickmark's own
reading. A derived copy shares the answers of the page it was made from.
"""
import argparse
import json
import random
import shutil
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pypdfium2 as pdfium
from openpyxl import Workbook
from PIL import Image, ImageEnhance, ImageFilter

from tickmark.extract.text import normalize_number
from tickmark.sample import normalize_vendor

EXT = Path("data/external")
OUT = Path("data/real/variety")
IMAGES = EXT / "hq-invoice-images"
CORPUS = EXT / "zugferd-corpus"
IMAGE_LICENSE = "ODbL 1.0 (huggingface.co/datasets/Shubhal829/high-quality-invoice-images-for-ocr)"
CORPUS_LICENSE = "Apache-2.0 (github.com/ZUGFeRD/corpus)"
CORPUS_PICKS = [  # new layouts or new situations, not renderings of invoices already in data/real/facturx
    "ZUGFeRDv2/correct/FNFE-factur-x-examples/Facture_FR_MINIMUM.pdf",
    "ZUGFeRDv2/correct/FNFE-factur-x-examples/Facture_UE_MINIMUM.pdf",
    "ZUGFeRDv2/correct/FNFE-factur-x-examples/Facture_DOM_MINIMUM.pdf",
    "ZUGFeRDv2/correct/PHP_@gpFacturX/sample_inofficial_20190125_atgp_factur-x_v_1_0.pdf",
    "ZUGFeRDv2/correct/symtrax/Beispiele/EXTENDED/zugferd_2p1_EXTENDED_Fremdwaehrung.pdf",
    "ZUGFeRDv2/correct/symtrax/Beispiele/EXTENDED/zugferd_2p1_EXTENDED_InnergemeinschLieferungMehrereBestellungen.pdf",
    "ZUGFeRDv2/correct/symtrax/Beispiele/MINIMUM/zugferd_2p1_MINIMUM_Rechnung.pdf",
    "ZUGFeRDv2/correct/symtrax/Beispiele/BASIC WL/zugferd_2p1_BASIC-WL_Buchungshilfe.pdf",
    "ZUGFeRDv2/correct/intarsys/EN16931/zugferd_2p0_EN16931_1_Teilrechnung.pdf",
    "ZUGFeRDv2/correct/intarsys/EN16931/zugferd_2p0_EN16931_2_Teilrechnung.pdf",
    "ZUGFeRDv2/correct/intarsys/EN16931/zugferd_2p0_EN16931_Haftpflichtversicherung_Versicherungssteuer.pdf",
    "ZUGFeRDv2/correct/intarsys/EN16931/zugferd_2p0_EN16931_ElektronischeAdresse.pdf",
    "ZUGFeRDv2/correct/intarsys/EN16931/zugferd_2p0_EN16931_Gutschrift.pdf",
    "ZUGFeRDv2/correct/intarsys/EN16931/zugferd_2p0_EN16931_Rabatte.pdf",
    "XML-Rechnung/FX/EN16931_Einfach_negativePaymentDue.pdf",
    "XML-Rechnung/FX/EN16931_Einfach_DueDate.pdf",
    "XML-Rechnung/FX/EN16931_Sachversicherung_berechneter_Steuersatz.pdf",
    "XML-Rechnung/FX/XRECHNUNG_Betriebskostenabrechnung.pdf",
    "XML-Rechnung/FX/EN16931_RechnungsUebertragung.pdf",
    "ZUGFeRDv1/correct/Intarsys/ZUGFeRD_1p0_EXTENDED_Warenrechnung.pdf",
    "ZUGFeRDv1/correct/Intarsys/ZUGFeRD_1p0_COMFORT_Rabatte.pdf",
    "ZUGFeRDv1/correct/Intarsys/ZUGFeRD_1p0_BASIC_Einfach.pdf",
    "ZUGFeRDv1/correct/Mustangproject/MustangGnuaccountingBeispielRE-20140519_499.pdf",
    "ZUGFeRDv1/correct/Mustangproject/MustangGnuaccountingBeispielRE-20140522_501.pdf",
    "unstructured/RE-E-974-Hetzner_2016-01-19_R0005532486.pdf",
]
N_DERIVED = 15  # pages copied as a bad scan and as a phone photo


def _pages(pdf: Path, dpi: int) -> list[Image.Image]:
    doc = pdfium.PdfDocument(str(pdf))
    return [doc[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(doc))]


def _save_images(pages: list[Image.Image], out: Path, quality: int) -> None:
    """An image-only PDF, as a scanner writes it: no text layer at all."""
    jpgs = []
    for p in pages:  # through JPEG, so compression artefacts are real
        tmp = out.with_suffix(f".{len(jpgs)}.jpg")
        p.save(tmp, "JPEG", quality=quality)
        jpgs.append(Image.open(tmp).convert("RGB"))
        tmp.unlink()
    jpgs[0].save(out, "PDF", resolution=150, save_all=True, append_images=jpgs[1:])


def _scan(img: Image.Image, rng: random.Random) -> Image.Image:
    """Grey, a little tilted, slightly soft, speckled: a cheap office scanner at 150 dpi."""
    img = img.convert("L").rotate(rng.uniform(-1.8, 1.8), expand=True, fillcolor=255, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    noise = Image.effect_noise(img.size, 18).point(lambda v: 0 if v < 70 else 255)
    return Image.composite(img, noise, noise).convert("RGB")


def _photo(img: Image.Image, rng: random.Random) -> Image.Image:
    """Shot with a phone: skewed, tilted, a shadow across the page, warm and soft."""
    w, h = img.size
    img = img.transform((w, h), Image.AFFINE, (1, rng.uniform(0.03, 0.07), -40, rng.uniform(-0.03, 0.03), 1, 0),
                        fillcolor=(205, 200, 190), resample=Image.BICUBIC)
    img = img.rotate(rng.uniform(-4, 4), expand=True, fillcolor=(205, 200, 190), resample=Image.BICUBIC)
    shade = Image.new("L", (2, 2))  # light falls off smoothly towards one side
    shade.putdata([rng.randint(150, 255) for _ in range(4)])
    shade = shade.resize(img.size, Image.BICUBIC)
    img = Image.composite(img, ImageEnhance.Brightness(img).enhance(0.6), shade)
    img = ImageEnhance.Color(img).enhance(0.85).filter(ImageFilter.GaussianBlur(1.0))
    r, g, b = img.split()
    return Image.merge("RGB", (r.point(lambda v: min(255, v + 8)), g, b.point(lambda v: max(0, v - 10))))


SETS = {"originals": ("image", "digital"), "scans": ("scan",), "photos": ("photo",)}  # one run each: a copy
# of an invoice in the same folder as the invoice would rightly look like a duplicate


def _folder(kind: str) -> Path:
    return OUT / next(s for s, kinds in SETS.items() if kind in kinds) / "invoices"


def build_invoices() -> None:
    for s in SETS:
        shutil.rmtree(OUT / s / "invoices", ignore_errors=True)
        (OUT / s / "invoices").mkdir(parents=True)
    manifest, n = [], 0

    def add(entry: dict) -> Path:
        nonlocal n
        n += 1
        entry["file"] = f"v{n:03d}.pdf"
        manifest.append(entry)
        return _folder(entry["kind"]) / entry["file"]

    for jpg in sorted(IMAGES.glob("batch*.jpg")):
        path = add({"source": f"hq-invoice-images/{jpg.name}", "license": IMAGE_LICENSE, "kind": "image",
                    "layout": jpg.name.split("-")[0]})
        Image.open(jpg).convert("RGB").save(path, "PDF", resolution=150)
    corpus = []
    for rel in CORPUS_PICKS:
        path = add({"source": f"zugferd-corpus/{rel}", "license": CORPUS_LICENSE, "kind": "digital"})
        shutil.copyfile(CORPUS / rel, path)
        corpus.append(path)
    rng = random.Random(11)
    for src in rng.sample(corpus, N_DERIVED):
        pages = _pages(src, 150)
        for kind, fn, q in (("scan", _scan, 55), ("photo", _photo, 60)):
            path = add({"source": f"derived from {src.name}", "license": "as its source", "kind": kind,
                        "copy_of": src.name})
            _save_images([fn(p, rng) for p in pages], path, q)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{n} PDFs: " + ", ".join(f"{s} {sum(m['kind'] in kinds for m in manifest)}" for s, kinds in SETS.items()))


VAT_RATES = "0,2.1,5.5,7,8.5,10,19,20"  # every rate printed on these invoices (DE, FR incl. overseas, US 10 %)
SEEN = {"v031.pdf": "r025", "v032.pdf": "r026", "v033.pdf": "r024", "v049.pdf": "r005", "v050.pdf": "r013"}  # same
# text as an invoice in data/real/facturx: not unseen
KEEP = {"v045.pdf", "v042.pdf"}  # of several PDFs with one supplier + number, the one this run keeps
EXPECT = {  # judgement the label's yes/no checks cannot carry
    "v015.pdf": (["difference", "needs_review"], ("line totals are not qty x price (4x372 printed 1460) and add up "
                                                  "to 3952, not the printed total 3930: never ✅")),
    "v055.pdf": (["matched", "needs_review"], "1 cent between the item lines and the net (rounding): an auditor accepts it"),
}


def _stray(label: dict) -> str:
    """Why a page cannot be a sample row (a continuation page, a booking aid), else ''."""
    p = label["printed"]
    if label.get("doc_type") == "other":
        return "not an invoice of its own: " + (label.get("unsure") or "a page of a longer document")[:120]
    if not p.get("invoice_number") or not p.get("invoice_date"):
        return "no invoice number or date printed"
    if p.get("total") is None and p.get("amount_due") is None:
        return "no total printed"
    return ""


def _expect(label: dict) -> tuple[list[str], str]:
    """What a clean row (it says what the page says) should get, and why, from the invoice alone."""
    if label["file"] in EXPECT:
        return EXPECT[label["file"]]
    p, c, traps = label["printed"], label.get("checks", {}), " ".join(label.get("traps", []))
    if c.get("lines_sum_to_subtotal") is False or c.get("subtotal_plus_tax_equals_total") is False:
        return ["difference", "needs_review"], "the invoice does not add up: never ✅ (" + c.get("detail", "") + ")"
    if not p.get("vendor"):
        return ["needs_review", "difference"], "the invoice names no vendor: the row's vendor cannot be confirmed"
    if "both ways" in traps:
        return ["matched", "needs_review"], "the date reads both ways: ✅ if the page settles it, else 👀"
    return ["matched"], ""


def build_sample(seed: int = 7) -> None:
    manifest = {m["file"]: m for m in json.loads((OUT / "manifest.json").read_text())}
    labels = {}
    for f, m in manifest.items():  # a copy shares the answers of the page it was made from
        src = OUT / "labels" / f"{Path(m.get('copy_of', f)).stem}.json"
        if src.exists():
            labels[f] = json.loads(src.read_text())
    missing = [f for f, m in manifest.items() if m["kind"] in ("image", "digital") and f not in labels]
    if missing:
        raise SystemExit(f"no label yet for {len(missing)} invoices: {', '.join(missing)}")
    rng = random.Random(seed)
    vendors = sorted({lb["printed"]["vendor"] for lb in labels.values() if lb["printed"].get("vendor")})
    aside: dict[str, str] = {}
    for name, kinds in SETS.items():
        files, seen_keys = [], {}
        for f in sorted((f for f, m in manifest.items() if m["kind"] in kinds), key=lambda f: f not in KEEP):
            p, src = labels[f]["printed"], manifest[f].get("copy_of", f)
            key = (normalize_vendor(p.get("vendor") or ""), normalize_number(p.get("invoice_number") or f))
            if src in SEEN and name == "originals":
                aside[f] = f"same invoice and text as data/real/facturx {SEEN[src]}"
            elif key in seen_keys:  # one supplier + number per folder, as in real books
                aside[f] = f"same supplier and number as {seen_keys[key]}: would rightly look like a duplicate"
            else:
                seen_keys[key] = f
                files.append(f)
            if f in aside:
                (OUT / "set-aside").mkdir(exist_ok=True)
                shutil.move(OUT / name / "invoices" / f, OUT / "set-aside" / f)
        files.sort()
        wb = Workbook()
        ws = wb.active
        ws.title = "Sample"
        ws.append(["Row ID", "Vendor", "Invoice No", "Invoice Date", "Amount", "Currency"])
        rows, dates = {}, []
        for f in files:
            p = labels[f]["printed"]
            if why := _stray(labels[f]):  # stays in the folder: no row may claim it
                rows[f"stray {f}"] = {"file": f, "expect": "unlinked", "note": why}
                continue
            rid = f"{name[0].upper()}-{len(rows) + 1:03d}"
            vendor, d = p.get("vendor") or "Unknown supplier", date.fromisoformat(p["invoice_date"])
            amount = Decimal(p["total"] if p.get("total") is not None else p["amount_due"])
            expect, note = _expect(labels[f])
            kind = rng.choices(["clean", "amount", "date", "vendor"], [0.72, 0.12, 0.10, 0.06])[0]
            if kind == "amount":
                amount += Decimal(rng.choice(["1.00", "10.00", "-0.50", "100.00"]))
                expect, note = ["difference"], f"amount changed in the row: page says {p.get('total') or p['amount_due']}"
            elif kind == "date":
                d += timedelta(days=rng.choice([-3, 1, 14, 365]))
                expect, note = ["difference"], f"date changed in the row: page says {p['invoice_date']}"
            elif kind == "vendor" and p.get("vendor"):
                vendor = rng.choice([v for v in vendors if v != p["vendor"]])
                expect, note = ["difference", "needs_review"], f"vendor changed in the row: page says {p['vendor']}"
            ws.append([rid, vendor, p["invoice_number"], d, float(amount), p.get("currency")])
            rows[rid] = {"file": f, "expect": expect if len(expect) > 1 else expect[0], "note": note}
            dates.append(d)
        for _ in range(max(2, len(files) // 10)):  # invoices the auditor asked for but nobody provided
            p = labels[rng.choice([f for f in files if not _stray(labels[f])])]["printed"]
            rid = f"{name[0].upper()}-{len(rows) + 1:03d}"
            d = date.fromisoformat(p["invoice_date"]) + timedelta(days=rng.randint(20, 90))
            ws.append([rid, p.get("vendor") or "Unknown supplier", f"{p['invoice_number']}-X{rng.randint(10, 99)}", d,
                       float(Decimal(rng.randint(100, 99999)) / 100), p.get("currency")])
            rows[rid] = {"file": None, "expect": "no_evidence", "note": "no such invoice in the folder"}
            dates.append(d)
        wb.save(OUT / name / "sample.xlsx")
        truth = {"note": "Answers read by a person from the printed page (labels/). Run with this period, "
                         "currency and VAT rates.",
                 "period": f"{min(dates)}:{max(dates)}", "currency": "EUR", "vat_rates": VAT_RATES, "rows": rows}
        (OUT / name / "truth.json").write_text(json.dumps(truth, indent=1, ensure_ascii=False) + "\n")
        print(f"{name}: {sum(not r.startswith('stray') for r in rows)} rows, "
              f"{sum(r.startswith('stray') for r in rows)} stray pages, period {truth['period']}")
    (OUT / "set-aside" / "why.json").write_text(json.dumps(aside, indent=1) + "\n")
    run_of = {f: s for s, kinds in SETS.items() for f, m in manifest.items() if m["kind"] in kinds and f not in aside}
    answers = [{"file": f, "run": run_of.get(f, "set-aside"), "kind": m["kind"], "source": m["source"],
                "license": m["license"], **({"set_aside": aside[f]} if f in aside else {}),
                **{k: labels[f][k] for k in ("printed", "doc_type", "checks", "traps", "unsure") if k in labels[f]}}
               for f, m in manifest.items() if f in labels]
    (OUT / "answers.json").write_text(json.dumps(answers, indent=1, ensure_ascii=False) + "\n")
    print(f"set aside: {len(aside)} PDFs (data/real/variety/set-aside/why.json)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("invoices", "sample"))
    args = ap.parse_args()
    if args.step == "invoices":
        build_invoices()
    else:
        build_sample()


if __name__ == "__main__":
    main()
