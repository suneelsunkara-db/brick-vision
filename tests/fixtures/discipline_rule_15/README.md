# `gold_no_mock_v1` — discipline-rule-15 gold set

Per [`docs/17-eval-framework.md`](../../../docs/17-eval-framework.md)
§13.3 (production-package discipline) + [`docs/01-overview.md`](../../../docs/01-overview.md)
§0 rule 15.

This is a **synthetic** Python corpus consumed only by the
`NoMockOrFakeImplementations()` scorer's contract test
(`tests/unit/test_no_mock_or_fake_implementations.py`). None of these
files are imported by the runtime.

## Layout

- `violation/` — 6 fixtures, each demonstrating one of the 6 forbidden
  patterns. Every file MUST trigger ≥1 violation.
- `clean/` — 6 fixtures, each demonstrating one legitimate
  alternative (env-gate, real wrapper, private structural typing,
  etc.). Every file MUST trigger 0 violations.

## Acceptance

`tests/unit/test_no_mock_or_fake_implementations.py` asserts:

1. Running the scorer with `roots=[violation/]` returns `score == 0.0`.
   The 6 violation files together produce exactly the union of
   `MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE` +
   `PROTOCOL_HAS_ONLY_MOCK_SUBCLASSES` reason codes; total violation
   count ≥ 6.
2. Running the scorer with `roots=[clean/]` returns `score == 1.0`,
   `reason_codes == ()`, and zero hard violations.
3. The `soft_warn_files` argument moves matched-file violations from
   `details["violations"]` into `details["soft_warnings"]` without
   changing the score for clean files.

## How to add a new fixture

The matrix in §13.3 enumerates the 6 violation kinds. If a new
forbidden pattern is added (e.g. a `Spy*` prefix), append a 7th row
to the matrix in `docs/17-eval-framework.md`, add the corresponding
`violation/` and `clean/` files here, and bump the gold set version
to `gold_no_mock_v2`.
