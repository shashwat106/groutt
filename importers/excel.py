"""Excel / CSV import: every sheet is detected and mapped to an entity."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from importers.mapper import detect_entity, map_columns


def read_records(path: str | Path) -> tuple[dict[str, list[dict]], list[str]]:
    """Return ({entity: [records]}, notes)."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        sheets = {path.stem: pd.read_csv(path)}
    else:
        sheets = pd.read_excel(path, sheet_name=None)
    out: dict[str, list[dict]] = {}
    notes: list[str] = []
    for name, df in sheets.items():
        df = df.dropna(how="all")
        headers = [str(c) for c in df.columns]
        entity = detect_entity(name, headers)
        if entity is None:
            notes.append(f"{path.name}/{name}: could not tell what this sheet holds (headers: {', '.join(headers)})")
            continue
        mapping, unmapped = map_columns(headers, entity)
        recs = []
        for _, row in df.iterrows():
            rec = {f: row[h] for f, h in mapping.items() if pd.notna(row[h])}
            if rec:
                recs.append(rec)
        out.setdefault(entity, []).extend(recs)
        mapped = ", ".join(f"{h} -> {f}" for f, h in mapping.items())
        notes.append(f"{path.name}/{name}: {entity} ({len(recs)} rows; {mapped})"
                     + (f"; ignored columns: {', '.join(unmapped)}" if unmapped else ""))
    return out, notes
