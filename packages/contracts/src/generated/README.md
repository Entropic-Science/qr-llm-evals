# Generated — do not edit

Every file in this directory is written by `python scripts/export_schemas.py`
from the pydantic models under `backend/src/qr_llm_evals/**/schema.py`
(JSON Schema → TypeScript types + zod validators). CI runs
`scripts/export_schemas.py --check` and fails on drift.

To change a contract, change the pydantic model and regenerate.
