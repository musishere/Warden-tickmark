"""Decide which column of a client's sheet holds each field, once per client and header layout.

    python -m tools.map_columns SAMPLE.xlsx                         # show what is known, what is missing
    python -m tools.map_columns SAMPLE.xlsx --suggest               # ask the LLM (needs TICKMARK_LLM_MAPPING=on)
    python -m tools.map_columns SAMPLE.xlsx --set amount=D --set vendor=B
    python -m tools.map_columns SAMPLE.xlsx --meaning contract_no=identifier:"Contract no|Contract" --set contract_no=F
    python -m tools.map_columns SAMPLE.xlsx --suggest --confirm --tenant T --engagement E   # save it
    python -m tools.map_columns SAMPLE.xlsx --set ... --confirm --out mapping.json         # offline

Starts from a mapping saved for this client and header layout, else the known header names.
--suggest fills the rest from the LLM (TICKMARK_LLM_* in .env, e.g. OpenRouter): by default it sees only
headers and value PATTERNS, never
values (TICKMARK_LLM_MAPPING_VALUES=on separately allows 5 examples per column). Every column is
then checked against all its values. Nothing is used until --confirm, which records who confirmed.
"""
import argparse
import json
import sys
from pathlib import Path

from tickmark.config import settings
from tickmark.definitions import DEFINITIONS
from tickmark.fields.mapping import ColumnMapping, Meaning, alias_mapping, headers_sha256, validate
from tickmark.fields.profile import profile_sheet
from tickmark.fields.sheet import read_sheet
from tools.tenant_args import add_tenant_args, tenant_context


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sample", type=Path)
    ap.add_argument("--definition", default="invoice_vouching", choices=list(DEFINITIONS))
    ap.add_argument("--suggest", action="store_true", help="ask the LLM for the fields still unmapped")
    ap.add_argument("--set", action="append", default=[], metavar="FIELD=COL", help="map a field by hand")
    ap.add_argument("--meaning", action="append", default=[], metavar="NAME=TYPE:LABEL|LABEL",
                    help="add a meaning of this layout: its type and the words printed next to it on the invoice")
    ap.add_argument("--confirm", action="store_true", help="accept the mapping (records you as confirming)")
    ap.add_argument("--out", type=Path, help="also write the confirmed mapping to a JSON file")
    add_tenant_args(ap)
    args = ap.parse_args()
    definition = DEFINITIONS[args.definition]
    sheet = read_sheet(args.sample)
    ctx = tenant_context(ap, args) if (args.tenant or args.engagement) else None

    columns, origin, notes, proposal, meanings = {}, {}, {}, None, {}
    saved = None
    if ctx:
        from tickmark.store.mappings import current_mapping

        saved = current_mapping(ctx, definition.name, definition.version, headers_sha256(sheet))
    start = saved or alias_mapping(sheet, definition)
    meanings = dict(start.meanings)
    for f, c in start.columns.items():
        columns[f], origin[f] = c, "saved" if saved else "known header"
    ambiguous = {f: n for f, n in start.notes.items() if f not in columns}

    if args.suggest:
        if not settings.llm_mapping:
            ap.error("LLM mapping is off: set TICKMARK_LLM_MAPPING=on (headers + value patterns are sent to "
                     "the LLM provider; values only with TICKMARK_LLM_MAPPING_VALUES=on)")
        from tickmark.fields.llm_mapping import LLMNotConfigured, LLMProposer

        try:
            p = LLMProposer().propose(sheet, definition)
        except LLMNotConfigured as e:
            ap.error(str(e))
        if p is None:
            print("the LLM gave no proposal (refused or failed): map by hand with --set")
        else:
            proposal = p.model_dump(mode="json")
            meanings |= {n: m for n, m in p.meanings.items() if n not in columns}
            for pf in p.fields:
                notes[pf.field] = f"{pf.reason} (confidence {pf.confidence:.2f})"
                if pf.column and pf.field not in columns:
                    columns[pf.field], origin[pf.field] = pf.column, "LLM"
            print(f"LLM ({p.model}) saw headers and value patterns" + (" and example values" if p.sent_values else
                                                                       ", no values"))
    for item in args.meaning:
        name, _, rest = item.partition("=")
        type_, _, labels = rest.partition(":")
        meanings[name.strip()] = Meaning(type=type_.strip(), labels=[x.strip() for x in labels.split("|") if x.strip()])
    for item in args.set:
        field, _, col = item.partition("=")
        columns[field.strip()], origin[field.strip()] = col.strip().upper(), "you"

    by_col = {h.col: h for h in sheet.headers}
    profiles = {p.col: p for p in profile_sheet(sheet)}
    print(f"\n{args.sample}: sheet {sheet.title!r}, header on row {sheet.header_row}, {len(sheet.headers)} columns, "
          f"definition {definition.name}\n")
    shown = definition.with_meanings({n: m.model_dump() for n, m in meanings.items()})
    for spec in shown.fields:
        col = columns.get(spec.name)
        kind = "own" if spec.name in meanings else spec.role
        if col:
            head = by_col[col].header if col in by_col else "?"
            pat = "; ".join(profiles[col].pattern) if col in profiles else ""
            print(f"  {spec.name:<13} {spec.type:<10} {kind:<13} → {col} {head!r:<16} [{origin[spec.name]}]  {pat}")
        else:
            print(f"  {spec.name:<13} {spec.type:<10} {kind:<13} → (not mapped)"
                  + (f"  {ambiguous[spec.name]}" if spec.name in ambiguous else ""))
        if spec.name in notes:
            print(f"  {'':<33}LLM: {notes[spec.name]}")
    unused = [h for h in sheet.headers if h.col not in columns.values()]
    print("\n  other columns (kept, not used): " + ", ".join(f"{h.col} {h.header!r}" for h in unused))

    mapping = ColumnMapping(definition=definition.name, version=definition.version, columns=columns,
                            meanings={n: m for n, m in meanings.items() if n in columns}, source="claude" if "LLM" in origin.values() else "person",  # 'claude': any LLM proposal
                            headers_sha256=headers_sha256(sheet), notes=notes, proposal=proposal)
    problems = validate(sheet, definition, mapping)
    for p in problems:
        print(f"  ❌ {p}")
    if problems:
        print("\nnot usable yet: fix with --set FIELD=COL (and --meaning NAME=TYPE:LABEL for a meaning of your own)")
        sys.exit(1)
    if not args.confirm:
        print("\n✅ usable. Review it, then run again with --confirm to accept it.")
        return
    mapping = mapping.model_copy(update={"confirmed_by": args.user})
    if ctx:
        from tickmark.store.mappings import save_mapping

        mid = save_mapping(ctx, mapping, sheet.headers)
        print(f"\n✅ confirmed by {args.user}, saved for this engagement ({mid}); runs on this layout use it")
    if args.out:
        args.out.write_text(json.dumps(mapping.model_dump(mode="json"), indent=2))
        print(f"✅ confirmed by {args.user}, written to {args.out} (use with --mapping)")
    if not (ctx or args.out):
        print("\nconfirmed, but not stored: add --tenant/--engagement to save it, or --out FILE")


if __name__ == "__main__":
    main()
