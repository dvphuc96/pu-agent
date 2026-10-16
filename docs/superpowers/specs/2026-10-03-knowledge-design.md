# Knowledge (Memory) — design

Date: 2026-10-03
Status: approved (chat design with Sean)

## Goal

A **Knowledge** sub-tab in the dashboard's Memory view: user-added knowledge
sources (pasted text, uploaded files, a watched folder, Google Drive import),
grouped and individually disableable, that PU uses automatically when
answering (via the retrieval gate) and on demand (a `search_knowledge` tool).

## Decisions (from the design conversation)

- **Retrieval: both paths.** The retrieval gate searches knowledge alongside
  facts/episodes; a `search_knowledge` tool lets the agent dig on demand
  (always registered, like `search_web` — knowledge is core memory, no flag).
- **Sources:** paste text + upload file (`.md`/`.txt`) in the UI, plus a
  watched folder `.pu/knowledge/` re-indexed on demand. Sources are
  snapshots except folder origin, which re-syncs from the file.
- **Google Drive import ships in this batch**: Desktop OAuth with
  `drive.readonly` (same pattern as Gmail), own token
  `.pu/drive-token.json`, card in "Memory & Storage". Supported: Google
  Docs (exported to text), `.md`, `.txt`. Anything else reports "not
  supported" honestly.
- **No Map view** (deferred); **no PDF** (would need a new dependency —
  declined for now per the house rule).
- Storage: plain SQLite + FTS5, chunked by paragraph to ~800 chars.

## Components

1. `pu/db.py` — `knowledge_sources` (id, title, group, origin, path,
   fingerprint, enabled, chars, added_at) and `knowledge_chunks` (id,
   source_id, content) with a `knowledge_fts` FTS5 index + triggers.
2. `pu/memory/knowledge.py` — pure store: `add_source`, `search` (FTS,
   enabled-sources only), `set_enabled`, `delete`, `set_group`, `reindex_folder`,
   `chunk_text`. Returns sentences, never raises through the loop.
3. `pu/memory/__init__.py` — `gated_retrieve` appends knowledge results.
4. `pu/tools/__init__.py` — registers `search_knowledge` (from
   `pu/tools/knowledge_search.py`) unconditionally.
5. `pu/tools/google_drive.py` — OAuth connect/probe, `list_files`
   (supported types only), `import_file` (download → knowledge source).
6. `pu/integrations.py` — "Google Drive" card (Memory & Storage), fields
   `PU_DRIVE`, probe without browser. `[drive]` extra (same Google libs).
7. Dashboard — Memory sub-tab `knowledge` (+ pillar card on Memory overview):
   search box (FTS), sources table, Add source dialog (paste/upload via JS
   FileReader → JSON), Drive dialog (connect → pick → import), Sync folder
   button; routes `/api/knowledge/*` and `/api/drive/*`.
8. `pu/__main__.py` — `pu connect drive`.
9. `.env.example` regenerated.

## Error handling

Every path returns a sentence: Drive not connected → setup hint; unsupported
file type → named as such; folder missing → treated as empty. UI dialogs show
the server's message verbatim.

## Testing

Deterministic only (`evals/deterministic/test_knowledge.py`): chunking,
FTS search + enabled filtering, reindex (add/update/remove), gate wiring,
tool output, Drive list/import over fake googleapiclient, registry card,
dashboard actions. No network in `make gate`.
