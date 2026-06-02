"""End-to-end tests for the v0.6 acceptance bar.

These exercises run the four-step proof from
``docs/09-self-bootstrap.md`` §7.10 + the three question paths from
``docs/02-bet-and-principles.md`` §3 criterion 6 + the independence
test from criterion 5. They are deliberately deterministic — no
LLM, no Databricks workspace — so the nightly tier (per §7.10
"two-tier CI") can run them without network.
"""
