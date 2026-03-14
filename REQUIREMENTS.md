# Firefly III Importer Requirements

## Goal

Build a minimal-dependency importer for Firefly III that can ingest messy bank exports, let the operator verify every transaction before submission, and learn from repeated corrections so the amount of manual work decreases over time.

## Product Shape

- Keep the project rooted in the current workspace.
- Keep runtime-generated data under a visible `runtime_data/` directory.
- Keep private example statements out of version control.
- Prefer minimal Python dependencies. Current core dependencies are `textual` and `openpyxl`.

## Supported Inputs

- Accept CSV and XLSX inputs.
- Handle exports with leading metadata blocks, blank rows, and non-transaction summary rows.
- Support interactive parsing setup so delimiter, header row, and first data row can be corrected.
- Support profile auto-detection from the input file when no profile argument is provided.

## Interactive Review Requirements

- The first stage is a local review TUI.
- Show raw file context around the selected row with the current row highlighted.
- Let the operator approve, skip, revisit, and jump between rows.
- Keep each transaction editable before submission.
- Use numbered editable fields so targeted edits are fast.
- Support direct field editing with prefill behavior for quick correction.
- Provide field-specific hints while the operator is typing.
- Make transaction type editing explain the available Firefly III transaction types used by the importer.

## Upload and Pairing Requirements

- Keep upload and pairing inside the TUI instead of dropping back to a separate CLI-only step.
- After local review is complete, transition into an upload/pairing stage.
- Show normalized transactions rather than raw rows.
- Show source account, destination account, description, date, amount, and row state.
- Highlight Firefly-matched accounts in green.
- Show row states such as:
  - `READY`
  - `NEW EXT`
  - `DUPLICATE`
  - `MISSING REF`
  - `UPLOAD FAIL`
  - `UPLOADED`
- Show a compact selected-row detail panel explaining the row state and next action.
- Keep the upload-complete view read-only.

## Firefly III Integration Requirements

- Support credentials via environment variables or project-root `FIREFLY.yaml`.
- Use live Firefly data when available for:
  - account lookup
  - category lookup
  - budget lookup
  - duplicate detection
  - transaction-type inference from matched account types
- Match accounts using:
  - saved aliases
  - account names
  - Firefly server-side identifiers such as `account_number`, `iban`, and `bic`
  - digit fallback from names only as a last resort

## Account Matching and Counterparty Rules

- Treat `source_account` for the statement file as the account represented by the file itself.
- Infer withdrawal vs deposit from amount sign when no better information exists.
- When live Firefly account types are available, re-infer transaction type based on the matched account types.
- Require both sides to resolve for transfers.
- For withdrawals, allow raw external destination counterparties.
- For deposits, allow raw external source counterparties.
- If a raw external counterparty has not been seen before, require per-row confirmation before upload.
- Keep that confirmation per entry, not document-wide.

## Learning and Memory Requirements

- Remember parse hints and field mappings by profile.
- Support saved account aliases.
- Allow alias memory scope to be either:
  - per-profile
  - global
- Save session state so work can be resumed after interruption.

## Submission and Safety Requirements

- Do not submit anything before review is complete.
- Block upload on unresolved internal references, unconfirmed new external counterparties, and unresolved duplicate warnings.
- Write run reports for submit and rollback operations.
- Support rollback by deleting transactions created in a recorded run.

## UX Requirements Added During Development

- Use `runtime_data/` as the project runtime root.
- Make upload-mode editing explicit with an `edit` prefix.
- Show upload-mode actions as bracketed commands.
- Keep the completed-upload screen read-only and explicit about rollback/quit behavior.
- Prefer actionable, row-specific hints over generic instructions.
- Keep the UI adaptive to terminal width and height changes.
- Keep the current row visually obvious without overusing background colors.
