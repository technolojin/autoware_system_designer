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


"""E/E architecture of a system: checks of the description and its resolved export.

The description holds networks, devices and their execution environments, and
binds each compute unit of a system to one environment. Resolution checks the
description against the system and flattens it to one record per environment,
which is what export consumers read.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from autoware_system_designer.builder.modes import apply_mode_configuration, select_modes
from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.common.source_location import format_source, source_from_config
from autoware_system_designer.model.config import EeArchitectureConfig, SystemConfig

logger = logging.getLogger(__name__)

NETWORK_HOST = "host"
NETWORK_OWN = "own"
KIND_ONBOARD = "onboard"


def _duplicates(names: Iterable[Any]) -> List[Any]:
    seen: Set[Any] = set()
    repeated: List[Any] = []
    for name in names:
        if name in seen and name not in repeated:
            repeated.append(name)
        seen.add(name)
    return repeated


def system_units(system_config: SystemConfig) -> Dict[str, str]:
    """Kind of each compute unit of a system; the units its components name when it declares none."""
    if system_config.compute_units:
        return {unit.name: unit.kind for unit in system_config.compute_units}
    units: Dict[str, str] = {}
    for component in system_config.components or []:
        unit = component.get("compute_unit") if isinstance(component, dict) else None
        if unit:
            units.setdefault(unit, KIND_ONBOARD)
    return units


def system_requirements(system_config: SystemConfig) -> Dict[str, List[str]]:
    """Capabilities each declared compute unit of a system requires of its environment."""
    return {unit.name: list(unit.requires) for unit in system_config.compute_units or []}


def cross_unit_pairs(system_config: SystemConfig) -> Set[Tuple[str, str]]:
    """Pairs of compute units a connection crosses, in any mode of the system."""
    mode_names, _ = select_modes(system_config)
    pairs: Set[Tuple[str, str]] = set()
    for mode_name in mode_names:
        mode_config = apply_mode_configuration(system_config, mode_name)
        unit_of = {
            component.get("name"): component.get("compute_unit")
            for component in mode_config.components or []
            if isinstance(component, dict)
        }
        for connection in mode_config.connections or []:
            if not isinstance(connection, (list, tuple)):
                continue
            units = sorted(
                {unit_of.get(end.split(".", 1)[0]) for end in connection if isinstance(end, str)} - {None, ""}
            )
            pairs.update((a, b) for i, a in enumerate(units) for b in units[i + 1 :])
    return pairs


class _Resolver:
    def __init__(self, architecture: EeArchitectureConfig) -> None:
        self.architecture = architecture
        self.source = format_source(source_from_config(architecture, None))
        self.networks: Dict[str, Optional[Any]] = {}
        self.environments: Dict[str, Dict[str, Any]] = {}
        self.device_networks: Dict[str, List[Tuple[str, Optional[str]]]] = {}

    def fail(self, message: str) -> ValidationError:
        return ValidationError(f"E/E architecture '{self.architecture.name}': {message}{self.source}")

    def _address(self, text: Any, where: str) -> str:
        try:
            return str(ipaddress.ip_address(text))
        except ValueError:
            raise self.fail(f"{where} has invalid address '{text}'") from None

    def read_networks(self) -> None:
        networks = self.architecture.networks or []
        repeated = _duplicates(network["name"] for network in networks)
        if repeated:
            raise self.fail(f"duplicate network(s) {repeated}")
        for network in networks:
            subnet = network.get("subnet")
            try:
                self.networks[network["name"]] = None if subnet is None else ipaddress.ip_network(subnet)
            except ValueError:
                raise self.fail(f"network '{network['name']}' has invalid subnet '{subnet}'") from None

    def read_devices(self) -> None:
        devices = self.architecture.devices or []
        repeated = _duplicates(device["name"] for device in devices)
        if repeated:
            raise self.fail(f"duplicate device(s) {repeated}")
        addresses: List[str] = []
        for device in devices:
            name = device["name"]
            interfaces = device.get("interfaces") or []
            repeated = _duplicates(interface["name"] for interface in interfaces)
            if repeated:
                raise self.fail(f"device '{name}' has duplicate interface(s) {repeated}")
            attached: List[Tuple[str, Optional[str]]] = []
            for interface in interfaces:
                where = f"interface '{interface['name']}' of device '{name}'"
                network = interface["network"]
                if network not in self.networks:
                    raise self.fail(f"{where} is on undeclared network '{network}'")
                address = interface.get("address")
                if address is not None:
                    address = self._address(address, where)
                    subnet = self.networks[network]
                    if subnet is not None and ipaddress.ip_address(address) not in subnet:
                        raise self.fail(f"{where} has address {address} outside {subnet} of network '{network}'")
                    addresses.append(address)
                attached.append((network, address))
            self.device_networks[name] = attached

            environments = device.get("environments") or []
            repeated = _duplicates(environment["name"] for environment in environments)
            if repeated:
                raise self.fail(f"device '{name}' has duplicate environment(s) {repeated}")
            for environment in environments:
                key = f"{name}/{environment['name']}"
                network = environment.get("network", NETWORK_HOST)
                address = environment.get("address")
                if network == NETWORK_OWN:
                    if address is None:
                        raise self.fail(f"environment '{key}' has its own network and needs an address")
                    address = self._address(address, f"environment '{key}'")
                    addresses.append(address)
                elif address is not None:
                    raise self.fail(f"environment '{key}' shares the device's network and takes no address")
                self.environments[key] = {
                    "id": key,
                    "device": name,
                    "name": environment["name"],
                    "kind": environment.get("kind", "host"),
                    "network": network,
                    "network_namespace": key if network == NETWORK_OWN else name,
                    "address": address,
                    "shared_memory": bool(environment.get("shared_memory", False)),
                    "daemon_port": environment.get("daemon_port"),
                    "provides": list(environment.get("provides") or []),
                    "compute_units": [],
                }
        repeated = _duplicates(addresses)
        if repeated:
            raise self.fail(f"address(es) {repeated} are assigned more than once")

    def bind(self, units: Dict[str, str], requirements: Dict[str, List[str]], system_name: str) -> None:
        bindings = self.architecture.bindings or []
        repeated = _duplicates(binding["compute_unit"] for binding in bindings)
        if repeated:
            raise self.fail(f"compute unit(s) {repeated} are bound more than once")
        for binding in bindings:
            unit, key = binding["compute_unit"], binding["environment"]
            if unit not in units:
                raise self.fail(f"binds '{unit}', which is no compute unit of system '{system_name}' {sorted(units)}")
            if units[unit] != KIND_ONBOARD:
                raise self.fail(f"binds '{unit}', an {units[unit]} compute unit of system '{system_name}'")
            if key not in self.environments:
                raise self.fail(f"binds '{unit}' to undeclared environment '{key}' {sorted(self.environments)}")
            missing = [name for name in requirements.get(unit, []) if name not in self.environments[key]["provides"]]
            if missing:
                raise self.fail(
                    f"binds '{unit}' to environment '{key}', which does not provide {missing} the unit requires"
                )
            self.environments[key]["compute_units"].append(unit)
        bound = {binding["compute_unit"] for binding in bindings}
        unbound = [unit for unit, kind in units.items() if kind == KIND_ONBOARD and unit not in bound]
        if unbound:
            raise self.fail(f"onboard compute unit(s) {unbound} of system '{system_name}' are bound to no environment")

    def _reach_address(self, environment: Dict[str, Any], main_device: Optional[str]) -> Optional[str]:
        """Address the environment answers on, preferring a network the main environment's device is on."""
        if environment["network"] == NETWORK_OWN:
            return environment["address"]
        attached = [(network, address) for network, address in self.device_networks[environment["device"]] if address]
        shared = {network for network, _ in self.device_networks.get(main_device, [])}
        for network, address in attached:
            if network in shared:
                return address
        return attached[0][1] if attached else None

    def check_daemons(self) -> Optional[str]:
        """Daemon reachability of the bound environments; returns the main environment."""
        main = self.architecture.main_environment
        if main is not None and main not in self.environments:
            raise self.fail(f"main environment '{main}' is not declared {sorted(self.environments)}")
        main_device = self.environments[main]["device"] if main else None
        for environment in self.environments.values():
            environment["address"] = self._reach_address(environment, main_device)
        bound = [environment for environment in self.environments.values() if environment["compute_units"]]
        if len(bound) < 2:
            return main
        if main is None:
            raise self.fail("compute units are bound to several environments; 'main_environment' names the serving one")
        serving = bound if self.environments[main] in bound else [self.environments[main], *bound]
        missing = [environment["id"] for environment in serving if environment["daemon_port"] is None]
        if missing:
            raise self.fail(f"environment(s) {missing} need a 'daemon_port'")
        repeated = _duplicates(environment["daemon_port"] for environment in serving)
        if repeated:
            raise self.fail(f"daemon port(s) {repeated} are used by more than one environment")
        main_namespace = self.environments[main]["network_namespace"]
        for environment in bound:
            if environment["network_namespace"] != main_namespace and environment["address"] is None:
                raise self.fail(
                    f"environment '{environment['id']}' is outside the main environment's network and has no address"
                )
        return main

    def warn_shared_memory(self, pairs: Set[Tuple[str, str]]) -> List[str]:
        """Warnings for connections between environments of one device that share no memory."""
        environment_of = {
            unit: environment for environment in self.environments.values() for unit in environment["compute_units"]
        }
        warnings: List[str] = []
        for first, second in sorted(pairs):
            a, b = environment_of.get(first), environment_of.get(second)
            if a is None or b is None or a is b or a["device"] != b["device"]:
                continue
            if a["shared_memory"] and b["shared_memory"]:
                continue
            warnings.append(
                f"E/E architecture '{self.architecture.name}': compute units '{first}' ({a['id']}) and '{second}' "
                f"({b['id']}) exchange topics on device '{a['device']}' without shared memory; disable the DDS "
                f"shared memory transport in these environments{self.source}"
            )
        return warnings


def resolve_ee_architecture(architecture: EeArchitectureConfig, system_config: SystemConfig) -> Dict[str, Any]:
    """Check an E/E architecture against a system and return its export record.

    Every onboard compute unit is bound to exactly one declared environment,
    which provides what the unit requires, and no offboard unit is bound. With units in several environments, each has a
    distinct daemon port and one reachable from the main environment.
    """
    resolver = _Resolver(architecture)
    resolver.read_networks()
    resolver.read_devices()
    resolver.bind(system_units(system_config), system_requirements(system_config), system_config.name)
    main = resolver.check_daemons()
    for warning in resolver.warn_shared_memory(cross_unit_pairs(system_config)):
        logger.warning(warning)
    return {
        "name": architecture.name,
        "description": architecture.config.get("description", ""),
        "main_environment": main,
        "networks": architecture.networks or [],
        "devices": architecture.devices or [],
        "bindings": architecture.bindings or [],
        "environments": list(resolver.environments.values()),
    }
