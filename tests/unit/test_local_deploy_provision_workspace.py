"""Unit tests for the ``scripts/local_deploy/`` workspace bootstrapper.

These tests run **without a Databricks workspace** by exercising the
shape contract of the helpers — config loading from the env, the
``LocalDeployConfig`` dataclass, the ``--doctor`` argument plumbing,
the Jobs API payload builder in ``deploy_indexer_job.py``, and the
``log()`` side-effects on the configured log file.

Discipline rule 15 compliant: no Protocol seams, no mock classes. The
provisioner phase functions are not exercised here (they require a
real ``WorkspaceClient``); they are covered by the live-run integration
documented in ``scripts/local_deploy/README.md``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Make `scripts.*` importable without polluting tests/conftest.py.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from scripts.local_deploy import _lib, deploy_indexer_job  # noqa: E402


_REQUIRED_ENV = {
    "DATABRICKS_HOST": "https://example.cloud.databricks.com",
    "DATABRICKS_TOKEN": "dapi-test-token",
}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every env var the provisioner reads so individual tests
    can re-set only what they need; otherwise the dev's shell-level
    BV_* exports would leak in."""

    for key in [
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_WAREHOUSE_ID",
        "BV_CATALOG",
        "BV_SCHEMA",
        "BV_VS_ENDPOINT",
        "BV_INDEXER_VS_INDEX_NAME",
        "BV_INDEXER_WAREHOUSE_ID",
        "BV_INDEXER_STATE_VOLUME",
        "BV_INDEXER_EMBEDDING_ENDPOINT",
        "BV_INDEXER_DAILY_TOKEN_CAP",
        "BV_INDEXER_FRESHNESS_TOLERANCE_DAYS",
        "BV_WAREHOUSE_ID",
        "BV_DRY_RUN",
        "BV_FAKE_LLM",
        "BV_LAKEBASE_PROJECT_ID",
        "BV_LAKEBASE_BRANCH",
        "BV_LAKEBASE_DATABASE",
        "BV_LAKEBASE_SYNC_MODE",
        "BV_LOCAL_DEPLOY_INDEXER_SP_NAME",
        "BV_LOCAL_DEPLOY_APP_SP_NAME",
        "BV_LOCAL_DEPLOY_OPS_EMAIL",
        "BV_LOCAL_DEPLOY_AUTO_PROVISION_SPS",
        "BV_LOCAL_DEPLOY_AUTO_PROVISION_CATALOG",
        "BV_LOCAL_DEPLOY_AUTO_PROVISION_VS",
        "BV_LOCAL_DEPLOY_AUTO_PROVISION_WAREHOUSE",
        "BV_LOCAL_DEPLOY_DEPLOY_INDEXER_JOB",
        "BV_LOCAL_DEPLOY_TRIGGER_FIRST_REFRESH",
        "BV_LOCAL_DEPLOY_VS_ENDPOINT_TIMEOUT_SEC",
        "BV_LOCAL_DEPLOY_INDEXER_TIMEOUT_SEC",
    ]:
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# 1. env_required + env_bool
# ---------------------------------------------------------------------------


def test_env_required_raises_clear_error_on_missing() -> None:
    with pytest.raises(SystemExit) as exc:
        _lib.env_required("DATABRICKS_HOST")
    message = str(exc.value)
    assert "DATABRICKS_HOST" in message
    assert ".env" in message


def test_env_required_returns_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://x.cloud.databricks.com")
    assert _lib.env_required("DATABRICKS_HOST") == "https://x.cloud.databricks.com"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", False),
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
    ],
)
def test_env_bool_matches_documented_truthiness(
    raw: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BV_DRY_RUN", raw)
    assert _lib.env_bool("BV_DRY_RUN", False) is expected


def test_env_bool_default_returned_when_unset() -> None:
    assert _lib.env_bool("BV_DRY_RUN", True) is True
    assert _lib.env_bool("BV_DRY_RUN", False) is False


# ---------------------------------------------------------------------------
# 2. load_dotenv (no python-dotenv dependency)
# ---------------------------------------------------------------------------


def test_load_dotenv_parses_keys_and_strips_inline_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# top-level comment",
                "DATABRICKS_HOST=https://example.cloud.databricks.com",
                "DATABRICKS_TOKEN=<example-token>  # inline comment",
                'BV_CATALOG="brickvision_dev"',
                "EMPTY=",
                "",
            ]
        ),
        encoding="utf-8",
    )

    parsed = _lib.load_dotenv(env_file)

    assert parsed["DATABRICKS_HOST"] == "https://example.cloud.databricks.com"
    assert parsed["DATABRICKS_TOKEN"] == "<example-token>"
    assert parsed["BV_CATALOG"] == "brickvision_dev"
    assert parsed["EMPTY"] == ""
    # And the env was hydrated (override=False semantics — we cleared
    # the keys in the autouse fixture).
    assert os.environ["BV_CATALOG"] == "brickvision_dev"


def test_load_dotenv_does_not_override_existing_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://shell-export.cloud.databricks.com")
    env_file = tmp_path / ".env"
    env_file.write_text("DATABRICKS_HOST=https://file-value.cloud.databricks.com\n")
    _lib.load_dotenv(env_file)
    assert os.environ["DATABRICKS_HOST"] == "https://shell-export.cloud.databricks.com"


def test_load_dotenv_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    assert _lib.load_dotenv(tmp_path / ".env") == {}


# ---------------------------------------------------------------------------
# 3. LocalDeployConfig.from_env — full + defaults
# ---------------------------------------------------------------------------


def test_local_deploy_config_from_env_uses_documented_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)

    cfg = _lib.LocalDeployConfig.from_env()

    assert cfg.databricks_host == _REQUIRED_ENV["DATABRICKS_HOST"]
    assert cfg.databricks_token == _REQUIRED_ENV["DATABRICKS_TOKEN"]
    assert cfg.catalog == "brickvision"
    assert cfg.schema == "brickvision"
    assert cfg.vs_endpoint == "brickvision-dev"
    assert cfg.indexer_sp_name == "bv_indexer_sp"
    assert cfg.app_sp_name == "bv_app_sp"
    assert cfg.auto_provision_sps is True
    assert cfg.auto_provision_catalog is True
    assert cfg.auto_provision_vs is True
    assert cfg.auto_provision_warehouse is True
    assert cfg.deploy_indexer_job is True
    assert cfg.trigger_first_refresh is True
    # Documented in .env.example: 900 s VS timeout, 2400 s indexer timeout
    assert cfg.vs_endpoint_timeout_sec == 900
    assert cfg.indexer_timeout_sec == 2400


def test_local_deploy_config_overrides_via_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BV_CATALOG", "my_catalog")
    monkeypatch.setenv("BV_VS_ENDPOINT", "my-endpoint")
    monkeypatch.setenv("BV_LOCAL_DEPLOY_AUTO_PROVISION_VS", "false")
    monkeypatch.setenv("BV_LOCAL_DEPLOY_DEPLOY_INDEXER_JOB", "false")
    monkeypatch.setenv("BV_LOCAL_DEPLOY_VS_ENDPOINT_TIMEOUT_SEC", "1800")

    cfg = _lib.LocalDeployConfig.from_env()
    assert cfg.catalog == "my_catalog"
    assert cfg.vs_endpoint == "my-endpoint"
    assert cfg.auto_provision_vs is False
    assert cfg.deploy_indexer_job is False
    assert cfg.vs_endpoint_timeout_sec == 1800


def test_local_deploy_config_raises_on_missing_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No credentials → typed exit (not silent default), so an
    operator running the script gets a clear error."""

    monkeypatch.setenv("DATABRICKS_HOST", "")  # explicitly empty

    with pytest.raises(SystemExit):
        _lib.LocalDeployConfig.from_env()


def test_local_deploy_config_has_no_region_field() -> None:
    """Pin: BV_REGION was speculative — never read by any code path.
    Removed in v0.7.7 cleanup. This test fails if anyone re-introduces
    a dead `region` knob without wiring a real consumer."""

    import dataclasses

    field_names = {f.name for f in dataclasses.fields(_lib.LocalDeployConfig)}
    assert "region" not in field_names


def test_local_deploy_config_has_no_databricks_cluster_id_concept() -> None:
    """Pin: discipline rule 12 — every BrickVision compute path is
    Databricks Serverless. There is no all-purpose / job-cluster
    knob anywhere in the local-deploy surface. ``DATABRICKS_CLUSTER_ID``
    was correctly removed in v0.7.6.2 (per docs/22-changelog.md) and
    must NOT come back via this script."""

    import dataclasses

    fields = dataclasses.fields(_lib.LocalDeployConfig)
    for f in fields:
        assert "cluster" not in f.name.lower(), (
            f"LocalDeployConfig field {f.name!r} mentions 'cluster' — "
            f"discipline rule 12 forbids cluster references."
        )


# ---------------------------------------------------------------------------
# 4. configure_log_file + log() — file mirroring is real
# ---------------------------------------------------------------------------


def test_log_writes_to_configured_log_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "deploy.log"
    _lib.configure_log_file(log_path)

    _lib.log("ok", "phase 1 ready", phase="sp")
    _lib.log("warn", "vs endpoint cold-creating", phase="vs")

    contents = log_path.read_text(encoding="utf-8")
    assert "phase 1 ready" in contents
    assert "vs endpoint cold-creating" in contents
    # Phase tag formatting: left-aligned width-8 inside square brackets.
    assert "[sp      ]" in contents
    assert "[vs      ]" in contents


def test_log_falls_back_to_stderr_when_file_unconfigured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _lib._LOG_FILE = None  # explicit reset
    _lib.log("info", "hello world")
    captured = capsys.readouterr()
    assert "hello world" in captured.err


# ---------------------------------------------------------------------------
# 5. chunk()
# ---------------------------------------------------------------------------


def test_chunk_partitions_correctly() -> None:
    assert _lib.chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert _lib.chunk([], 2) == []
    assert _lib.chunk([1], 5) == [[1]]


# ---------------------------------------------------------------------------
# 6. deploy_indexer_job — Jobs API payload construction
# ---------------------------------------------------------------------------


def test_job_api_payload_runs_as_current_user() -> None:
    """The deploy script sets run_as to the current user, not an SP."""

    pytest.importorskip("yaml")

    payload = deploy_indexer_job._job_api_payload(
        {
            "resources": {
                "jobs": {
                    "capability_indexer": {
                        "name": "bv_capability_indexer",
                        "run_as": {"service_principal_name": "${var.indexer_sp}"},
                        "tasks": [
                            {
                                "task_key": "plan",
                                "spark_python_task": {
                                    "python_file": "src/brickvision_runtime/databricks_jobs/run_capability_indexer.py",
                                },
                            }
                        ],
                    }
                }
            }
        },
        cfg=_lib.LocalDeployConfig.from_env(),
        workspace_root="/Workspace/Users/test@example.com/.brickvision/indexer",
        run_as_user="test@example.com",
    )
    assert payload["run_as"] == {"user_name": "test@example.com"}
    assert "service_principal_name" not in payload["run_as"]
    task = payload["tasks"][0]
    pf = task["spark_python_task"]["python_file"]
    assert pf.startswith("/Workspace/Users/test@example.com/")
    assert pf.endswith(".py")


# ---------------------------------------------------------------------------
# 7. provision_workspace argparse — phase skip flag is wired
# ---------------------------------------------------------------------------


def test_provision_workspace_argparse_accepts_skip_flag() -> None:
    """The ``--skip`` flag must accept exactly the 7 phase ids the
    operator-facing README documents — anything else is a typo at
    parse time, not a silent no-op."""

    from scripts.local_deploy import provision_workspace as pw

    parser = pw._build_parser()
    args = parser.parse_args(["--skip", "vs", "--skip", "sp"])
    assert args.skip == ["vs", "sp"]
    assert args.doctor is False

    with pytest.raises(SystemExit):
        parser.parse_args(["--skip", "made-up-phase"])


def test_provision_workspace_doctor_flag_is_distinct() -> None:
    from scripts.local_deploy import provision_workspace as pw

    args = pw._build_parser().parse_args(["--doctor"])
    assert args.doctor is True
    assert args.skip == []


# ---------------------------------------------------------------------------
# 8. Real DDL is renderable — Phase 4 won't blow up at runtime on a
#    bad placeholder substitution.
# ---------------------------------------------------------------------------


def test_capability_graph_ddl_renders_without_template_residue() -> None:
    """Pin: the real DDL must substitute both ``${BV_CATALOG}`` and
    ``${BV_SCHEMA}`` cleanly so the provisioner never issues a
    statement with a literal placeholder against the workspace.

    Per v0.7.7 schema consolidation every table lives at
    ``<BV_CATALOG>.<BV_SCHEMA>.<table>`` (single flat schema)."""

    from brickvision_runtime.capability_graph.schemas import ALL_DDL, render

    for name, raw_ddl in ALL_DDL.items():
        rendered = render(raw_ddl, "test_catalog", "test_schema")
        assert "${BV_CATALOG}" not in rendered, f"{name} still has template residue"
        assert "${BV_SCHEMA}" not in rendered, f"{name} still has template residue"
        assert "test_catalog.test_schema." in rendered, (
            f"{name} did not interpolate <catalog>.<schema>."
        )
        assert " CHECK " not in rendered.upper(), (
            f"{name} contains CHECK constraints unsupported by this UC path"
        )
        assert "CONSTRAINT" not in rendered.upper(), (
            f"{name} contains named constraints unsupported by this UC path"
        )


def test_bundle_deploy_var_args_forwards_core_dab_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v0.7.7 — closing the gap where ``databricks bundle deploy`` ran
    with no ``--var`` flags. The provisioner created the operator's
    catalog from ``BV_CATALOG`` while the deployed Job ran with the
    DAB default ``brickvision_dev`` and silently wrote to the wrong
    catalog. The helper must forward every operator-supplied DAB var
    so the deployed Job's runtime config exactly matches what was
    just provisioned."""

    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BV_CATALOG", "operator_catalog")
    monkeypatch.setenv("BV_SCHEMA", "operator_schema")
    monkeypatch.setenv("BV_VS_ENDPOINT", "operator-vs")
    monkeypatch.setenv("BV_INDEXER_VS_INDEX_NAME", "entity_index")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh_abc123")
    monkeypatch.setenv("BV_DRY_RUN", "true")

    cfg = _lib.LocalDeployConfig.from_env()
    args = _lib.bundle_deploy_var_args(cfg)
    assert "--var=catalog=operator_catalog" in args
    assert "--var=schema=operator_schema" in args
    assert "--var=vs_endpoint=operator-vs" in args
    assert "--var=indexer_vs_index_name=entity_index" in args
    assert "--var=indexer_warehouse_id=wh_abc123" in args
    assert "--var=bv_dry_run=true" in args


def test_bundle_deploy_var_args_skips_empty_warehouse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset ``DATABRICKS_WAREHOUSE_ID`` (operator wants the
    deploy script to auto-provision one) must NOT be forwarded as
    ``--var=indexer_warehouse_id=`` — the empty override would
    short-circuit the DAB default + leave the Job pointed at no
    warehouse."""

    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)

    cfg = _lib.LocalDeployConfig.from_env()
    assert cfg.warehouse_id == ""
    args = _lib.bundle_deploy_var_args(cfg)
    assert not any(a.startswith("--var=indexer_warehouse_id=") for a in args)


def test_local_deploy_config_warehouse_alias_flows_to_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BV_INDEXER_WAREHOUSE_ID", "wh_indexer")

    cfg = _lib.LocalDeployConfig.from_env()
    args = _lib.bundle_deploy_var_args(cfg)
    assert cfg.warehouse_id == "wh_indexer"
    assert "--var=indexer_warehouse_id=wh_indexer" in args


def test_bundle_deploy_var_args_omits_lakebase_when_project_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``BV_LAKEBASE_PROJECT_ID`` is empty (Lakebase not yet
    provisioned), every Lakebase var must be omitted so the DAB
    default ``""`` flows through and T14 publish becomes a structured
    no-op. We must NOT forward ``--var=lakebase_branch=production``
    on its own — the Job would think Lakebase was partially
    configured."""

    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)

    cfg = _lib.LocalDeployConfig.from_env()
    args = _lib.bundle_deploy_var_args(cfg)
    assert not any(a.startswith("--var=lakebase_") for a in args)


def test_bundle_deploy_var_args_includes_lakebase_block_when_project_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BV_LAKEBASE_PROJECT_ID", "my-project")

    cfg = _lib.LocalDeployConfig.from_env()
    args = _lib.bundle_deploy_var_args(cfg)
    assert "--var=lakebase_project_id=my-project" in args
    assert "--var=lakebase_branch=production" in args
    assert "--var=lakebase_database=databricks_postgres" in args
    assert "--var=lakebase_sync_mode=snapshot" in args


def test_bundle_deploy_var_args_forwards_alert_email_only_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    cfg_no_email = _lib.LocalDeployConfig.from_env()
    args_no_email = _lib.bundle_deploy_var_args(cfg_no_email)
    assert not any(a.startswith("--var=indexer_alert_email=") for a in args_no_email)

    monkeypatch.setenv("BV_LOCAL_DEPLOY_OPS_EMAIL", "ops@example.com")
    cfg = _lib.LocalDeployConfig.from_env()
    args = _lib.bundle_deploy_var_args(cfg)
    assert "--var=indexer_alert_email=ops@example.com" in args


def test_local_deploy_config_has_schema_field() -> None:
    """Pin: v0.7.7 consolidation — ``LocalDeployConfig`` must carry a
    ``schema`` field so the provisioner can target a single flat
    schema. Fails if anyone removes it without coordinating a roll
    forward."""

    import dataclasses

    field_names = {f.name for f in dataclasses.fields(_lib.LocalDeployConfig)}
    assert "schema" in field_names
    assert "catalog" in field_names


def test_provision_workspace_creates_only_one_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin: v0.7.7 single-schema invariant — Phase 3 of the
    provisioner must create exactly one schema. If a future commit
    re-introduces ``CREATE SCHEMA ... config`` (or ``capability_graph``,
    ``staging``, ``audit``, ``kg``, etc.) the static-source check
    here will fail and force the author to reconsider."""

    src = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "local_deploy"
        / "provision_workspace.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "{cfg.catalog}.config",
        "{cfg.catalog}.capability_graph",
        "{cfg.catalog}.staging",
        "{cfg.catalog}.audit",
        "{cfg.catalog}.kg",
        "{cfg.catalog}.cache",
        "{cfg.catalog}.builds",
        "{cfg.catalog}.policy",
    )
    for token in forbidden:
        assert token not in src, (
            f"provision_workspace.py contains old-schema reference {token!r};"
            f" v0.7.7 consolidates everything to <BV_CATALOG>.<BV_SCHEMA>.*"
        )
    create_schema_count = src.count("CREATE SCHEMA IF NOT EXISTS")
    assert create_schema_count == 1, (
        f"expected exactly one CREATE SCHEMA, found {create_schema_count}"
    )
