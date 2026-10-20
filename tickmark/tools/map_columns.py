"""Decide which column of a client's sheet holds each field, once per client and header layout.

    python -m tools.map_columns SAMPLE.xlsx                         # show what is known, what is missing
    python -m tools.map_columns SAMPLE.xlsx --suggest               # ask Claude (needs TICKMARK_LLM_MAPPING=on)
    python -m tools.map_columns SAMPLE.xlsx --set amount=D --set vendor=B
    python -m tools.map_columns SAMPLE.xlsx --suggest --confirm --tenant T --engagement E   # save it
    python -m tools.map_columns SAMPLE.xlsx --set ... --confirm --out mapping.json         # offline

Starts from a mapping saved for this client and header layout, else the known header names.
--suggest fills the rest from Claude: by default it sees only headers and value PATTERNS, never
values (TICKMARK_LLM_MAPPING_VALUES=on separately allows 5 examples per column). Every column is
then checked against all its values. Nothing is used until --confirm, which records who confirmed.
"""
import argparse
import json
import sys
from pathlib import Path

from tickmark.config import settings
from tickmark.definitions import DEFINITIONS
from tickmark.fields.mapping import ColumnMapping, alias_mapping, headers_sha256, validate
from tickmark.fields.profile import profile_sheet
from tickmark.fields.sheet import read_sheet
from tools.tenant_args import add_tenant_args, tenant_context


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sample", type=Path)
    ap.add_argument("--definition", default="invoice_vouching", choices=list(DEFINITIONS))
    ap.add_argument("--suggest", action="store_true", help="ask Claude for the fields still unmapped")
    ap.add_argument("--set", action="append", default=[], metavar="FIELD=COL", help="map a field by hand")
    ap.add_argument("--confirm", action="store_true", help="accept the mapping (records you as confirming)")
    ap.add_argument("--out", type=Path, help="also write the confirmed mapping to a JSON file")
    add_tenant_args(ap)
    args = ap.parse_args()
    definition = DEFINITIONS[args.definition]
    sheet = read_sheet(args.sample)
    ctx = tenant_context(ap, args) if (args.tenant or args.engagement) else None

    columns, origin, notes, proposal = {}, {}, {}, None
    saved = None
    if ctx:
        from tickmark.store.mappings import current_mapping

        saved = current_mapping(ctx, definition.name, definition.version, headers_sha256(sheet))
    start = saved or alias_mapping(sheet, definition)
    for f, c in start.columns.items():
        columns[f], origin[f] = c, "saved" if saved else "known header"

    if args.suggest:
        if not settings.llm_mapping:
            ap.error("Claude mapping is off: set TICKMARK_LLM_MAPPING=on (headers + value patterns are sent to "
                     "Anthropic; values only with TICKMARK_LLM_MAPPING_VALUES=on)")
        from tickmark.fields.llm_mapping import ClaudeProposer

        p = ClaudeProposer().propose(sheet, definition)
        if p is None:
            print("Claude gave no proposal (refused or failed): map by hand with --set")
        else:
            proposal = p.model_dump(mode="json")
            for pf in p.fields:
                notes[pf.field] = f"{pf.reason} (confidence {pf.confidence:.2f})"
                if pf.column and pf.field not in columns:
                    columns[pf.field], origin[pf.field] = pf.column, "Claude"
            print(f"Claude ({p.model}) saw headers and value patterns" + (" and example values" if p.sent_values else
                                                                          ", no values"))
    for item in args.set:
        field, _, col = item.partition("=")
        columns[field.strip()], origin[field.strip()] = col.strip().upper(), "you"

    by_col = {h.col: h for h in sheet.headers}
    profiles = {p.col: p for p in profile_sheet(sheet)}
    print(f"\n{args.sample}: sheet {sheet.title!r}, {len(sheet.headers)} columns, definition {definition.name}\n")
    for spec in definition.fields:
        col = columns.get(spec.name)
        need = "required" if spec.required else "optional"
        if col:
            head = by_col[col].header if col in by_col else "?"
            pat = "; ".join(profiles[col].pattern) if col in profiles else ""
            print(f"  {spec.name:<13} {spec.type:<10} {need:<8} → {col} {head!r:<16} [{origin[spec.name]}]  {pat}")
        else:
            print(f"  {spec.name:<13} {spec.type:<10} {need:<8} → (not mapped)")
        if spec.name in notes:
            print(f"  {'':<33}Claude: {notes[spec.name]}")
    unused = [h for h in sheet.headers if h.col not in columns.values()]
    print("\n  other columns (kept, not used): " + ", ".join(f"{h.col} {h.header!r}" for h in unused))

    mapping = ColumnMapping(definition=definition.name, version=definition.version, columns=columns,
                            source="claude" if "Claude" in origin.values() else "person",
                            headers_sha256=headers_sha256(sheet), notes=notes, proposal=proposal)
    problems = validate(sheet, definition, mapping)
    for p in problems:
        print(f"  ❌ {p}")
    if problems:
        print("\nnot usable yet: fix with --set FIELD=COL")
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
