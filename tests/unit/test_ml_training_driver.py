import json

from brickvision_runtime.ml import training_driver


def test_training_driver_is_contract_stub() -> None:
    try:
        training_driver.main([])
    except SystemExit as exc:
        payload = json.loads(str(exc))
    else:
        raise AssertionError("contract stub should block direct ML runtime training")

    assert payload["status"] == "blocked"
    assert payload["reason"] == "BRICKVISION_RUNTIME_ML_ENGINE_REMOVED"
    assert "Databricks-native ML training artifact" in payload["message"]
