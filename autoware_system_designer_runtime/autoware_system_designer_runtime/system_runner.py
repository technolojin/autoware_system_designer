# Copyright 2026 TIER IV, inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI entry: launch a system_structure JSON via the actor runtime.

The XML LaunchService backend and ``build_launch_description`` have been
replaced by the actor coordinator in
:mod:`autoware_system_designer_runtime`. Each member of the system runs
as its own supervised subprocess, with composable nodes loaded directly
via the ``composition_interfaces/srv/LoadNode`` service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from ._impl.core.config import ActorConfig
from ._impl.core.coordinator import ensure_output_dir
from ._impl.core.stdin_console import run_console
from ._impl.measure.clock import CLOCK_AUTO, CLOCK_CHOICES
from ._impl.measure.node_graph import NodeGraph
from ._impl.measure.session import MeasureOptions, MeasureSession, default_latency_out
from ._impl.ros2.builder import populate_builder

logger = logging.getLogger("autoware_system_designer")


_NODE_PREFIX = re.compile(r"^\[([^\]]+)\] (.*)", re.DOTALL)

# Workspace-path token written by the exporter; kept in sync with
# autoware_system_designer.common.path_utils (the runtime stays stdlib-only).
_WORKSPACE_ROOT_TOKEN = "${workspace_root}"
_WORKSPACE_ROOT_ENV = "AUTOWARE_SYSTEM_DESIGNER_WORKSPACE_ROOT"
_ARTIFACTS_FILENAME = "deployment.json"


def _derive_workspace_root(json_path: Path) -> Optional[str]:
    """Workspace root implied by where the export tree actually resides."""
    env_root = os.environ.get(_WORKSPACE_ROOT_ENV)
    if env_root:
        return os.path.realpath(env_root)
    # <output_root>/exports/<system>/deployment.json, beside the system_structure dir.
    manifest = json_path.parent.parent / _ARTIFACTS_FILENAME
    try:
        tokenized = json.loads(manifest.read_text()).get("deployment_package_path", "")
    except (OSError, ValueError):
        return None
    if not tokenized.startswith(_WORKSPACE_ROOT_TOKEN):
        return None
    tail = [p for p in tokenized[len(_WORKSPACE_ROOT_TOKEN) :].split(os.sep) if p]
    # system_structure dir sits at <output_root>/exports/<system>/system_structure.
    try:
        output_root = json_path.resolve().parents[3]
    except IndexError:
        return None
    actual = str(output_root).rstrip(os.sep).split(os.sep)
    if tail and actual[-len(tail) :] != tail:
        return None
    return os.sep.join(actual[: -len(tail)] if tail else actual) or os.sep


def _expand_workspace_paths(value: Any, workspace_root: str) -> Any:
    root = workspace_root.rstrip(os.sep)
    if isinstance(value, str):
        value = value.replace(_WORKSPACE_ROOT_TOKEN + os.sep, root + os.sep)
        return root if value == _WORKSPACE_ROOT_TOKEN else value
    if isinstance(value, dict):
        return {
            _expand_workspace_paths(k, workspace_root): _expand_workspace_paths(v, workspace_root)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_expand_workspace_paths(item, workspace_root) for item in value]
    return value


def _resolve_structure_paths(data: Any, json_path: str) -> Any:
    """Expand tokenized workspace paths in a loaded system_structure payload."""
    text_has_token = _WORKSPACE_ROOT_TOKEN in json.dumps(data)
    if not text_has_token:
        return data
    root = _derive_workspace_root(Path(json_path))
    if root is None:
        raise RuntimeError(
            f"Cannot locate the workspace root for tokenized paths in {json_path}; "
            f"set {_WORKSPACE_ROOT_ENV} to the workspace root directory."
        )
    return _expand_workspace_paths(data, root)


class _ShortNameFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        m = _NODE_PREFIX.match(msg)
        if m:
            record.short_name = f"Autoware Runtime {m.group(1)}"
            record.msg = m.group(2)
            record.args = ()
        else:
            record.short_name = "Autoware Runtime"
        return True


def launch_from_json(
    json_path: str,
    *,
    ecu: Optional[str] = None,
    output_dir: Optional[Path] = None,
    respawn: bool = False,
    respawn_delay: float = 1.0,
    max_respawn_attempts: Optional[int] = None,
    graceful_shutdown_timeout: float = 5.0,
    interactive: bool = False,
    measure: Optional[MeasureOptions] = None,
) -> int:
    with open(json_path) as f:
        data = json.load(f)
    data = _resolve_structure_paths(data, json_path)

    out_dir = output_dir or ensure_output_dir()
    logger.info("logs: %s", out_dir)

    graph: Optional[NodeGraph] = None
    latency_out: Optional[Path] = None
    if measure is not None:
        graph = NodeGraph.from_system_structure(data, ecu=ecu)
        mode = graph.mode or Path(json_path).stem
        latency_out = measure.latency_out or default_latency_out(mode, Path(json_path), out_dir)

    config = ActorConfig(
        respawn_enabled=respawn,
        respawn_delay=respawn_delay,
        max_respawn_attempts=max_respawn_attempts,
        output_dir=out_dir,
        graceful_shutdown_timeout=graceful_shutdown_timeout,
    )

    async def _run() -> int:
        builder, worker = populate_builder(data["data"], ecu=ecu, config=config)
        session: Optional[MeasureSession] = None
        if measure is not None and graph is not None and latency_out is not None:
            session = MeasureSession(
                graph,
                worker,
                measure,
                log_dir=out_dir,
                latency_out=latency_out,
                mode=graph.mode or Path(json_path).stem,
            )
            session.install(builder)
        coord = builder.build()
        console_task = None
        try:
            await worker.start()
            if interactive:
                console_task = asyncio.ensure_future(run_console(coord, measure=session))
            return await coord.run()
        finally:
            if console_task is not None and not console_task.done():
                console_task.cancel()
                try:
                    await asyncio.wait_for(console_task, timeout=0.5)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
            await worker.stop()

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("shutdown via KeyboardInterrupt (signal arrived before actor runtime was ready)")
        return 130


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch an Autoware system from a system_structure JSON file " "under per-process supervision."
    )
    parser.add_argument(
        "json_file",
        help="Path to system_structure JSON " "(e.g. .../system_structure/LoggingSimulation.json)",
    )
    parser.add_argument(
        "--ecu",
        default=None,
        help="Only launch nodes whose compute_unit matches this value "
        "(e.g. main_ecu, dummy_ecu). When omitted, all nodes are launched.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Base directory for per-node log output. Default: a fresh "
        "timestamped folder under /tmp/autoware_system_designer_logs.",
    )
    parser.add_argument(
        "--respawn",
        action="store_true",
        help="Restart processes that exit non-cleanly.",
    )
    parser.add_argument(
        "--respawn-delay",
        type=float,
        default=1.0,
        help="Seconds to wait before respawning (default: 1.0).",
    )
    parser.add_argument(
        "--max-respawn-attempts",
        type=int,
        default=None,
        help="Cap on consecutive respawn attempts (default: unlimited).",
    )
    parser.add_argument(
        "--graceful-shutdown-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait after SIGTERM before escalating to SIGKILL " "(default: 5.0).",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Read commands from stdin while running: status, stop <name>, " "restart <name>, kill <name>, quit.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    measure_group = parser.add_argument_group(
        "latency measurement",
        "Preload the rcl tracer into every process and analyze node process time, "
        "topic communication and event chains into a latency file.",
    )
    measure_group.add_argument(
        "--measure",
        action="store_true",
        help="Arm the tracer and open the measurement window once every actor has started.",
    )
    measure_group.add_argument(
        "--measure-duration",
        type=float,
        default=None,
        metavar="S",
        help="Close the window after S seconds (after the settle time), shut the system down "
        "and analyze once the actors have terminated.",
    )
    measure_group.add_argument(
        "--measure-settle",
        type=float,
        default=5.0,
        metavar="S",
        help="Seconds after launch_ready excluded from the window (default: 5).",
    )
    measure_group.add_argument(
        "--measure-keep-running",
        action="store_true",
        help="With --measure-duration, keep the system running after the analysis.",
    )
    measure_group.add_argument(
        "--no-probe",
        action="store_true",
        help="Do not add probe subscriptions on intra-process-only topics.",
    )
    measure_group.add_argument(
        "--measure-clock",
        default=CLOCK_AUTO,
        choices=CLOCK_CHOICES,
        help="Time base of durations and rates: ros when the system runs on /clock (use_sim_time), "
        "wall otherwise; auto picks by the trace (default).",
    )
    measure_group.add_argument(
        "--latency-out",
        type=Path,
        default=None,
        metavar="FILE",
        help="Latency file to write: the bundle's script under .js, bare JSON under any other suffix. "
        "Default: <Mode>_latency.js in the export's visualization/web/data/ (where the diagram reads it), "
        "else <log-dir>/latency/.",
    )
    args = parser.parse_args()

    measure: Optional[MeasureOptions] = None
    if args.measure or args.measure_duration is not None:
        measure = MeasureOptions(
            duration=args.measure_duration,
            settle=args.measure_settle,
            probe=not args.no_probe,
            keep_running=args.measure_keep_running,
            latency_out=args.latency_out,
            clock=args.measure_clock,
        )

    short_name_filter = _ShortNameFilter()
    handler = logging.StreamHandler()
    handler.addFilter(short_name_filter)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(short_name)s - %(message)s", datefmt="%H:%M:%S"))
    logging.root.setLevel(getattr(logging, args.log_level))
    logging.root.addHandler(handler)

    sys.exit(
        launch_from_json(
            args.json_file,
            ecu=args.ecu,
            output_dir=args.log_dir,
            respawn=args.respawn,
            respawn_delay=args.respawn_delay,
            max_respawn_attempts=args.max_respawn_attempts,
            graceful_shutdown_timeout=args.graceful_shutdown_timeout,
            interactive=args.interactive,
            measure=measure,
        )
    )
