"""Provision the ArchAI corpus-statistics dashboard in a RUNNING Superset instance, over its REST API.

Superset is a separate application (its own venv, metadata database and web UI); this script is the reproducible
form of the click-through that builds the prototype dashboard, so the view can be rebuilt instead of re-clicked:

* the DuckDB database connection that ``make statssql`` writes (``data/out/stapfer_stats.duckdb``),
* one dataset per datasource table the dashboard uses,
* five charts (corpus size, answers per section, sheet-size distribution, answers per page, markup rates),
* one dashboard that lays the charts out, published and owned by the admin user.

    python deploy/superset/provision_superset.py                       # http://127.0.0.1:8088, admin/archai-prototype-1
    SUPERSET_ADMIN_PASSWORD=... python deploy/superset/provision_superset.py --url http://127.0.0.1:8088

Idsempotent: an object that already exists under the same name is updated in place, never duplicated. Every step
reports what it did, and the last step reads the corpus-size chart back through the chart data API, so the run ends
with figures that came out of Superset rather than out of this script. The committed export of what this produced
is ``deploy/superset/assets/archai_corpus_dashboard.zip`` (``superset import-dashboards -p <zip>``).

Only the standard library is used, so any Python 3 can run it; Superset itself is never imported here.
"""
import argparse, http.cookiejar, json, os, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEFAULT_DB = ROOT / "data" / "out" / "stapfer_stats.duckdb"
DATABASE_NAME = "ArchAI corpus statistics (DuckDB file)"
DASHBOARD_TITLE = "ArchAI – Stapfer-Enquête corpus statistics"
DASHBOARD_SLUG = "archai-corpus-statistics"


def metric(column, aggregate="SUM"):
    """A simple Superset metric over one dataset column (the labels are what the charts show)."""
    return {"expressionType": "SIMPLE", "column": {"column_name": column}, "aggregate": aggregate,
            "label": f"{aggregate}({column})"}


# Chart types Superset 6.1 registers (echarts_timeseries_bar and table). The pre-4.0 `bar`/`dist_bar` type is
# gone in 6.1: it is still accepted by the REST API but the frontend renders "Item with key 'bar' is not
# registered", so the modern type is used.
BAR = {"adhoc_filters": [], "time_range": "No filter", "row_limit": 200, "groupby": [],
       "show_legend": False, "orientation": "vertical", "stack": False, "xAxisForceCategorical": True,
       "x_axis_sort_asc": True, "truncateXAxis": False, "rich_tooltip": True, "y_axis_format": "SMART_NUMBER"}

CHARTS = [
    {"name": "Corpus size: answers and sheets",
     "dataset": "documents", "viz_type": "table",
     "params": {"query_mode": "raw", "all_columns": ["answers", "documents"], "row_limit": 1,
                "order_by_cols": [], "include_search": False, "adhoc_filters": [], "time_range": "No filter"}},
    {"name": "Answers per questionnaire section",
     "dataset": "sections", "viz_type": "echarts_timeseries_bar",
     "params": {"x_axis": "section", "metrics": [metric("answers")], "x_axis_label_rotation": 45, **BAR}},
    {"name": "Sheet size: answers per sheet",
     "dataset": "answers_per_sheet", "viz_type": "echarts_timeseries_bar",
     "params": {"x_axis": "answers", "metrics": [metric("sheets")], **BAR}},
    {"name": "Answers per page",
     "dataset": "answers_per_page", "viz_type": "echarts_timeseries_bar",
     "params": {"x_axis": "page", "metrics": [metric("answers")], **BAR}},
    {"name": "Transcription markup and annotation rates",
     "dataset": "markup_rates", "viz_type": "echarts_timeseries_bar",
     "params": {"x_axis": "feature", "metrics": [metric("rate_pct")], "y_axis_format": ".2f",
                "x_axis_label_rotation": 25, **{k: v for k, v in BAR.items() if k != "y_axis_format"}}},
]

JSON_METADATA = {
    "chart_configuration": {},
    "global_chart_configuration": {"scope": {"rootPath": ["ROOT_ID"], "excluded": []}, "chartsInScope": [],
                                   "colorScheme": "", "forceOverride": False},
    "native_filter_configuration": [], "cross_filters_enabled": False, "color_scheme": "",
    "label_colors": {}, "shared_label_colors": {}, "map_label_colors": {}, "default_filters": "{}",
    "filter_scopes": {}, "expanded_slices": {}, "timed_refresh_immune_slices": [], "refresh_frequency": 0,
}


class Superset:
    """Minimal REST client: bearer-token login, then JSON calls that raise with Superset's own error message."""

    def __init__(self, url, username, password):
        self.url = url.rstrip("/")
        # Superset enforces CSRF on POST/PUT even for a bearer token, and the CSRF token belongs to the session
        # cookie, so the client keeps a cookie jar instead of turning CSRF off in Superset's configuration.
        self.csrf = None
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.access = self._call("POST", "/api/v1/security/login",
                                 {"username": username, "password": password, "provider": "db", "refresh": False},
                                 auth=False)["access_token"]
        self.csrf = self._call("GET", "/api/v1/security/csrf_token/")["result"]

    def _call(self, method, path, body=None, query=None, auth=True):
        url = f"{self.url}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        if auth:
            headers["Authorization"] = f"Bearer {self.access}"
        if self.csrf and method in ("POST", "PUT", "DELETE"):
            headers["X-CSRFToken"] = self.csrf
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=120) as response:
                payload = response.read().decode()
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:800]
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
        return json.loads(payload) if payload else {}

    def get(self, path, query=None):
        return self._call("GET", path, query=query)

    def post(self, path, body):
        return self._call("POST", path, body)

    def put(self, path, body):
        return self._call("PUT", path, body)

    def listed(self, path, **filters):
        """Every object of a collection, paged; filters are applied on the client (few objects, no RISON escaping)."""
        page, size, out = 0, 100, []
        while True:
            payload = self.get(path, {"q": f"(page_size:{size},page:{page})"})
            out += payload.get("result", [])
            if not payload.get("result") or (page + 1) * size >= payload.get("count", 0):
                return [r for r in out if all(r.get(k) == v for k, v in filters.items())]
            page += 1

    def first(self, path, **filters):
        found = self.listed(path, **filters)
        if len(found) > 1:
            raise RuntimeError(f"{len(found)} objects match {filters} in {path}: {[f.get('id') for f in found]}")
        return found[0] if found else None


def ensure_database(api, uri):
    body = {"database_name": DATABASE_NAME, "sqlalchemy_uri": uri, "expose_in_sqllab": True,
            "allow_ctas": False, "allow_cvas": False, "allow_dml": False, "allow_file_upload": False,
            "allow_run_async": False,
            # read-only: Superset never writes here, and the Docker route mounts data/out read-only, where a
            # read-write DuckDB open fails with "Read-only file system"
            "extra": json.dumps({"engine_params": {"connect_args": {"read_only": True}}})}
    existing = api.first("/api/v1/database/", database_name=DATABASE_NAME)
    if existing:
        api.put(f"/api/v1/database/{existing['id']}", body)
        return existing["id"], "updated"
    return api.post("/api/v1/database/", body)["id"], "created"


def ensure_dataset(api, database_id, table, owner):
    existing = api.first("/api/v1/dataset/", table_name=table)
    if existing and existing.get("database", {}).get("id") != database_id:  # never silently reuse another connection
        raise RuntimeError(f"dataset {table!r} already belongs to database id "
                           f"{existing['database'].get('id')} ({existing['database'].get('database_name')})")
    if existing:
        api.put(f"/api/v1/dataset/{existing['id']}", {"database_id": database_id, "schema": "main", "table_name": table,
                                                      "owners": [owner]})
        dataset_id, what = existing["id"], "updated"
    else:
        dataset_id = api.post("/api/v1/dataset/", {"database": database_id, "schema": "main", "table_name": table,
                                                   "owners": [owner]})["id"]
        what = "created"
    # Read the table's columns from DuckDB through Superset: this is also the first real query against the
    # datasource, because refreshing asks DuckDB for the table's schema.
    api.put(f"/api/v1/dataset/{dataset_id}/refresh", {})
    columns = api.get(f"/api/v1/dataset/{dataset_id}")["result"].get("columns") or []
    return dataset_id, what, [c["column_name"] for c in columns]


def ensure_dashboard(api, owner):
    existing = api.first("/api/v1/dashboard/", dashboard_title=DASHBOARD_TITLE)
    if existing:
        return existing["id"], "reused"
    created = api.post("/api/v1/dashboard/", {"dashboard_title": DASHBOARD_TITLE, "slug": DASHBOARD_SLUG,
                                              "published": True, "owners": [owner]})
    return created["id"], "created"


def query_of(params):
    """The query a chart's form data means, in the shape Superset's query context expects (raw vs aggregate mode)."""
    aggregate = params.get("query_mode", "aggregate") != "raw"
    dimensions = ([params["x_axis"]] if params.get("x_axis") else []) + list(params.get("groupby", []))
    return {"filters": params.get("adhoc_filters", []), "extras": {"having": "", "where": ""},
            "applied_time_extras": {}, "columns": dimensions if aggregate else params.get("all_columns", []),
            "metrics": params.get("metrics", []) if aggregate else [], "orderby": [], "annotation_layers": [],
            "row_limit": params.get("row_limit", 1000), "series_limit": 0,
            "order_desc": params.get("order_desc", True), "url_params": {}, "custom_params": {},
            "custom_form_data": {}}


def query_context(chart, dataset_id):
    """What Superset saves as the chart's query context, so `/api/v1/chart/<pk>/data/` can run the chart.

    Charts created through the REST API have no query context of their own (the web UI computes it in the
    browser), and without one the chart data API answers "Chart has no query context saved".
    """
    form_data = {"viz_type": chart["viz_type"], "datasource": f"{dataset_id}__table", **chart["params"]}
    return {"datasource": {"id": dataset_id, "type": "table"}, "force": False,
            "queries": [query_of(chart["params"])], "form_data": form_data, "result_format": "json",
            "result_type": "full"}


def ensure_chart(api, chart, dataset_id, dashboard_id):
    body = {"slice_name": chart["name"], "viz_type": chart["viz_type"], "datasource_id": dataset_id,
            "datasource_type": "table", "params": json.dumps({"viz_type": chart["viz_type"], **chart["params"]}),
            "query_context": json.dumps(query_context(chart, dataset_id)), "dashboards": [dashboard_id]}
    existing = api.first("/api/v1/chart/", slice_name=chart["name"])
    if existing:
        api.put(f"/api/v1/chart/{existing['id']}", body)
        return existing["id"], "updated"
    return api.post("/api/v1/chart/", body)["id"], "created"


def position_json(charts, title):
    """The dashboard layout: the corpus figures in one full-width row, then the four distributions two per row."""
    rows = [charts[:1], charts[1:3], charts[3:]]
    position = {
        "DASHBOARD_VERSION_KEY": "v2",
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": title}},
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "parents": ["ROOT_ID"],
                    "children": [f"ROW-{index}" for index in range(1, len(rows) + 1)]},
    }
    for index, row in enumerate(rows, start=1):
        row_id = f"ROW-{index}"
        position[row_id] = {"type": "ROW", "id": row_id, "children": [f"CHART-{pk}" for pk, _ in row],
                            "parents": ["ROOT_ID", "GRID_ID"],
                            "meta": {"background": "BACKGROUND_TRANSPARENT"}}
        for pk, name in row:
            position[f"CHART-{pk}"] = {
                "type": "CHART", "id": f"CHART-{pk}", "children": [], "parents": ["ROOT_ID", "GRID_ID", row_id],
                "meta": {"chartId": pk, "height": 24 if len(row) == 1 else 50, "width": 12 // len(row),
                         "sliceName": name}}
    return position


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the ArchAI corpus-statistics dashboard in a running Superset.")
    ap.add_argument("--url", default=os.environ.get("SUPERSET_URL", "http://127.0.0.1:8088"))
    ap.add_argument("--username", default=os.environ.get("SUPERSET_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("SUPERSET_ADMIN_PASSWORD", "archai-prototype-1"),
                    help="the local prototype admin password created by `superset fab create-admin`")
    ap.add_argument("--db-path", default=str(DEFAULT_DB),
                    help=f"the DuckDB file the datasource points at (default {DEFAULT_DB}; build it with `make statssql`)")
    ap.add_argument("--uri", default=None, help="register this SQLAlchemy URI instead of duckdb:///<--db-path>, "
                                                "e.g. duckdb:////app/data/out/stapfer_stats.duckdb for the Docker route")
    args = ap.parse_args(argv)

    db_path = Path(args.db_path).resolve()
    if not args.uri and not db_path.is_file():
        print(f"error: {db_path} is missing; build the datasource first: make statssql", file=sys.stderr)
        return 1
    # duckdb-engine wants duckdb:// plus an absolute path, which reads as four slashes: duckdb:////abs/path.
    uri = args.uri or f"duckdb:///{db_path}"
    api = Superset(args.url, args.username, args.password)
    owner = api.get("/api/v1/me/")["result"]["id"]  # the admin user gets ownership, so the objects are editable
    report = {"superset": args.url, "datasource": {"database_name": DATABASE_NAME, "sqlalchemy_uri": uri}}
    database_id, what = ensure_database(api, uri)
    report["datasource"].update({"id": database_id, what: True})
    datasets, failed = {}, []
    for chart in CHARTS:
        table = chart["dataset"]
        if table in datasets:
            continue
        dataset_id, what, columns = ensure_dataset(api, database_id, table, owner)
        datasets[table] = dataset_id
        report.setdefault("datasets", {})[table] = {"id": dataset_id, "columns": columns, what: True}
        if not columns:
            failed.append(table)
    if failed:
        print(f"error: Superset synced no columns for {failed}: check the DuckDB connection", file=sys.stderr)
        return 1
    dashboard_id, dashboard_what = ensure_dashboard(api, owner)
    report["dashboard"] = {"id": dashboard_id, "title": DASHBOARD_TITLE, dashboard_what: True,
                           "url": f"{args.url}/superset/dashboard/{DASHBOARD_SLUG}/"}
    report["charts"] = {}
    ids = []
    for chart in CHARTS:
        pk, what = ensure_chart(api, chart, datasets[chart["dataset"]], dashboard_id)
        report["charts"][chart["name"]] = {"id": pk, "viz_type": chart["viz_type"], what: True}
        ids.append((pk, chart["name"]))
    api.put(f"/api/v1/dashboard/{dashboard_id}", {"position_json": json.dumps(position_json(ids, DASHBOARD_TITLE)),
                                                 "json_metadata": json.dumps(JSON_METADATA), "published": True,
                                                 "owners": [owner]})
    # Read every chart back through Superset's chart data API: the figures below come out of Superset's own SQL
    # layer over the DuckDB file, not out of this script or out of archai.stats (see the README for the same
    # figures straight from the endpoint, which agree).
    report["verified_figures"] = {}
    for pk, name in ids:
        result = api.get(f"/api/v1/chart/{pk}/data/")["result"][0]
        data = result.get("data") or []
        report["verified_figures"][name] = {"colnames": result.get("colnames"), "rows": len(data),
                                            "first": data[:3], "last": data[-1:]}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
