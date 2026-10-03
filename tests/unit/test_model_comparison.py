"""Selection policy must depend only on validation accuracy and declared simplicity."""

import joblib
import numpy as np
import pandas as pd
import pytest
from src.ml.model_comparison import (
    FEATURES,
    candidate_specs,
    fit_candidate,
    predict_candidate,
    select_model,
)


def candidate(name, validation, tier, size, test=0):
    return {
        "model": name,
        "validation_mae": validation,
        "complexity_tier": tier,
        "model_bytes": size,
        "mae": test,
    }


def test_simpler_model_wins_inside_one_percent_even_with_worse_test_score():
    complex_model = candidate("Ensemble", 1.0, 3, 10000, test=0.1)
    simple_model = candidate("Linear", 1.009, 1, 500, test=100)
    winner, decision = select_model([complex_model, simple_model])
    assert winner["model"] == "Linear"
    assert len(decision["eligible_models"]) == 2
    # Altering historical test accuracy must never alter selection.
    complex_model["mae"], simple_model["mae"] = 1000, 0
    assert select_model([complex_model, simple_model])[0]["model"] == "Linear"


def test_simpler_model_outside_tolerance_does_not_win():
    assert (
        select_model([candidate("Tree", 1.011, 2, 50), candidate("Boost", 1, 3, 100)])[0]["model"]
        == "Boost"
    )


def test_equal_tier_prefers_smaller_model_and_is_order_independent():
    items = [candidate("Large", 1, 3, 10000), candidate("Small", 1.005, 3, 1000)]
    assert select_model(items)[0]["model"] == select_model(items[::-1])[0]["model"] == "Small"


def test_selection_rejects_invalid_validation_metric():
    with pytest.raises(ValueError, match="Invalid validation"):
        select_model([candidate("Broken", float("nan"), 1, 10)])


@pytest.mark.parametrize("spec", candidate_specs(), ids=[s[1] for s in candidate_specs()])
def test_candidate_families_predict_nonnegative_counts_and_reload(spec, tmp_path):
    _, kind, _, original_params = spec
    params = original_params.copy()
    for name in ["n_estimators", "max_iter", "iterations"]:
        if name in params:
            params[name] = 3
    frame = pd.DataFrame({name: np.arange(200) % 7 for name in FEATURES})
    frame["y"] = 2 + np.arange(200) % 5
    model = fit_candidate(kind, params, frame.iloc[:150], frame.iloc[150:])
    expected = predict_candidate(model, frame.iloc[150:])
    path = tmp_path / "model.joblib"
    joblib.dump(model, path, compress=1)
    actual = predict_candidate(joblib.load(path), frame.iloc[150:])
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)
    assert np.isfinite(actual).all() and (actual >= 0).all()
    if kind == "catboost":
        # CatBoost Poisson's MAE evaluation can use a different response scale;
        # the RMSE candidate must report the actual count-scale MAE used here.
        assert (
            abs(
                model.get_best_score()["validation"]["MAE"]
                - np.abs(expected - frame.iloc[150:].y).mean()
            )
            < 1e-4
        )
