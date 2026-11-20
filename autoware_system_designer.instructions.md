# Autoware System Designer Instruction Manifest

## 1. Role & Objective

This document describes how to create and manage Autoware System Designer configurations (written in autoware_system_design_format). The goal is to generate valid, modular, and consistent YAML configuration files that define the software architecture of an Autoware system.

## 2. File Format Version

All main-format files (Nodes, Modules, Systems, Parameter Sets) MUST start with the format version specification. Sub-format files (Deployments, Subsystems; see Section 9) are outside the format versioning. The tool supports files whose _major_ version matches and whose _minor_ version is less-than-or-equal-to `DESIGN_FORMAT_VERSION`. All entity types (Nodes, Modules, Systems, Parameter Sets) must use a version up to `DESIGN_FORMAT_VERSION`.

**Note**: The supported format version is defined in `autoware_system_designer/__init__.py` as `DESIGN_FORMAT_VERSION`.

## 3. File System Organization

Follow this directory structure for consistency (not mandatory).

- **Root**: `src/<package_name>/design/`
- **Nodes**: `src/<package_name>/design/node/` (suffix: `.node.yaml`)
- **Modules**: `src/<package_name>/design/module/` (suffix: `.module.yaml`)
- **Systems**: `src/<package_name>/design/system/` (suffix: `.system.yaml`)
- **Parameter Sets**: `src/<package_name>/design/parameter_set/` (suffix: `.parameter_set.yaml`)
- **Subsystems** (sub format): `src/<package_name>/design/subsystem/` (suffix: `.subsystem.yaml`)
- **Deployments** (sub format): `src/<package_name>/deployment/` (suffix: `.deployments.yaml`)
- **E/E Architectures**: `src/<package_name>/design/ee_architecture/` (suffix: `.ee_architecture.yaml`)
- **Devices**: `src/<package_name>/design/device/` (suffix: `.device.yaml`)

## 4. Main Formats: Configuration Entities & Schemas

The main formats describe the architecture. Each file defines one entity, declares the format version and is validated against the schema of that version.

### 4.1. Node Configuration (`.node.yaml`)

Represents a single ROS 2 node.
**Required Fields:**

- `autoware_system_design_format`: Must be a version up to the supported `DESIGN_FORMAT_VERSION`.
- `name`: Must match filename (e.g., `MyNode.node`).
- `description`: (Optional) Brief explanation of the node's role.
- `package`: Dictionary defining the ROS 2 package information.
  - `name`: ROS 2 package name.
  - `provider`: Provider of the package (e.g., `dummy`, `tier4`, `autoware`).
- `launch`: Dictionary defining execution details.
  - `plugin`: C++ class name (component) or script entry point.
  - `executable`: (Optional) Name of the executable.
  - `ros2_launch_file`: (Required if `executable` and `plugin` are not set) Alternative setting used for normal ros2 launcher wrapper.
  - `node_output`: (Optional) `screen`, `log`, etc.
- `subscribers`: List of input ports (subscribers).
  - `name`: Port name. Can include slashes (e.g., `perception/objects`).
  - `description`: (Optional) Brief explanation of the port.
  - `message_type`: Full ROS message type (e.g., `sensor_msgs/msg/PointCloud2`).
  - `remap_target`: (Optional) The internal ROS 2 topic name used by the node.
    - **Default**: If not provided, it defaults to `~/input/<name>`.
    - **Required when**: The node implementation uses a specific topic name that does not follow the `~/input/` convention (e.g., legacy code or global topics like `/tf`).
  - `global`: (Optional) If set, the input topic subscribes to a global topic name (e.g., `/tf`).
  - `qos`: (Optional) QoS settings (`reliability`, `durability`, etc.).
- `publishers`: List of output ports (publishers).
  - `name`: Port name. Can include slashes.
  - `description`: (Optional) Brief explanation of the port.
  - `message_type`: Full ROS message type.
  - `qos`: (Optional) QoS settings (`reliability`, `durability`, etc.).
  - `remap_target`: (Optional) The internal ROS 2 topic name used by the node.
    - **Default**: If not provided, it defaults to `~/output/<name>`.
    - **Required when**: The node implementation uses a specific topic name that does not follow the `~/output/` convention.
  - `global`: (Optional) If set, the output topic is published to a global topic name (e.g., `/tf`).
- `servers`: (Optional) List of service/action servers.
  - `name`: Server name.
  - `description`: (Optional) Brief explanation of the server.
  - `message_type`: Full ROS service or action type (e.g., `std_srvs/srv/SetBool` or `example_interfaces/action/Fibonacci`).
  - `global`: (Optional) If set, the server uses a global topic name.
  - `qos`: (Optional) QoS settings.
  - `remap_target`: (Optional) The internal ROS 2 service/action name used by the node.
- `clients`: (Optional) List of service/action clients.
  - `name`: Client name.
  - `description`: (Optional) Brief explanation of the client.
  - `message_type`: Full ROS service or action type (e.g., `std_srvs/srv/SetBool` or `example_interfaces/action/Fibonacci`).
  - `global`: (Optional) If set, the client uses a global topic name.
  - `qos`: (Optional) QoS settings.
  - `remap_target`: (Optional) The internal ROS 2 service/action name used by the node.
- `param_files`: List of parameter file references. Can be an empty list `[]`.
  - `name`: Identifier for the file reference.
  - `description`: (Optional) Brief explanation of the file reference.
  - `default`: Path to file (use `$(find-pkg-share pkg)/path` or relative path).
    A path keeping a launch-time substitution, such as a system argument `$(var name)`, is loaded as a file at launch time, after the values expanded from the files of its priority tier and before the direct values of that tier.
  - `schema`: (Optional) Path to JSON schema.
  - `allow_substs`: (Optional) `true`/`false` to allow substitution in parameter files.
- `param_values`: List of individual default parameters. Can be an empty list `[]`.
  - `name`: Parameter name.
  - `type`: Parameter type (`bool`, `int`, `double`, `string`, `array`, etc.).
  - `default`: Default value.
  - `description`: (Optional) Brief explanation of the parameter.
- `processes`: Execution logic / Event chains.
  - `name`: Name of the process/callback.
  - `description`: (Optional) Brief explanation of the process.
  - `trigger_conditions`: Logic to start process. Can be nested with `or`/`and`. The process runs at the rate of the conditions that pace it, and that rate follows its outcomes to the ports it publishes: an `and` runs at its slowest condition, any other at its quickest.
    - `on_input`: Triggered by input port (`on_input: port_name`).
    - `on_trigger`: Triggered by another process (`on_trigger: process_name`).
    - `periodic`: Triggered periodically (`periodic: 10.0` [Hz]). The rate may reference the node's effective parameter (`periodic: ${parameter rate}`), so a parameter set that retunes the parameter retunes the design rate.
    - `once`: Triggered once. Can be `once: null` or `once: <port_name>` to trigger once when a specific port receives data. Inside an `and`/`or` it is a latch: it gates the chain and leaves the rate to the other conditions.
    - **Monitoring**: Optional fields `warn_rate`, `error_rate`, `timeout` can be added to trigger definitions. Like `periodic`, each is a number or a `${parameter ...}` reference that resolves to one.
  - `expected_latency_ms`: (Optional) Run the process is expected to take, in milliseconds (`expected_latency_ms: 30.0`); a number or a `${parameter ...}` reference. The latency view times the process by it; without it a tenth of the process's interval is assumed.
  - `outcomes`: Result of process.
    - `to_output`: Sends result to output port (`to_output: port_name`).
    - `to_trigger`: Triggers another process (`to_trigger: process_name`).
    - `to_queue`: Parks the result in a node-owned queue (`to_queue: queue_name`). The queue exists once any process of the node names it; its fill rate is the filling process's rate.
    - `terminal`: Ends the chain (`terminal: null`).
  - `reads`: (Optional) Queues the process reads when it runs (`- from_queue: queue_name`). A read is a loose connection: it neither triggers the process nor paces it, so the process keeps the rate of its `trigger_conditions` and the filling chain ends at the queue. Every queue read must be filled by a `to_queue` of the same node. A distortion corrector queues IMU samples on their own callback and reads the queue when a point cloud arrives:

    ```yaml
    processes:
      - name: queue_imu
        trigger_conditions:
          - on_input: imu
        outcomes:
          - to_queue: imu
      - name: undistort
        trigger_conditions:
          - on_input: pointcloud
        reads:
          - from_queue: imu
        outcomes:
          - to_output: pointcloud
    ```

### 4.2. Module Configuration (`.module.yaml`)

Represents a composite component containing nodes or other modules.
**Required Fields:**

- `autoware_system_design_format`: Must be a version up to the supported `DESIGN_FORMAT_VERSION`.
- `name`: Must match filename (e.g., `MyModule.module`).
- `description`: (Optional) Brief explanation of the module's role.
- `instances`: List of internal entities.
  - `name`: Local name for the instance (e.g., `lidar_driver`).
  - `entity`: Reference to the entity definition (e.g., `LidarDriver.node`).
  - `description`: (Optional) Brief explanation of the instance.
  - `launch`: (Optional) Override launch configurations for this instance.
- `subscribers`: List of externally accessible input ports.
  - `name`: Port name.
  - `description`: (Optional) Brief explanation of the port.
- `publishers`: List of externally accessible output ports.
  - `name`: Port name.
  - `description`: (Optional) Brief explanation of the port.
- `servers`: (Optional) List of externally accessible service/action servers.
  - `name`: Server name.
  - `description`: (Optional) Brief explanation of the server.
- `clients`: (Optional) List of externally accessible service/action clients.
  - `name`: Client name.
  - `description`: (Optional) Brief explanation of the client.
- `connections`: Internal wiring. List of connection pairs, where each connection is a list of two port paths. Supports wildcards (e.g., `subscriber.*` or `node.publisher.*`); a wildcard matches only ports of the declared kind, so `publisher.*` and `server.*` lines pair independent port sets. Connection entries do not take a `description`.

**Connection Syntax:**

For topic-based connections (subscribers/publishers):

- **External Subscriber to Internal Subscriber**:

  ```yaml
  - - subscriber.<external_subscriber_port>
    - <instance>.subscriber.<port>
  ```

- **Internal Publisher to Internal Subscriber**:

  ```yaml
  - - <instance_a>.publisher.<port>
    - <instance_b>.subscriber.<port>
  ```

- **Internal Publisher to External Publisher**:

  ```yaml
  - - <instance>.publisher.<port>
    - publisher.<external_publisher_port>
  ```

For service/action-based connections (servers/clients):

- **External Client to Internal Client**:

  ```yaml
  - - client.<external_client_port>
    - <instance>.client.<port>
  ```

- **Internal Server to Internal Client**:

  ```yaml
  - - <instance_a>.server.<port>
    - <instance_b>.client.<port>
  ```

- **Internal Server to External Server**:

  ```yaml
  - - <instance>.server.<port>
    - server.<external_server_port>
  ```

### 4.3. System Configuration (`.system.yaml`)

Top-level entry point defining the complete system.
**Required Fields:**

- `autoware_system_design_format`: Must be a version up to the supported `DESIGN_FORMAT_VERSION`.
- `name`: Must match filename (e.g., `MyCar.system`).
- `description`: (Optional) Brief explanation of the system.
- `arguments`: (Optional) List of system arguments.
  - `name`: Argument name.
  - `description`: (Optional) Brief explanation of the argument.
  - Arguments and variables share the `$(var name)` syntax. A variable resolves at build time; an argument stays in the launchers as a launch argument, which deployments fill. A name may not be both. A `$(var name)` naming neither warns; when it reaches a launcher and no deployment sets it as an argument, it also fails a strict build.
- `variables`: List of system variables.
  - `name`: Variable name.
  - `value`: Variable value (supports `$(find-pkg-share pkg)`, `$(env VAR)` and `$(eval expr)` substitutions). An `$(eval)` expression is arithmetic over literals, a fixed set of `math` functions and constants, `abs`, `min`, `max`, `pow`, `round`, `int`, `float`, `str`, `pi`, `sin`, `cos`, `tan`, `sqrt`, `atan2`, operators, comparisons and `a if c else b`; other Python constructs, and results beyond 4096 bits or 4096 elements, are left unresolved.
  - `description`: (Optional) Brief explanation of the variable.
- `variable_files`: (Optional) List of variable file references.
  - `name`: Variable file identifier.
  - `value`: Path to variable file.
  - `description`: (Optional) Brief explanation of the file reference.
- `modes`: List of operation modes.
  - `name`: Mode name (e.g., `Runtime`, `LoggingSimulation`).
  - `description`: (Optional) Description of the mode.
  - `default`: (Optional) `true`/`false` to mark as default mode.
- `parameter_sets`: List of parameter set files. Can be an empty list `[]`.
- `compute_units`: (Optional) Compute units the system is deployed on. A unit is one independently launched environment (an ECU, a container or a partition) with no copy-free IPC to other units; the hardware or execution environment hosting it is outside the system design. Components on different units communicate over the network only, so a node group cannot span units. Once declared, every component's `compute_unit` must name one of them, and each onboard unit gets a launcher in every mode even when no component of that mode runs on it.
  - `name`: Unit identifier referenced by `components[].compute_unit` (e.g., `main_ecu`). Lowercase letters, digits and underscores, starting with a letter (`^[a-z][a-z0-9_]*$`); the name is used as the launcher directory and file name and as the runtime `--compute-unit` value.
  - `kind`: (Optional) Where the unit exists relative to the deployed vehicle. `onboard` (default) runs on the vehicle and gets a launcher; `offboard` is a participant outside the vehicle (infrastructure, cloud, remote operation) whose components are declared for wiring only. Offboard units get no launcher and no launch-command entry; their components remain in the structure export.
  - `description`: (Optional) Brief explanation of the unit.
  - `requires`: (Optional) Capabilities the unit needs from the execution environment it is launched in (e.g., `[gpu]`). Names are free (`^[a-z][a-z0-9_]*$`); an E/E architecture may bind the unit only to an environment whose `provides` holds every one of them.
- `peripherals`: (Optional) Hardware outside the compute units that components talk to (sensors, actuators, vehicle controllers), by logical name. The system design names no device; an E/E architecture binds each name to one.
  - `name`: Peripheral identifier (`^[a-z][a-z0-9_]*$`); the key an E/E architecture binds by.
  - `node`: (Optional) Path of the node that drives the peripheral (e.g., `/sensing/lidar/lidar_top`).
  - `component`: (Optional) Top-level component whose nodes talk to the peripheral; derived from `node` when omitted. The interfaces and addresses the component's compute units reach the device by are derived from it.
  - `parameters`: (Optional, with `node`) Parameters of the node whose values the bound E/E architecture gives. Network information of the hardware belongs here, not in parameter files.
    - `name`: Parameter name of the node.
    - `value`: `address` (the peripheral's address on the network both are on), `interface` (the peripheral's interface), `network`, `host_address` or `host_interface` (of the compute unit's device that reaches the peripheral), or `device` (the bound device instance).
    - Each parameter is set to the system argument `<peripheral>_<parameter>`, declared by the build; a deployment that names an E/E architecture sets it (Section 4.5).
  - `description`: (Optional) Brief explanation of the peripheral.
- `components`: Top-level instances.
  - `name`: Name of the component instance.
  - `entity`: Reference to module/node (e.g., `SensingModule.module`).
  - `description`: (Optional) Brief explanation of the component.
  - `namespace`: ROS namespace prefix.
  - `compute_unit`: Compute unit the component runs on (e.g., `main_ecu`); a declared `compute_units` entry when the system declares any.
  - `parameter_set`: (Optional) Parameter set file name(s) to apply. Can be a string or an array of strings.
- `node_groups`: (Optional) Group nodes into a ROS 2 component container (synthetic container node).
  - `name`: Unique group name. It will be used as the component name for the container.
  - `type`: Node group execution type. select `ros2_component_container_mt` or `ros2_component_container`.
  - `nodes`: List of non-empty path patterns. Glob patterns (`*`, `?`, `[...]`) match the full node path.
- `connections`: Top-level wiring between components. List of connection pairs, where each connection is a list of two port paths. Supports wildcards (e.g., `component.publisher.^` for wildcard); a wildcard matches only ports of the declared kind. Connection entries do not take a `description`.

**Mode-Specific Overrides:**
Each mode can define overrides using the mode name as a key:

- `override`: Dictionary containing mode-specific overrides. All system configuration fields can be overridden (e.g., `variables`, `variable_files`, `modes`, `parameter_sets`, `compute_units`, `components`, `connections`). The variant resolver applies the appropriate merge strategy for each field type (key-based replacement for fields with identifiers, append for lists without keys, dictionary merge for dictionaries).
- `remove`: Dictionary specifying what to remove in this mode. All system configuration fields can be removed (e.g., `modes`, `parameter_sets`, `compute_units`, `components`, `variables`, `connections`). The variant resolver applies the appropriate removal strategy (key-based removal for fields with identifiers, full match for lists without keys). When components are removed, connections involving them are automatically filtered out.

### 4.4. Parameter Set Configuration (`.parameter_set.yaml`)

Overrides parameters for specific nodes within the system hierarchy.
**Fields:**

- `autoware_system_design_format`: Must be a version up to the supported `DESIGN_FORMAT_VERSION`.
- `name`: Must match filename.
- `description`: (Optional) Brief explanation of the parameter set.
- `parameters`: List of overrides.
  - `node`: Full hierarchical path to the node instance (e.g., `/perception/object_recognition/detector_a1/node_detector`).
  - `description`: (Optional) Brief explanation of the override entry.
  - `param_files`: List of dictionaries mapping parameter file keys to new paths.
    - Format: `- <key>: <path>` (e.g., `- model_param_path: path/to/file.yaml`).
  - `param_values`: List of individual parameter value overrides.
    - `name`: Parameter name.
    - `type`: Parameter type (`bool`, `int`, `double`, `string`, etc.).
    - `value`: Override value (not `default`).

### 4.5. E/E Architecture Configuration (`.ee_architecture.yaml`)

> **Experimental.** The E/E architecture (`.ee_architecture.yaml`, `.device.yaml`, and `peripherals` and compute unit `requires` in a system) is under development. Its format is not fixed and has no JSON schema; only what the build resolves is checked.

Describes the hardware a system is deployed on: device instances with their interfaces, the connections between interfaces, the networks addressed over them, time synchronization, and the binding of each compute unit to an execution environment and of each peripheral to a device. A system design names compute units and peripherals only; an E/E architecture names devices, compute units and peripherals only, never components. The same system runs on another architecture without a change to its design. Addresses, interface names and ports of the hardware belong here, not in node parameters.
**Fields:**

- `autoware_system_design_format`: Must be a version up to the supported `DESIGN_FORMAT_VERSION`.
- `name`: Must match filename.
- `description`: (Optional) Brief explanation of the architecture.
- `networks`: (Optional) Networks addressed over the wiring.
  - `name`: Network identifier (`^[a-z][a-z0-9_]*$`).
  - `subnet`: (Optional) Address range in CIDR notation (e.g., `192.168.30.0/24`); interface addresses on the network lie inside it.
  - `vlan`: (Optional) VLAN id of the network on the interfaces it is on.
  - `carries`: (Optional) Traffic the network is for (e.g., `[dds]`, `[daemon, ptp]`). Names are free; `daemon` selects the addresses the studio daemons answer on.
  - `description`: (Optional) Brief explanation of the network.
- `devices`: Device instances of the vehicle: ECUs, switches, routers, sensors, actuators.
  - `name`: Instance identifier (`^[a-z][a-z0-9_]*$`).
  - `device`: (Optional) Device entity the instance is of (e.g., `MainEcu.device`, Section 4.6). Its interfaces, environments and parts are then configured by name here; `role` and `hardware` come from the entity. Without it, the instance is described inline.
  - `role`: (Optional, inline only) `ecu`, `switch`, `router`, `sensor`, `actuator` or `other`. Default `ecu` with environments, else `other`. A `switch` or `router` forwards between its interfaces.
  - `hardware`: (Optional, inline only) Hardware class (e.g., `x86_64`, `jetson_orin`).
  - `description`: (Optional) Brief explanation of the device.
  - `interfaces`: (Optional) Interfaces of the instance.
    - `name`: Interface name on the device (e.g., `eth0`, `can0`, `p1`); no dots.
    - `type`: (Optional, inline only) `ethernet` (default), `can`, `lin`, `flexray`, `serial`, `gmsl`, `usb` or `other`.
    - `bitrate`: (Optional, inline only) Capacity in bit/s, as an integer or with suffix `k`, `M`, `G` (e.g., `500k`, `10G`).
    - `data_rate`: (Optional, inline only) Data the device sends on the interface in bit/s, in the form of `bitrate` (e.g., a LiDAR's `200M`).
    - `network`, `address`: (Optional) Declared network the interface is on and its fixed address there, the same on every vehicle of the architecture.
    - `networks`: (Optional) List of `{network, address}` for an interface on several networks (VLANs, secondary addresses).
  - `environments`: Execution environments on the device. A device without one hosts no compute unit.
    - `name`: Environment identifier (`^[a-z][a-z0-9_]*$`); referenced as `<device>/<environment>`.
    - `kind`: (Optional) `host` (default, the device's operating system), `container` or `vm`.
    - `network`: (Optional) `host` (default) shares the device's network namespace, loopback included; `own` has its own network namespace.
    - `address`: Fixed address of an environment whose `network` is `own`; not allowed otherwise.
    - `shared_memory`: (Optional) `true` when the environment shares the device's shared memory with the other environments of the device that set it. Default `false`.
    - `provides`: (Optional) Capabilities the environment gives the compute units bound to it (e.g., `[gpu]`), matched by name against each unit's `requires`. A capability belongs to the environment, not the device: a container has a GPU only when it is passed through.
    - `daemon_port`: Loopback port of the environment's studio daemon. Required, and distinct, once compute units are bound to more than one environment.
    - `description`: (Optional) Brief explanation of the environment.
  - `parts`: (Optional, with `device`) Configuration of the parts of a composite device, each with `name` and its own `interfaces`, `environments` and `parts`. A part is referenced as `<device>.<part>`.
- `connections`: (Optional) Physical connections between interfaces. An endpoint is `<device>.<interface>`; `<device>` alone stands for any port of a switch or router.
  - `link`: Two endpoints joined point to point. Or:
  - `bus`: Two or more endpoints on a shared medium (e.g., a CAN bus).
  - `name`: (Optional) Connection identifier; the key a variant overrides and removes by.
  - `bitrate`: (Optional) Capacity in bit/s. Default: the lowest `bitrate` of the joined interfaces.
  - `description`: (Optional) Brief explanation of the connection.
- `time_sync`: (Optional) Time synchronization domains.
  - `name`: Domain identifier.
  - `profile`: (Optional) Synchronization profile (e.g., `ptpv2`, `gptp`).
  - `grandmaster`: `<device>.<interface>` the domain's time is sent from.
  - `members`: (Optional) `<device>.<interface>` entries synchronized to the grandmaster. An interface is a member of one domain.
  - `source`: (Optional) Domain the grandmaster's device takes its time from; the device has a member interface in it.
  - `reference`: (Optional, without `source`) External time the grandmaster's device follows (e.g., `ntp`, `gnss`).
- `main_environment`: `<device>/<environment>` whose daemon serves the compute units of the other environments. Required once compute units are bound to more than one environment.
- `bindings`: What the system's logical names are on this hardware. Each entry is one of:
  - `compute_unit` + `environment`: Name of an `onboard` compute unit and the `<device>/<environment>` it is launched in. Several units may share one environment. The build fails when the environment does not provide a capability the unit requires.
  - `peripheral` + `device`: Name of a peripheral of the system and the device instance it is.

A deployments table lists the architectures its system is deployed on under `ee_architectures`. Each is checked against the system and exported with it. A deploy names the architecture of its vehicle with `ee_architecture`, one of the listed; the build sets the deploy's argument of every peripheral parameter from it, and a studio daemon running in another architecture refuses the deploy:

```yaml
base: TypeAlpha.system
ee_architectures:
  - TypeAlphaBridge.ee_architecture
deploy_list:
  - name: vehicle_x
    ee_architecture: TypeAlphaBridge.ee_architecture
```

**Rules:**

- Every `onboard` compute unit of the system is bound exactly once; `offboard` units are not bound. Every peripheral of the system is bound exactly once.
- Addresses are unique within the architecture.
- A connection joins interfaces of one `type`. An interface is in one connection of the architecture, and in one of the device entity it belongs to.
- Once the architecture declares connections, a path of them joins each peripheral's device to the device of every compute unit of its component, and each time sync member to its grandmaster. A path passes through switches, routers and ports wired on inside a composite device.
- An environment outside the main environment's network namespace has an address: its own, or an interface address of its device on a network that carries `daemon`; when no network carries it, on a network the main environment's device is on.
- A connection between compute units in different environments of one device, where not both set `shared_memory`, warns to disable the DDS shared memory transport there.

**Derived and exported with each architecture:**

- One record per environment, with its compute units and the address its daemon answers on.
- `wiring`: every device, parts included, with its interfaces, and every connection with its capacity.
- `peripherals`: for each, its device and addresses, per compute unit of its component the interface, network and address that reach it and the connections on the way, and the value of each of its node parameters.
- `time_sync`: for each member, the connections from the grandmaster.
- `analysis`: per mode, the flows into and out of the compute units and the load on each connection. A flow is one peripheral feeding one unit, at the `data_rate` of the device's interfaces, or the topics one unit publishes to another, with their output rate and no bit rate, since message sizes are measured, not declared; topics between units of one device stay off the wiring and travel over Ethernet connections otherwise. The load of a link is that of its busiest direction, of a bus the sum of its senders; a load over the capacity warns.

A variant (`base`) replaces `networks`, `devices`, `connections` and `time_sync` by `name` and `bindings` by `compute_unit` or `peripheral` under `override`, sets `main_environment` there, and drops entries under `remove` (see Section 5).

### 4.6. Device Configuration (`.device.yaml`)

Describes one kind of hardware, instantiated by E/E architectures and as a part of other devices. It holds what is the same for every instance; addresses, networks and daemon ports are set on the instance.
**Fields:**

- `autoware_system_design_format`: Must be a version up to the supported `DESIGN_FORMAT_VERSION`.
- `name`: Must match filename (e.g., `MainEcu.device`).
- `description`: (Optional) Brief explanation of the device.
- `role`: (Optional) `ecu`, `switch`, `router`, `sensor`, `actuator` or `other` (default).
- `hardware`: (Optional) Hardware class.
- `interfaces`: (Optional) Interfaces, each with `name`, `type`, `bitrate`, `data_rate` and `description` as in Section 4.5.
- `environments`: (Optional) Execution environments, each with `name`, `kind`, `network`, `shared_memory`, `provides` and `description`.
- `parts`: (Optional) Devices inside this one, e.g., the SoCs of a board or an on-board switch.
  - `name`: Part identifier.
  - `device`: Device entity of the part.
  - `description`: (Optional) Brief explanation of the part.
- `connections`: (Optional) Connections inside the device, in the form of Section 4.5. An endpoint is `<interface>` of the device itself or `<part>.<interface>`. An interface connected here and by an architecture is a port wired on through the device.

```yaml
name: MainEcu.device
role: ecu
hardware: x86_64
interfaces:
  - { name: eth0, bitrate: 10G }
  - { name: can0, type: can }
environments:
  - { name: host, provides: [gpu] }
parts:
  - { name: switch, device: SwitchIc.device }
connections:
  - link: [eth0, switch.p0]
```

## 5. Base-Variant Pattern

The Autoware System Designer supports a base-variant pattern that allows you to define a base configuration and then create variants that inherit from it. This pattern is useful for creating reusable configurations, reducing duplication, and creating mode-specific configurations (e.g., Runtime vs. Simulation) or vehicle-specific variants.

### 5.1. Using the `base` Field

Instead of defining all fields directly, you can specify a `base` field that references another entity of the same type. When using `base`, the current configuration inherits all fields from the base entity. To modify the inherited configuration, you must use `override` and `remove` keys (see Sections 5.2 and 5.3 for details).

**Base Field Syntax:**

- `base`: Reference to another entity (e.g., `BaseNode.node`, `BaseModule.module`, `BaseSystem.system`).
- `override`: Dictionary containing fields to override or extend (see Section 5.2).
- `remove`: Dictionary containing fields to remove (see Section 5.3).

**When to Use Base:**

- When creating variants of an existing configuration with minor differences
- When you want to inherit the structure from a parent configuration
- When you want to reduce duplication across similar configurations

**Required Fields When Using Base:**
When using `base`, the required fields listed in Section 4.1 (Nodes), Section 4.2 (Modules), and Section 4.3 (Systems) are **not required** in the variant configuration (they are inherited from the base).

**Required Fields When NOT Using Base:**
When not using `base`, you must define all required fields directly as specified in Section 4.1 (Nodes), Section 4.2 (Modules), and Section 4.3 (Systems).

### 5.2. Override Mechanism

The `override` section merges items into the base configuration. Merge behavior depends on field type:

- **Key-based merging** (lists with identifiable keys like `name`): Items with matching keys replace existing items; new keys are appended. Examples: `variables`, `modes`, `compute_units`, `components`, `instances`, `subscribers`, `publishers`, `servers`, `clients`, `param_values`, `processes`.
- **Append-only merging** (lists without keys): All override items are appended. Examples: `connections`, `variable_files`, `parameter_sets`.
- **Dictionary merging**: Fields are merged recursively (e.g., `launch` configuration in nodes).

### 5.3. Remove Mechanism

The `remove` section removes specific items from the base configuration:

- **Key-based removal**: Items are removed where `item[key_field]` matches `spec[key_field]`. For components/instances, connections involving removed entities are automatically filtered out.
- **Full match removal** (lists without keys): Items are removed if they match all properties in the spec (e.g., `connections` require both source and destination ports to match).

### 5.4. Order of Operations

Removals are applied **before** overrides to ensure removed items don't interfere with new additions.

## 6. Constraints & Validation Rules

1. **Type Safety**: Connected ports MUST have identical `message_type`.
2. **Single Publisher**: A subscriber port can have multiple sources, but a publisher port generally drives the topic. In the autoware system designer, one topic is published by one node/port.
3. **Naming Convention**:
   - Files: `PascalCase.type.yaml` (e.g., `LidarDriver.node.yaml`).
   - Instance/Port Names: `snake_case` (e.g., `pointcloud_input`).
4. **Path Resolution**:
   - Use `$(find-pkg-share <package_name>)` for absolute ROS paths.
   - Relative paths are resolved relative to the package defining them.
5. **Required Fields**: See Section 5.1 for details on required fields when using or not using `base`.
6. **Entity References**: `entity`, `base`, `parameter_set` and the deployment target name an entity as `Name.type`. When several packages declare the same name, a bare reference resolves to the definition in the deployment package, then the one in the nearest package. Prefix the package to name one definition: `<package_name>/Name.type` (e.g., `entity: my_sensor_kit_description/SampleSensorKit.module`); the reference fails when that package does not declare the name.

## 7. Examples

### Node Example (0.4.0)

```yaml
autoware_system_design_format: 0.4.0
name: Detector.node
description: Camera-based object detector.
package:
  name: my_perception
  provider: tier4
launch:
  plugin: my_perception::Detector
  executable: detector_node
  node_output: screen
subscribers:
  - name: image
    message_type: sensor_msgs/msg/Image
  - name: ros_transform
    message_type: tf2_msgs/msg/TFMessage
    global: /tf
publishers:
  - name: objects
    description: Detected objects in the camera frame.
    message_type: autoware_perception_msgs/msg/DetectedObjects
    qos:
      reliability: reliable
      durability: transient_local
param_files: []
param_values: []
processes:
  - name: detect
    trigger_conditions:
      - or:
          - on_input: image
          - once: image
    outcomes:
      - to_output: objects
```

### Module Example (0.4.0)

```yaml
autoware_system_design_format: 0.4.0
name: DetectorA.module
instances:
  - name: node_detector
    entity: DetectorA.node
  - name: node_filter
    entity: FilterA.node
subscribers:
  - name: pointcloud
  - name: vector_map
publishers:
  - name: objects
connections:
  - - subscriber.pointcloud
    - node_detector.subscriber.pointcloud
  - - node_detector.publisher.objects
    - node_filter.subscriber.objects
  - - subscriber.vector_map
    - node_filter.subscriber.vector_map
  - - node_filter.publisher.*
    - publisher.*
```

### System Example (0.5.0)

```yaml
autoware_system_design_format: 0.5.0
name: AutowareSample.system
variables:
  - name: config_path
    value: $(find-pkg-share autoware_sample_designs)/config
  - name: vehicle_model
    value: sample_vehicle
variable_files:
  - name: vehicle_info
    value: $(find-pkg-share sample_vehicle_description)/config/vehicle_info.param.yaml
modes:
  - name: Runtime
    description: on-vehicle runtime mode
    default: true
  - name: LoggingSimulation
    description: Logged data replay simulation mode
parameter_sets: []
compute_units:
  - name: main_ecu
  - name: remote_ecu
    kind: offboard
    description: Remote operation station.
components:
  - name: sensing
    entity: SampleSensorKit.module
    namespace: sensing
    compute_unit: main_ecu
    parameter_set: sample_system_sensing.parameter_set

node_groups:
  - name: sensing_container
    type: ros2_component_container_mt
    nodes:
      - /sensing
connections:
  - - localization.publisher.kinematic_state
    - sensing.subscriber.odometry
LoggingSimulation:
  override:
    components:
      - name: sensing
        entity: SampleSensorKit_sim.module
        namespace: sensing
        compute_unit: main_ecu
    node_groups:
      - name: sensing_container
        type: ros2_component_container_mt
        nodes:
          - /sensing
```

### Parameter Set Example (0.4.0)

```yaml
autoware_system_design_format: 0.4.0
name: PerceptionModuleA.parameter_set
parameters:
  - node: /perception/object_recognition/detector_a1/node_detector
    param_files:
      - model_param_path: perception/object_recognition/detector_a1/node_detector/model_param_path.param.yaml
      - ml_package_param_path: perception/object_recognition/detector_a1/node_detector/ml_package_param_path.param.yaml
    param_values:
      - name: build_only
        type: bool
        value: false
```

### E/E Architecture Example (0.5.0)

```yaml
autoware_system_design_format: 0.5.0
name: TypeAlphaBridge.ee_architecture
networks:
  - name: vehicle
    subnet: 172.30.87.0/24
devices:
  - name: ecu_a
    hardware: x86_64
    interfaces:
      - name: eth0
        network: vehicle
        address: 172.30.87.11
    environments:
      - name: host
        daemon_port: 8781
  - name: ecu_b
    hardware: x86_64
    interfaces:
      - name: eth0
        network: vehicle
        address: 172.30.87.12
    environments:
      - name: host
        daemon_port: 8782
main_environment: ecu_a/host
bindings:
  - compute_unit: sensing_perception
    environment: ecu_a/host
  - compute_unit: map_localization
    environment: ecu_b/host
```

## 8. Build System Functions

The `autoware_system_designer` package provides CMake macros to automate the build and deployment process.

### `autoware_system_designer_build_deploy`

Builds a sub-format target: the deployments of a system (`<name>.deployments`) or a subsystem (`<name>.subsystem`).

```cmake
autoware_system_designer_build_deploy(
  <project_name>
  <name>.deployments | <name>.subsystem
  [PRINT_LEVEL=<DEBUG|INFO|WARNING|ERROR|CRITICAL>]
  [STRICT=<AUTO|ON|OFF>]
)
```

### `autoware_system_designer_generate_launcher`

Generates individual node launchers from node configurations.

```cmake
autoware_system_designer_generate_launcher()
```

### `autoware_system_designer_parameter`

Generates parameter files from JSON schemas.

```cmake
autoware_system_designer_parameter()
```

## 9. Sub Formats

The sub formats are build inputs of `autoware_system_designer_build_deploy`. A sub-format file names main-format entities to build, is read only by the package that builds it, and is plain YAML checked by its parser without a schema.

### 9.1. Deployment (`.deployments.yaml`)

Instantiates one system for each listed environment or vehicle.

- `base`: The system to deploy (e.g., `TypeAlpha.system`).
- `deploy_list`: List of deployments.
  - `name`: Deployment name.
  - `arguments`: List of `name` / `value` pairs filling the system `arguments`.

### 9.2. Subsystem (`.subsystem.yaml`)

Launches one module or node behind a generated entry launch file with fixed topics and launch arguments. A subsystem never holds a system; a system is built through its deployments.

- `name`: Launch file stem. Must match filename; lowercase letters, digits and underscores.
- `instance`: The module or node to launch (`name`, `entity`, `path`, `parameter_set` as a list).
- `arguments`: (Optional) Launch arguments of the generated file (`name`, `default`, `description`).
- `variables`: (Optional) Build-time values, as in a system.
- `subscribers` / `publishers` / `servers` / `clients`: (Optional) External interface. Each entry binds the instance port `name` to a `topic` (topic or service name).
- `system_launches`: (Optional) Launch files included ahead of the instance (`package`, `file`, `scoped`, `arguments`).
- `node_groups`: (Optional) As in a system; `external: true` loads into a container the caller starts.
