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

The description holds networks, device instances with their interfaces and
execution environments, the connections between interfaces, time
synchronization domains, and binds each compute unit of a system to one
environment and each peripheral to one device. Resolution checks the
description against the system, expands composite devices into their parts,
and derives what export consumers read: one record per environment, the
wiring, the path of every synchronized interface to its grandmaster, and the
interfaces and addresses a peripheral is reached by.
"""

from __future__ import annotations

import ipaddress
import logging
from collections import deque
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from autoware_system_designer.builder.modes import apply_mode_configuration, select_modes
from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.common.source_location import format_source, source_from_config
from autoware_system_designer.model.config import DeviceConfig, EeArchitectureConfig, SystemConfig

logger = logging.getLogger(__name__)

NETWORK_HOST = "host"
NETWORK_OWN = "own"
KIND_ONBOARD = "onboard"
CARRIES_DAEMON = "daemon"
DEFAULT_INTERFACE_TYPE = "ethernet"
FORWARDING_ROLES = ("switch", "router")
BITRATE_SUFFIX = {"k": 10**3, "M": 10**6, "G": 10**9}

# (device id, interface name); the interface is None for an unspecified port of a forwarding device.
Node = Tuple[str, Optional[str]]
DeviceLookup = Callable[[str], DeviceConfig]


def parse_bitrate(value: Any) -> Optional[int]:
    """Capacity in bit/s of an integer or a suffixed number such as 500k or 10G."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    scale = BITRATE_SUFFIX.get(text[-1])
    return int(float(text[:-1]) * scale) if scale else int(float(text))


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


def component_units(system_config: SystemConfig) -> Dict[str, List[str]]:
    """Compute units each top-level component runs on, over all modes of the system."""
    mode_names, _ = select_modes(system_config)
    units: Dict[str, List[str]] = {}
    for mode_name in mode_names:
        mode_config = apply_mode_configuration(system_config, mode_name)
        for component in mode_config.components or []:
            if not isinstance(component, dict) or not component.get("name"):
                continue
            found = units.setdefault(component["name"], [])
            unit = component.get("compute_unit")
            if unit and unit not in found:
                found.append(unit)
    return units


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
    def __init__(self, architecture: EeArchitectureConfig, device_lookup: Optional[DeviceLookup] = None) -> None:
        self.architecture = architecture
        self.device_lookup = device_lookup
        self.source = format_source(source_from_config(architecture, None))
        self.networks: Dict[str, Optional[Any]] = {}
        self.daemon_networks: Set[str] = set()
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.environments: Dict[str, Dict[str, Any]] = {}
        self.device_networks: Dict[str, List[Tuple[str, Optional[str]]]] = {}
        self.addresses: List[str] = []
        self.connections: List[Dict[str, Any]] = []
        self.connections_of: Dict[Node, List[int]] = {}
        self.environment_of: Dict[str, Dict[str, Any]] = {}

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
            if CARRIES_DAEMON in (network.get("carries") or []):
                self.daemon_networks.add(network["name"])

    # ---- devices

    def _definition(self, name: str, where: str, stack: Tuple[str, ...]) -> DeviceConfig:
        if name in stack:
            raise self.fail(f"{where} is of device '{name}', which contains itself {list(stack)}")
        if self.device_lookup is None:
            raise self.fail(f"{where} is of device '{name}', and no device entities are available")
        try:
            return self.device_lookup(name)
        except ValidationError as error:
            raise self.fail(f"{where}: {error}") from None

    def _named(self, entries: Optional[List[Dict[str, Any]]], what: str, where: str) -> Dict[str, Dict[str, Any]]:
        entries = entries or []
        repeated = _duplicates(entry["name"] for entry in entries)
        if repeated:
            raise self.fail(f"{where} has duplicate {what}(s) {repeated}")
        return {entry["name"]: entry for entry in entries}

    def _configured(
        self, defined: Dict[str, Dict[str, Any]], given: Dict[str, Dict[str, Any]], what: str, where: str
    ) -> List[Dict[str, Any]]:
        """Entries of a device entity with the instance's settings of the same name laid over them."""
        unknown = sorted(set(given) - set(defined))
        if unknown:
            raise self.fail(f"{where} configures {what}(s) {unknown} its device does not have {sorted(defined)}")
        return [{**entry, **given.get(name, {})} for name, entry in defined.items()]

    def _attachments(self, interface: Dict[str, Any], where: str) -> List[Dict[str, Any]]:
        entries = list(interface.get("networks") or [])
        if "network" in interface:
            entries.insert(0, {"network": interface["network"], "address": interface.get("address")})
        elif "address" in interface:
            raise self.fail(f"{where} has an address and names no network")
        attachments = []
        for entry in entries:
            network = entry["network"]
            if network not in self.networks:
                raise self.fail(f"{where} is on undeclared network '{network}'")
            address = entry.get("address")
            if address is not None:
                address = self._address(address, where)
                subnet = self.networks[network]
                if subnet is not None and ipaddress.ip_address(address) not in subnet:
                    raise self.fail(f"{where} has address {address} outside {subnet} of network '{network}'")
                self.addresses.append(address)
            attachments.append({"network": network, "address": address})
        repeated = _duplicates(attachment["network"] for attachment in attachments)
        if repeated:
            raise self.fail(f"{where} is on network(s) {repeated} more than once")
        return attachments

    def _add_device(
        self,
        device_id: str,
        instance: Dict[str, Any],
        definition_name: Optional[str],
        parent: Optional[str],
        stack: Tuple[str, ...],
    ) -> None:
        """Resolved record of one device instance and, for a composite device, of its parts."""
        where = f"device '{device_id}'"
        given_interfaces = self._named(instance.get("interfaces"), "interface", where)
        given_environments = self._named(instance.get("environments"), "environment", where)
        given_parts = self._named(instance.get("parts"), "part", where)
        definition: Optional[DeviceConfig] = None
        if definition_name is None:
            interfaces = list(given_interfaces.values())
            environments = list(given_environments.values())
            role = instance.get("role") or ("ecu" if environments else "other")
            hardware = instance.get("hardware")
            if given_parts:
                raise self.fail(f"{where} configures parts and is of no device entity")
        else:
            definition = self._definition(definition_name, where, stack)
            for key in ("role", "hardware"):
                if key in instance:
                    raise self.fail(f"{where} takes its {key} from device '{definition_name}'")
            interfaces = self._configured(
                self._named(definition.interfaces, "interface", f"device '{definition_name}'"),
                given_interfaces,
                "interface",
                where,
            )
            environments = self._configured(
                self._named(definition.environments, "environment", f"device '{definition_name}'"),
                given_environments,
                "environment",
                where,
            )
            role, hardware = definition.role, definition.hardware

        record = {
            "id": device_id,
            "name": instance["name"],
            "parent": parent,
            "definition": definition_name,
            "role": role,
            "hardware": hardware,
            "description": instance.get("description")
            or (definition.config.get("description", "") if definition else ""),
            "interfaces": [],
            "environments": [],
        }
        self.devices[device_id] = record
        attached: List[Tuple[str, Optional[str]]] = []
        for interface in interfaces:
            attachments = self._attachments(interface, f"interface '{interface['name']}' of {where}")
            attached.extend((attachment["network"], attachment["address"]) for attachment in attachments)
            record["interfaces"].append(
                {
                    "name": interface["name"],
                    "type": interface.get("type", DEFAULT_INTERFACE_TYPE),
                    "bitrate": parse_bitrate(interface.get("bitrate")),
                    "description": interface.get("description", ""),
                    "attachments": attachments,
                }
            )
        self.device_networks[device_id] = attached

        for environment in environments:
            key = f"{device_id}/{environment['name']}"
            network = environment.get("network", NETWORK_HOST)
            address = environment.get("address")
            if network == NETWORK_OWN:
                if address is None:
                    raise self.fail(f"environment '{key}' has its own network and needs an address")
                address = self._address(address, f"environment '{key}'")
                self.addresses.append(address)
            elif address is not None:
                raise self.fail(f"environment '{key}' shares the device's network and takes no address")
            record["environments"].append(key)
            self.environments[key] = {
                "id": key,
                "device": device_id,
                "name": environment["name"],
                "kind": environment.get("kind", "host"),
                "network": network,
                "network_namespace": key if network == NETWORK_OWN else device_id,
                "address": address,
                "shared_memory": bool(environment.get("shared_memory", False)),
                "daemon_port": environment.get("daemon_port"),
                "provides": list(environment.get("provides") or []),
                "compute_units": [],
            }

        if definition is None:
            return
        parts = self._named(definition.parts, "part", f"device '{definition_name}'")
        unknown = sorted(set(given_parts) - set(parts))
        if unknown:
            raise self.fail(f"{where} configures part(s) {unknown} its device does not have {sorted(parts)}")
        for name, part in parts.items():
            self._add_device(
                f"{device_id}.{name}",
                {**given_parts.get(name, {}), "name": name, "description": part.get("description", "")},
                part["device"],
                device_id,
                (*stack, definition_name),
            )
        for connection in definition.connections or []:
            self._add_connection(connection, internal=device_id)

    def read_devices(self) -> None:
        devices = self.architecture.devices or []
        repeated = _duplicates(device["name"] for device in devices)
        if repeated:
            raise self.fail(f"duplicate device(s) {repeated}")
        for device in devices:
            self._add_device(device["name"], device, device.get("device"), None, ())
        repeated = _duplicates(self.addresses)
        if repeated:
            raise self.fail(f"address(es) {repeated} are assigned more than once")

    # ---- connections

    def _interface(self, node: Node) -> Optional[Dict[str, Any]]:
        device, name = node
        return next((i for i in self.devices[device]["interfaces"] if i["name"] == name), None)

    def _node(self, text: str, where: str, internal: Optional[str] = None, port_optional: bool = True) -> Node:
        """Interface an endpoint names; inside a device entity the endpoint is relative to its instance."""
        path = f"{internal}.{text}" if internal else text
        if internal is None and path in self.devices:
            if not port_optional or self.devices[path]["role"] not in FORWARDING_ROLES:
                raise self.fail(f"{where} names device '{path}' and no interface of it")
            return path, None
        device, _, name = path.rpartition(".")
        if device not in self.devices:
            raise self.fail(f"{where} names '{text}', which is no interface of a declared device")
        node = (device, name)
        if self._interface(node) is None:
            known = sorted(i["name"] for i in self.devices[device]["interfaces"])
            raise self.fail(f"{where} names interface '{name}', which device '{device}' does not have {known}")
        return node

    def _add_connection(self, connection: Dict[str, Any], internal: Optional[str] = None) -> None:
        kind = "link" if "link" in connection else "bus"
        index = len(self.connections)
        label = connection.get("name") or f"{kind} {connection[kind]}"
        where = f"connection '{label}'" + (f" inside device '{internal}'" if internal else "")
        nodes = [self._node(text, where, internal) for text in connection[kind]]
        for node in nodes:
            if node[1] is None:
                continue
            taken = [self.connections[i] for i in self.connections_of.get(node, [])]
            if any(other["internal"] == internal for other in taken):
                raise self.fail(f"{where} uses interface '{node[0]}.{node[1]}', which is connected already")
        interfaces = [self._interface(node) for node in nodes if node[1] is not None]
        types = sorted({interface["type"] for interface in interfaces})
        if len(types) > 1:
            raise self.fail(f"{where} joins interfaces of different types {types}")
        bitrate = parse_bitrate(connection.get("bitrate"))
        if bitrate is None:
            rates = [interface["bitrate"] for interface in interfaces if interface["bitrate"]]
            bitrate = min(rates) if rates else None
        self.connections.append(
            {
                "id": f"c{index}",
                "kind": kind,
                "name": connection.get("name"),
                "type": types[0] if types else DEFAULT_INTERFACE_TYPE,
                "bitrate": bitrate,
                "description": connection.get("description", ""),
                "internal": internal,
                "endpoints": [{"device": device, "interface": name} for device, name in nodes],
            }
        )
        for node in nodes:
            self.connections_of.setdefault(node, []).append(index)

    def read_connections(self) -> None:
        connections = self.architecture.connections or []
        repeated = _duplicates(c["name"] for c in connections if c.get("name"))
        if repeated:
            raise self.fail(f"duplicate connection(s) {repeated}")
        for connection in connections:
            self._add_connection(connection)

    @property
    def wired(self) -> bool:
        return bool(self.connections)

    def _exits(self, node: Node) -> List[Node]:
        """Interfaces a frame arriving at an interface may leave by: itself, and every port of a forwarding device."""
        device = node[0]
        if self.devices[device]["role"] not in FORWARDING_ROLES:
            return [node]
        return [other for other in self.connections_of if other[0] == device]

    def _path(self, sources: List[Node], reached: Callable[[Node], bool]) -> Optional[Tuple[Node, Node, List[str]]]:
        """Shortest chain of connections from one of the source interfaces to an interface that satisfies reached."""
        queue = deque((source, source, None, []) for source in sources)
        seen: Set[Node] = set(sources)
        while queue:
            source, node, via, path = queue.popleft()
            exits = [node] if via is None else self._exits(node)
            for exit_node in exits:
                for index in self.connections_of.get(exit_node, []):
                    if index == via:
                        continue
                    for other in self.connections[index]["endpoints"]:
                        target = (other["device"], other["interface"])
                        if target == exit_node or target in seen:
                            continue
                        seen.add(target)
                        step = [*path, self.connections[index]["id"]]
                        if reached(target):
                            return source, target, step
                        queue.append((source, target, index, step))
        return None

    def _terminal(self, node: Node) -> bool:
        """An interface that ends a path: one in a single connection, unlike a port wired on through its device."""
        return node[1] is not None and len(self.connections_of.get(node, [])) == 1

    # ---- bindings

    def bind(self, units: Dict[str, str], requirements: Dict[str, List[str]], system_name: str) -> None:
        bindings = [b for b in self.architecture.bindings or [] if "compute_unit" in b]
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
            self.environment_of[unit] = self.environments[key]
        bound = {binding["compute_unit"] for binding in bindings}
        unbound = [unit for unit, kind in units.items() if kind == KIND_ONBOARD and unit not in bound]
        if unbound:
            raise self.fail(f"onboard compute unit(s) {unbound} of system '{system_name}' are bound to no environment")

    def _shared_network(self, host: Dict[str, Any], peer: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """Network two interfaces are both on, and the host interface's address on it."""
        peer_networks = {attachment["network"] for attachment in peer["attachments"]}
        for attachment in host["attachments"]:
            if attachment["network"] in peer_networks:
                return attachment["network"], attachment["address"]
        return None, None

    def _hosts(self, unit: str, device: str, peripheral: str) -> List[Dict[str, Any]]:
        """Interfaces a compute unit's device reaches a peripheral's device by, one per reached interface of it.

        Of several interfaces that reach one, the one on a network the peripheral's interface is on is taken, then
        the one with the fewest connections on the way.
        """
        environment = self.environment_of[unit]
        host_device = environment["device"]
        own = self.devices[host_device]["interfaces"]
        pairs: List[Tuple[Dict[str, Any], Dict[str, Any], Optional[List[str]]]] = []
        if self.wired:
            for peer in self.devices[device]["interfaces"]:
                target = (device, peer["name"])
                if not self._terminal(target):
                    continue
                found = [
                    (candidate, self._path([(host_device, candidate["name"])], lambda node, t=target: node == t))
                    for candidate in own
                ]
                reaching = [(candidate, path[2]) for candidate, path in found if path is not None]
                if reaching:
                    best = min(reaching, key=lambda c: (self._shared_network(c[0], peer)[0] is None, len(c[1])))
                    pairs.append((best[0], peer, best[1]))
            if not pairs:
                raise self.fail(
                    f"peripheral '{peripheral}' is device '{device}', which no connection path joins to device "
                    f"'{host_device}' of compute unit '{unit}'"
                )
        else:
            pairs = [
                (candidate, peer, None)
                for peer in self.devices[device]["interfaces"]
                for candidate in own
                if self._shared_network(candidate, peer)[0] is not None
            ][:1]
        hosts = []
        for candidate, peer, path in pairs:
            network, address = self._shared_network(candidate, peer)
            hosts.append(
                {
                    "compute_unit": unit,
                    "environment": environment["id"],
                    "device": host_device,
                    "interface": candidate["name"],
                    "peer_interface": peer["name"],
                    "network": network,
                    "address": address,
                    "path": path,
                }
            )
        return hosts

    def bind_peripherals(
        self, peripherals: List[Dict[str, Any]], units_of: Dict[str, List[str]], system_name: str
    ) -> List[Dict[str, Any]]:
        """Device of every peripheral of the system, with the interfaces its component's units reach it by."""
        declared = self._named(peripherals, "peripheral", f"system '{system_name}'")
        bindings = [b for b in self.architecture.bindings or [] if "peripheral" in b]
        repeated = _duplicates(binding["peripheral"] for binding in bindings)
        if repeated:
            raise self.fail(f"peripheral(s) {repeated} are bound more than once")
        records = []
        for binding in bindings:
            name, device = binding["peripheral"], binding["device"]
            if name not in declared:
                raise self.fail(f"binds '{name}', which is no peripheral of system '{system_name}' {sorted(declared)}")
            if device not in self.devices:
                raise self.fail(f"binds peripheral '{name}' to undeclared device '{device}' {sorted(self.devices)}")
            component = declared[name].get("component")
            if component is not None and component not in units_of:
                raise self.fail(f"peripheral '{name}' names '{component}', which is no component of '{system_name}'")
            units = [unit for unit in units_of.get(component, []) if unit in self.environment_of]
            records.append(
                {
                    "name": name,
                    "device": device,
                    "component": component,
                    "description": declared[name].get("description", ""),
                    "addresses": [
                        {"interface": interface["name"], **attachment}
                        for interface in self.devices[device]["interfaces"]
                        for attachment in interface["attachments"]
                    ],
                    "hosts": [host for unit in units for host in self._hosts(unit, device, name)],
                }
            )
        unbound = sorted(set(declared) - {binding["peripheral"] for binding in bindings})
        if unbound:
            raise self.fail(f"peripheral(s) {unbound} of system '{system_name}' are bound to no device")
        return records

    # ---- time synchronization

    def time_sync(self) -> List[Dict[str, Any]]:
        """Domains with the connection path from the grandmaster to each member."""
        domains = self._named(self.architecture.time_sync, "time sync domain", "the architecture")
        member_of: Dict[Node, str] = {}
        records = []
        for name, domain in domains.items():
            where = f"time sync domain '{name}'"
            grandmaster = self._node(domain["grandmaster"], f"grandmaster of {where}", port_optional=False)
            members = []
            for text in domain.get("members") or []:
                node = self._node(text, f"member of {where}", port_optional=False)
                if node == grandmaster:
                    raise self.fail(f"{where} lists its grandmaster '{text}' as a member")
                if node in member_of:
                    raise self.fail(
                        f"interface '{text}' is a member of time sync domains '{member_of[node]}' and '{name}'"
                    )
                member_of[node] = name
                path = None
                if self.wired:
                    found = self._path([grandmaster], lambda reached, node=node: reached == node)
                    if found is None:
                        raise self.fail(
                            f"{where}: no connection path joins grandmaster '{domain['grandmaster']}' to '{text}'"
                        )
                    path = found[2]
                members.append({"device": node[0], "interface": node[1], "path": path})
            source = domain.get("source")
            if source is not None and "reference" in domain:
                raise self.fail(f"{where} has a source domain and takes no reference")
            records.append(
                {
                    "name": name,
                    "description": domain.get("description", ""),
                    "profile": domain.get("profile"),
                    "reference": domain.get("reference"),
                    "source": source,
                    "grandmaster": {"device": grandmaster[0], "interface": grandmaster[1]},
                    "members": members,
                }
            )
        for record in records:
            source, name = record["source"], record["name"]
            if source is None:
                continue
            if source not in domains:
                raise self.fail(f"time sync domain '{name}' has undeclared source domain '{source}' {sorted(domains)}")
            device = record["grandmaster"]["device"]
            if not any(member_of.get(node) == source for node in member_of if node[0] == device):
                raise self.fail(
                    f"time sync domain '{name}' takes its time from '{source}', of which device '{device}' has no "
                    f"member interface"
                )
            chain = [name]
            while source is not None:
                if source in chain:
                    raise self.fail(f"time sync domains {chain} take their time from each other")
                chain.append(source)
                source = domains[source].get("source")
        return records

    # ---- daemons

    def _reach_address(self, environment: Dict[str, Any], main_device: Optional[str]) -> Optional[str]:
        """Address the environment answers on: on a network that carries daemon traffic when one is declared,
        else preferring a network the main environment's device is on."""
        if environment["network"] == NETWORK_OWN:
            return environment["address"]
        attached = [(network, address) for network, address in self.device_networks[environment["device"]] if address]
        if self.daemon_networks:
            return next((address for network, address in attached if network in self.daemon_networks), None)
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


def resolve_ee_architecture(
    architecture: EeArchitectureConfig, system_config: SystemConfig, device_lookup: Optional[DeviceLookup] = None
) -> Dict[str, Any]:
    """Check an E/E architecture against a system and return its export record.

    Every onboard compute unit is bound to exactly one declared environment,
    which provides what the unit requires, and no offboard unit is bound. With units in several environments, each has a
    distinct daemon port and one reachable from the main environment. Every
    peripheral of the system is bound to one device. Once the architecture
    declares connections, a path of them joins each peripheral to the devices
    of its component's units and each time sync member to its grandmaster.

    device_lookup returns the device entity of a name, for instances that are of one.
    """
    resolver = _Resolver(architecture, device_lookup)
    resolver.read_networks()
    resolver.read_devices()
    resolver.read_connections()
    resolver.bind(system_units(system_config), system_requirements(system_config), system_config.name)
    peripherals = resolver.bind_peripherals(
        system_config.peripherals or [], component_units(system_config), system_config.name
    )
    time_sync = resolver.time_sync()
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
        "wiring": {"devices": list(resolver.devices.values()), "connections": resolver.connections},
        "time_sync": time_sync,
        "peripherals": peripherals,
    }
