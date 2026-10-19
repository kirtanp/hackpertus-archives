# Apache Superset over the ArchAI corpus statistics (prototype)

Two parts, deliberately separate:

1. **The datasource** – `src/archai/statssql.py` writes a DuckDB **database file** that exposes the corpus
   statistics aggregates as ordinary SQL tables. This is repository code, it is built by a command, and it is
   covered by tests.
2. **Superset itself** – provisioning and documentation, not application code. Superset is a full application
   (Flask, Celery, Redis, its own metadata database and its own web UI); it is *not* mounted inside `app.py`,
   and nothing here changes `/api/stats`, `/api/ask` or any other endpoint. Superset runs **alongside** this
   repository and reads the datasource that this repository produces.

The primary route below installs Superset from PyPI into its own virtualenv and was **run end to end** on this
machine; the secondary route is a `docker compose` file for a host that has Docker (Docker's daemon was not
running where this prototype was built, so that route is not exercised here).

## 1. The datasource

The committed corpus is the parquet file `data/out/stapfer_answers.parquet` (92,524 answers over 2,250 sheets).
The DuckDB database file is **derived** from it, is a build artifact, and is never committed: it lands in
`data/out/`, which `.gitignore` covers with `data/out/*.duckdb`.

```bash
# from the repository root; needs the Python 3.12 virtualenv that `just setup` creates
just statssql
```

`just statssql` is `PYTHONPATH=src uv run --all-groups python -m archai.statssql`; the module also takes `--db PATH`,
`--corpus PATH` and `--report` (print what an existing file contains without rebuilding), and prints a JSON
report of every relation it wrote. The ~41 MB file is mostly the materialised `answers` table.

| Relation | Rows | What one row is |
|---|---|---|
| `answers` | 92,524 | one corpus answer with every parquet column, so anything else can be sliced in SQL |
| `sections` | 19 | answers per questionnaire section (`III.11.b` -> `III.11`) |
| `documents` | 1 | answers counted and distinct sheets represented |
| `answers_per_page` | 16 | answers per page of the transcription |
| `sheet_summary` | 1 | sheets, min/median/max answers per sheet |
| `answers_per_sheet` | 146 | sheet-size histogram, answers -> sheets |
| `pages_per_sheet` | 16 | page-count histogram, pages -> sheets |
| `markup` | 1 | answers carrying markup, markup errors, uncertain readings, margin notes, editor comments |
| `content` | 19 | per section: answers, empty, trivial (fewer than 3 characters after trimming), non-empty |
| `section_page` | 173 | answers per (section, page), the two filter dimensions together |
| `content_page` | 16 | the empty/trivial/non-empty rule per page |
| `markup_rates` (view) | 5 | the markup counts as rates, one row per markup feature |
| `corpus` | 1 | what was read and when: parquet path, engine, rows, columns, build time |

The aggregate SQL is not written a second time: `src/archai/stats.py` exposes `aggregate_sql(query, where)`, the
very statements `/api/stats` runs, and `statssql.py` creates every aggregate table from them, so the HTTP
endpoint and the datasource cannot drift apart. `stats.py` keeps its semantics (the SQL strings are the same
ones, only moved into a public function); `section_page`, `content_page` and `markup_rates` are the extra cuts a
dashboard wants, and they are built from `stats.py`'s own definitions (`SECTION`, `EMPTY`, `TRIVIAL`, `PLAIN`).

`tests/test_statssql.py` re-derives every table from the parquet in pandas, and compares the tables against the
`/api/stats` blocks for the same aggregates, following the pattern of `tests/test_stats.py`.

## 2. Route A (primary): Superset from PyPI in its own virtualenv

Versions this was run with: **apache-superset 6.1.0**, **duckdb-engine 0.17.0**, **duckdb 1.5.6**,
flask-caching 2.1.0, rich 15.0.0, cachetools 7.2.3, on **Python 3.12.12** (macOS arm64, `uv` for the venv).

### Install

```bash
# a dedicated virtualenv for Superset; it is gitignored (deploy/superset/.venv/)
uv venv --python 3.12 deploy/superset/.venv

# apache-superset[duckdb] declares duckdb>=1.4.2,<2 and duckdb-engine>=0.17.0 (resolved to 1.5.6 / 0.17.0 here)
uv pip install --python deploy/superset/.venv/bin/python "apache-superset[duckdb]==6.1.0" "flask-caching==2.1.0" rich cachetools
```

`flask-caching==2.1.0`, `rich` and `cachetools` are the version constraints of this prototype, explained under
"What did not work" below; without them Superset 6.1.0 does not start here.

### Configure

```bash
cp deploy/superset/superset_config.py.example deploy/superset/superset_config.py

# generate the secret key Superset 6 refuses to start without, then paste it into SECRET_KEY
deploy/superset/.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(42))"

# every `superset` command below needs this
export SUPERSET_CONFIG_PATH=$PWD/deploy/superset/superset_config.py
```

`superset_config.py` is gitignored: it holds the generated key and points at the metadata database. The
metadata database is a local SQLite file (`deploy/superset/superset.db`, also gitignored) — enough for one
localhost prototype process, and it keeps Superset's state out of the repository. Neither file is committed.

### Initialise

```bash
# Superset's own metadata schema
deploy/superset/.venv/bin/superset db upgrade

# the local prototype admin (localhost only; nothing is exposed beyond 127.0.0.1)
deploy/superset/.venv/bin/superset fab create-admin --username admin --firstname ArchAI \
    --lastname Prototype --email admin@localhost.invalid --password 'archai-prototype-1'

# roles, permissions and the default datasource; no example dashboards (SUPERSET_LOAD_EXAMPLES = False)
deploy/superset/.venv/bin/superset init
```

### Run

```bash
# http://127.0.0.1:8088  (localhost only, plain HTTP, CSRF and login stay on)
deploy/superset/.venv/bin/superset run -p 8088 --with-threads
```

### Register the datasource and build the dashboard

In a second shell, with the server running:

```bash
# 1. the DuckDB datasource the repository produces
just statssql

# 2. the database connection, five datasets, five charts and one dashboard, over Superset's REST API
.venv/bin/python deploy/superset/provision_superset.py
```

`provision_superset.py` uses only the standard library, logs in with the admin credential, keeps the session
cookie Superset's CSRF check needs, and is idempotent: an object that already exists under the same name is
updated, never duplicated. It prints a JSON report and finishes by reading **every chart back through
Superset's chart data API**, so a run ends with figures that came out of Superset rather than out of the script.
`--url`, `--username`, `--password` (or `SUPERSET_URL` / `SUPERSET_ADMIN_USER` / `SUPERSET_ADMIN_PASSWORD`) and
`--db-path` / `--uri` are the knobs; `--uri` is how the Docker route points Superset at the container path.

The dashboard is published at http://127.0.0.1:8088/superset/dashboard/archai-corpus-statistics/ (5 charts:
corpus size, answers per questionnaire section, sheet-size distribution, answers per page, markup and annotation
rates). `assets/dashboard.png` in this directory is the screenshot of that page as it rendered.

### Export and re-import

```bash
# what the provisioning built, as Superset's own YAML bundle (ZIP) — committed, no credentials inside
deploy/superset/.venv/bin/superset export-dashboards -f deploy/superset/assets/archai_corpus_dashboard.zip

# on another machine: the database connection, the datasets, the charts and the dashboard in one import
deploy/superset/.venv/bin/superset import-dashboards -p deploy/superset/assets/archai_corpus_dashboard.zip
```

`assets/archai_corpus_dashboard.zip` contains `databases/…yaml` (the DuckDB connection), five
`datasets/…yaml`, five `charts/…yaml`, the `dashboards/…yaml` and `metadata.yaml`; it was scanned for
`password`, `secret`, `token` and `api_key` and contains none (the DuckDB URI has no credentials). The URI in
the export is the absolute path of the machine that exported it, so after importing run
`provision_superset.py` (or `superset set-database-uri`) to point the connection at your own
`data/out/stapfer_stats.duckdb`.

## 3. Route B (secondary): `docker compose`

For a host that has Docker, `deploy/superset/docker-compose.yml` starts Superset 6.1.0 with a Postgres metadata
database and Redis, with the repository's `data/out/` mounted read-only at `/app/data/out`:

```bash
# starts the stack on http://127.0.0.1:8088 (Postgres + Redis + Superset, image built with duckdb-engine)
docker compose -f deploy/superset/docker-compose.yml up --build -d

# points the connection at the path INSIDE the container (the mounted data/out)
.venv/bin/python deploy/superset/provision_superset.py --uri duckdb:////app/data/out/stapfer_stats.duckdb
```

**This route was not run here**: `/var/run/docker.sock` does not exist on this machine, so no container was
started and no compose output is claimed. It is written from Superset's official image and configuration and
uses the same provisioning script as the primary route, but expect to adjust image tags or credentials on
another host. The image itself has since been built and run (Route C, below), which found and fixed two bugs
that also affected this route: `Dockerfile.superset` installed duckdb-engine into the system Python instead of
Superset's `/app/.venv`, and a read-write DuckDB open fails on the read-only `data/out` mount (the provisioning
script now registers the connection with `read_only`).

### Route C: one container on a small host (run on the team demo host)

On a host with ~1 GB of RAM to spare, Postgres and Redis are not needed: the same image runs alone with a
SQLite metadata database in a named volume (`superset_config_docker.py` reads `SUPERSET_METADATA_DB`). Measured
there: ~250 MB resident under a 1.2 GB limit.

```bash
docker build -t archai-superset -f deploy/superset/Dockerfile.superset deploy/superset
# secrets live outside the repository, mode 600: SUPERSET_SECRET_KEY, SUPERSET_ADMIN_USER, SUPERSET_ADMIN_PASSWORD
# (generate both secrets with python -c "import secrets; print(secrets.token_urlsafe(42))")
docker run -d --name archai-superset --restart unless-stopped --memory 1200m --env-file ~/.config/archai/superset.env \
  -e SUPERSET_CONFIG_PATH=/app/pythonpath/superset_config_docker.py \
  -e SUPERSET_METADATA_DB=sqlite:////app/superset_home/superset.db -e SUPERSET_LOAD_EXAMPLES=no \
  -v $PWD/deploy/superset/superset_config_docker.py:/app/pythonpath/superset_config_docker.py:ro \
  -v $PWD/data/out:/app/data/out:ro -v archai-superset-home:/app/superset_home -p 127.0.0.1:8088:8088 \
  archai-superset /bin/sh -c 'superset db upgrade && (superset fab create-admin --username "$SUPERSET_ADMIN_USER" \
    --firstname ArchAI --lastname Admin --email admin@localhost.invalid --password "$SUPERSET_ADMIN_PASSWORD" || true) \
    && superset init && exec superset run -h 0.0.0.0 -p 8088 --with-threads'
# with the same env file loaded, so the generated admin password is used
set -a; . ~/.config/archai/superset.env; set +a
python3 deploy/superset/provision_superset.py --uri duckdb:////app/data/out/stapfer_stats.duckdb
```

The provisioning run read every chart back through Superset: 92,524 answers over 2,250 sheets, 19 sections,
146 sheet-size buckets, 16 pages.

## 4. What was actually run, and what it showed

Commands: the install, configure, initialise and run steps above, then `just statssql` (41,431,040 bytes,
13 relations) and `.venv/bin/python deploy/superset/provision_superset.py`.

```bash
curl -sS http://127.0.0.1:8088/health        # -> OK
```

The provisioning report of the recorded run (figures read back through `/api/v1/chart/<pk>/data/`, that is,
through Superset's SQL layer over the DuckDB file):

| Chart | Rows | Figures |
|---|---|---|
| Corpus size: answers and sheets | 1 | `answers = 92524`, `documents = 2250` |
| Answers per questionnaire section | 19 | largest section `III.11 = 19204`, `I.1 = 13953`, `IV.16 = 9291`, `IV.15 = 7685`, `IV.13 = 5741` |
| Sheet size: answers per sheet | 146 | `1 answer = 25 sheets`, `2 = 59`, `3 = 11`; median 38, max 215 |
| Answers per page | 16 | `page 1 = 32885`, `page 2 = 28966`, `page 3 = 20557` |
| Transcription markup and annotation rates | 5 | markup 42.03%, markup errors 0.29%, uncertain readings 0.68%, margin notes 0.69%, editor comments 0.07% |

Those figures agree with `/api/stats` over HTTP for the same aggregates (`stats.summary` reports the same
numbers), which is what the datasource tests pin. The dashboard screenshot in `assets/dashboard.png` shows the
same figures rendered by Superset: `92524` / `2250` in the corpus table, the `III.11` bar at 19.2k, and the
42.03% markup bar.

## 5. What did not work, and why

- **flask-caching >= 2.3 breaks `superset db upgrade`.** `uv` resolved flask-caching 2.5.1 for
  apache-superset 6.1.0 (whose requirement is only `>=2.1.0,<3`), and Superset then died with
  `SupersetMetastoreCache.__init__() got an unexpected keyword argument 'ignore_delete_many_errors'`. Pinning
  `flask-caching==2.1.0` fixes it.
- **`rich` and `cachetools` are imported but not declared.** `superset --help` failed with
  `ModuleNotFoundError: No module named 'rich'` (`superset/cli/test_db.py`) and `/login/` answered HTTP 500
  with `No module named 'cachetools'` (`superset/db_engine_specs/aws_iam.py`); neither appears in the package
  metadata (not even under an extra), so both are installed explicitly.
- **The `bar` chart type no longer exists in Superset 6.** Charts created with `viz_type: "bar"` are accepted
  by the REST API and even return data from `/api/v1/chart/<pk>/data/`, but the browser renders
  `Error: Item with key "bar" is not registered`. The registered set in 6.1.0 (read out of the frontend bundle)
  has `echarts_timeseries_bar` instead, which is what the charts use now; this is exactly the trap the
  screenshot caught.
- **Charts created over the REST API need a saved `query_context`.** Without one, the chart data API answers
  `Chart has no query context saved. Please save the chart again.` because the browser normally computes it;
  `provision_superset.py` builds and sends it.
- **A GET with a JSON content type is a 400.** Superset's error-logging path calls `request.get_json()` when
  the request claims to be JSON, so the client must not send `Content-Type: application/json` on GETs.
- **`/api/v1/security/me` does not exist** (it is `/api/v1/me/`), and `PUT /api/v1/dataset/<pk>/refresh`
  answers `{"message": "OK"}` rather than the column list, so the columns are read back with a second GET.
- A wheel-only Superset logs `Could not load default spinner SVG` because the navigation asset is not packaged;
  harmless for this prototype.

## 6. Next steps

- Run the compose route on a Docker host and record the same verification, then make it the documented route
  for a server deployment (the local-venv route stays the developer route).
- Turn the DuckDB file into a scheduled artifact (a CI or cron step after `just statssql`) and let Superset
  read it from a fixed path instead of a worktree path.
- Add the dimensions the dashboard will want next: per-school-type and per-language cuts from the geocoded
  places, per-decade trends from the consultation corpus, and native dashboard filters over `section` and
  `page` using `section_page` / `content_page`, which already exist in the datasource.
- Decide what Superset's metadata database should be for anything shared (Postgres, as in the compose route)
  instead of the local SQLite file.
