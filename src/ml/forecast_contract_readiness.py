from __future__ import annotations

from typing import Any

CONTRACT_READINESS_GATES = (
    'contract_implementation',
    'canonical_data_access',
    'dataset_60m',
    'dataset_120m',
    'target_validation',
    'lag_validation',
    'feature_leakage',
    'chronological_split',
    'baseline_definition',
    'metric_definition',
    'artifact_validation',
    'deterministic_generation',
    'tests',
)


def build_contract_readiness(gates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    relevant = {
        name: gates.get(name, {'status': 'PASS'})
        for name in CONTRACT_READINESS_GATES
        if name in gates
    }
    if not relevant:
        return {'status': 'BLOCKED', 'blocking_gates': list(CONTRACT_READINESS_GATES), 'gates': {}}
    blocking_gates = [
        name
        for name, values in relevant.items()
        if str(values.get('status', 'BLOCKED')).upper() != 'PASS'
    ]
    status = 'PASS' if not blocking_gates else 'BLOCKED'
    return {
        'status': status,
        'blocking_gates': blocking_gates,
        'gates': relevant,
    }


def build_production_model_readiness() -> dict[str, Any]:
    return {
        'status': 'NOT_APPLICABLE',
        'reason': 'No production model is expected at forecasting-contract validation stage.',
        'evidence': 'Model training is intentionally out of scope for forecasting contract readiness.',
    }


def build_overall_readiness(
    contract_readiness: dict[str, Any],
    production_model_readiness: dict[str, Any],
) -> dict[str, Any]:
    contract_status = str(contract_readiness.get('status', 'BLOCKED')).upper()
    production_status = str(production_model_readiness.get('status', 'NOT_APPLICABLE')).upper()
    overall_status = 'PASS' if contract_status == 'PASS' and production_status in {'PASS', 'NOT_APPLICABLE'} else 'BLOCKED'
    return {
        'contract_readiness': contract_readiness,
        'production_model_readiness': production_model_readiness,
        'overall_status': overall_status,
    }
