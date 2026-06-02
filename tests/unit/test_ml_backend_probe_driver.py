from brickvision_runtime.ml import backend_probe_driver


def test_backend_probe_driver_reports_failed_imports() -> None:
    errors: dict[str, str] = {}

    assert backend_probe_driver._probe_import(
        "brickvision_module_that_does_not_exist",
        errors,
        "missing_module",
    ) is False
    assert "missing_module" in errors


def test_backend_probe_driver_runtime_payload_has_expected_keys() -> None:
    evidence = backend_probe_driver._probe_runtime()

    assert set(evidence) == {
        "databricks_automl_available",
        "spark_ml_allowed",
        "mlflow_uc_registry_available",
        "mosaic_ai_available",
        "substrate",
        "errors",
    }
    assert set(evidence["substrate"]) == {
        "python_imports",
        "data_movement",
        "mlflow_primitives",
    }
