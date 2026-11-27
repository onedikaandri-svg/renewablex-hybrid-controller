"""JSON and CSV validation report exporters for simulation runs."""

from __future__ import annotations

import csv
from pathlib import Path

from renewablex_controller.simulation.harness import SimulationRunResult

_CSV_COLUMNS = [
    "step_idx",
    "dt_seconds",
    "operating_mode",
    "sequence_state",
    "soc_start_fraction",
    "soc_end_fraction",
    "local_load_kw",
    "parasitic_load_kw",
    "pv_available_kw",
    "pv_command_kw",
    "pv_curtailment_kw",
    "biomass_status",
    "biomass_command_kw",
    "bess_charge_kw",
    "bess_discharge_kw",
    "grid_import_kw",
    "grid_export_kw",
    "p_pcc_export_kw",
    "served_load_kw",
    "unmet_load_kw",
    "constraint_status",
    "balance_residual_kw",
    "balanced",
    "mutual_exclusion_ok",
    "violations",
    "active_rule_ids",
    "trace_reason",
]


def export_result_json(result: SimulationRunResult, path: str | Path) -> Path:
    """Write a complete SimulationRunResult to formatted JSON."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return out_path


def export_schedule_csv(result: SimulationRunResult, path: str | Path) -> Path:
    """Write step-by-step power dispatch, SoC, and trace schedule to CSV."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for rec in result.records:
            row = rec.model_dump()
            row["operating_mode"] = rec.operating_mode.value
            row["biomass_status"] = rec.biomass_status.value
            row["constraint_status"] = rec.constraint_status.value
            row["violations"] = ";".join(rec.violations)
            row["active_rule_ids"] = ";".join(rec.active_rule_ids)
            writer.writerow(row)
    return out_path


def format_summary_table(results: list[SimulationRunResult]) -> str:
    """Format a compact Markdown table summarizing multiple scenario runs."""
    lines = [
        "| Scenario | Steps | Served (kWh) | Unmet (kWh) | SoC Range | Max Residual (kW) | Pass |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for res in results:
        s = res.summary
        soc_range = f"{s.min_soc_fraction:.2f}..{s.max_soc_fraction:.2f}"
        status = "PASS" if s.all_invariants_passed else "FAIL"
        lines.append(
            f"| {s.scenario_name} | {s.step_count} | {s.served_energy_kwh:.1f} | "
            f"{s.unmet_energy_kwh:.1f} | {soc_range} | "
            f"{s.max_abs_balance_residual_kw:.2e} | {status} |"
        )
    return "\n".join(lines)
