"""Plain-string HTML templates (BUILD_PLAN WP8: "jinja2 or plain string
templates — your call"). No frontend build step, no CDN, inline CSS only.
"""

from __future__ import annotations

import html
from typing import Any

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1b1f24; --muted: #5b6673; --border: #d9dee3;
  --accent: #2454ad; --code-bg: #f2f4f7;
  --pass: #1a7f37; --fail: #cf222e; --skip: #9a6700;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a; --fg: #e6edf3; --muted: #9aa7b3; --border: #333a42;
    --accent: #7aa7ff; --code-bg: #1c2126;
    --pass: #3fb950; --fail: #ff7b72; --skip: #d29922;
  }
}
:root[data-theme="dark"] {
  --bg: #14171a; --fg: #e6edf3; --muted: #9aa7b3; --border: #333a42;
  --accent: #7aa7ff; --code-bg: #1c2126;
  --pass: #3fb950; --fail: #ff7b72; --skip: #d29922;
}
:root[data-theme="light"] {
  --bg: #ffffff; --fg: #1b1f24; --muted: #5b6673; --border: #d9dee3;
  --accent: #2454ad; --code-bg: #f2f4f7;
  --pass: #1a7f37; --fail: #cf222e; --skip: #9a6700;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.5;
}
header {
  border-bottom: 1px solid var(--border); padding: 0.9rem 1.5rem;
  display: flex; align-items: baseline; gap: 1.25rem;
}
header a { color: var(--fg); text-decoration: none; font-weight: 600; }
header nav a { color: var(--accent); font-weight: 500; margin-right: 1rem; }
main { max-width: 960px; margin: 0 auto; padding: 1.5rem; }
h1 { font-size: 1.4rem; }
h2 {
  font-size: 1.1rem; margin-top: 2rem;
  border-bottom: 1px solid var(--border); padding-bottom: 0.3rem;
}
table { border-collapse: collapse; width: 100%; margin: 0.75rem 0 1.5rem; }
th, td {
  text-align: left; padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--border); vertical-align: top;
}
th { color: var(--muted); font-weight: 600; font-size: 0.85rem; text-transform: uppercase; }
code, pre, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre {
  background: var(--code-bg); border: 1px solid var(--border); border-radius: 6px;
  padding: 0.75rem 1rem; overflow-x: auto; font-size: 0.85rem;
}
.muted { color: var(--muted); }
.badge {
  display: inline-block; padding: 0.1rem 0.55rem; border-radius: 999px;
  font-size: 0.78rem; font-weight: 600; border: 1px solid var(--border);
}
.badge.pass { color: var(--pass); border-color: var(--pass); }
.badge.fail { color: var(--fail); border-color: var(--fail); }
.badge.skipped { color: var(--skip); border-color: var(--skip); }
.banner {
  border: 2px solid var(--skip); color: var(--skip); border-radius: 8px;
  padding: 0.75rem 1rem; margin: 1rem 0; font-weight: 700;
}
.banner.fail { border-color: var(--fail); color: var(--fail); }
.frag {
  border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem;
  background: var(--code-bg); overflow-x: auto;
}
.frag svg { max-width: 100%; height: auto; display: block; margin: 0 auto; }
a { color: var(--accent); }
ul.plain { list-style: none; padding: 0; margin: 0; }
ul.plain li { padding: 0.25rem 0; }
"""


def _e(s: Any) -> str:
    return html.escape(str(s), quote=True)


def page(title: str, body: str, active: str = "") -> str:
    def nav_link(href: str, label: str, key: str) -> str:
        cur = ' style="text-decoration: underline;"' if key == active else ""
        return f'<a href="{href}"{cur}>{label}</a>'

    return f"""<title>{_e(title)}</title>
<style>{_CSS}</style>
<header>
  <a href="/">InferSynth sidecar</a>
  <nav>
    {nav_link("/", "Catalog", "catalog")}
    {nav_link("/gates", "Gates", "gates")}
  </nav>
</header>
<main>
{body}
</main>
"""


def catalog_list(cells: list[dict[str, Any]], load_errors: list[str]) -> str:
    rows = []
    for c in cells:
        rows.append(
            "<tr>"
            f'<td><a href="/cell/{_e(c["name"])}/{_e(c["version"])}">{_e(c["name"])}</a></td>'
            f'<td class="mono">{_e(c["version"])}</td>'
            f"<td>{_e(c['description'])}</td>"
            f'<td class="mono">{_e(c["depth"])}</td>'
            f"<td>{', '.join(_e(k) for k in c['keywords'])}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Cell</th><th>Version</th><th>Description</th>"
        "<th>Depth</th><th>Keywords</th></tr></thead><tbody>"
        + ("".join(rows) if rows else '<tr><td colspan="5" class="muted">no cells loaded</td></tr>')
        + "</tbody></table>"
    )
    errors_html = ""
    if load_errors:
        items = "".join(f"<li>{_e(e)}</li>" for e in load_errors)
        errors_html = (
            '<div class="banner fail">Catalog load errors:'
            f'<ul class="plain">{items}</ul></div>'
        )
    body = f"<h1>Catalog</h1>{errors_html}{table}"
    return page("InferSynth — catalog", body, active="catalog")


def _kv_table(d: dict[str, Any]) -> str:
    if not d:
        return '<p class="muted">(empty)</p>'
    rows = "".join(
        f"<tr><th>{_e(k)}</th><td class='mono'>{_e(v)}</td></tr>" for k, v in sorted(d.items())
    )
    return f"<table>{rows}</table>"


def cell_page(cell_info: dict[str, Any], svg_url: str) -> str:
    name, version = cell_info["name"], cell_info["version"]
    ports_rows = "".join(
        f"<tr><td class='mono'>{_e(p)}</td><td>{_e(spec['direction'])}</td>"
        f"<td>{_e(spec['kind'])}</td></tr>"
        for p, spec in sorted(cell_info["ports"].items())
    )
    bindings_rows = "".join(
        f"<tr><td class='mono'>{_e(ref)}</td><td class='mono'>{_e(expr)}</td></tr>"
        for ref, expr in sorted(cell_info["bindings"].items())
    )
    idiom_params_rows = "".join(
        f"<tr><td class='mono'>{_e(p)}</td><td class='mono'>{_e(schema)}</td></tr>"
        for p, schema in sorted(cell_info["idiom_params"].items())
    )
    keywords = "".join(f"<li>{_e(k)}</li>" for k in cell_info["keywords"])
    none_row = '<tr><td colspan="{}" class="muted">none declared</td></tr>'
    disambiguation = cell_info.get("disambiguation")
    disambiguation_html = (
        f"<p><strong>Disambiguation:</strong> {_e(disambiguation)}</p>" if disambiguation else ""
    )

    body = f"""
<p><a href="/">&larr; catalog</a></p>
<h1>{_e(name)} <span class="muted">@ {_e(version)}</span></h1>
<p>{_e(cell_info['description'])}</p>

<h2>Fragment</h2>
<div class="frag"><img src="{_e(svg_url)}" alt="fragment schematic for {_e(name)}"></div>

<h2>Manifest</h2>
{_kv_table(cell_info['manifest'])}

<h2>Ports</h2>
<table><thead><tr><th>Port</th><th>Direction</th><th>Kind</th></tr></thead>
<tbody>{ports_rows or none_row.format(3)}</tbody></table>

<h2>Idioms</h2>
<p><strong>Keywords:</strong></p><ul class="plain">{keywords}</ul>
<p><strong>Params:</strong></p>
<table><thead><tr><th>Param</th><th>Schema</th></tr></thead>
<tbody>{idiom_params_rows or none_row.format(2)}</tbody></table>
{disambiguation_html}

<h2>Bindings</h2>
<table><thead><tr><th>Ref</th><th>Expression</th></tr></thead>
<tbody>{bindings_rows or none_row.format(2)}</tbody></table>

<h2>Selection</h2>
{_kv_table(cell_info['selection'])}

<h2>Depth</h2>
{_kv_table(cell_info['depth'])}
"""
    return page(f"InferSynth — {name}@{version}", body, active="catalog")


def gates_index(report_files: list[str]) -> str:
    if report_files:
        items = "".join(
            f'<li><a href="/gates?report={_e(f)}" class="mono">{_e(f)}</a></li>'
            for f in report_files
        )
        listing = f'<ul class="plain">{items}</ul>'
    else:
        listing = (
            '<p class="muted">No reports directory configured (--reports), '
            "and no ?report= given.</p>"
        )
    body = f"""
<h1>Gate reports</h1>
<p class="muted">Pass a specific report with <code>?report=&lt;path-to-json&gt;</code>,
or pick one below.</p>
{listing}
"""
    return page("InferSynth — gate reports", body, active="gates")


def gates_report(report_path: str, data: dict[str, Any]) -> str:
    results = data["results"]
    skipped = [r for r in results if r["status"] == "skipped"]
    failed = [r for r in results if r["status"] == "fail"]
    ok = not failed

    none_diag = '<span class="muted">(none)</span>'
    rows = []
    for r in results:
        diags = "".join(f"<div>{_e(d)}</div>" for d in r["diagnostics"]) or none_diag
        status = _e(r["status"])
        status_label = _e(r["status"].upper())
        rows.append(
            "<tr>"
            f"<td class='mono'>{_e(r['gate'])}</td>"
            f'<td><span class="badge {status}">{status_label}</span></td>'
            f"<td class='mono'>{diags}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Gate</th><th>Status</th><th>Diagnostics</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )

    banner = ""
    if skipped:
        names = ", ".join(r["gate"] for r in skipped)
        banner += (
            '<div class="banner">!! GATE SKIPPED !! '
            f"{len(skipped)} gate(s) SKIPPED ({_e(names)}) &mdash; "
            "the design is NOT fully verified !!</div>"
        )
    result_word = "OK" if ok else "FAILED"
    banner += f'<div class="banner{"" if ok else " fail"}">RESULT: {result_word}</div>'

    body = f"""
<p><a href="/gates">&larr; all reports</a></p>
<h1>Gate report</h1>
<p class="muted mono">{_e(report_path)}</p>
{banner}
{table}
"""
    return page("InferSynth — gate report", body, active="gates")


def error_page(title: str, message: str, status_hint: str = "") -> str:
    body = f"<h1>{_e(title)}</h1><div class='banner fail'>{_e(message)}</div>{status_hint}"
    return page(title, body)
