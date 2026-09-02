#!/usr/bin/env python3
import hashlib
import json
import os
import resource
import sqlite3
import subprocess
import time
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

SOURCE_COMMIT = "e7e6d5f008e35d3f89d8b8a4f8d38e3bfa7e34bd"
LICENSE_BLOB = "d596e90a25aa5b1e894ad3b20eca067791cdcdca"
DATABASE_BLOB = "b559c7394abe5dedc1944483de9d715a7fcaa2e8"
SCRIPT_BLOB = "cff14dac8c464a969c2fedd5a4f941f9affd0d0a"
LOWER = "2012-01-01 00:00:00"
UPPER = "2013-01-01 00:00:00"
WRONG_LOWER = "2011-01-01 00:00:00"
WRONG_UPPER = "2012-01-01 00:00:00"

CANDIDATE_SQL = """
WITH genre_artist_revenue AS (
  SELECT g.GenreId AS GenreId, g.Name AS GenreName,
         ar.ArtistId AS ArtistId, ar.Name AS ArtistName,
         SUM(CAST(ROUND(il.UnitPrice * 100.0) AS INTEGER) * il.Quantity) AS RevenueCents
  FROM Invoice AS i
  JOIN InvoiceLine AS il ON il.InvoiceId = i.InvoiceId
  JOIN Track AS t ON t.TrackId = il.TrackId
  JOIN Genre AS g ON g.GenreId = t.GenreId
  JOIN Album AS al ON al.AlbumId = t.AlbumId
  JOIN Artist AS ar ON ar.ArtistId = al.ArtistId
  WHERE i.InvoiceDate >= ? AND i.InvoiceDate < ?
  GROUP BY g.GenreId, g.Name, ar.ArtistId, ar.Name
  HAVING RevenueCents > 0
), ranked AS (
  SELECT GenreId, GenreName, ArtistId, ArtistName, RevenueCents,
         ROW_NUMBER() OVER (
           PARTITION BY GenreId
           ORDER BY RevenueCents DESC, ArtistId ASC
         ) AS rank_within_genre
  FROM genre_artist_revenue
)
SELECT GenreId, GenreName, ArtistId, ArtistName, RevenueCents
FROM ranked
WHERE rank_within_genre = 1
ORDER BY GenreId ASC
""".strip()


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def git_blob(root: Path, relative: str) -> str:
    return run("git", "-C", str(root), "hash-object", relative)


def schema_snapshot(conn: sqlite3.Connection) -> dict:
    tables = ["Genre", "Track", "Album", "Artist", "InvoiceLine", "Invoice"]
    return {
        table: [
            {"cid": row[0], "name": row[1], "type": row[2], "notnull": row[3], "pk": row[5]}
            for row in conn.execute(f'PRAGMA table_info("{table}")')
        ]
        for table in tables
    }


def candidate(conn: sqlite3.Connection, lower: str, upper: str, broken: bool = False) -> list:
    sql = CANDIDATE_SQL
    if broken:
        sql = sql.replace("JOIN Album AS al ON al.AlbumId = t.AlbumId", "JOIN Album AS al ON al.AlbumId = t.AlbumId + 1")
    return [list(row) for row in conn.execute(sql, (lower, upper))]


def oracle(conn: sqlite3.Connection, lower: str, upper: str) -> list:
    genres = {int(r[0]): str(r[1]) for r in conn.execute("SELECT GenreId, Name FROM Genre")}
    artists = {int(r[0]): str(r[1]) for r in conn.execute("SELECT ArtistId, Name FROM Artist")}
    albums = {int(r[0]): int(r[1]) for r in conn.execute("SELECT AlbumId, ArtistId FROM Album")}
    tracks = {int(r[0]): (int(r[1]), int(r[2])) for r in conn.execute("SELECT TrackId, AlbumId, GenreId FROM Track")}
    invoices = {
        int(r[0])
        for r in conn.execute("SELECT InvoiceId FROM Invoice WHERE InvoiceDate >= ? AND InvoiceDate < ?", (lower, upper))
    }
    revenue = defaultdict(int)
    for invoice_id, track_id, unit_price, quantity in conn.execute(
        "SELECT InvoiceId, TrackId, UnitPrice, Quantity FROM InvoiceLine"
    ):
        if int(invoice_id) not in invoices:
            continue
        album_id, genre_id = tracks[int(track_id)]
        artist_id = albums[album_id]
        cents = int((Decimal(str(unit_price)) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        revenue[(genre_id, artist_id)] += cents * int(quantity)
    winners = {}
    for (genre_id, artist_id), cents in revenue.items():
        if cents <= 0:
            continue
        key = (-cents, artist_id)
        current = winners.get(genre_id)
        if current is None or key < current[0]:
            winners[genre_id] = (key, artist_id, cents)
    return [
        [genre_id, genres[genre_id], artist_id, artists[artist_id], cents]
        for genre_id, (_, artist_id, cents) in sorted(winners.items())
    ]


def main() -> None:
    started = time.perf_counter()
    root = Path(os.environ["CHINOOK_ROOT"]).resolve()
    db = root / "ChinookDatabase/DataSources/Chinook_Sqlite.sqlite"
    sql_source = root / "ChinookDatabase/DataSources/Chinook_Sqlite.sql"
    license_path = root / "LICENSE.md"
    identity = {
        "commit_sha": run("git", "-C", str(root), "rev-parse", "HEAD"),
        "license_blob_sha": git_blob(root, "LICENSE.md"),
        "database_blob_sha": git_blob(root, "ChinookDatabase/DataSources/Chinook_Sqlite.sqlite"),
        "construction_script_blob_sha": git_blob(root, "ChinookDatabase/DataSources/Chinook_Sqlite.sql"),
    }
    expected = {
        "commit_sha": SOURCE_COMMIT,
        "license_blob_sha": LICENSE_BLOB,
        "database_blob_sha": DATABASE_BLOB,
        "construction_script_blob_sha": SCRIPT_BLOB,
    }
    if identity != expected:
        raise RuntimeError(f"identity mismatch: {identity}")
    before_sha256 = sha256(db)
    before_size = db.stat().st_size
    before_status = run("git", "-C", str(root), "status", "--porcelain")
    uri = f"file:{db}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        schema_started = time.perf_counter()
        schema = schema_snapshot(conn)
        required = {
            "Genre": {"GenreId", "Name"}, "Track": {"TrackId", "AlbumId", "GenreId"},
            "Album": {"AlbumId", "ArtistId"}, "Artist": {"ArtistId", "Name"},
            "InvoiceLine": {"InvoiceId", "TrackId", "UnitPrice", "Quantity"},
            "Invoice": {"InvoiceId", "InvoiceDate"},
        }
        for table, names in required.items():
            actual = {c["name"] for c in schema[table]}
            if not names <= actual:
                raise RuntimeError(f"schema mismatch for {table}: {sorted(actual)}")
        schema_seconds = time.perf_counter() - schema_started
        range_row = conn.execute("SELECT MIN(InvoiceDate), MAX(InvoiceDate), COUNT(*) FROM Invoice").fetchone()
        in_scope = conn.execute("SELECT COUNT(*) FROM Invoice WHERE InvoiceDate >= ? AND InvoiceDate < ?", (LOWER, UPPER)).fetchone()[0]
        sql_receipt = {"sha256": hashlib.sha256(CANDIDATE_SQL.encode()).hexdigest(), "statement_count": 1, "data_rows_read_before_freeze": False}
        plan = [list(row) for row in conn.execute("EXPLAIN QUERY PLAN " + CANDIDATE_SQL, (LOWER, UPPER))]
        t0 = time.perf_counter()
        cand = candidate(conn, LOWER, UPPER)
        candidate_seconds = time.perf_counter() - t0
        t0 = time.perf_counter()
        independent = oracle(conn, LOWER, UPPER)
        oracle_seconds = time.perf_counter() - t0
        t0 = time.perf_counter()
        wrong = candidate(conn, WRONG_LOWER, WRONG_UPPER)
        wrong_year_seconds = time.perf_counter() - t0
        t0 = time.perf_counter()
        broken = candidate(conn, LOWER, UPPER, broken=True)
        broken_join_seconds = time.perf_counter() - t0
        t0 = time.perf_counter()
        replay = candidate(conn, LOWER, UPPER)
        replay_seconds = time.perf_counter() - t0
    finally:
        conn.close()
    after_sha256 = sha256(db)
    after_size = db.stat().st_size
    after_status = run("git", "-C", str(root), "status", "--porcelain")
    result = {
        "schema_version": 1,
        "record_type": "generation29_external_relational_query_repaired_execution",
        "identity": identity,
        "database": {"sha256_before": before_sha256, "sha256_after": after_sha256, "bytes_before": before_size, "bytes_after": after_size},
        "schema": schema,
        "sqlite": {"python_module_version": sqlite3.version, "engine_version": sqlite3.sqlite_version},
        "invoice_range": {"minimum": range_row[0], "maximum": range_row[1], "count": range_row[2], "in_scope_2012_count": in_scope},
        "candidate_sql": CANDIDATE_SQL,
        "candidate_sql_receipt": sql_receipt,
        "candidate_query_plan": plan,
        "candidate_rows": cand,
        "oracle_rows": independent,
        "wrong_year_rows": wrong,
        "broken_join_rows": broken,
        "checks": {
            "identity_exact": identity == expected,
            "in_scope_nonempty": in_scope > 0,
            "candidate_equals_oracle": cand == independent,
            "wrong_year_diverges": wrong != cand,
            "broken_join_diverges": broken != cand,
            "clean_replay_identical": replay == cand,
            "fixture_unchanged": before_sha256 == after_sha256 and before_size == after_size,
            "zero_upstream_writes": before_status == after_status == "",
        },
        "source_status_before": before_status,
        "source_status_after": after_status,
        "row_set_digests": {
            "candidate_sha256": stable_digest(cand),
            "oracle_sha256": stable_digest(independent),
            "wrong_year_sha256": stable_digest(wrong),
            "broken_join_sha256": stable_digest(broken),
            "replay_sha256": stable_digest(replay),
        },
        "timings_seconds": {
            "schema": schema_seconds,
            "candidate": candidate_seconds,
            "oracle": oracle_seconds,
            "wrong_year": wrong_year_seconds,
            "broken_join": broken_join_seconds,
            "replay": replay_seconds,
            "total": time.perf_counter() - started,
        },
        "resources": {"max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},
        "reported_output": {
            "columns": ["GenreId", "GenreName", "ArtistId", "ArtistName", "Revenue"],
            "revenue_representation": "exact decimal string normalized from internally compared integer cents",
            "rows": [[r[0], r[1], r[2], r[3], f"{r[4] // 100}.{r[4] % 100:02d}"] for r in cand],
        },
        "claim_boundary": "One exact tagged fixture and frozen task only; no generalized-data, activation, production, AGI, upper-objective, user-level, or monitor-completion claim.",
    }
    result["verdict"] = "SOLVED" if all(result["checks"].values()) else "FAILED"
    print("GEN29_RELATIONAL_RESULT=" + json.dumps(result, sort_keys=True, separators=(",", ":")))
    if result["verdict"] != "SOLVED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
