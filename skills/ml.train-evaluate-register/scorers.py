"""Scorer registry for ``skill:ml.train-evaluate-register``."""

from __future__ import annotations

from brickvision_runtime.eval.scorers.write_side import (
    training_run_replay_determinism,
    val_metric_floor,
)

SKILL_ID = "skill:ml.train-evaluate-register"

SCORERS = {
    "ValMetricFloor": val_metric_floor,
    "TrainingRunReplayDeterminism": training_run_replay_determinism,
}

__all__ = ["SCORERS", "SKILL_ID"]
