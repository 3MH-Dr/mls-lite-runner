from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
import uuid
from pathlib import Path

import yaml

from mls_agent.typing_compat import enable_python310_typing_compat

from .assets import prepare_task_assets, unload_task_assets
from .config import DEFAULT_LLMROUTER_BASE_URL, render_miniswe_config
from .doctor import Check, inspect_task, run_doctor
from .io import atomic_write_json, atomic_write_text
from .manifest import Manifest, load_manifest
from .mls import RunSettings, agent_command, run_agent, semantic_success
from .prepare import cleanup_images, execute_commands, export_images, hydrate_images, packages_for_round, prepare_commands
from .preflight import audit_suite, audit_task, write_reports
from .state import SuiteState


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "manifests" / "lite30.json"
DEFAULT_ASSET_MANIFEST_DIR = PROJECT_ROOT / "manifests" / "task-assets"


def _manifest(value: str) -> Manifest:
    return load_manifest(Path(value).resolve())


def _print_checks(checks: list[Check]) -> int:
    for item in checks:
        print(f"[{item.level}] {item.subject}: {item.message}")
    errors = sum(item.level == "ERROR" for item in checks)
    print(
        f"doctor: {errors} infrastructure error(s), "
        f"{sum(item.level == 'BLOCKED' for item in checks)} task block(s), "
        f"{sum(item.level == 'WARN' for item in checks)} warning(s)"
    )
    return 2 if errors else 0


def cmd_validate(args: argparse.Namespace) -> int:
    manifest = _manifest(args.manifest)
    print(f"OK: {manifest.name}: {len(manifest.rounds)} rounds, {len(manifest.tasks)} unique tasks")
    for round_spec in manifest.rounds:
        print(f"round {round_spec.id}: profile={round_spec.platform_profile}, gpus={round_spec.platform_gpus}, tasks={len(round_spec.tasks)}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    manifest = _manifest(args.manifest)
    if args.round is not None:
        manifest = Manifest(name=manifest.name, rounds=(manifest.round(args.round),))
    checks = run_doctor(
        manifest,
        Path(args.mls_root).resolve(),
        Path(args.agent_root).resolve() if args.agent_root else None,
        Path(args.python).resolve() if args.python else None,
    )
    return _print_checks(checks)


def cmd_write_config(args: argparse.Namespace) -> int:
    destination = Path(args.output).resolve()
    if destination.exists() and not args.force:
        print(f"refusing to overwrite {destination}; pass --force", file=sys.stderr)
        return 2
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        render_miniswe_config(Path(args.save_path).resolve(), api_base=args.api_base),
        encoding="utf-8",
        newline="\n",
    )
    print(destination)
    return 0


def cmd_api_smoke(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        print(f"configuration is missing: {config_path}", file=sys.stderr)
        return 2
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        model_config = dict(config.get("miniswe_bash", {}).get("model", {}))
    except (OSError, TypeError, yaml.YAMLError) as exc:
        print(f"invalid configuration: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if model_config.get("model_class") != "litellm":
        print("API smoke requires model_class: litellm", file=sys.stderr)
        return 2
    enable_python310_typing_compat()
    from minisweagent.models import get_model

    model = get_model(args.model, config=model_config)
    response = model.query(
        [{"role": "user", "content": "Reply with a short acknowledgement."}]
    )
    if not isinstance(response, dict) or not str(response.get("content", "")).strip():
        print("model returned no non-empty assistant content", file=sys.stderr)
        return 3
    print("API_SMOKE_OK: model returned non-empty content (content suppressed)")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    report = audit_suite(
        Path(args.mls_root).resolve(),
        _manifest(args.manifest),
        Path(args.asset_manifest_dir).resolve(),
    )
    write_reports(report, Path(args.json_output).resolve(), Path(args.markdown_output).resolve())
    print(json.dumps(report["counts"], sort_keys=True))
    print(f"AUDIT_JSON={Path(args.json_output).resolve()}")
    print(f"AUDIT_MARKDOWN={Path(args.markdown_output).resolve()}")
    return 0


def cmd_init_state(args: argparse.Namespace) -> int:
    state = SuiteState(Path(args.state).resolve(), _manifest(args.manifest))
    value = state.initialize()
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def _settings(args: argparse.Namespace) -> RunSettings:
    return RunSettings(
        python=Path(args.python).resolve(),
        mls_root=Path(args.mls_root).resolve(),
        config=Path(args.config).resolve(),
        model=args.model,
        runtime_root=Path(args.runtime_root).resolve(),
    )


def _validate_run_infrastructure(args: argparse.Namespace) -> str | None:
    config = Path(args.config)
    if not config.is_file():
        return f"configuration is missing: {config}"
    config_text = config.read_text(encoding="utf-8", errors="replace")
    if "model_class: litellm" not in config_text or "QueueProxyModel" in config_text:
        return "configuration is not the required direct LiteLLM transport"
    if args.network_mode == "offline":
        return "direct Agent execution requires network-mode online or internal-endpoint"
    if args.preflight_report and not Path(args.preflight_report).is_file():
        return f"static preflight reference is missing: {args.preflight_report}"
    if not Path(args.asset_manifest_dir).is_dir():
        return f"task asset manifest directory is missing: {args.asset_manifest_dir}"
    return None


def _ensure_task_ready(args: argparse.Namespace, manifest: Manifest, mls_root: Path, task_id: str) -> tuple[bool, list[str]]:
    task = next((item for item in manifest.tasks if item.id == task_id), None)
    if task is None:
        print(f"unknown manifest task: {task_id}", file=sys.stderr)
        return False, ["unknown manifest task"]
    if args.preflight_report:
        reference = json.loads(Path(args.preflight_report).read_text(encoding="utf-8"))
        prior = next((item for item in reference.get("tasks", []) if item.get("task") == task_id), None)
        if prior:
            print(f"REFERENCE {task_id}: local-static={prior.get('status')} MLS={reference.get('mls_commit')}")
    assets_ready, asset_actions = prepare_task_assets(
        task_id,
        asset_manifest_dir=Path(args.asset_manifest_dir).resolve(),
        source_root=Path(args.asset_source_root).resolve() if args.asset_source_root else None,
        mls_root=mls_root,
        receipt_root=Path(args.asset_receipt_root).resolve(),
        execute=args.execute,
    )
    for action in asset_actions:
        print(f"ASSET {action}")
    audit = audit_task(mls_root, task_id, Path(args.asset_manifest_dir).resolve())
    issues = [f"{item['code']}: {item['message']}" for item in audit["issues"] if item["level"] in {"BLOCKED", "ERROR"}]
    if assets_ready and not issues:
        packages = [package for package in audit["packages"] if package not in args.prepared_packages]
        commands = prepare_commands(Path(args.python).resolve(), mls_root, Path(args.config).resolve(), packages)
        for package, command in zip(packages, commands):
            print("PREPARE_COMMAND:", shlex.join(command))
            if not args.execute:
                continue
            try:
                execute_commands(
                    [command],
                    mls_root,
                    Path(args.prepare_lock).resolve() if args.prepare_lock else None,
                    attempts=3,
                    retry_delays=(20.0, 40.0),
                )
            except Exception as exc:
                issues.append(f"PACKAGE_PREP_FAILED: {package}: {type(exc).__name__}: {exc}")
                break
            args.prepared_packages.add(package)
        if args.execute and not issues:
            audit = audit_task(mls_root, task_id, Path(args.asset_manifest_dir).resolve())
            issues = [f"{item['code']}: {item['message']}" for item in audit["issues"] if item["level"] in {"BLOCKED", "ERROR"}]
    if task.review_required:
        print(f"[WARN] {task.id}: {task.notes}", file=sys.stderr)
    return assets_ready and not issues, issues


def _run_one(args: argparse.Namespace, manifest: Manifest, state: SuiteState, task_id: str) -> str:
    settings = _settings(args)
    ready, issues = _ensure_task_ready(args, manifest, settings.mls_root, task_id)
    if not ready:
        if args.execute:
            state.block(task_id, issues or ["registered asset could not be prepared"])
        print(f"BLOCKED {task_id}: {'; '.join(issues) or 'asset preparation failed'}")
        return "blocked"
    current = state.initialize()["tasks"][task_id]
    if current["status"] == "succeeded":
        print(f"SKIP {task_id}: already succeeded")
        return "succeeded"
    attempt = int(current["attempts"]) + 1
    command = agent_command(settings, task_id, attempt)
    print("COMMAND:", shlex.join(command))
    if not args.execute:
        return "dry-run"
    attempt = state.start(task_id)
    log = settings.runtime_root / "logs" / task_id / f"attempt-{attempt:03d}.log"
    try:
        returncode, output, summary = run_agent(command, cwd=settings.mls_root, log_path=log)
        succeeded, error = semantic_success(returncode, summary)
        state.finish(task_id, succeeded=succeeded, summary=summary, error=error)
        print(f"RESULT {task_id}: {'succeeded' if succeeded else 'failed'}; log={log}")
        return "succeeded" if succeeded else "failed"
    except Exception as exc:
        state.finish(task_id, succeeded=False, error=f"runner exception: {type(exc).__name__}: {exc}")
        print(f"RESULT {task_id}: failed with {type(exc).__name__}: {exc}; log={log}", file=sys.stderr)
        return "failed"


def cmd_run_task(args: argparse.Namespace) -> int:
    infrastructure_error = _validate_run_infrastructure(args)
    if infrastructure_error:
        print(f"INFRASTRUCTURE_ERROR: {infrastructure_error}", file=sys.stderr)
        return 2
    manifest = _manifest(args.manifest)
    args.prepared_packages = set()
    state = SuiteState(Path(args.state).resolve(), manifest)
    state.recover_interrupted()
    outcome = _run_one(args, manifest, state, args.task)
    return 0 if outcome in {"succeeded", "dry-run"} else (4 if outcome == "blocked" else 3)


def cmd_run_round(args: argparse.Namespace) -> int:
    infrastructure_error = _validate_run_infrastructure(args)
    if infrastructure_error:
        print(f"INFRASTRUCTURE_ERROR: {infrastructure_error}", file=sys.stderr)
        return 2
    manifest = _manifest(args.manifest)
    args.prepared_packages = set()
    state = SuiteState(Path(args.state).resolve(), manifest)
    recovered = state.recover_interrupted()
    if recovered:
        print(f"recovered interrupted tasks: {', '.join(recovered)}")
    round_task_ids = [task.id for task in manifest.round(args.round).tasks]
    selected_tasks = list(args.tasks) if args.tasks else round_task_ids
    if len(selected_tasks) != len(set(selected_tasks)):
        print("--tasks contains duplicates", file=sys.stderr)
        return 2
    invalid = [task for task in selected_tasks if task not in round_task_ids]
    if invalid:
        print(f"tasks do not belong to round {args.round}: {', '.join(invalid)}", file=sys.stderr)
        return 2
    tasks = state.pending_for_round(
        args.round,
        retry_failed=args.retry_failed,
        task_ids=selected_tasks,
    )
    outcomes: dict[str, str] = {}
    if not tasks:
        print(f"round {args.round}: nothing pending")
    for task_id in tasks:
        outcomes[task_id] = _run_one(args, manifest, state, task_id)
    summary = state.round_summary(args.round, task_ids=selected_tasks)
    summary["outcomes_this_run"] = outcomes
    report_path = Path(args.report).resolve() if args.report else Path(args.runtime_root).resolve() / "reports" / f"round-{args.round}.json"
    atomic_write_json(report_path, summary)
    markdown_path = report_path.with_suffix(".md")
    lines = [
        f"# MLS-Bench Lite round {args.round}",
        "",
        f"Counts: `{json.dumps(summary['counts'], sort_keys=True)}`",
        "",
        "| Task | Status | Attempts | Detail |",
        "|---|---|---:|---|",
    ]
    for task_id, item in summary["tasks"].items():
        details = item.get("last_error") or "; ".join(item.get("preflight_issues", [])) or "-"
        lines.append(f"| `{task_id}` | {item['status']} | {item['attempts']} | {str(details).replace('|', '/')} |")
    lines.append("")
    atomic_write_text(markdown_path, "\n".join(lines))
    print(f"ROUND_REPORT={report_path}")
    print(f"ROUND_REPORT_MARKDOWN={markdown_path}")
    print(f"ROUND_COUNTS={json.dumps(summary['counts'], sort_keys=True)}")
    complete = summary["counts"].get("succeeded", 0) == len(selected_tasks)
    print(f"ROUND_COMPLETE={'yes' if complete else 'no'}")
    return 3 if any(value == "failed" for value in outcomes.values()) else 0


def cmd_prepare_round(args: argparse.Namespace) -> int:
    manifest = _manifest(args.manifest)
    mls_root = Path(args.mls_root).resolve()
    round_spec = manifest.round(args.round)
    packages = packages_for_round(mls_root, round_spec)
    commands = prepare_commands(Path(args.python).resolve(), mls_root, Path(args.config).resolve(), packages)
    print(f"round {args.round} packages: {', '.join(packages)}")
    for command in commands:
        print("COMMAND:", shlex.join(command))
    if args.export_images and not args.artifact_dir:
        print("--export-images requires --artifact-dir", file=sys.stderr)
        return 2
    if args.execute:
        execute_commands(commands, mls_root, attempts=3, retry_delays=(20.0, 40.0))
        if args.export_images:
            manifest_path = export_images(packages, Path(args.artifact_dir).resolve())
            print(f"image artifact manifest: {manifest_path}")
    return 0


def cmd_hydrate_round(args: argparse.Namespace) -> int:
    artifact_dir = Path(args.artifact_dir).resolve()
    print(f"verified image artifacts: {artifact_dir / 'images.json'}")
    if not args.execute:
        return 0
    for image in hydrate_images(artifact_dir):
        print(f"LOADED {image}")
    return 0


def cmd_cleanup_images(args: argparse.Namespace) -> int:
    artifact_dir = Path(args.artifact_dir).resolve()
    if not (artifact_dir / "images.json").is_file():
        print(f"refusing cleanup: no images.json in exact directory {artifact_dir}", file=sys.stderr)
        return 2
    if not args.execute:
        print(f"WOULD_REMOVE images listed in {artifact_dir / 'images.json'}")
        print(f"WOULD_DELETE_ARTIFACTS={args.delete_artifacts}")
        return 0
    for image in cleanup_images(artifact_dir, delete_artifacts=args.delete_artifacts):
        print(f"REMOVED {image}")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    manifest = _manifest(args.manifest)
    simulation_root = PROJECT_ROOT / ".simulation" / uuid.uuid4().hex
    simulation_root.mkdir(parents=True)
    try:
        state = SuiteState(simulation_root / "state.json", manifest)
        state.initialize()
        interrupted = args.interrupt_after
        completed = 0
        for round_spec in manifest.rounds:
            for task in round_spec.tasks:
                state.start(task.id)
                if interrupted is not None and completed == interrupted:
                    recovered = state.recover_interrupted()
                    if recovered != [task.id]:
                        raise RuntimeError("interruption recovery simulation failed")
                    state.start(task.id)
                state.finish(task.id, succeeded=True, summary={"done": True, "tests": 1})
                completed += 1
        value = state.load()
        succeeded = sum(item["status"] == "succeeded" for item in value["tasks"].values())
        print(f"SIMULATION_OK: rounds={len(manifest.rounds)}, unique_tasks={len(manifest.tasks)}, succeeded={succeeded}")
    finally:
        shutil.rmtree(simulation_root, ignore_errors=True)
    return 0


def cmd_prepare_task(args: argparse.Namespace) -> int:
    ready, actions = prepare_task_assets(
        args.task,
        asset_manifest_dir=Path(args.asset_manifest_dir).resolve(),
        source_root=Path(args.source_root).resolve() if args.source_root else None,
        mls_root=Path(args.mls_root).resolve(),
        receipt_root=Path(args.receipt_root).resolve(),
        execute=args.execute,
    )
    for action in actions:
        print(action)
    return 0 if ready else 4


def cmd_unload_task(args: argparse.Namespace) -> int:
    safe, actions = unload_task_assets(
        args.task,
        mls_root=Path(args.mls_root).resolve(),
        receipt_root=Path(args.receipt_root).resolve(),
        execute=args.execute,
    )
    for action in actions:
        print(action)
    return 0 if safe else 4


def add_common_manifest(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))


def add_run_args(parser: argparse.ArgumentParser) -> None:
    add_common_manifest(parser)
    parser.add_argument("--mls-root", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--network-mode", choices=["offline", "online", "internal-endpoint"], default="online")
    parser.add_argument("--preflight-report")
    parser.add_argument("--asset-manifest-dir", default=str(DEFAULT_ASSET_MANIFEST_DIR))
    parser.add_argument("--asset-source-root")
    parser.add_argument("--asset-receipt-root", required=True)
    parser.add_argument(
        "--prepare-lock",
        help="cross-process lock file used while MLS prepares shared packages",
    )
    parser.add_argument("--execute", action="store_true", help="actually run; without this flag only print the command")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resumable MLS-Bench Lite five-round runner")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    add_common_manifest(validate)
    validate.set_defaults(func=cmd_validate)

    audit = commands.add_parser("audit")
    add_common_manifest(audit)
    audit.add_argument("--mls-root", required=True)
    audit.add_argument("--asset-manifest-dir", default=str(DEFAULT_ASSET_MANIFEST_DIR))
    audit.add_argument("--json-output", default=str(PROJECT_ROOT / "reports" / "lite30-preflight.json"))
    audit.add_argument("--markdown-output", default=str(PROJECT_ROOT / "reports" / "lite30-preflight.md"))
    audit.set_defaults(func=cmd_audit)

    doctor = commands.add_parser("doctor")
    add_common_manifest(doctor)
    doctor.add_argument("--mls-root", required=True)
    doctor.add_argument("--agent-root")
    doctor.add_argument("--python")
    doctor.add_argument("--round", type=int, choices=range(1, 6))
    doctor.set_defaults(func=cmd_doctor)

    config = commands.add_parser("write-config")
    config.add_argument("--output", required=True)
    config.add_argument("--save-path", required=True)
    config.add_argument("--api-base", default=DEFAULT_LLMROUTER_BASE_URL)
    config.add_argument("--force", action="store_true")
    config.set_defaults(func=cmd_write_config)

    api_smoke = commands.add_parser("api-smoke")
    api_smoke.add_argument("--config", required=True)
    api_smoke.add_argument("--model", required=True)
    api_smoke.set_defaults(func=cmd_api_smoke)

    init = commands.add_parser("init-state")
    add_common_manifest(init)
    init.add_argument("--state", required=True)
    init.set_defaults(func=cmd_init_state)

    prepare = commands.add_parser("prepare-round")
    add_common_manifest(prepare)
    prepare.add_argument("--round", type=int, choices=range(1, 6), required=True)
    prepare.add_argument("--mls-root", required=True)
    prepare.add_argument("--python", required=True)
    prepare.add_argument("--config", required=True)
    prepare.add_argument("--execute", action="store_true")
    prepare.add_argument("--export-images", action="store_true")
    prepare.add_argument("--artifact-dir")
    prepare.set_defaults(func=cmd_prepare_round)

    hydrate = commands.add_parser("hydrate-round")
    hydrate.add_argument("--artifact-dir", required=True)
    hydrate.add_argument("--execute", action="store_true")
    hydrate.set_defaults(func=cmd_hydrate_round)

    cleanup = commands.add_parser("cleanup-images")
    cleanup.add_argument("--artifact-dir", required=True)
    cleanup.add_argument("--delete-artifacts", action="store_true")
    cleanup.add_argument("--execute", action="store_true")
    cleanup.set_defaults(func=cmd_cleanup_images)

    task = commands.add_parser("run-task")
    add_run_args(task)
    task.add_argument("task")
    task.set_defaults(func=cmd_run_task)

    round_parser = commands.add_parser("run-round")
    add_run_args(round_parser)
    round_parser.add_argument("--round", type=int, choices=range(1, 6), required=True)
    round_parser.add_argument("--retry-failed", action="store_true")
    round_parser.add_argument(
        "--tasks",
        nargs="+",
        help="explicit task ids to run; every task must belong to --round",
    )
    round_parser.add_argument("--report")
    round_parser.set_defaults(func=cmd_run_round)

    simulate = commands.add_parser("simulate")
    add_common_manifest(simulate)
    simulate.add_argument("--interrupt-after", type=int)
    simulate.set_defaults(func=cmd_simulate)

    prepare_task = commands.add_parser("prepare-task")
    prepare_task.add_argument("task")
    prepare_task.add_argument("--mls-root", required=True)
    prepare_task.add_argument("--asset-manifest-dir", default=str(DEFAULT_ASSET_MANIFEST_DIR))
    prepare_task.add_argument("--source-root")
    prepare_task.add_argument("--receipt-root", required=True)
    prepare_task.add_argument("--execute", action="store_true")
    prepare_task.set_defaults(func=cmd_prepare_task)

    unload_task = commands.add_parser("unload-task")
    unload_task.add_argument("task")
    unload_task.add_argument("--mls-root", required=True)
    unload_task.add_argument("--receipt-root", required=True)
    unload_task.add_argument("--execute", action="store_true")
    unload_task.set_defaults(func=cmd_unload_task)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
