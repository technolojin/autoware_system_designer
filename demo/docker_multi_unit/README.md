# Multi-unit environment on Docker

One studio daemon per execution environment of the compute units, each in its own container,
standing in for a multi-ECU bench.
The containers build the system designer packages of this checkout and run the demo system
`autoware_system_designer_demo_systems:TypeAlpha`: the nodes of
[`autoware_system_designer_demo_nodes`](../autoware_system_designer_demo_nodes/README.md) on three onboard
units, exchanging `std_msgs` messages, every unit subscribing topics published on the other two.

| Unit                 | Components                                 | Daemon port |
| -------------------- | ------------------------------------------ | ----------- |
| `sensing_perception` | sensing, perception, robot state publisher | 8781        |
| `map_localization`   | map, localization, vehicle                 | 8782        |
| `planning_control`   | planning, control                          | 8783        |

In `TypeAlphaSharedEnvironment` one daemon on port 8782 serves `map_localization` and `planning_control`.

This directory holds what every architecture shares: the image, its entry point, the compose
services (`compose.base.yaml`), the runner and the tests. Which units run where, on which devices,
ports and addresses, and which daemon serves the others is the content of the
[E/E architecture descriptions](../../autoware_system_designer.instructions.md) of the system, in
[`autoware_system_designer_demo_systems/design/ee_architecture`](../autoware_system_designer_demo_systems/design/ee_architecture).
A description added there and listed in the deployments table is a new architecture to run;
`ASD_SYSTEM` and `ASD_ARCHITECTURE_DIR` select the descriptions of another system.

## Architectures

| Architecture                 | Stands for                                       | Network                                              | `sensing_perception` reaches its peers by |
| ---------------------------- | ------------------------------------------------ | ---------------------------------------------------- | ----------------------------------------- |
| `TypeAlphaHost`              | containers of one ECU                            | host network and IPC, DDS on loopback                | the shared loopback                       |
| `TypeAlphaNativeMain`        | one ECU: its operating system and two containers | host network and IPC, DDS on loopback                | the shared loopback                       |
| `TypeAlphaBridge`            | separate ECUs                                    | bridge network `172.30.87.0/24`, fixed addresses     | SSH forwards it keeps as tunnel jobs      |
| `TypeAlphaLocalPair`         | two containers of the main ECU and a second ECU  | the same network, two units in one network namespace | the shared loopback, an SSH forward       |
| `TypeAlphaRemotePair`        | the main ECU and two containers of a second ECU  | the same network, two peers behind one address       | two SSH forwards from one destination     |
| `TypeAlphaSharedEnvironment` | the main ECU and one environment of two units    | the same network, one daemon serving two units       | one SSH forward, one link for both units  |

`run.py` builds the workspace in a container, reads each architecture from the export
(`autoware-system-designer-studio environment --ee-architecture <name>`) and derives a Compose
project from it, written under the state directory:

| In the description                          | In the stand-in                                                              |
| ------------------------------------------- | ---------------------------------------------------------------------------- |
| An environment with compute units           | a service, its daemon given the architecture and the environment only        |
| A device                                    | the service holding a network namespace, with the device's addresses         |
| An environment on the device's network      | joins that namespace (`network_mode: service:<holder>`)                      |
| An environment with its own network         | holds a namespace of its own at its address                                  |
| `shared_memory` on environments of a device | one IPC namespace for them                                                   |
| `networks` with a subnet                    | bridge networks                                                              |
| The only device, or `ASD_HOST_DEVICE`       | this machine: host network and IPC for its containers                        |
| Its environment of kind `host`              | a daemon started on this machine, from the install tree `ASD_NATIVE_INSTALL` |
| Its interface address                       | this machine's address on the bridge network (the gateway)                   |
| `provides: [gpu]` on an environment         | the GPUs of this machine in its container (`gpus: all`)                      |

In every architecture the daemon of `sensing_perception` is in the main environment and has the
others as [peers](../../autoware_system_designer_studio/README.md#peers): its workbench and API on
port 8781 launch, observe and shut down every unit. Each daemon takes its compute units, port,
peers and SSH forwards from the [architecture](../../autoware_system_designer_studio/README.md#ee-architecture);
no site file is written.

Every daemon binds the loopback of its network namespace. A container holding a network namespace
runs an `sshd` on port 2222 as the unprivileged user `asd`. `run.py` makes two keys per `up`: the
host's, with which it forwards the main daemon's port to the same port on the host (a daemon
answers only requests whose `Host` names its port), and the one a main daemon opens its forwards
to the other devices with; user, port and key are in the SSH configuration of the container's
user, the destination is the address of the description. DDS crosses the bridge network without
shared memory.

A daemon started on this machine needs the studio and the system built here for the same ROS
distribution as the image, and `ASD_NATIVE_INSTALL` naming that install tree; its output is
`native.log` in the state directory. `TypeAlphaNativeMain` has one, and so has any architecture
whose device `ASD_HOST_DEVICE` names has an environment of kind `host`:

```bash
ASD_HOST_DEVICE=ecu_a ASD_NATIVE_INSTALL=~/ws/install ./run.py up TypeAlphaBridge
```

runs `ecu_a/host` here and `ecu_b`, `ecu_c` as containers. The daemon here opens its forwards with
`--ssh-config` on a configuration written under the state directory, and DDS crosses the bridge
network from this machine's address on it.

## Use

```bash
./run.py architectures                # TypeAlphaBridge, TypeAlphaHost, ...
./run.py up TypeAlphaBridge           # build, start, wait until every daemon is healthy
./run.py url TypeAlphaBridge          # http://127.0.0.1:8781/ the main daemon's workbench
./run.py units TypeAlphaBridge        # environment, service, daemon port, address, network namespace, role of each unit
./run.py compose TypeAlphaBridge logs -f ecu_a-host
./run.py ssh TypeAlphaBridge ecu_b-host   # shell in a service with an sshd
./run.py down TypeAlphaBridge
```

The image holds the repositories of [`build_depends.repos`](../../build_depends.repos) as an underlay,
so this checkout alone is the workspace. One architecture runs at a time: they share the daemon
ports and the bridge subnet.

`up` runs the `build` service first: `colcon build` of the checkout (mounted read-only) into the
volume `asd-multi-unit-ws-<distro>`, which every architecture shares, so a later `up` rebuilds only
what changed, and reads the architectures from the new export. Launch a unit from the Launch activity of the workbench, or by request,
with the deploy variant built for the running architecture (`vehicle_x` is the one of `TypeAlphaBridge`; a daemon refuses
the variant of another architecture):

```bash
curl -s http://127.0.0.1:8781/api/rpc -H 'Content-Type: application/json' -H 'X-System-Designer-Client: shell' \
    -d '{"id": 1, "method": "launch.start", "params": {"system": "autoware_system_designer_demo_systems:TypeAlpha", "compute_unit": "map_localization", "mode": "default", "deploy": "vehicle_x"}}'
```

| Variable                 | Default                             | Role                                            |
| ------------------------ | ----------------------------------- | ----------------------------------------------- |
| `ASD_ROS_DISTRO`         | `humble`                            | ROS distribution of the image                   |
| `ASD_ROS_DOMAIN_ID`      | `87`                                | DDS domain of the units                         |
| `ASD_RMW_IMPLEMENTATION` | the distribution's                  | RMW implementation, e.g. `rmw_cyclonedds_cpp`   |
| `ASD_UID`, `ASD_GID`     | the calling user's                  | ids of the container user                       |
| `ASD_STATE_DIR`          | `$XDG_RUNTIME_DIR/asd-multi-unit`   | keys, known hosts, exports, derived projects    |
| `ASD_PACKAGES`           | studio, demo systems                | packages the `build` service builds up to       |
| `ASD_SYSTEM`             | `TypeAlpha`                         | system whose architectures are run              |
| `ASD_ARCHITECTURE_DIR`   | the demo systems' `ee_architecture` | directory of `<system>*.ee_architecture.yaml`   |
| `ASD_NATIVE_INSTALL`     | none                                | install tree of the daemon run on this machine  |
| `ASD_HOST_DEVICE`        | the only device of the architecture | device of the architecture that is this machine |
| `ASD_HOST_WORKSPACE`     | none                                | workspace of this machine to run instead        |
| `ASD_GPUS`               | passed to `provides: [gpu]`         | `none` runs those environments without a GPU    |

The variables are the environment's own: `ROS_DISTRO` and `ROS_DOMAIN_ID` of the calling shell are
not read. Daemon ports and addresses are the description's.

## Another system

`ASD_HOST_WORKSPACE` names a colcon workspace built on this machine. Nothing is built in a
container then: the architectures are read from the export of that workspace, the containers mount
it read-only at its own path and their daemons serve its install tree, and so does the daemon
started here. With `ASD_SYSTEM` and `ASD_ARCHITECTURE_DIR` it stands in for the architecture of
any system of that workspace:

```bash
ASD_HOST_WORKSPACE=~/autoware ASD_SYSTEM=MySystem ASD_ARCHITECTURE_DIR=~/autoware/src/my_system/design/ee_architecture \
    ASD_HOST_DEVICE=main ./run.py up MySystem
```

The daemons need nothing but Python; a launch in a container needs what the system's nodes need,
which this image does not hold. An environment that provides `gpu` needs the NVIDIA container
toolkit on this machine; `ASD_GPUS=none` starts it without a GPU.

## Faults

A device other than the main daemon's can be cut off the way an ECU fails, with every unit on it.
The main daemon reports them `unreachable` within about 15 s and reads them anew once their daemons
answer again:

```bash
./run.py compose TypeAlphaBridge pause ecu_c-host                        # frozen ECU
./run.py compose TypeAlphaBridge unpause ecu_c-host
./run.py compose TypeAlphaBridge kill ecu_c-host                         # power loss
./run.py compose TypeAlphaBridge start ecu_c-host                        # power back, launch gone
docker network disconnect asd-typealphabridge_vehicle asd-typealphabridge-ecu_c-host-1
docker network connect --ip 172.30.87.13 asd-typealphabridge_vehicle asd-typealphabridge-ecu_c-host-1
./run.py compose TypeAlphaBridge exec -u root ecu_c-host tc qdisc add dev eth0 root netem delay 50ms loss 5%
```

## Tests

```bash
python3 -m pytest demo/docker_multi_unit/test            # --keep leaves the last architecture up
```

The tests carry the marker `docker` and are skipped without a Docker daemon. They bring up each
architecture in turn and drive every unit through its main daemon alone: the peers connect without
mismatch, a peer on another device through an SSH forward, every unit launches, each
unit receives a topic of another unit, member output is readable, a shutdown ends one unit's
processes and leaves the others running, and the past runs of all three are listed and read. The
units of a device other than the main daemon's also stay reachable over a slow, lossy link, and are
reported `unreachable` and then read anew when the device is paused, taken off the network, or
killed and started again. The units, their services and devices are read from `run.py`; the
system and the topics listened to are the tests' own; the deploy is the one built for the architecture. `TypeAlphaNativeMain` is skipped
without `ASD_NATIVE_INSTALL`; with `ASD_HOST_DEVICE`, only the architecture `ASD_HOST_ARCHITECTURE`
names is run. They are not part of
`colcon test`.

## Limits

Docker does not reproduce NIC and switch timing, time synchronisation, GPU or Jetson targets, or
the start order of system services. `docker network disconnect` removes the container's interface,
where a pulled cable leaves it in place: the daemons find each other again, the DDS participants of
a running launch do not, and the unit needs a new launch. The same holds, some of the time, for
a device frozen longer than the discovery lease under Fast DDS, whose participants then never
match their peers again; CycloneDDS (`ASD_RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`) finds them
within seconds. The tests launch the units of such a device anew, except after a freeze under
CycloneDDS.
