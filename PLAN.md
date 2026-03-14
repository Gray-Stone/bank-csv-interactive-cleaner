# Firefly III Interactive Importer (Python + Textual, Minimal Dependencies)

## Summary
Build a custom, local-first importer that accepts messy `CSV`/`XLSX`, interactively normalizes and maps data, requires manual verification of every transaction, then commits in an end-of-session batch to Firefly III.
The tool will persist session state and learned rules in JSON so progress can pause/resume and correction history improves future suggestions without auto-approving.

## Scope
- In scope: `CSV` + `XLSX`, interactive parse setup (metadata lines/header detection), per-row review/edit, backtracking, pause/resume, dry-run output, batch submit, duplicate warnings, targeted retry, rollback command, rule learning (exact + regex).
- Out of scope for v1: legacy `.xls`, automatic approval, multi-user sync, remote DB, external preprocessors (VisiData/OpenRefine) in the core path.

## Public Interfaces

### 1. CLI Commands
- `python -m ff3_importer import <input_file> [--profile <profile_name>] [--sheet <sheet>] [--dry-run]`
- `python -m ff3_importer resume <run_id>`
- `python -m ff3_importer rollback <run_id>`
- `python -m ff3_importer profiles list`
- `python -m ff3_importer profiles show <profile_name>`
- If `--profile` is omitted for a CSV import, auto-detect the best profile match from existing profiles before entering mapping/review.

### 2. Environment and Config
- Required env vars: `FIREFLY_URL`, `FIREFLY_TOKEN`.
- Project root is the current workspace folder that contains this plan file: `<project_root>`.
- Optional config file: `<project_root>/runtime_data/config.json` for defaults; env vars override config.
- Runtime root dir: `<project_root>/runtime_data/`.
- `runtime_data/` must be excluded from version control (add `runtime_data/` to `.gitignore`).

### 3. On-Disk JSON Contracts
- `runtime_data/profiles/<name>.json`: parse hints, column map, default account/currency settings, learned rules.
- `runtime_data/sessions/<run_id>.json`: autosaved review state after every decision/edit.
- `runtime_data/runs/<run_id>.json`: dry-run or submit report, including created Firefly transaction IDs for rollback.
- `runtime_data/history/fingerprints.json`: local duplicate history indexed by profile.

## Important API/Type Additions (Internal)

### Normalized transaction model
- Fields: `row_id`, `source_row_index`, `type`, `date`, `amount`, `description`, `source_account`, `destination_account`, `payee`, `currency`, `category`, `budget`, `tags`, `notes`, `external_id`, `internal_reference`, `status`, `warnings`.
- Status enum: `pending`, `approved`, `skipped`, `blocked_missing_ref`, `submitted`, `submit_failed`.

### Learned rule model
- Fields: `rule_id`, `enabled`, `priority`, `match_exact`, `match_regex`, `set_fields`, `confidence`, `uses`, `accepted_count`, `overridden_count`, `created_at`, `updated_at`.
- Rule application: ranked suggestions only; never auto-approve.

### Firefly adapter interface
- `list_accounts(type_filter)`
- `list_categories()`
- `list_budgets()`
- `list_tags()`
- `list_transactions(start, end, page)`
- `create_transaction(payload)`
- `delete_transaction(transaction_id)`

## Architecture and Implementation Plan

### 1. Minimal project structure
- `ff3_importer/__main__.py`: argparse entrypoint.
- `ff3_importer/cli.py`: command routing.
- `ff3_importer/models.py`: dataclasses/types.
- `ff3_importer/io_loader.py`: CSV/XLSX ingestion.
- `ff3_importer/parse_wizard.py`: delimiter/encoding/header/skip detection and confirmation.
- `ff3_importer/rules.py`: rule scoring, application, persistence.
- `ff3_importer/session_store.py`: autosave/resume JSON handling.
- `ff3_importer/firefly_client.py`: HTTP calls to Firefly API.
- `ff3_importer/dedup.py`: fingerprint generation and comparisons.
- `ff3_importer/tui_app.py`: Textual app and screens.
- `ff3_importer/submit.py`: batch commit, retry, rollback tracking.

### 2. Dependency policy
- External deps only: `textual`, `openpyxl`.
- Standard library for everything else (`argparse`, `csv`, `json`, `re`, `datetime`, `urllib.request`, `hashlib`, `uuid`, `pathlib`).

### 3. Interactive parse stage
- Read first sample window from file and propose: encoding, delimiter, metadata skip count, header row, first data row.
- Show preview and require explicit confirmation/edit before moving forward.
- For XLSX, select sheet via `--sheet` or interactive picker.
- If CSV is opened without `--profile`, compute profile match scores using persisted parse hints and header signatures (delimiter, normalized header names, column count, and sampled value patterns).
- If the best CSV profile score is below threshold or tied, prompt to pick a profile or create a new one.
- Persist chosen parse hints in profile.

### 4. Column mapping stage
- Map source columns to normalized fields with suggestions from profile/rules.
- Require minimum mapped set: `date`, `amount`, `description`.
- Support amount modes: single signed amount or debit/credit pair.
- If no CSV profile was confidently matched, start from empty mappings and offer saving as a new profile once confirmed.
- Save mapping to profile when user confirms.

### 5. Review stage (Textual)
- Main screen layout: row list + detail editor + warnings panel.
- Row actions: approve, edit, skip, back, jump-to-row, save-rule, pause/quit.
- Every row must be explicitly approved or skipped before commit.
- Autosave session on every action.
- Full metadata editable per row: type/date/amount/description/accounts/payee/currency/category/budget/tags/notes/external/internal refs.

### 6. Account/currency defaults and overrides
- Session starts with default account mapping and default currency from profile or prompt.
- Per-row override always available.
- Missing referenced account/payee/category/budget triggers `blocked_missing_ref`; user must resolve or skip row.
- No silent auto-creation of missing references.

### 7. Learning behavior
- Apply learned rules as ranked suggestions with confidence.
- Learning triggers only via explicit `save as rule`.
- Rule matching strategy: exact keys first, then regex templates.
- Conflict resolution: show top-ranked suggestions; user-selected choice can update rule stats.

### 8. Duplicate detection
- Local fingerprint: `date + normalized_amount + normalized_description + counterparty`.
- Build remote fingerprint index by querying Firefly transactions for statement date range.
- On potential duplicate: show warning and require explicit override choice.
- Override flag stored in session for audit trail.

### 9. Submit flow
- Commit available only when all rows are `approved` or `skipped`.
- `--dry-run` required path supported: emit exact Firefly payload report, no API calls.
- Live submit: sequential POST per approved row at end of session.
- Partial failure policy: stop on first failure batch segment, show failed rows, allow targeted retry only for failed/pending.
- Store created transaction IDs and row mapping in `runtime_data/runs/<run_id>.json`.

### 10. Rollback flow
- `rollback <run_id>` reads stored created IDs.
- Deletes in reverse creation order via Firefly delete endpoint.
- Produces rollback report with success/failure per ID.

## Test Cases and Scenarios
- Parse messy CSV with metadata preface lines and custom delimiter.
- Parse CSV without `--profile` and auto-detect correct existing profile.
- Parse CSV without `--profile` with ambiguous scores and verify prompt to pick/create profile.
- Parse XLSX with non-first sheet and header offset.
- Map debit/credit columns into signed amount correctly.
- Resume interrupted session and continue at exact cursor with prior edits intact.
- Back/edit previous transaction and ensure autosave consistency.
- Rule suggestion ranking works and explicit save-rule persists.
- Duplicate warnings from local history and Firefly lookup both trigger correctly.
- Dry-run produces deterministic payload and no network calls.
- Batch submit handles mixed success; targeted retry submits only failed rows.
- Rollback deletes only IDs created in run report.
- Validation blocks unresolved account/payee/category/budget references.

## Acceptance Criteria
- User can import a messy statement file and manually verify every transaction before any commit.
- User can import CSV without specifying `--profile`, and the tool auto-detects a profile or prompts when ambiguous.
- User can pause at any point and resume without losing decisions or edits.
- User can go back and change earlier reviewed transactions.
- User sees learned suggestions in later imports from same bank profile.
- Tool runs with only `textual` and `openpyxl` as external dependencies.
- Dry-run and rollback both function with auditable run artifacts.

## Assumptions and Defaults Chosen
- Python 3.11+ runtime.
- Target Firefly API under `${FIREFLY_URL}/api` with bearer token auth.
- Transaction types exposed in UI: `withdrawal`, `deposit`, `transfer`.
- Single-split transaction creation in v1 (no multi-split edit UI).
- Per-bank profile model is authoritative; no global cross-bank rule sharing.
- JSON persistence is intentionally v1; migration hook left for future SQLite upgrade.

## Sources
- Firefly III API docs repository: https://github.com/firefly-iii/api-docs
- OpenAPI spec used for endpoint/field verification (v6.4.17, generated 2026-02-07): https://raw.githubusercontent.com/firefly-iii/api-docs/main/dist/firefly-iii-6.4.17-v1.yaml
