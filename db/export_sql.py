"""Write the Postgres schema for Supabase from the SQLAlchemy models.

Run:  uv run python -m db.export_sql   -> supabase/migrations/0001_init.sql
To apply on Supabase: set DATABASE_URL to the Supabase Postgres URL and run
      uv run python -m seed.sharma_footwear   (creates tables + demo data)
or paste the SQL file into the Supabase SQL editor.
"""
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from db.models import Base

OUT = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "0001_init.sql"

RLS_NOTE = """
-- Row-level security: enable before more than one business shares this database.
-- Example (requires Supabase Auth with a business_id claim):
--   alter table orders enable row level security;
--   create policy biz_isolation on orders
--     using (business_id = (auth.jwt() ->> 'business_id')::int);
"""


def main() -> Path:
    dialect = postgresql.dialect()
    parts = ["-- Grout schema (generated from db/models.py). Do not edit by hand.\n"]
    for table in Base.metadata.sorted_tables:
        parts.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";\n")
        for idx in table.indexes:
            parts.append(str(CreateIndex(idx).compile(dialect=dialect)).strip() + ";\n")
    parts.append(RLS_NOTE)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    return OUT


if __name__ == "__main__":
    print(f"Wrote {main()}")
