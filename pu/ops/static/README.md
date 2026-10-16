# Dashboard frontend

Plain static files served by `pu/ops/dashboard.py` using the stdlib HTTP server.
There is no framework, bundler or build step.

## Files

| file | responsibility |
|---|---|
| `index.html` | sidebar, central workspace, persistent chat dock |
| `style.css` | shared design tokens, light/dark styling |
| `notebook.css` | customer notebook layout, responsive source/report/calendar panes |
| `js/notebook.js` | customer workspaces, source selection/viewer, cited Q&A and report approval actions |
| `js/util.js` | globals, escaping, markdown, HTTP helpers |
| `js/memory.js` | memory/SOUL/skill editing actions |
| `js/models.js` | provider model cards, catalog and pins |
| `js/render.js` | chat rendering and streaming |
| `js/diagram.js` | architecture visualization |
| `js/graph.js` | triage graph visualization |
| `js/views.js` | workspace, agents, research, reports, approvals and Connections views |
| `js/dock.js` | chat sessions/history/model chip |
| `js/main.js` | refresh/render loop, resizers and bootstrap |

The visual layout follows the Archive UI reference while the data and actions are
served entirely by PU Agent backend boundaries.

## Validation

Run `node --check` over `js/*.js` and `pytest evals/deterministic/test_static_assets.py`.

## Customer research notebooks

Open **Khách hàng · Research** (`#research`) to create a customer workspace.
Existing report JSON artifacts are imported into workspaces without rerunning a model.
Each notebook persists selected sources, immutable report versions and Q&A history in SQLite.
General dashboard polling does not replace notebook inputs or dialogs.

Sources can be pasted, read from public websites, imported from the synced inbox or knowledge,
or uploaded as TXT/Markdown/DOCX/PDF (PDF extraction needs `pu-agent[sources]`).
Source documents are untrusted evidence. Q&A uses only selected sources, has no write tools,
and rejects citations outside that selection. Scanned PDFs require OCR before import.

Calendar availability is computed from structured start/end intervals (09:00–18:00 weekdays),
with configurable date range, timezone and meeting duration. A failed connected Google calendar
returns unknown availability. A local-only check is explicitly labeled local-only.

Report actions create proposals for the existing Human Approval queue. Knowledge saves and
Gmail sends bind to the stored report hash, require a passing quality gate, and execute only
after approval. Sending Gmail requires a separate explicit OAuth connection with `gmail.send`;
the read-only inbox token is not reused. Ambiguous sends are recorded and never automatically retried.

The report editor creates an immutable new version, reevaluates edited factual text/citations,
and cancels unexecuted proposals for the previous version. Markdown, JSON and DOCX exports
come directly from the stored snapshot; HTML/PDF are offered when artifacts are available.

The dashboard retains its single-user, loopback deployment model. Mutation requests reject
cross-origin browser calls; this does not introduce a multi-user login system.

Tests: `pytest evals/deterministic/test_research_workspace.py` and
`node --test evals/deterministic/test_notebook_runtime.cjs`.

`js/inbox-chat.js` is the primary chat screen and report approval preview. It uses the existing SSE chat API; `render.js` preserves report references and displays inbox/research progress. Inbox report generation is deterministic application routing in `pu/orchestration/inbox_chat.py`, with model-based business classification and grounded report generation.
