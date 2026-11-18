// Unit tests for the wiring model, layout and overlays of the E/E activity,
// over the exported record of a vehicle with composite ECUs, switches, buses,
// hung sensors and chained time sync domains, and over records without wiring.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const DIAGRAMS = path.join(
  __dirname,
  "..",
  "..",
  "autoware_system_designer_studio",
  "web",
  "js",
  "diagrams",
);

global.window = { SD: {} };
global.document = { createElementNS: () => ({}) };
require(path.join(DIAGRAMS, "wiring_model.js"));
require(path.join(DIAGRAMS, "wiring_layout.js"));
require(path.join(DIAGRAMS, "wiring_diagram.js"));
const wiring = global.window.SD.wiring;

const RECORD = require(path.join(__dirname, "..", "data", "multi_ecu_ee_architecture.json"));

// Record of the format without wiring: devices carry their networks.
const DECLARED = {
  name: "Bridge",
  main_environment: "ecu_a/host",
  networks: [{ name: "vehicle", subnet: "172.30.87.0/24" }],
  devices: ["ecu_a", "ecu_b", "ecu_c"].map((name, i) => ({
    name,
    hardware: "x86_64",
    interfaces: [{ name: "eth0", network: "vehicle", address: `172.30.87.1${i + 1}` }],
    environments: [{ name: "host", daemon_port: 8781 + i }],
  })),
  environments: [],
};

const box = (layout, id) => layout.devices.find((d) => d.id === id);
const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

test("bit rates shorten to a capacity label", () => {
  assert.equal(wiring.formatBitrate(10e9), "10G");
  assert.equal(wiring.formatBitrate(2.5e9), "2.5G");
  assert.equal(wiring.formatBitrate(500000), "500k");
  assert.equal(wiring.formatBitrate(null), "");
});

test("the model resolves parts to the device they lie in", () => {
  const model = wiring.model(RECORD);
  assert.equal(model.logical, false);
  assert.equal(model.owner("main.switch"), "main");
  assert.equal(model.owner("lidar_rear_upper"), "lidar_rear_upper");
  assert.deepEqual(
    model.parts.get("main").map((p) => p.id),
    ["main.switch"],
  );
  assert.deepEqual(wiring.deviceEnvironments(model, model.byId.get("main")), ["main/host"]);
});

test("a record without wiring joins its devices by their networks", () => {
  const model = wiring.model(DECLARED);
  assert.equal(model.logical, true);
  assert.equal(
    model.devices.every((d) => d.role === "ecu"),
    true,
  );
  assert.deepEqual(
    model.connections.map((c) => [c.kind, c.name, c.endpoints.length]),
    [[wiring.KIND_NETWORK, "vehicle", 3]],
  );
  const layout = wiring.layout(model);
  assert.equal(layout.devices.length, 3);
  assert.equal(layout.wires.length, 1);
  assert.equal(layout.wires[0].ends.length, 3);
});

test("an empty record lays out to nothing", () => {
  const layout = wiring.layout(wiring.model({}));
  assert.equal(layout.devices.length, 0);
  assert.equal(Number.isFinite(layout.width + layout.height), true);
});

test("hosting devices form the core row, shared switches rails, the rest hangs or sits above", () => {
  const layout = wiring.layout(wiring.model(RECORD));
  const rows = Object.fromEntries(layout.devices.map((d) => [d.id, d.row]));
  ["main", "sub", "perception1", "perception2", "addon", "logging"].forEach((id) =>
    assert.equal(rows[id], "core", id),
  );
  assert.deepEqual(
    layout.rails.map((rail) => rail.id),
    ["hub_10g", "hub_1g"],
  );
  assert.equal(rows.gnss, "above");
  // A switch wired to one core device hangs below it with the devices behind it.
  assert.equal(rows.radar_gateway, "below");
  assert.equal(rows.radar_front_center, "below");
  assert.equal(box(layout, "gnss").y < layout.rails[0].y, true);
  assert.equal(layout.rails[1].y < box(layout, "main").y, true);
  assert.equal(box(layout, "radar_front_center").y > box(layout, "radar_gateway").y, true);
  assert.deepEqual(
    layout.parts.map((p) => [p.id, p.parent, p.bar]),
    [
      ["main.switch", "main", true],
      ["sub.switch", "sub", true],
    ],
  );
});

test("boxes hold finite coordinates and do not overlap", () => {
  const layout = wiring.layout(wiring.model(RECORD));
  const text = JSON.stringify(layout, (key, value) =>
    typeof value === "number" && !Number.isFinite(value) ? "BAD" : value,
  );
  assert.equal(text.includes("BAD"), false);
  layout.devices.forEach((a, i) =>
    layout.devices.slice(i + 1).forEach((b) => {
      assert.equal(overlaps(a, b), false, `${a.id} over ${b.id}`);
    }),
  );
  layout.devices.forEach((d) => {
    assert.equal(d.x >= 0 && d.x + d.w <= layout.width, true, d.id);
    assert.equal(d.y >= 0 && d.y + d.h <= layout.height, true, d.id);
  });
});

test("every connection between devices is a wire of axis-aligned runs ending on its devices", () => {
  const model = wiring.model(RECORD);
  const layout = wiring.layout(model);
  const external = model.connections.filter((c) => !c.internal);
  assert.deepEqual(layout.wires.map((w) => w.id).sort(), external.map((c) => c.id).sort());
  layout.wires.forEach((wire) => {
    assert.equal(wire.segments.length > 0, true, wire.id);
    wire.segments.forEach((s) => assert.equal(s.x1 === s.x2 || s.y1 === s.y2, true, wire.id));
    assert.equal(wire.ends.length, wire.connection.endpoints.length, wire.id);
  });
  // A device hung by a link sits straight under its port.
  const hung = layout.wires.find((w) => w.id === "c19");
  assert.equal(hung.segments.length, 1);
  assert.equal(hung.segments[0].x1, hung.segments[0].x2);
  // The wiring inside a composite device is drawn in its box.
  assert.deepEqual(layout.innerStubs.map((s) => s.connection).sort(), ["c0", "c1", "c2", "c3"]);
});

test("each wire keeps a capacity label, filled where the bit rate is known", () => {
  const layout = wiring.layout(wiring.model(RECORD));
  const label = (id) => layout.wires.find((w) => w.id === id).label;
  assert.equal(label("c4").text, "10G");
  assert.equal(label("c10").text, "1G");
  assert.equal(label("c28").text, "");
  assert.equal(label("c34").text, "vcu_can_1 · can · 500k");
  layout.wires.forEach((wire) => assert.notEqual(wire.label, null, wire.id));
});

test("a run passing over a rail or another wire's run is a crossing", () => {
  const layout = wiring.layout(wiring.model(RECORD));
  const [upper, lower] = layout.rails;
  const stub = layout.wires.find((w) => w.id === "c5").segments[0];
  assert.equal(lower.y > upper.y, true);
  assert.equal(
    layout.crossings.some((c) => c.x === stub.x1 && c.y === lower.y),
    true,
  );
  assert.equal(
    layout.crossings.some((c) => c.x === stub.x1 && c.y === upper.y),
    false,
  );
});

test("a time sync domain lights its paths and points at its members", () => {
  const model = wiring.model(RECORD);
  const layout = wiring.layout(model);
  const all = wiring.syncOverlay(model, layout, "");
  assert.deepEqual(
    all.badges.get("main").map((b) => b.text),
    ["GM ecus", "GM sensors_main"],
  );
  assert.deepEqual(
    all.badges.get("sub").map((b) => b.text),
    ["GM sensors_sub ← ecus"],
  );
  assert.notEqual(all.lit.get("c10"), all.lit.get("c19"));

  const one = wiring.syncOverlay(model, layout, "sensors_sub");
  assert.deepEqual([...one.lit.keys()].sort(), ["c23", "c24", "c25", "c26", "c3"]);
  assert.equal(one.arrows.length, 4);
  const lidar = box(layout, "lidar_front_upper");
  assert.equal(
    one.arrows.some((a) => a.device === "lidar_front_upper" && a.y === lidar.y && a.dir === "down"),
    true,
  );
  // A member behind a hung switch is entered from its side.
  const main = wiring.syncOverlay(model, layout, "sensors_main");
  assert.equal(main.arrows.find((a) => a.device === "radar_front_center").dir, "right");
});

test("a peripheral lights the paths to its hosts and marks both ends", () => {
  const model = wiring.model(RECORD);
  const one = wiring.peripheralOverlay(model, "lidar_rear_upper");
  assert.deepEqual([...one.lit.keys()].sort(), ["c0", "c19"]);
  assert.deepEqual([...one.devices].sort(), ["lidar_rear_upper", "main"]);
  const all = wiring.peripheralOverlay(model, "");
  assert.equal(all.devices.size > 2, true);
});
