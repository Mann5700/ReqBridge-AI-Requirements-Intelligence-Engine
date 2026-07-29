"""
ROM Scoring Engine
Computes slice scores and total ROM from structured input.
Called by the agent to avoid LLM-based arithmetic.

Usage:
  python rom_engine.py --slices slices.json [--config config/rom_config.yaml]

Input (slices.json):
  [
    {
      "slice": "EDC",
      "change_type": "multi_rule_or_multi_field",
      "dependencies": "one_dependency",
      "test_breadth": "functional_plus_full_regression",
      "data_env": "moderate_setup_or_conditioning"
    },
    ...
  ]

Output (stdout JSON):
  {
    "slices": [...with scores...],
    "total_score": 12.5,
    "rom_band": "large",
    "story_points_total": 26,
    "hours_total": 169.0,
    "sprint": { "name": "PI12.3", "iteration_path": "..." },
    "story_package": [...]
  }
"""
import json
import os
import sys
import argparse
from datetime import date, datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    # Fallback: try to load with a simple parser if PyYAML not installed
    yaml = None

# Default config bundled with the backend package.
_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config" / "rom_config.yaml"


def resolve_config_path(config_path: str | None = None) -> str:
    """Resolve the ROM config path.

    Precedence:
        1. Explicit ``config_path`` argument
        2. ``ROM_CONFIG_PATH`` environment variable
        3. Bundled default at ``scoring/config/rom_config.yaml``
    """
    if config_path:
        return config_path
    env_path = os.environ.get("ROM_CONFIG_PATH")
    if env_path:
        return env_path
    return str(_DEFAULT_CONFIG_PATH)


def load_config(config_path: str | None = None) -> dict:
    path = Path(resolve_config_path(config_path))
    if not path.exists():
        print(f"Error: config file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        if yaml:
            return yaml.safe_load(f)
        else:
            print("Error: PyYAML is required. Install with: pip install pyyaml", file=sys.stderr)
            sys.exit(1)


def resolve_sprint(config: dict, today: date = None) -> dict:
    """Resolve current sprint from sprint_timeline based on a target date.

    The target date is chosen in this order:
        1. Explicit ``today`` argument
        2. ``config["_override_date"]`` (set from CLI ``--date`` or session metadata)
        3. ``date.today()``
    """
    if today is None:
        today = config.get("_override_date") or date.today()
    timeline = config.get("sprint_timeline", [])
    for entry in timeline:
        start = datetime.strptime(entry["start"], "%Y-%m-%d").date() if isinstance(entry["start"], str) else entry["start"]
        end = datetime.strptime(entry["end"], "%Y-%m-%d").date() if isinstance(entry["end"], str) else entry["end"]
        if start <= today <= end:
            return {
                "name": entry["sprint"],
                "iteration_path": entry["iteration_path"],
                "start": str(start),
                "end": str(end),
            }
    # Fallback: nearest sprint boundary
    if timeline:
        first = timeline[0]
        last = timeline[-1]
        first_start = datetime.strptime(first["start"], "%Y-%m-%d").date() if isinstance(first["start"], str) else first["start"]
        if today < first_start:
            return {"name": first["sprint"], "iteration_path": first["iteration_path"], "start": first["start"], "end": first["end"]}
        return {"name": last["sprint"], "iteration_path": last["iteration_path"], "start": last["start"], "end": last["end"]}
    return {"name": "UNKNOWN", "iteration_path": "UNKNOWN", "start": "", "end": ""}


def score_slice(slice_input: dict, config: dict, perf_triggered: bool) -> dict:
    """Score a single slice using config weights and multipliers."""
    base_weights = config["base_weights"]
    multipliers = config["multipliers"]

    slice_name = slice_input["slice"]
    base = base_weights.get(slice_name, 1.0)

    change_type_val = multipliers["change_type"].get(slice_input.get("change_type", "single_rule_or_single_path"), 1.0)
    dep_val = multipliers["dependencies"].get(slice_input.get("dependencies", "one_dependency"), 1.1)

    # Apply slice-aware perf multiplier overrides
    if perf_triggered and slice_name != "PERF":
        traffic_slices = {"EDC", "IDC", "ODC", "BRE_SEC", "MSG", "SCAN_STS"}
        if slice_name in traffic_slices:
            tb_val = multipliers["test_breadth"].get("functional_plus_full_regression", 1.30)
            de_val = multipliers["data_env"].get("heavy_setup_or_multi_env", 1.35)
        else:
            tb_val = multipliers["test_breadth"].get("functional_plus_targeted_regression", 1.15)
            de_val = multipliers["data_env"].get("moderate_setup_or_conditioning", 1.15)
    elif slice_name == "PERF":
        tb_val = multipliers["test_breadth"].get("functional_plus_regression_plus_performance", 1.60)
        de_val = multipliers["data_env"].get("perf_prep_purge_image_copy_tooling", 1.60)
    else:
        tb_val = multipliers["test_breadth"].get(slice_input.get("test_breadth", "functional_only"), 1.0)
        de_val = multipliers["data_env"].get(slice_input.get("data_env", "minimal_existing_setup"), 1.0)

    score = round(base * change_type_val * dep_val * tb_val * de_val, 3)

    return {
        "slice": slice_name,
        "base_weight": base,
        "change_type": slice_input.get("change_type", "single_rule_or_single_path"),
        "change_type_multiplier": change_type_val,
        "dependencies": slice_input.get("dependencies", "one_dependency"),
        "dependency_multiplier": dep_val,
        "test_breadth_multiplier": tb_val,
        "data_env_multiplier": de_val,
        "score": score,
    }


def determine_rom_band(total_score: float, config: dict) -> str:
    """Determine ROM band from total score."""
    bands = config["rom_bands"]
    if total_score < bands["small"][1]:
        return "small"
    elif total_score < bands["medium"][1]:
        return "medium"
    elif total_score < bands["large"][1]:
        return "large"
    else:
        return "xlarge"


def compute_story_package(scored_slices: list, perf_triggered: bool, config: dict) -> list:
    """Determine the story package based on slices and perf trigger."""
    stories = list(config.get("default_story_package", []))
    if perf_triggered:
        stories.extend(config.get("perf_story_package", []))
    return stories


def assign_story_points(rom_band: str, story_count: int) -> list:
    """Assign fibonacci story points based on ROM band and story count."""
    # Heuristic point distribution by band
    band_base = {"small": 3, "medium": 5, "large": 8, "xlarge": 13}
    base_pts = band_base.get(rom_band, 5)
    points = []
    for i in range(story_count):
        if i == 0:  # Test plan/prep is typically largest
            points.append(base_pts)
        elif "Signoff" in str(i):
            points.append(max(1, base_pts // 3))
        else:
            points.append(max(2, base_pts - 2))
    return points


def run(slices_input: list, config: dict, sprint_start: str | date | None = None) -> dict:
    """Main scoring function.

    ``sprint_start`` optionally overrides the date used for sprint resolution.
    Accepts a ``date`` or an ISO ``YYYY-MM-DD`` string (e.g. from session
    metadata supplied at upload time).
    """
    # Resolve an override date for sprint selection, if provided.
    override_date: date | None = None
    if isinstance(sprint_start, date):
        override_date = sprint_start
    elif isinstance(sprint_start, str) and sprint_start.strip():
        try:
            override_date = datetime.strptime(sprint_start.strip(), "%Y-%m-%d").date()
        except ValueError:
            override_date = None

    # Check if PERF slice present
    perf_triggered = any(s.get("slice") == "PERF" for s in slices_input)

    # Score all slices
    scored = [score_slice(s, config, perf_triggered) for s in slices_input]
    total_score = round(sum(s["score"] for s in scored), 3)
    rom_band = determine_rom_band(total_score, config)

    # Build story package
    story_package = compute_story_package(scored, perf_triggered, config)
    story_points = assign_story_points(rom_band, len(story_package))

    # Calculate hours
    sph = config.get("story_point_hours", 6.5)
    total_points = sum(story_points)
    total_hours = round(total_points * sph, 1)

    # Resolve sprint
    sprint = resolve_sprint(config, today=override_date)

    # Build story detail
    stories_detail = []
    for i, name in enumerate(story_package):
        pts = story_points[i] if i < len(story_points) else 3
        stories_detail.append({
            "title": name,
            "story_points": pts,
            "hours": round(pts * sph, 1),
        })

    return {
        "slices": scored,
        "total_score": total_score,
        "rom_band": rom_band,
        "story_points_total": total_points,
        "hours_total": total_hours,
        "sprint": sprint,
        "story_package": stories_detail,
        "perf_triggered": perf_triggered,
        "ado_paths": {
            "feature_iteration_path": config["ado_paths"]["feature_iteration_path"],
            "feature_area_path": config["ado_paths"]["feature_area_path"],
            "story_task_area_path": config["ado_paths"]["story_task_area_path"],
            "story_task_iteration_path": sprint["iteration_path"],
        },
    }


def main():
    parser = argparse.ArgumentParser(description="ROM Scoring Engine")
    parser.add_argument("--slices", required=True, help="Path to slices JSON input file")
    parser.add_argument("--config", default="config/rom_config.yaml", help="Path to config YAML")
    parser.add_argument("--date", default=None, help="Override date (YYYY-MM-DD) for sprint resolution")
    args = parser.parse_args()

    config = load_config(args.config)

    with open(args.slices, "r", encoding="utf-8") as f:
        slices_input = json.load(f)

    if args.date:
        override_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        config["_override_date"] = override_date

    result = run(slices_input, config)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
