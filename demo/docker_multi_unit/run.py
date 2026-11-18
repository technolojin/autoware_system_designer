#!/usr/bin/env python3
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

"""Multi-unit environment on Docker: one studio daemon per execution environment of an E/E architecture.

An architecture is an E/E architecture description of ASD_SYSTEM, e.g. TypeAlphaHost, TypeAlphaBridge.
The workspace is built in a container, the architecture is read from its export, and a Compose project
standing in for the hardware is derived from it:

- a device is a container holding a network namespace, with the device's addresses and an sshd;
- an environment on the device's network joins that namespace, one with its own network gets its own;
- environments of a device that share memory share an IPC namespace;
- one device is this machine, the only device of an architecture or the one ASD_HOST_DEVICE names:
  its containers use the host network, its environment of kind host is a daemon started here from
  the install tree ASD_NATIVE_INSTALL, and its address is this machine's on the bridge network;
- an environment that provides "gpu" is given the GPUs of this machine.

With ASD_HOST_WORKSPACE the workspace is one built on this machine: nothing is built in a container,
and the containers mount it at its own path and serve from its install tree.

Every daemon is given the architecture and its environment only.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
STATE_DIR = Path(
    os.environ.get("ASD_STATE_DIR") or Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "asd-multi-unit"
)
SYSTEM = os.environ.get("ASD_SYSTEM") or "TypeAlpha"
ARCHITECTURE_DIR = Path(
    os.environ.get("ASD_ARCHITECTURE_DIR")
    or HERE.parent / "autoware_system_designer_demo_systems" / "design" / "ee_architecture"
)
SUFFIX = ".ee_architecture.yaml"
BASE_FILE = HERE / "compose.base.yaml"
BUILD_PROJECT = "asd-multi-unit-build"
ROS_DISTRO = os.environ.get("ASD_ROS_DISTRO") or "humble"
ROS_DOMAIN_ID = os.environ.get("ASD_ROS_DOMAIN_ID") or "87"

# Mounted read-only into every environment: authorized_keys and the key a daemon reaches its peers with.
SITE_DIR = STATE_DIR / "site"
CLIENT_KEY = STATE_DIR / "id_ed25519"
PEER_KEY = SITE_DIR / "peer_ed25519"
KNOWN_HOSTS = STATE_DIR / "known_hosts"
CONTROL_SOCKET = STATE_DIR / "main.sock"
NATIVE_PID = STATE_DIR / "native.pid"
NATIVE_LOG = STATE_DIR / "native.log"
NATIVE_SSH_CONFIG = STATE_DIR / "native_ssh_config"
NATIVE_KNOWN_HOSTS = STATE_DIR / "native_known_hosts"
HOST_DEVICE = os.environ.get("ASD_HOST_DEVICE")
# Traffic of a network the stand-in containers are attached to; sensor networks stay out.
UNIT_TRAFFIC = {"daemon", "dds"}
HOST_WORKSPACE = (
    Path(os.environ["ASD_HOST_WORKSPACE"]).expanduser().resolve() if os.environ.get("ASD_HOST_WORKSPACE") else None
)
NATIVE_INSTALL = os.environ.get("ASD_NATIVE_INSTALL") or (HOST_WORKSPACE and str(HOST_WORKSPACE / "install"))
WORKSPACE_VOLUME = f"asd-multi-unit-ws-{ROS_DISTRO}"
STUDIO = Path("autoware_system_designer_studio") / "bin" / "autoware-system-designer-studio"
GPU = "gpu"
# Off runs an environment that provides a GPU without one, on a machine with no NVIDIA container toolkit.
PASS_GPUS = os.environ.get("ASD_GPUS") != "none"
USER = "asd"
SSHD_PORT = 2222
KIND_HOST = "host"
NETWORK_OWN = "own"
NATIVE = "native"

COMPOSE_ENV = {
    "ASD_UID": os.environ.get("ASD_UID") or str(os.getuid()),
    "ASD_GID": os.environ.get("ASD_GID") or str(os.getgid()),
    "ASD_SOURCE_DIR": os.environ.get("ASD_SOURCE_DIR") or str(HERE.parent.parent),
    "ASD_SITE_DIR": str(SITE_DIR),
    "ASD_ROS_DISTRO": ROS_DISTRO,
    "ASD_ROS_DOMAIN_ID": ROS_DOMAIN_ID,
    "ASD_SSHD_PORT": str(SSHD_PORT),
}


class ArchitectureError(Exception):
    pass


@dataclass(frozen=True)
class Unit:
    """Place of one compute unit in the stand-in of an architecture.

    ``environment`` is ``<device>/<environment>`` of the description. ``service`` is the Compose
    service of the environment, "native" for the daemon started on this machine. ``netns`` is the
    service holding its network namespace, None on this machine's network; ``address`` is that
    namespace's and None there too. ``main`` marks the units of the daemon that has the others as
    peers.
    """

    name: str
    environment: str
    service: str
    port: int
    address: Optional[str]
    device: str
    netns: Optional[str]
    main: bool

    @property
    def destination(self) -> str:
        """SSH destination of the unit's network namespace."""
        return f"ssh://{USER}@{self.address}:{SSHD_PORT}"


def architectures() -> List[str]:
    """E/E architectures of the system, one per description beside its design."""
    return sorted(path.name[: -len(SUFFIX)] for path in ARCHITECTURE_DIR.glob(f"{SYSTEM}*{SUFFIX}"))


def _docker_compose(project: str, file: Path, *args: str, **kwargs) -> subprocess.CompletedProcess:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)
    command = ["docker", "compose", "-p", project, "-f", str(file), *args]
    return subprocess.run(command, env={**os.environ, **COMPOSE_ENV}, check=False, **kwargs)


def _export_file(name: str) -> Path:
    return STATE_DIR / "export" / f"{name}.json"


def _compose_file(name: str) -> Path:
    return STATE_DIR / "compose" / f"{name}.compose.yaml"


def _studio(install: str) -> Path:
    studio = Path(install).expanduser().resolve() / STUDIO
    if not studio.is_file():
        raise ArchitectureError(f"no studio in {studio.parents[2]}")
    return studio


def _describe(name: str) -> subprocess.CompletedProcess:
    """The architecture as the workspace exports it."""
    if HOST_WORKSPACE is not None:
        install = HOST_WORKSPACE / "install"
        command = [str(_studio(str(install))), "environment", "--install-root", str(install), "--ee-architecture", name]
        return subprocess.run(command, capture_output=True, text=True, check=False)
    return _docker_compose(
        BUILD_PROJECT, BASE_FILE, "run", "--rm", "-T", "build", "describe", name, capture_output=True, text=True
    )


def build() -> None:
    """Builds the image and, in it, the workspace; then keeps the export of every architecture.

    A workspace of this machine is taken as built; the image alone is built.
    """
    if HOST_WORKSPACE is not None:
        built = _docker_compose(BUILD_PROJECT, BASE_FILE, "build", "unit")
        subprocess.run(["docker", "volume", "create", WORKSPACE_VOLUME], capture_output=True, check=False)
    else:
        built = _docker_compose(BUILD_PROJECT, BASE_FILE, "run", "--rm", "--build", "build")
    if built.returncode != 0:
        raise ArchitectureError("workspace build failed")
    _export_file("x").parent.mkdir(parents=True, exist_ok=True)
    for name in architectures():
        described = _describe(name)
        if described.returncode != 0:
            raise ArchitectureError(f"no export of E/E architecture '{name}':\n{described.stderr.strip()}")
        _export_file(name).write_text(described.stdout)


class Architecture:
    """The stand-in of an E/E architecture: the one place its export is read."""

    def __init__(self, name: str) -> None:
        if name not in architectures():
            raise ArchitectureError(f"no E/E architecture '{name}' of {SYSTEM} in {ARCHITECTURE_DIR}")
        if not _export_file(name).is_file():
            build()
        self.name = name
        self.project = f"asd-{name.lower()}"
        self.record: dict = json.loads(_export_file(name).read_text())
        devices = [device["name"] for device in self.record["devices"]]
        if HOST_DEVICE is not None and HOST_DEVICE not in devices:
            raise ArchitectureError(f"E/E architecture '{name}' has no device '{HOST_DEVICE}' (found: {devices})")
        hosting = [device["name"] for device in self.record["devices"] if device.get("environments")]
        self.host_device: Optional[str] = HOST_DEVICE or (hosting[0] if len(hosting) == 1 else None)
        self.environments = [env for env in self.record["environments"] if env["compute_units"]]
        main = self.record["main_environment"] or self.environments[0]["id"]
        # Units on other devices than this machine: DDS and the forwards to them cross the bridge network.
        self.distributed = any(env["device"] != self.host_device for env in self.environments)

        self.services: Dict[str, dict] = {}
        self.native: Optional[dict] = None
        holders: Dict[str, str] = {}
        memory: Dict[str, str] = {}
        self.units: List[Unit] = []
        for env in self.environments:
            service = env["id"].replace("/", "-")
            on_host = env["device"] == self.host_device
            if on_host and env["kind"] == KIND_HOST:
                self.native, service, netns = env, NATIVE, None
            elif on_host:
                if env["network"] == NETWORK_OWN:
                    raise ArchitectureError(f"environment '{env['id']}' of this machine needs the host network")
                self.services[service] = self._on_host(env)
                netns = None
            else:
                netns = holders.setdefault(env["network_namespace"], service)
                self.services[service] = self._on_device(env, netns == service, netns, memory)
            for unit in env["compute_units"]:
                self.units.append(
                    Unit(
                        name=unit,
                        environment=env["id"],
                        service=service,
                        port=env["daemon_port"],
                        address=None if netns is None else env["address"],
                        device=env["device"],
                        netns=netns,
                        main=env["id"] == main,
                    )
                )
        if not self.units:
            raise ArchitectureError(f"no compute unit is bound in E/E architecture '{name}'")
        self.main: Unit = next(unit for unit in self.units if unit.main)

    def _service(self, env: dict) -> dict:
        port = env["daemon_port"]
        gpus = {"gpus": "all"} if GPU in env["provides"] and PASS_GPUS else {}
        workspace = {}
        variables = {"ASD_EE_ARCHITECTURE": self.name, "ASD_ENVIRONMENT": env["id"]}
        if HOST_WORKSPACE is not None:
            workspace = {"volumes": [f"{HOST_WORKSPACE}:{HOST_WORKSPACE}:ro"]}
            variables["ASD_INSTALL_ROOT"] = str(HOST_WORKSPACE / "install")
        return {
            **gpus,
            **workspace,
            "extends": {"file": str(BASE_FILE), "service": "unit"},
            "environment": variables,
            "healthcheck": {
                "test": ["CMD-SHELL", f"curl -fsS http://127.0.0.1:{port}/api/health"],
                "interval": "2s",
                "timeout": "3s",
                "retries": 30,
            },
        }

    def _on_host(self, env: dict) -> dict:
        service = self._service(env)
        service["network_mode"] = "host"
        if env["shared_memory"]:
            service["ipc"] = "host"
        if not self.distributed:
            service["environment"]["ROS_LOCALHOST_ONLY"] = "1"
        return service

    def _on_device(self, env: dict, holder: bool, netns: str, memory: Dict[str, str]) -> dict:
        service = self._service(env)
        if holder:
            service["hostname"] = env["id"].split("/")[0].replace("_", "-")
            if env["network"] == NETWORK_OWN:
                service["hostname"] = env["id"].replace("/", "-").replace("_", "-")
            service["networks"] = self._attachments(env)
            service["environment"]["ASD_SSHD"] = "true"
            service["cap_add"] = ["NET_ADMIN"]  # tc netem on the device's interface
        else:
            service["network_mode"] = f"service:{netns}"
            service["depends_on"] = {netns: {"condition": "service_started"}}
        if env["shared_memory"]:
            first = memory.setdefault(env["device"], env["id"].replace("/", "-"))
            service["ipc"] = "shareable" if first == env["id"].replace("/", "-") else f"service:{first}"
        return service

    def _network_of(self, address: str) -> str:
        for network in self.record["networks"]:
            if network.get("subnet") and ipaddress.ip_address(address) in ipaddress.ip_network(network["subnet"]):
                return network["name"]
        raise ArchitectureError(f"address {address} of E/E architecture '{self.name}' is on no network with a subnet")

    def _attachments(self, env: dict) -> Dict[str, dict]:
        """Networks of the namespace an environment holds: its own address, else its device's interfaces."""
        if env["network"] == NETWORK_OWN:
            return {self._network_of(env["address"]): {"ipv4_address": env["address"]}}
        device = next(device for device in self.record["devices"] if device["name"] == env["device"])
        attached = {
            interface["network"]: {"ipv4_address": interface["address"]}
            for interface in device.get("interfaces") or []
            if interface.get("address") and interface["network"] in self._unit_networks()
        }
        if not attached:
            raise ArchitectureError(f"device '{env['device']}' has no interface with an address")
        return attached

    def _unit_networks(self) -> set:
        """Networks the units talk over: those carrying daemon or DDS traffic, every one when none says."""
        networks = self.record["networks"]
        carrying = {n["name"] for n in networks if set(n.get("carries") or []) & UNIT_TRAFFIC}
        return carrying or {n["name"] for n in networks}

    def _host_addresses(self) -> Dict[str, str]:
        """Address of this machine on each network, when one of the devices is this machine."""
        device = next((device for device in self.record["devices"] if device["name"] == self.host_device), {})
        return {
            interface["network"]: interface["address"]
            for interface in device.get("interfaces") or []
            if interface.get("address")
        }

    def compose_config(self) -> dict:
        used = {network for service in self.services.values() for network in service.get("networks") or {}}
        gateways = self._host_addresses()
        networks = {}
        for network in self.record["networks"]:
            name = network["name"]
            if name in used:
                gateway = {"gateway": gateways[name]} if name in gateways else {}
                networks[name] = {"ipam": {"config": [{"subnet": network["subnet"], **gateway}]}}
        config: dict = {"name": self.project, "services": self.services}
        if networks:
            config["networks"] = networks
        config["volumes"] = {"ws": {"name": WORKSPACE_VOLUME, "external": True}}
        return config

    def compose(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        """``docker compose`` on the project derived from the architecture."""
        file = _compose_file(self.name)
        file.parent.mkdir(parents=True, exist_ok=True)
        # JSON is YAML.
        file.write_text(json.dumps(self.compose_config(), indent=2) + "\n")
        return _docker_compose(self.project, file, *args, **kwargs)

    def unit(self, name: str) -> Unit:
        return next(unit for unit in self.units if unit.name == name)

    def remote(self, unit: Unit) -> bool:
        """Whether ``unit`` is on another device than the main daemon, behind an SSH forward."""
        return unit.device != self.main.device

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.main.port}/"


def _ssh(unit: Unit, *args: str) -> List[str]:
    return [
        "ssh",
        *("-i", str(CLIENT_KEY)),
        *("-o", "BatchMode=yes"),
        *("-o", "IdentitiesOnly=yes"),
        *("-o", "StrictHostKeyChecking=accept-new"),
        *("-o", f"UserKnownHostsFile={KNOWN_HOSTS}"),
        *("-o", "ConnectTimeout=5"),
        *("-o", "ServerAliveInterval=2"),
        *("-o", "ServerAliveCountMax=3"),
        *args,
        unit.destination,
    ]


def _make_site() -> None:
    """Keys of this run.

    The host's client key and the key a daemon reaches its peers with; the containers read the
    public halves as authorized_keys.
    """
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    for key in (CLIENT_KEY, PEER_KEY):
        for path in (key, key.with_name(f"{key.name}.pub")):
            path.unlink(missing_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", key.name, "-f", str(key)], check=True)
    KNOWN_HOSTS.unlink(missing_ok=True)
    public = [key.with_name(f"{key.name}.pub").read_text() for key in (CLIENT_KEY, PEER_KEY)]
    (SITE_DIR / "authorized_keys").write_text("".join(public))


def _close_tunnel() -> None:
    if CONTROL_SOCKET.exists():
        closed = subprocess.run(["ssh", "-S", str(CONTROL_SOCKET), "-O", "exit", "main"], capture_output=True)
        if closed.returncode != 0:
            CONTROL_SOCKET.unlink(missing_ok=True)


def _open_tunnel(architecture: Architecture) -> None:
    """Forwards the main daemon's port from its container's loopback to the host's, on the same port."""
    main = architecture.main
    if main.netns is None:
        return
    forward = f"127.0.0.1:{main.port}:127.0.0.1:{main.port}"
    options = ("-S", str(CONTROL_SOCKET), "-f", "-N", "-M", "-o", "ExitOnForwardFailure=yes", "-L", forward)
    subprocess.run(_ssh(main, *options), check=True)


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2.0):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _stop_native() -> None:
    try:
        pid = int(NATIVE_PID.read_text())
    except (OSError, ValueError):
        return
    NATIVE_PID.unlink(missing_ok=True)
    try:
        os.killpg(pid, signal.SIGINT)
    except ProcessLookupError:
        return
    for _ in range(100):
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    os.killpg(pid, signal.SIGKILL)


def _start_native(architecture: Architecture) -> None:
    """The daemon of this machine's own environment, from the install tree ASD_NATIVE_INSTALL."""
    env = architecture.native
    if not NATIVE_INSTALL:
        raise ArchitectureError(
            f"environment '{env['id']}' runs on this machine; ASD_NATIVE_INSTALL names the install tree "
            f"of a workspace built with the studio and the system"
        )
    studio = _studio(NATIVE_INSTALL)
    root = studio.parents[2]
    argv = [str(studio), "serve", "--install-root", str(root), "--env-script", str(root / "setup.bash")]
    argv += ["--ee-architecture", architecture.name, "--environment", env["id"]]
    variables = {"ROS_DOMAIN_ID": ROS_DOMAIN_ID}
    if architecture.distributed:
        NATIVE_KNOWN_HOSTS.unlink(missing_ok=True)
        NATIVE_SSH_CONFIG.write_text(
            "Host *\n"
            f"  User {USER}\n"
            f"  Port {SSHD_PORT}\n"
            f"  IdentityFile {PEER_KEY}\n"
            "  IdentitiesOnly yes\n"
            "  StrictHostKeyChecking accept-new\n"
            f"  UserKnownHostsFile {NATIVE_KNOWN_HOSTS}\n"
            "  ConnectTimeout 5\n"
        )
        argv += ["--ssh-config", str(NATIVE_SSH_CONFIG)]
    else:
        variables["ROS_LOCALHOST_ONLY"] = "1"
    if os.environ.get("ASD_RMW_IMPLEMENTATION"):
        variables["RMW_IMPLEMENTATION"] = os.environ["ASD_RMW_IMPLEMENTATION"]
    with NATIVE_LOG.open("w") as log:
        process = subprocess.Popen(
            argv, env={**os.environ, **variables}, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
    NATIVE_PID.write_text(str(process.pid))
    for _ in range(60):
        if _healthy(env["daemon_port"]):
            return
        if process.poll() is not None:
            break
        time.sleep(0.5)
    _stop_native()
    raise ArchitectureError(f"the daemon of '{env['id']}' did not start:\n{NATIVE_LOG.read_text()}")


def up(name: str) -> int:
    _close_tunnel()
    _stop_native()
    build()
    architecture = Architecture(name)
    _make_site()
    if architecture.services:
        started = architecture.compose("up", "--wait")
        if started.returncode != 0:
            return started.returncode
    if architecture.native is not None:
        _start_native(architecture)
    _open_tunnel(architecture)
    print(architecture.url)
    return 0


def down(architecture: Architecture, *args: str) -> int:
    _close_tunnel()
    _stop_native()
    return architecture.compose("down", *args).returncode if architecture.services else 0


def show_units(architecture: Architecture) -> None:
    rows = [("UNIT", "ENVIRONMENT", "SERVICE", "PORT", "ADDRESS", "NETNS", "ROLE")]
    for unit in architecture.units:
        role = "main" if unit.main else "peer"
        row = (unit.name, unit.environment, unit.service, str(unit.port), unit.address or "-", unit.netns or "host")
        rows.append((*row, role))
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())


def shell(architecture: Architecture, service: str, *command: str) -> int:
    unit = next((unit for unit in architecture.units if unit.netns == service), None)
    if unit is None:
        raise ArchitectureError(f"no service '{service}' with an sshd in the stand-in of '{architecture.name}'")
    os.execvp("ssh", _ssh(unit, *command))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("architectures", help="E/E architectures of the system")
    commands.add_parser("build", help="build the image and the workspace, and read the architectures from its export")
    for name, summary in (
        ("up", "build, start and wait until every daemon is healthy"),
        ("down", "stop and remove the containers (--volumes drops their volumes)"),
        ("compose", "docker compose on the derived project (config, ps, logs, pause, exec, ...)"),
        ("units", "environment, service, daemon port, address, network namespace and role of each unit"),
        ("url", "URL of the daemon that serves every unit"),
        ("ssh", "shell in a service with an sshd: ssh ARCHITECTURE SERVICE [CMD...]"),
    ):
        command = commands.add_parser(name, help=summary)
        command.add_argument("architecture")
        if name in ("down", "compose", "ssh"):
            command.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == "architectures":
            print("\n".join(architectures()))
        elif args.command == "build":
            build()
        elif args.command == "up":
            return up(args.architecture)
        elif args.command == "down":
            return down(Architecture(args.architecture), *args.args)
        elif args.command == "compose":
            return Architecture(args.architecture).compose(*args.args).returncode
        elif args.command == "units":
            show_units(Architecture(args.architecture))
        elif args.command == "url":
            print(Architecture(args.architecture).url)
        elif args.command == "ssh":
            if not args.args:
                parser.error("ssh needs a service")
            return shell(Architecture(args.architecture), *args.args)
    except ArchitectureError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
