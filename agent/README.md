# agent/

The self-contained Argus agent: minimal runtime, idempotent step engine (check -> apply -> record,
SQLite ledger), gojira-reader, and renderers. Built to register into cabal and adopt techne later.
Its competence is packaged as Agent Skills under `skills/`.
