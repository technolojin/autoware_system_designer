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

TWO_DEVICES = HEAD + """
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

ONE_DEVICE = HEAD + """
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
    text = HEAD + """
devices:
  - name: pc
    environments:
      - name: host

bindings:
  - {compute_unit: main_ecu, environment: pc/host}
  - {compute_unit: sub_ecu, environment: pc/host}
"""
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
