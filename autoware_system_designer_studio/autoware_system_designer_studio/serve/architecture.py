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


"""A daemon's place in an E/E architecture: its compute units, its port and the peers it serves.

Exports carry the E/E architectures their system is declared for, resolved to one record per
execution environment. A daemon told its architecture and environment serves the compute units
bound to that environment on the environment's daemon port. The daemon of the main environment
has the daemons of the other environments as peers: on their loopback port when both share a
network namespace, through an SSH forward from the environment's address otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from ..exports import SystemExport
from .peers import PeerSpec

ENTITY_SUFFIX = ".ee_architecture"


class ArchitectureError(Exception):
    pass


@dataclass(frozen=True)
class EnvironmentBinding:
    """What the architecture gives the daemon of one environment."""

    architecture: str
    environment: str
    main: bool
    port: Optional[int]
    units: tuple[str, ...]
    peers: tuple[PeerSpec, ...]

    def to_view(self) -> dict[str, Any]:
        return {
            "name": self.architecture,
            "environment": self.environment,
            "main": self.main,
            "port": self.port,
            "compute_units": list(self.units),
            "peers": [{"unit": peer.unit, "url": peer.url, "ssh": peer.ssh} for peer in self.peers],
        }


def architecture_name(text: str) -> str:
    return text[: -len(ENTITY_SUFFIX)] if text.endswith(ENTITY_SUFFIX) else text


def architectures(systems: Iterable[SystemExport]) -> dict[str, list[dict[str, Any]]]:
    """Resolved records of each architecture name, one per system declared for it."""
    found: dict[str, list[dict[str, Any]]] = {}
    for system in systems:
        records = system.manifest.get("ee_architectures")
        for record in records if isinstance(records, list) else []:
            if isinstance(record, dict) and isinstance(record.get("name"), str):
                found.setdefault(record["name"], []).append(record)
    return found


def _environments(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    environments = record.get("environments")
    return {
        environment["id"]: environment
        for environment in (environments if isinstance(environments, list) else [])
        if isinstance(environment, dict) and isinstance(environment.get("id"), str)
    }


def _units(environment: dict[str, Any]) -> list[str]:
    units = environment.get("compute_units")
    return [unit for unit in units if isinstance(unit, str)] if isinstance(units, list) else []


def describe(systems: Iterable[SystemExport], architecture: str) -> dict[str, Any]:
    """Environments of ``architecture`` with the compute units of every system declared for it."""
    name = architecture_name(architecture)
    found = architectures(systems)
    if name not in found:
        raise ArchitectureError(
            f"no exported system is declared for E/E architecture {name!r} (found: {sorted(found)})"
        )
    environments: dict[str, dict[str, Any]] = {}
    for record in found[name]:
        for key, environment in _environments(record).items():
            merged = environments.setdefault(key, {**environment, "compute_units": []})
            merged["compute_units"] += [unit for unit in _units(environment) if unit not in merged["compute_units"]]
    return {
        "name": name,
        "main_environment": found[name][0].get("main_environment"),
        "networks": found[name][0].get("networks") or [],
        "devices": found[name][0].get("devices") or [],
        "environments": list(environments.values()),
    }


def resolve(systems: Iterable[SystemExport], architecture: str, environment: str) -> EnvironmentBinding:
    """Binding of ``environment`` in ``architecture``, over every system declared for it."""
    name = architecture_name(architecture)
    found = architectures(systems)
    if name not in found:
        raise ArchitectureError(
            f"no exported system is declared for E/E architecture {name!r} (found: {sorted(found)})"
        )
    port: Optional[int] = None
    main = False
    units: list[str] = []
    peers: dict[str, PeerSpec] = {}
    for record in found[name]:
        environments = _environments(record)
        own = environments.get(environment)
        if own is None:
            raise ArchitectureError(
                f"E/E architecture {name!r} has no environment {environment!r} (found: {sorted(environments)})"
            )
        if isinstance(own.get("daemon_port"), int):
            port = own["daemon_port"]
        units += [unit for unit in _units(own) if unit not in units]
        if record.get("main_environment") != environment:
            continue
        main = True
        for other in environments.values():
            if other is own or not _units(other) or not isinstance(other.get("daemon_port"), int):
                continue
            remote = other.get("network_namespace") != own.get("network_namespace")
            address = other.get("address")
            if remote and not isinstance(address, str):
                raise ArchitectureError(f"environment {other['id']!r} of E/E architecture {name!r} has no address")
            for unit in _units(other):
                peers[unit] = PeerSpec(unit, f"http://127.0.0.1:{other['daemon_port']}", address if remote else None)
    return EnvironmentBinding(name, environment, main, port, tuple(units), tuple(peers.values()))
