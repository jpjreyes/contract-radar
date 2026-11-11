"""Move the processed data into a cloud database: export here, load there (no Postgres client tools needed).

  export  (on the Mac)  contracts, chunks (masked text, offset maps, embeddings) and extracted fields as CSV in one
                        .tar.gz, via COPY. The keyword index (tsv) is a generated column and is rebuilt on load.
  load    (in the cloud, a one-off Container Apps job inside the VNet, run by scripts/azure_load_db.sh)
          1. download the archive from private blob storage with the job's managed identity (no keys)
          2. create the schema, COPY the data in, move the id sequences past the loaded ids
          3. only then switch on roles, row-level security, audit, review queue, limits and sign-in (the managed
             admin is not a superuser, so loading after RLS is on would be refused), and map the Entra groups
          4. smoke test the security that differs on a managed server: reveal() must return originals to
             legal_admin and nothing to the analyst; the app role must not read the original column

Run:  uv run python -m contract_copilot.cloud_load export /tmp/contract-radar-data.tar.gz
"""

from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import urllib.request

from contract_copilot.db import connect, ensure_schema

TABLES = {
    "contracts": "id, name, contract_type, region, business_unit, chars, sha, pii_version, pii_counts",
    "chunks": "id, contract_id, idx, start_char, end_char, heading, text, text_masked, mask_map, embedding",
    "fields": "contract_id, name, present, value, quote, chunk_id, start_char, end_char, confidence, issues, status, "
              "model, extracted_at, reviewed_by, reviewed_at",
}


def export(path: str) -> None:
    conn = connect()
    with tarfile.open(path, "w:gz") as tar:
        for table, cols in TABLES.items():
            buf = io.BytesIO()
            with conn.cursor().copy(f"COPY (SELECT {cols} FROM {table} ORDER BY 1) TO STDOUT (FORMAT csv)") as cp:
                for block in cp:
                    buf.write(block)
            info = tarfile.TarInfo(f"{table}.csv")
            info.size = buf.tell()
            buf.seek(0)
            tar.addfile(info, buf)
            print(f"  {table}: {info.size / 1e6:.1f} MB", flush=True)


def _managed_identity_token(resource: str) -> str:
    """Container Apps exposes a local token endpoint for the app's managed identity."""
    url = (f"{os.environ['IDENTITY_ENDPOINT']}?resource={resource}&api-version=2019-08-01"
           f"&client_id={os.environ['AZURE_CLIENT_ID']}")
    req = urllib.request.Request(url, headers={"X-IDENTITY-HEADER": os.environ["IDENTITY_HEADER"]})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["access_token"]


def _download(blob_url: str) -> bytes:
    token = _managed_identity_token("https://storage.azure.com/")
    req = urllib.request.Request(blob_url, headers={"Authorization": f"Bearer {token}", "x-ms-version": "2021-08-06"})
    return urllib.request.urlopen(req, timeout=300).read()


def load() -> None:
    data = _download(os.environ["BLOB_URL"])
    print(f"downloaded {len(data) / 1e6:.1f} MB", flush=True)
    load_bytes(data)


def load_bytes(data: bytes) -> None:
    from contract_copilot import extract, permissions
    admin = connect()
    ensure_schema(admin)
    admin.execute(extract.SETUP)  # fields table
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar, admin.transaction():
        for table, cols in TABLES.items():
            admin.execute(f"TRUNCATE {table} CASCADE")
        for table, cols in TABLES.items():
            raw = tar.extractfile(f"{table}.csv").read()
            with admin.cursor().copy(f"COPY {table} ({cols}) FROM STDIN (FORMAT csv)") as cp:
                cp.write(raw)
            n = admin.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {n} rows", flush=True)
        for table in ("contracts", "chunks"):
            admin.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))")
    permissions.main()  # roles, RLS, audit, review queue, limits, sign-in
    if os.environ.get("IDENTITY_SQL"):
        admin.execute(os.environ["IDENTITY_SQL"])
        print("mapped Entra groups to app roles", flush=True)
    smoke(admin)


def smoke(admin) -> None:
    import psycopg

    from contract_copilot.permissions import app_connect, as_user
    app = app_connect()
    chunk_id = admin.execute("SELECT k.id FROM chunks k JOIN contracts c ON c.id = k.contract_id "
                             "WHERE c.business_unit = 'Legal' AND k.text_masked LIKE '%%[PERSON_%%' LIMIT 1").fetchone()[0]
    with as_user(app, "legal_admin"):
        assert app.execute("SELECT count(*) FROM reveal(%s)", ([chunk_id],)).fetchone()[0] == 1, "reveal broken"
    with as_user(app, "analyst"):
        assert app.execute("SELECT count(*) FROM reveal(%s)", ([chunk_id],)).fetchone()[0] == 0, "PII leak"
    try:
        with as_user(app, "legal_admin"):
            app.execute("SELECT text FROM chunks LIMIT 1")
        raise AssertionError("app role can read the original column")
    except psycopg.errors.InsufficientPrivilege:
        pass
    print("smoke test passed: reveal works for legal_admin, masked for the analyst, original column locked", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "export":
        export(sys.argv[2])
    else:
        load()
