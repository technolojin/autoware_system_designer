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


"""E/E architecture: checks of the description against a system, variants, and its export."""

import logging
from pathlib import Path

import pytest
from pipeline_harness import FIXTURES_DIR, run_pipeline, stage_case

from autoware_system_designer.builder.config.config_registry import ConfigRegistry
from autoware_system_designer.builder.ee_architecture import cross_unit_pairs, resolve_ee_architecture
from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.deploy import load_build_artifacts
from autoware_system_designer.parser.data_parser import ConfigParser

SYSTEM = FIXTURES_DIR / "compute_units" / "units_pkg" / "Multi.system.yaml"

HEAD = """\
autoware_system_design_format: 0.5.0

name: Bench.ee_architecture
"""

TWO_DEVICES = (
    HEAD
    + """
networks:
  - name: control
    subnet: 10.0.1.0/24
  - name: data
    subnet: 10.0.2.0/24

devices:
  - name: ecu_a
    interfaces:
      - {name: eth0, network: control, address: 10.0.1.1}
    environments:
      - {name: host, daemon_port: 8781}
  - name: ecu_b
    interfaces:
      - {name: eth0, network: data, address: 10.0.2.2}
      - {name: eth1, network: control, address: 10.0.1.2}
    environments:
      - {name: host, daemon_port: 8782}

main_environment: ecu_a/host

bindings:
  - {compute_unit: main_ecu, environment: ecu_a/host}
  - {compute_unit: sub_ecu, environment: ecu_b/host}
"""
)

ONE_DEVICE = (
    HEAD
    + """
devices:
  - name: ecu
    environments:
      - {name: main, kind: container, shared_memory: %(main)s, daemon_port: 8781}
      - {name: sub, kind: container, shared_memory: %(sub)s, daemon_port: 8782}

main_environment: ecu/main

bindings:
  - {compute_unit: main_ecu, environment: ecu/main}
  - {compute_unit: sub_ecu, environment: ecu/sub}
"""
)


def _system():
    return ConfigParser().parse_entity_file(str(SYSTEM))


def _architecture(tmp_path: Path, text: str, name: str = "Bench"):
    path = tmp_path / f"{name}.ee_architecture.yaml"
    path.write_text(text)
    return ConfigParser().parse_entity_file(str(path))


def _resolve(tmp_path: Path, text: str) -> dict:
    return resolve_ee_architecture(_architecture(tmp_path, text), _system())


def _rejected(tmp_path: Path, text: str) -> str:
    with pytest.raises(ValidationError) as excinfo:
        _resolve(tmp_path, text)
    return str(excinfo.value)


def test_environments_carry_units_namespace_and_reach_address(tmp_path):
    record = _resolve(tmp_path, TWO_DEVICES)
    assert record["name"] == "Bench"
    assert record["main_environment"] == "ecu_a/host"
    environments = {environment["id"]: environment for environment in record["environments"]}
    assert environments["ecu_a/host"]["compute_units"] == ["main_ecu"]
    assert environments["ecu_b/host"]["compute_units"] == ["sub_ecu"]
    assert environments["ecu_b/host"]["network_namespace"] == "ecu_b"
    # The address on the network the main environment's device is on, not the first interface's.
    assert environments["ecu_b/host"]["address"] == "10.0.1.2"
    assert environments["ecu_b/host"]["kind"] == "host"
    assert environments["ecu_b/host"]["shared_memory"] is False


def test_environment_with_its_own_network_is_its_own_namespace(tmp_path):
    text = TWO_DEVICES.replace(
        "      - {name: host, daemon_port: 8782}",
        "      - {name: host, daemon_port: 8782}\n"
        "      - {name: box, kind: container, network: own, address: 10.0.1.3, daemon_port: 8783}",
    ).replace("environment: ecu_b/host", "environment: ecu_b/box")
    environments = {environment["id"]: environment for environment in _resolve(tmp_path, text)["environments"]}
    assert environments["ecu_b/box"]["network_namespace"] == "ecu_b/box"
    assert environments["ecu_b/box"]["address"] == "10.0.1.3"
    assert environments["ecu_b/box"]["compute_units"] == ["sub_ecu"]
    assert environments["ecu_b/host"]["compute_units"] == []


@pytest.mark.parametrize(
    "old, new, expected",
    [
        ("  - {compute_unit: sub_ecu, environment: ecu_b/host}\n", "", "['sub_ecu']"),
        ("compute_unit: sub_ecu", "compute_unit: remote_ecu", "offboard"),
        ("compute_unit: sub_ecu", "compute_unit: ghost_ecu", "no compute unit"),
        ("compute_unit: sub_ecu", "compute_unit: main_ecu", "bound more than once"),
        ("environment: ecu_b/host", "environment: ecu_b/guest", "undeclared environment"),
        ("main_environment: ecu_a/host\n", "", "main_environment"),
        ("main_environment: ecu_a/host", "main_environment: ecu_c/host", "not declared"),
        ("daemon_port: 8782", "kind: host", "daemon_port"),
        ("daemon_port: 8782", "daemon_port: 8781", "more than one environment"),
        ("network: data", "network: backbone", "undeclared network"),
        ("address: 10.0.2.2", "address: 10.0.3.2", "outside"),
        ("address: 10.0.1.2", "address: 10.0.1.1", "more than once"),
        ("{name: host, daemon_port: 8782}", "{name: host, network: own, daemon_port: 8782}", "needs an address"),
        ("{name: host, daemon_port: 8782}", "{name: host, address: 10.0.1.9, daemon_port: 8782}", "takes no address"),
        ("  - name: ecu_b\n", "  - name: ecu_a\n", "duplicate device"),
    ],
)
def test_rejected_descriptions(tmp_path, old, new, expected):
    assert old in TWO_DEVICES
    assert expected in _rejected(tmp_path, TWO_DEVICES.replace(old, new))


def test_remote_environment_needs_an_address(tmp_path):
    text = TWO_DEVICES.replace(", address: 10.0.2.2", "").replace(", address: 10.0.1.2", "")
    assert "no address" in _rejected(tmp_path, text)


def test_one_environment_needs_no_daemon_port_or_main(tmp_path):
    text = (
        HEAD
        + """
devices:
  - name: pc
    environments:
      - name: host

bindings:
  - {compute_unit: main_ecu, environment: pc/host}
  - {compute_unit: sub_ecu, environment: pc/host}
"""
    )
    record = _resolve(tmp_path, text)
    assert record["main_environment"] is None
    assert record["environments"][0]["compute_units"] == ["main_ecu", "sub_ecu"]


def _shared_memory_warnings(tmp_path: Path, text: str) -> list:
    """Shared memory warnings of a resolution, read off the module's logger."""
    records = []
    handler = logging.Handler(logging.WARNING)
    handler.emit = records.append
    logger = logging.getLogger("autoware_system_designer.builder.ee_architecture")
    logger.addHandler(handler)
    try:
        _resolve(tmp_path, text)
    finally:
        logger.removeHandler(handler)
    return [record.getMessage() for record in records if "shared memory" in record.getMessage()]


def test_connections_cross_onboard_and_offboard_units():
    assert cross_unit_pairs(_system()) == {("main_ecu", "sub_ecu"), ("main_ecu", "remote_ecu")}


@pytest.mark.parametrize(
    "main, sub, warned", [("true", "true", False), ("true", "false", True), ("false", "false", True)]
)
def test_connection_within_a_device_without_shared_memory_warns(tmp_path, main, sub, warned):
    messages = _shared_memory_warnings(tmp_path, ONE_DEVICE % {"main": main, "sub": sub})
    assert bool(messages) == warned
    if warned:
        assert "'main_ecu' (ecu/main)" in messages[0] and "'sub_ecu' (ecu/sub)" in messages[0]


def test_connection_between_devices_does_not_warn(tmp_path):
    assert _shared_memory_warnings(tmp_path, TWO_DEVICES) == []


VARIANT = """\
autoware_system_design_format: 0.5.0

name: Bench_lite.ee_architecture

base: Bench.ee_architecture

remove:
  devices:
    - name: ecu_b

override:
  devices:
    - name: ecu_a
      interfaces:
        - {name: mgbe0, network: control, address: 10.0.1.1}
      environments:
        - {name: host, daemon_port: 8781}
        - {name: box, kind: container, daemon_port: 8782}
  bindings:
    - {compute_unit: sub_ecu, environment: ecu_a/box}
"""


def test_variant_replaces_devices_and_bindings_by_name(tmp_path):
    base = tmp_path / "Bench.ee_architecture.yaml"
    base.write_text(TWO_DEVICES)
    variant = tmp_path / "Bench_lite.ee_architecture.yaml"
    variant.write_text(VARIANT)
    registry = ConfigRegistry([str(base), str(variant)])

    architecture = registry.get_ee_architecture("Bench_lite.ee_architecture")
    assert [device["name"] for device in architecture.devices] == ["ecu_a"]
    assert architecture.main_environment == "ecu_a/host"
    record = resolve_ee_architecture(architecture, _system())
    assert record["name"] == "Bench_lite"
    assert {e["id"]: e["compute_units"] for e in record["environments"]} == {
        "ecu_a/host": ["main_ecu"],
        "ecu_a/box": ["sub_ecu"],
    }
    # The base is left as written.
    assert [device["name"] for device in registry.get_ee_architecture("Bench").devices] == ["ecu_a", "ecu_b"]


def test_variant_keeps_fields_under_override(tmp_path):
    text = VARIANT.replace("override:\n  devices:", "devices:").replace("\n  bindings:", "\noverride:\n  bindings:")
    path = tmp_path / "Bench_lite.ee_architecture.yaml"
    path.write_text(text)
    with pytest.raises(ValidationError) as excinfo:
        ConfigParser().parse_entity_file(str(path))
    assert "must be under 'override'" in str(excinfo.value) or "Schema validation failed" in str(excinfo.value)


def _stage_with_deployment(tmp_path: Path, architectures: str) -> Path:
    workspace = stage_case("compute_units", tmp_path)
    package = workspace / "units_pkg"
    (package / "Bench.ee_architecture.yaml").write_text(TWO_DEVICES)
    (package / "multi.deployments.yaml").write_text(
        "autoware_system_design_format: 0.5.0\n\nbase: Multi.system\n\n"
        f"ee_architectures: {architectures}\n\ndeploy_list:\n  - name: bench\n"
    )
    return workspace


def test_export_carries_the_architectures_of_the_deployment(tmp_path):
    workspace = _stage_with_deployment(tmp_path, "[Bench.ee_architecture]")
    run = run_pipeline(workspace, "units_pkg/multi.deployments.yaml", tmp_path)

    artifacts = load_build_artifacts(str(run.out_root), run.system_name)
    assert [record["name"] for record in artifacts.ee_architectures] == ["Bench"]
    assert [e["compute_units"] for e in artifacts.ee_architectures[0]["environments"]] == [["main_ecu"], ["sub_ecu"]]


def test_system_only_target_exports_no_architecture(tmp_path):
    workspace = _stage_with_deployment(tmp_path, "[Bench.ee_architecture]")
    run = run_pipeline(workspace, "units_pkg/Multi.system.yaml", tmp_path)
    assert load_build_artifacts(str(run.out_root), run.system_name).ee_architectures == []


def test_deployment_naming_an_unknown_architecture_fails(tmp_path):
    workspace = _stage_with_deployment(tmp_path, "[Ghost.ee_architecture]")
    with pytest.raises(Exception) as excinfo:
        run_pipeline(workspace, "units_pkg/multi.deployments.yaml", tmp_path)
    assert "Ghost" in str(excinfo.value)


def _system_requiring(tmp_path: Path, unit: str, capability: str):
    text = SYSTEM.read_text().replace(f"  - name: {unit}\n", f"  - name: {unit}\n    requires: [{capability}]\n", 1)
    assert capability in text
    path = tmp_path / SYSTEM.name
    path.write_text(text)
    return ConfigParser().parse_entity_file(str(path))


def test_binding_needs_an_environment_providing_what_the_unit_requires(tmp_path):
    system = _system_requiring(tmp_path, "sub_ecu", "gpu")
    with pytest.raises(ValidationError) as excinfo:
        resolve_ee_architecture(_architecture(tmp_path, TWO_DEVICES), system)
    assert "binds 'sub_ecu' to environment 'ecu_b/host', which does not provide ['gpu']" in str(excinfo.value)

    provided = TWO_DEVICES.replace(
        "{name: host, daemon_port: 8782}", "{name: host, daemon_port: 8782, provides: [gpu]}"
    )
    record = resolve_ee_architecture(_architecture(tmp_path, provided), system)
    assert {environment["id"]: environment["provides"] for environment in record["environments"]} == {
        "ecu_a/host": [],
        "ecu_b/host": ["gpu"],
    }


DEVICE_HEAD = "autoware_system_design_format: 0.5.0\n\nname: %s.device\n"

SWITCH_DEVICE = (
    DEVICE_HEAD % "Switch"
    + """
role: switch
interfaces:
  - {name: p0, bitrate: 10G}
  - {name: p1, bitrate: 10G}
  - {name: p2, bitrate: 10G}
"""
)

BOARD_DEVICE = (
    DEVICE_HEAD % "Board"
    + """
role: ecu
hardware: x86_64
interfaces:
  - {name: eth0, bitrate: 1G}
  - {name: can0, type: can}
environments:
  - {name: host, provides: [gpu]}
parts:
  - {name: switch, device: Switch.device}
connections:
  - link: [eth0, switch.p0]
"""
)

WIRED = (
    HEAD
    + """
networks:
  - {name: control, subnet: 10.0.1.0/24, carries: [daemon]}
  - {name: sensor, subnet: 10.0.3.0/24}

devices:
  - name: ecu_a
    device: Board.device
    interfaces:
      - name: eth0
        networks:
          - {network: sensor, address: 10.0.3.1}
          - {network: control, address: 10.0.1.1}
    environments:
      - {name: host, daemon_port: 8781}
  - name: ecu_b
    interfaces:
      - {name: eth0, network: sensor, address: 10.0.3.2}
      - {name: eth1, network: control, address: 10.0.1.2}
      - {name: can0, type: can}
    environments:
      - {name: host, daemon_port: 8782}
  - name: lidar
    role: sensor
    interfaces:
      - {name: eth, network: sensor, address: 10.0.3.9}
  - name: vcu
    role: actuator
    interfaces:
      - {name: can, type: can}

connections:
  - link: [ecu_a.switch.p1, lidar.eth]
  - link: [ecu_a.switch.p2, ecu_b.eth0]
  - name: vehicle_can
    bus: [ecu_a.can0, ecu_b.can0, vcu.can]
    bitrate: 500k

time_sync:
  - name: ecus
    profile: ptpv2
    reference: ntp
    grandmaster: ecu_a.eth0
    members: [ecu_b.eth0, lidar.eth]

main_environment: ecu_a/host

bindings:
  - {compute_unit: main_ecu, environment: ecu_a/host}
  - {compute_unit: sub_ecu, environment: ecu_b/host}
  - {peripheral: lidar_front, device: lidar}
  - {peripheral: vcu, device: vcu}
"""
)

PERIPHERALS = """
peripherals:
  - {name: lidar_front, component: talker}
  - {name: vcu, component: listener}
"""


def _device_lookup(tmp_path: Path):
    devices = {}
    for name, text in (("Switch", SWITCH_DEVICE), ("Board", BOARD_DEVICE)):
        path = tmp_path / f"{name}.device.yaml"
        path.write_text(text)
        devices[f"{name}.device"] = ConfigParser().parse_entity_file(str(path))

    def lookup(name: str):
        if name not in devices:
            raise ValidationError(f"Device '{name}' not found")
        return devices[name]

    return lookup


def _system_with_peripherals(tmp_path: Path, peripherals: str = PERIPHERALS):
    path = tmp_path / SYSTEM.name
    path.write_text(SYSTEM.read_text().replace("\ncomponents:\n", f"{peripherals}\ncomponents:\n", 1))
    return ConfigParser().parse_entity_file(str(path))


def _resolve_wired(tmp_path: Path, text: str = WIRED, peripherals: str = PERIPHERALS) -> dict:
    system = _system_with_peripherals(tmp_path, peripherals)
    return resolve_ee_architecture(_architecture(tmp_path, text), system, _device_lookup(tmp_path))


def _wired_rejected(tmp_path: Path, text: str, peripherals: str = PERIPHERALS) -> str:
    with pytest.raises(ValidationError) as excinfo:
        _resolve_wired(tmp_path, text, peripherals)
    return str(excinfo.value)


def test_device_entity_expands_into_its_parts_and_internal_connections(tmp_path):
    wiring = _resolve_wired(tmp_path)["wiring"]
    devices = {device["id"]: device for device in wiring["devices"]}
    assert list(devices) == ["ecu_a", "ecu_a.switch", "ecu_b", "lidar", "vcu"]
    assert devices["ecu_a"]["definition"] == "Board.device"
    assert devices["ecu_a"]["hardware"] == "x86_64"
    assert devices["ecu_a"]["environments"] == ["ecu_a/host"]
    assert devices["ecu_a.switch"]["parent"] == "ecu_a"
    assert devices["ecu_a.switch"]["role"] == "switch"
    assert devices["ecu_b"]["role"] == "ecu"
    eth0 = devices["ecu_a"]["interfaces"][0]
    assert eth0["bitrate"] == 10**9
    assert eth0["attachments"] == [
        {"network": "sensor", "address": "10.0.3.1"},
        {"network": "control", "address": "10.0.1.1"},
    ]

    connections = wiring["connections"]
    assert [c["id"] for c in connections] == ["c0", "c1", "c2", "c3"]
    assert connections[0]["internal"] == "ecu_a"
    assert connections[0]["endpoints"] == [
        {"device": "ecu_a", "interface": "eth0"},
        {"device": "ecu_a.switch", "interface": "p0"},
    ]
    # The slower end of a link bounds it.
    assert connections[0]["bitrate"] == 10**9
    assert (connections[3]["kind"], connections[3]["type"], connections[3]["bitrate"]) == ("bus", "can", 500000)


def test_peripheral_is_reached_through_the_wiring_from_its_components_unit(tmp_path):
    peripherals = {p["name"]: p for p in _resolve_wired(tmp_path)["peripherals"]}
    lidar = peripherals["lidar_front"]
    assert lidar["device"] == "lidar"
    assert lidar["addresses"] == [{"interface": "eth", "network": "sensor", "address": "10.0.3.9"}]
    assert lidar["hosts"] == [
        {
            "compute_unit": "main_ecu",
            "environment": "ecu_a/host",
            "device": "ecu_a",
            "interface": "eth0",
            "peer_interface": "eth",
            "network": "sensor",
            "address": "10.0.3.1",
            "path": ["c0", "c1"],
        }
    ]
    vcu = peripherals["vcu"]["hosts"][0]
    assert (vcu["compute_unit"], vcu["interface"], vcu["peer_interface"]) == ("sub_ecu", "can0", "can")
    assert (vcu["network"], vcu["address"], vcu["path"]) == (None, None, ["c3"])


def test_unwired_architecture_matches_a_peripheral_by_shared_network(tmp_path):
    text = WIRED[: WIRED.index("connections:")] + WIRED[WIRED.index("main_environment:") :]
    text = text.replace("    device: Board.device\n", "").replace(
        "      - name: eth0\n        networks:", "      - name: eth0\n        networks:"
    )
    record = _resolve_wired(
        tmp_path, text.replace("  - {peripheral: vcu, device: vcu}\n", ""), PERIPHERALS.split("  - {name: vcu")[0]
    )
    host = record["peripherals"][0]["hosts"][0]
    assert (host["interface"], host["address"], host["path"]) == ("eth0", "10.0.3.1", None)
    assert record["wiring"]["connections"] == []


def test_time_sync_members_carry_the_path_from_the_grandmaster(tmp_path):
    (domain,) = _resolve_wired(tmp_path)["time_sync"]
    assert domain["grandmaster"] == {"device": "ecu_a", "interface": "eth0"}
    assert domain["members"] == [
        {"device": "ecu_b", "interface": "eth0", "path": ["c0", "c2"]},
        {"device": "lidar", "interface": "eth", "path": ["c0", "c1"]},
    ]
    assert (domain["profile"], domain["reference"], domain["source"]) == ("ptpv2", "ntp", None)


CHAINED = """
  - name: can_side
    source: ecus
    grandmaster: ecu_b.can0
    members: [vcu.can]
"""


def test_time_sync_domain_takes_its_time_from_a_domain_its_grandmaster_is_in(tmp_path):
    text = WIRED.replace("\nmain_environment:", CHAINED + "\nmain_environment:", 1)
    domains = {domain["name"]: domain for domain in _resolve_wired(tmp_path, text)["time_sync"]}
    assert domains["can_side"]["source"] == "ecus"
    assert domains["can_side"]["members"] == [{"device": "vcu", "interface": "can", "path": ["c3"]}]

    assert "has no member interface" in _wired_rejected(
        tmp_path, text.replace("members: [ecu_b.eth0, lidar.eth]", "members: [lidar.eth]")
    )
    assert "undeclared source domain" in _wired_rejected(tmp_path, text.replace("source: ecus", "source: ghost"))


@pytest.mark.parametrize(
    "old, new, expected",
    [
        ("  - {peripheral: vcu, device: vcu}\n", "", "peripheral(s) ['vcu']"),
        ("peripheral: vcu, device: vcu", "peripheral: horn, device: vcu", "no peripheral of system"),
        ("peripheral: vcu, device: vcu", "peripheral: vcu, device: ghost", "undeclared device 'ghost'"),
        ("peripheral: vcu, device: vcu", "peripheral: lidar_front, device: vcu", "bound more than once"),
        ("  - link: [ecu_a.switch.p1, lidar.eth]\n", "", "no connection path joins"),
        ("link: [ecu_a.switch.p1, lidar.eth]", "link: [ecu_a.switch.p1, vcu.can]", "different types"),
        ("link: [ecu_a.switch.p2, ecu_b.eth0]", "link: [ecu_a.switch.p1, ecu_b.eth0]", "connected already"),
        ("link: [ecu_a.switch.p2, ecu_b.eth0]", "link: [ecu_a.switch.p9, ecu_b.eth0]", "does not have"),
        ("link: [ecu_a.switch.p2, ecu_b.eth0]", "link: [ecu_a.switch.p2, ecu_b]", "no interface of it"),
        (
            "link: [ecu_a.switch.p2, ecu_b.eth0]",
            "link: [ecu_a.switch.p2, ghost.eth0]",
            "no interface of a declared device",
        ),
        ("    device: Board.device\n", "    device: Ghost.device\n", "Ghost.device"),
        (
            "      - {name: host, daemon_port: 8781}",
            "      - {name: guest, daemon_port: 8781}",
            "does not have ['host']",
        ),
        ("members: [ecu_b.eth0, lidar.eth]", "members: [ecu_b.eth1]", "no connection path joins grandmaster"),
        ("members: [ecu_b.eth0, lidar.eth]", "members: [ecu_a.eth0]", "as a member"),
        ("reference: ntp", "reference: ntp\n    source: ecus", "takes no reference"),
    ],
)
def test_wiring_rules(tmp_path, old, new, expected):
    assert old in WIRED
    assert expected in _wired_rejected(tmp_path, WIRED.replace(old, new, 1))


def test_peripheral_of_an_unknown_component_is_rejected(tmp_path):
    peripherals = PERIPHERALS.replace("component: listener", "component: ghost")
    assert "no component of" in _wired_rejected(tmp_path, WIRED, peripherals)


def test_any_port_of_a_switch_may_be_left_unnamed(tmp_path):
    text = WIRED.replace("  - name: vcu\n", "  - name: hub\n    role: switch\n  - name: vcu\n", 1).replace(
        "link: [ecu_a.switch.p2, ecu_b.eth0]", "link: [ecu_a.switch.p2, hub]\n  - link: [hub, ecu_b.eth0]"
    )
    record = _resolve_wired(tmp_path, text)
    assert record["wiring"]["connections"][2]["endpoints"][1] == {"device": "hub", "interface": None}
    assert record["time_sync"][0]["members"][0]["path"] == ["c0", "c2", "c3"]


def test_daemons_answer_on_the_network_that_carries_daemon_traffic(tmp_path):
    environments = {e["id"]: e for e in _resolve_wired(tmp_path)["environments"]}
    # eth0 on the sensor network comes first on both devices.
    assert environments["ecu_a/host"]["address"] == "10.0.1.1"
    assert environments["ecu_b/host"]["address"] == "10.0.1.2"

    text = WIRED.replace("      - {name: eth1, network: control, address: 10.0.1.2}\n", "")
    assert "has no address" in _wired_rejected(tmp_path, text)


def test_variant_rebinds_a_peripheral_by_its_name(tmp_path):
    packages = tmp_path / "designs"
    packages.mkdir()
    base = packages / "Bench.ee_architecture.yaml"
    base.write_text(
        WIRED.replace(
            "  - name: vcu\n",
            "  - name: spare\n    interfaces:\n      - {name: eth, network: sensor}\n  - name: vcu\n",
            1,
        )
    )
    variant = packages / "BenchSpare.ee_architecture.yaml"
    variant.write_text(
        "autoware_system_design_format: 0.5.0\n\nname: BenchSpare.ee_architecture\n\nbase: Bench.ee_architecture\n\n"
        "override:\n  connections:\n    - link: [ecu_a.switch.p1, spare.eth]\n      name: spare_link\n"
        "  bindings:\n    - {peripheral: lidar_front, device: spare}\n"
        "remove:\n  time_sync:\n    - name: ecus\n"
    )
    registry = ConfigRegistry([str(base), str(variant)])
    architecture = registry.get_ee_architecture("BenchSpare.ee_architecture")
    assert [b for b in architecture.bindings if "peripheral" in b] == [
        {"peripheral": "lidar_front", "device": "spare"},
        {"peripheral": "vcu", "device": "vcu"},
    ]
    assert architecture.time_sync == []


def test_export_resolves_device_entities_of_the_workspace(tmp_path):
    workspace = stage_case("compute_units", tmp_path)
    package = workspace / "units_pkg"
    (package / "Switch.device.yaml").write_text(SWITCH_DEVICE)
    (package / "Board.device.yaml").write_text(BOARD_DEVICE)
    (package / "Bench.ee_architecture.yaml").write_text(WIRED)
    system = package / "Multi.system.yaml"
    system.write_text(system.read_text().replace("\ncomponents:\n", f"{PERIPHERALS}\ncomponents:\n", 1))
    (package / "multi.deployments.yaml").write_text(
        "autoware_system_design_format: 0.5.0\n\nbase: Multi.system\n\n"
        "ee_architectures: [Bench.ee_architecture]\n\ndeploy_list:\n  - name: bench\n"
    )
    run = run_pipeline(workspace, "units_pkg/multi.deployments.yaml", tmp_path)

    (record,) = load_build_artifacts(str(run.out_root), run.system_name).ee_architectures
    assert [device["id"] for device in record["wiring"]["devices"]][:2] == ["ecu_a", "ecu_a.switch"]
    assert record["peripherals"][0]["hosts"][0]["path"] == ["c0", "c1"]
    assert record["time_sync"][0]["name"] == "ecus"
