// Wiring model: one exported E/E architecture record as devices, their
// interfaces and the connections between them, with the lookups the wiring
// layout and the E/E activity read. A record without connections gets one
// logical connection per network, joining the interfaces attached to it.
(function () {
  const SD = window.SD;
  const wiring = (SD.wiring ||= {});

  const INFRA_ROLES = new Set(["switch", "router"]);
  const KIND_LINK = "link";
  const KIND_BUS = "bus";
  // Connection standing in for a network of a record without wiring.
  const KIND_NETWORK = "network";

  const list = (value) => (Array.isArray(value) ? value : []);
  const text = (value) => (typeof value === "string" ? value : "");

  // Short capacity label of a bit rate in bits/s: "10G", "500k"; empty without one.
  function formatBitrate(bitrate) {
    if (typeof bitrate !== "number" || !(bitrate > 0)) return "";
    const steps = [
      [1e9, "G"],
      [1e6, "M"],
      [1e3, "k"],
    ];
    for (const [scale, unit] of steps) {
      if (bitrate >= scale) return `${Number((bitrate / scale).toFixed(2))}${unit}`;
    }
    return String(bitrate);
  }

  function interfaceOf(raw) {
    return {
      name: text(raw?.name),
      type: text(raw?.type) || "ethernet",
      bitrate: typeof raw?.bitrate === "number" ? raw.bitrate : null,
      attachments: list(raw?.attachments)
        .filter((a) => a && typeof a === "object")
        .map((a) => ({ network: text(a.network), address: text(a.address) })),
    };
  }

  function wiredDevice(raw) {
    return {
      id: text(raw.id) || text(raw.name),
      name: text(raw.name) || text(raw.id),
      parent: text(raw.parent) || null,
      definition: text(raw.definition),
      role: text(raw.role) || "other",
      hardware: text(raw.hardware),
      description: text(raw.description),
      interfaces: list(raw.interfaces).map(interfaceOf),
      environments: list(raw.environments).filter((e) => typeof e === "string"),
    };
  }

  // Device of a record's declared `devices`, which carry networks on their interfaces.
  function declaredDevice(raw) {
    const name = text(raw.name);
    const environments = list(raw.environments)
      .map((e) => text(e?.name))
      .filter(Boolean)
      .map((e) => `${name}/${e}`);
    return {
      id: name,
      name,
      parent: null,
      definition: text(raw.device),
      role: environments.length ? "ecu" : "other",
      hardware: text(raw.hardware),
      description: text(raw.description),
      interfaces: list(raw.interfaces).map((i) =>
        interfaceOf({
          name: i?.name,
          type: i?.type,
          attachments: i?.network ? [{ network: i.network, address: i.address }] : [],
        }),
      ),
      environments,
    };
  }

  function endpointOf(raw) {
    return {
      device: text(raw?.device),
      interface: text(raw?.interface) || null,
    };
  }

  function connectionOf(raw, index) {
    return {
      id: text(raw.id) || `c${index}`,
      kind: raw.kind === KIND_BUS ? KIND_BUS : KIND_LINK,
      name: text(raw.name) || null,
      type: text(raw.type) || "ethernet",
      bitrate: typeof raw.bitrate === "number" ? raw.bitrate : null,
      internal: text(raw.internal) || null,
      endpoints: list(raw.endpoints).map(endpointOf),
    };
  }

  function networkConnections(networks, devices) {
    return networks
      .map((network) => ({
        id: `network:${network.name}`,
        kind: KIND_NETWORK,
        name: network.name,
        type: KIND_NETWORK,
        bitrate: null,
        internal: null,
        endpoints: devices.flatMap((device) =>
          device.interfaces
            .filter((i) => i.attachments.some((a) => a.network === network.name))
            .map((i) => ({ device: device.id, interface: i.name })),
        ),
      }))
      .filter((connection) => connection.endpoints.length);
  }

  function model(record) {
    const source = record && typeof record === "object" ? record : {};
    const networks = list(source.networks)
      .filter((n) => n && typeof n === "object" && text(n.name))
      .map((n) => ({
        name: n.name,
        subnet: text(n.subnet),
        vlan: typeof n.vlan === "number" ? n.vlan : null,
        carries: list(n.carries).filter((c) => typeof c === "string"),
        description: text(n.description),
      }));

    const wired = list(source.wiring?.devices).filter((d) => d && typeof d === "object");
    const devices = (
      wired.length
        ? wired.map(wiredDevice)
        : list(source.devices)
            .filter((d) => d && typeof d === "object")
            .map(declaredDevice)
    ).filter((device) => device.id);
    const byId = new Map(devices.map((device) => [device.id, device]));

    const known = (endpoint) => byId.has(endpoint.device);
    let connections = list(source.wiring?.connections)
      .filter((c) => c && typeof c === "object")
      .map(connectionOf)
      .map((c) => ({ ...c, endpoints: c.endpoints.filter(known) }))
      .filter((c) => c.endpoints.length);
    const logical = !connections.length;
    if (logical) connections = networkConnections(networks, devices);

    // Top-level device a device lies in; itself when it is no part.
    function owner(id) {
      let device = byId.get(id);
      const seen = new Set();
      while (device?.parent && byId.has(device.parent) && !seen.has(device.id)) {
        seen.add(device.id);
        device = byId.get(device.parent);
      }
      return device ? device.id : id;
    }

    const parts = new Map();
    devices.forEach((device) => {
      if (device.parent && byId.has(device.parent)) {
        if (!parts.has(device.parent)) parts.set(device.parent, []);
        parts.get(device.parent).push(device);
      }
    });

    const environments = new Map(
      list(source.environments)
        .filter((e) => e && typeof e.id === "string")
        .map((e) => [e.id, e]),
    );

    const timeSync = list(source.time_sync)
      .filter((d) => d && typeof d === "object" && text(d.name))
      .map((d) => ({
        name: d.name,
        profile: text(d.profile),
        reference: text(d.reference) || null,
        source: text(d.source) || null,
        grandmaster: d.grandmaster ? endpointOf(d.grandmaster) : null,
        members: list(d.members).map((m) => ({
          ...endpointOf(m),
          path: Array.isArray(m?.path) ? m.path.filter((p) => typeof p === "string") : null,
        })),
      }));

    const peripherals = list(source.peripherals)
      .filter((p) => p && typeof p === "object" && text(p.name))
      .map((p) => ({
        name: p.name,
        device: text(p.device),
        component: text(p.component),
        description: text(p.description),
        addresses: list(p.addresses).filter((a) => a && typeof a === "object"),
        hosts: list(p.hosts)
          .filter((h) => h && typeof h === "object")
          .map((h) => ({
            ...h,
            path: Array.isArray(h.path) ? h.path.filter((c) => typeof c === "string") : null,
          })),
      }));

    return {
      name: text(source.name),
      description: text(source.description),
      mainEnvironment: text(source.main_environment) || null,
      networks,
      devices,
      byId,
      parts,
      owner,
      connections,
      logical,
      environments,
      timeSync,
      peripherals,
    };
  }

  // Whether a top-level device forwards between its ports.
  function isInfra(device) {
    return INFRA_ROLES.has(device.role);
  }

  // Environments of a device and of its parts.
  function deviceEnvironments(wiringModel, device) {
    return [
      ...device.environments,
      ...(wiringModel.parts.get(device.id) || []).flatMap((p) => p.environments),
    ];
  }

  wiring.KIND_LINK = KIND_LINK;
  wiring.KIND_BUS = KIND_BUS;
  wiring.KIND_NETWORK = KIND_NETWORK;
  wiring.formatBitrate = formatBitrate;
  wiring.model = model;
  wiring.isInfra = isInfra;
  wiring.deviceEnvironments = deviceEnvironments;
})();
