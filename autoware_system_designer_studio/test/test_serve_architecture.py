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


"""A daemon's units, port and peers from an E/E architecture and its environment."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoware_system_designer_studio import cli
from autoware_system_designer_studio.serve import architecture, peers
from autoware_system_designer_studio.serve.peers import PeerSpec


def _environment(key, units, port, *, namespace=None, address=None):
    device, name = key.split("/")
    return {
        "id": key,
        "device": device,
        "name": name,
        "network_namespace": namespace or device,
        "address": address,
        "daemon_port": port,
        "compute_units": units,
    }


# ecu_a runs two environments on its own network; ecu_b is another device; ecu_a/box has its own network.
RECORD = {
    "name": "Bench",
    "main_environment": "ecu_a/main",
    "environments": [
        _environment("ecu_a/main", ["main_ecu"], 8781, address="10.0.1.1"),
        _environment("ecu_a/sub", ["sub_ecu_1"], 8782, address="10.0.1.1"),
        _environment("ecu_a/box", ["sub_ecu_2"], 8783, namespace="ecu_a/box", address="10.0.1.3"),
        _environment("ecu_b/host", ["sub_ecu_3"], 8784, address="10.0.1.2"),
        _environment("ecu_b/idle", [], None, address="10.0.1.2"),
    ],
}


def _system(*records):
    return SimpleNamespace(manifest={"ee_architectures": list(records)})


def test_main_environment_serves_the_others_as_peers():
    binding = architecture.resolve([_system(RECORD)], "Bench.ee_architecture", "ecu_a/main")
    assert (binding.architecture, binding.environment, binding.main, binding.port) == (
        "Bench",
        "ecu_a/main",
        True,
        8781,
    )
    assert binding.units == ("main_ecu",)
    assert list(binding.peers) == [
        # Same network namespace: the peer's loopback port is this daemon's loopback port.
        PeerSpec("sub_ecu_1", "http://127.0.0.1:8782"),
        # Its own network on the same device, and another device: forwarded from the address.
        PeerSpec("sub_ecu_2", "http://127.0.0.1:8783", "10.0.1.3"),
        PeerSpec("sub_ecu_3", "http://127.0.0.1:8784", "10.0.1.2"),
    ]
    peers.validate_specs(binding.peers)
    assert binding.to_view()["peers"][1] == {"unit": "sub_ecu_2", "url": "http://127.0.0.1:8783", "ssh": "10.0.1.3"}


def test_other_environment_serves_its_units_alone():
    binding = architecture.resolve([_system(RECORD)], "Bench", "ecu_b/host")
    assert (binding.main, binding.port, binding.units, binding.peers) == (False, 8784, ("sub_ecu_3",), ())


def test_units_of_every_system_declared_for_the_architecture():
    other = {
        "name": "Bench",
        "main_environment": "ecu_a/main",
        "environments": [
            _environment("ecu_a/main", ["front_ecu"], 8781, address="10.0.1.1"),
            _environment("ecu_b/host", ["rear_ecu"], 8784, address="10.0.1.2"),
        ],
    }
    binding = architecture.resolve([_system(RECORD), _system(other), _system()], "Bench", "ecu_a/main")
    assert binding.units == ("main_ecu", "front_ecu")
    assert {peer.unit for peer in binding.peers} == {"sub_ecu_1", "sub_ecu_2", "sub_ecu_3", "rear_ecu"}


def test_unknown_architecture_or_environment_is_refused():
    with pytest.raises(architecture.ArchitectureError, match="found: \\['Bench'\\]"):
        architecture.resolve([_system(RECORD)], "Other", "ecu_a/main")
    with pytest.raises(architecture.ArchitectureError, match="no environment 'ecu_c/host'"):
        architecture.resolve([_system(RECORD)], "Bench", "ecu_c/host")


def _install(tmp_path: Path) -> Path:
    export = tmp_path / "install" / "pkg" / "share" / "pkg" / "exports" / "Probe"
    export.mkdir(parents=True)
    (export / "deployment.json").write_text(json.dumps({"mode_keys": ["default"], "ee_architectures": [RECORD]}))
    return tmp_path / "install"


def test_environment_command_prints_the_binding(tmp_path, capsys):
    install = _install(tmp_path)
    cli.main(
        ["environment", "--install-root", str(install), "--ee-architecture", "Bench", "--environment", "ecu_a/sub"]
    )
    view = json.loads(capsys.readouterr().out)
    assert view == {
        "name": "Bench",
        "environment": "ecu_a/sub",
        "main": False,
        "port": 8782,
        "compute_units": ["sub_ecu_1"],
        "peers": [],
    }
    with pytest.raises(SystemExit, match="no environment"):
        cli.main(["environment", "--install-root", str(install), "--ee-architecture", "Bench", "--environment", "x/y"])


def test_serve_takes_the_environment_from_flags_or_the_site_file(tmp_path, monkeypatch):
    monkeypatch.delenv(peers.SITE_FILE_ENV, raising=False)
    install = _install(tmp_path)
    args = cli._parse_args(["serve", "--ee-architecture", "Bench", "--environment", "ecu_a/main"])
    assert (args.ee_architecture, args.environment, args.port) == ("Bench", "ecu_a/main", None)

    site = tmp_path / "vehicle.site.yaml"
    site.write_text("ee_architecture: Bench\nenvironment: ecu_a/main\npeers:\n  sub_ecu_3: http://127.0.0.1:9000\n")
    args = cli._parse_args(["serve", "--site-file", str(site), "--peer-ssh", "sub_ecu_1=ecu-a-sub"])
    assert (args.ee_architecture, args.environment) == ("Bench", "ecu_a/main")
    binding = cli._environment_binding(args, install)
    # The site file's binding and the flag's destination replace the architecture's, unit by unit.
    assert cli._peer_specs(args, binding.peers, binding.units, binding.port) == [
        PeerSpec("sub_ecu_1", "http://127.0.0.1:8782", "ecu-a-sub"),
        PeerSpec("sub_ecu_2", "http://127.0.0.1:8783", "10.0.1.3"),
        PeerSpec("sub_ecu_3", "http://127.0.0.1:9000"),
    ]

    with pytest.raises(SystemExit):
        cli._parse_args(["serve", "--ee-architecture", "Bench"])
    site.write_text("ee_architecture: Bench\n")
    with pytest.raises(SystemExit):
        cli._parse_args(["serve", "--site-file", str(site)])


def test_environment_command_prints_the_architecture_without_an_environment(tmp_path, capsys):
    install = _install(tmp_path)
    cli.main(["environment", "--install-root", str(install), "--ee-architecture", "Bench"])
    view = json.loads(capsys.readouterr().out)
    assert (view["name"], view["main_environment"]) == ("Bench", "ecu_a/main")
    assert {environment["id"]: environment["compute_units"] for environment in view["environments"]} == {
        environment["id"]: environment["compute_units"] for environment in RECORD["environments"]
    }
    with pytest.raises(SystemExit, match="no exported system is declared"):
        cli.main(["environment", "--install-root", str(install), "--ee-architecture", "Other"])
