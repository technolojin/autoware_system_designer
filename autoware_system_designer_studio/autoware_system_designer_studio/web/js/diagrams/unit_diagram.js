// Units diagram: the compute units of an E/E architecture inside the devices
// they are bound to, the peripherals on the left joined to the unit whose
// node drives them, and one line per pair of units that exchange topics,
// both directions on it. A line carries the rate of its flows; the topics
// behind it are the inspector's.
(function () {
  const SD = window.SD;
  const wiring = (SD.wiring ||= {});
  const NS = "http://www.w3.org/2000/svg";

  const G = {
    pad: 24,
    charW: 6.6,
    peripheralH: 34,
    peripheralGap: 8,
    // Room between the peripheral column and the devices for their lines.
    gutter: 150,
    deviceHead: 24,
    devicePad: 10,
    deviceGap: 24,
    unitW: 190,
    unitH: 40,
    unitGap: 12,
    // Bulge of a unit-to-unit arc: a base, a share of its height and a step per stacked arc.
    bulge: 46,
    bulgeShare: 0.3,
    bulgeStep: 34,
    labelRoom: 120,
  };
  const MIN_ZOOM = 0.3;
  const MAX_ZOOM = 2;

  const textW = (value) => Math.ceil((value || "").length * G.charW);

  function svg(tag, attributes = {}, className = "") {
    const node = document.createElementNS(NS, tag);
    if (className) node.setAttribute("class", className);
    Object.entries(attributes).forEach(([key, value]) =>
      node.setAttribute(key, value),
    );
    return node;
  }

  // Rate label of a flow: its bit rate, and the count of its topics without a rate.
  function rateLabel(flow) {
    if (!flow) return "";
    const known = wiring.formatBitrate(flow.bitrate);
    const unrated = flow.unrated ? `+${flow.unrated}?` : "";
    return [known || (unrated ? "" : "0"), unrated].filter(Boolean).join(" ");
  }

  // Stroke width of a line, growing with the log of its busiest direction.
  function weight(line) {
    const rate = Math.max(
      line.forward?.bitrate || 0,
      line.backward?.bitrate || 0,
    );
    if (!rate) return 1.2;
    return Math.min(6, Math.max(1.2, 1.2 + Math.log10(rate / 1e4)));
  }

  function layout(model, mode) {
    const analysis = model.analysis.get(mode) || {
      flows: [],
      loads: new Map(),
    };
    const lines = wiring.unitLines(analysis.flows);

    // Devices with bound units, the main environment's first, in record order.
    const order = new Map(
      model.devices.map((device, index) => [device.id, index]),
    );
    const hosting = new Map();
    [...model.environments.values()]
      .filter((e) => (e.compute_units || []).length)
      .forEach((environment) => {
        const device = model.owner(environment.device);
        if (!hosting.has(device)) hosting.set(device, []);
        environment.compute_units.forEach((unit) =>
          hosting.get(device).push({ unit, environment: environment.id }),
        );
      });
    const mainDevice = model.mainEnvironment
      ? model.owner(model.environments.get(model.mainEnvironment)?.device || "")
      : null;
    const deviceIds = [...hosting.keys()].sort(
      (a, b) =>
        (b === mainDevice) - (a === mainDevice) ||
        (order.get(a) ?? 0) - (order.get(b) ?? 0),
    );

    const peripherals = model.peripherals.filter((p) => p.name);
    const peripheralW = Math.max(
      110,
      ...peripherals.map(
        (p) => Math.max(textW(p.name) + 8, textW(p.device) + 4) + 20,
      ),
    );
    const deviceX = G.pad + (peripherals.length ? peripheralW + G.gutter : 0);
    const deviceW = G.unitW + 2 * G.devicePad;

    const devices = [];
    const units = new Map();
    let y = G.pad;
    deviceIds.forEach((id) => {
      const hosted = hosting.get(id);
      const h =
        G.deviceHead +
        G.devicePad +
        hosted.length * (G.unitH + G.unitGap) -
        G.unitGap +
        G.devicePad;
      const device = model.byId.get(id);
      devices.push({
        id,
        x: deviceX,
        y,
        w: deviceW,
        h,
        label: id,
        sub: device?.hardware || "",
      });
      hosted.forEach(({ unit, environment }, index) => {
        units.set(unit, {
          id: unit,
          environment,
          device: id,
          x: deviceX + G.devicePad,
          y: y + G.deviceHead + G.devicePad + index * (G.unitH + G.unitGap),
          w: G.unitW,
          h: G.unitH,
        });
      });
      y += h + G.deviceGap;
    });
    const devicesBottom = y - G.deviceGap;

    // Peripherals beside the unit they feed, pushed down to keep their order without overlap.
    const target = (p) =>
      lines.find(
        (line) => line.kind === "peripheral" && line.ends[0] === p.name,
      )?.ends[1];
    const centre = (unit) =>
      units.has(unit) ? units.get(unit).y + G.unitH / 2 : Infinity;
    const placed = [];
    let bottom = G.pad - G.peripheralGap;
    peripherals
      .map((p, index) => ({ p, index, want: centre(target(p)) }))
      .sort((a, b) => a.want - b.want || a.index - b.index)
      .forEach(({ p, want }) => {
        const top = Math.max(
          bottom + G.peripheralGap,
          Number.isFinite(want)
            ? want - G.peripheralH / 2
            : bottom + G.peripheralGap,
        );
        placed.push({
          id: p.name,
          device: p.device,
          x: G.pad,
          y: top,
          w: peripheralW,
          h: G.peripheralH,
        });
        bottom = top + G.peripheralH;
      });
    const peripheralBoxes = new Map(placed.map((box) => [box.id, box]));

    // Arcs right of the units, shorter ones inside the longer ones.
    const rightX = deviceX + deviceW;
    const arcs = lines
      .filter((line) => line.kind !== "peripheral")
      .filter((line) => units.has(line.ends[0]) && units.has(line.ends[1]))
      .map((line) => {
        const [a, b] = line.ends.map((unit) => units.get(unit));
        const [upper, lower] = a.y <= b.y ? [a, b] : [b, a];
        return { line, upper, lower, span: lower.y - upper.y };
      })
      .sort((p, q) => p.span - q.span || p.line.id.localeCompare(q.line.id));
    let widest = 0;
    const drawn = [];
    arcs.forEach((arc, index) => {
      const y1 = arc.upper.y + G.unitH / 2 + 6;
      const y2 = arc.lower.y + G.unitH / 2 - 6;
      const bulge =
        G.bulge + arc.span * G.bulgeShare + index * G.bulgeStep * 0.5;
      widest = Math.max(widest, bulge);
      const x1 = rightX - G.devicePad;
      const down =
        arc.line.ends[0] === arc.upper.id
          ? arc.line.forward
          : arc.line.backward;
      const up =
        arc.line.ends[0] === arc.upper.id
          ? arc.line.backward
          : arc.line.forward;
      drawn.push({
        id: arc.line.id,
        line: arc.line,
        d: `M${x1} ${y1} C${x1 + bulge} ${y1}, ${x1 + bulge} ${y2}, ${x1} ${y2}`,
        width: weight(arc.line),
        label: {
          x: x1 + bulge * 0.75 + 6,
          y: (y1 + y2) / 2,
          lines: [
            down && `↓ ${rateLabel(down)}`,
            up && `↑ ${rateLabel(up)}`,
          ].filter(Boolean),
        },
      });
    });
    lines
      .filter((line) => line.kind === "peripheral")
      .forEach((line) => {
        const from = peripheralBoxes.get(line.ends[0]);
        const to = units.get(line.ends[1]);
        if (!from || !to) return;
        const x1 = from.x + from.w;
        const y1 = from.y + from.h / 2;
        const x2 = to.x;
        const y2 = to.y + to.h / 2;
        const mid = (x1 + x2) / 2;
        drawn.push({
          id: line.id,
          line,
          d: `M${x1} ${y1} C${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`,
          width: weight(line),
          label: { x: x1 + 8, y: y1 - 5, lines: [rateLabel(line.forward)] },
        });
      });

    return {
      devices,
      units: [...units.values()],
      peripherals: placed,
      lines: drawn,
      width: rightX + widest + G.labelRoom + G.pad,
      height: Math.max(devicesBottom, bottom) + G.pad,
    };
  }

  function unitDiagram(container, { onSelect } = {}) {
    const scroller = document.createElement("div");
    scroller.className = "wiring-scroll";
    container.appendChild(scroller);
    let shape = null;
    let root = null;
    let zoom = 1;
    let selection = null;

    function applyZoom() {
      if (!root) return;
      root.setAttribute("width", shape.width * zoom);
      root.setAttribute("height", shape.height * zoom);
    }

    function pick(target, event) {
      event.stopPropagation();
      onSelect?.(target);
    }

    function text(layer, x, y, value, cls, anchor = "start") {
      const node = svg(
        "text",
        { x, y, "text-anchor": anchor },
        `wiring-text ${cls}`,
      );
      node.textContent = value;
      layer.appendChild(node);
    }

    function draw() {
      scroller.replaceChildren();
      root = svg(
        "svg",
        { viewBox: `0 0 ${shape.width} ${shape.height}` },
        "wiring-diagram unit-diagram",
      );
      root.addEventListener("click", () => onSelect?.(null));
      const boxes = svg("g");
      const wires = svg("g");
      const texts = svg("g");
      root.append(boxes, wires, texts);

      shape.devices.forEach((box) => {
        const rect = svg(
          "rect",
          { x: box.x, y: box.y, width: box.w, height: box.h, rx: 3 },
          "wiring-device row-core",
        );
        rect.dataset.device = box.id;
        rect.addEventListener("click", (e) =>
          pick({ type: "device", id: box.id }, e),
        );
        boxes.appendChild(rect);
        text(texts, box.x + 8, box.y + 16, box.label, "name");
        if (box.sub)
          text(texts, box.x + box.w - 8, box.y + 16, box.sub, "sub", "end");
      });
      shape.units.forEach((box) => {
        const rect = svg(
          "rect",
          { x: box.x, y: box.y, width: box.w, height: box.h, rx: 3 },
          "wiring-device unit-box",
        );
        rect.dataset.unit = box.id;
        rect.addEventListener("click", (e) =>
          pick({ type: "unit", id: box.id }, e),
        );
        boxes.appendChild(rect);
        text(texts, box.x + 8, box.y + 16, box.id, "name");
        text(texts, box.x + 8, box.y + 31, box.environment, "sub");
      });
      shape.peripherals.forEach((box) => {
        const rect = svg(
          "rect",
          { x: box.x, y: box.y, width: box.w, height: box.h, rx: 3 },
          "wiring-device role-sensor",
        );
        rect.dataset.device = box.device;
        rect.addEventListener("click", (e) =>
          pick({ type: "device", id: box.device }, e),
        );
        boxes.appendChild(rect);
        text(texts, box.x + 8, box.y + 14, box.id, "name peripheral");
        text(texts, box.x + 8, box.y + 27, box.device, "sub");
      });
      shape.lines.forEach((line) => {
        const cls = `unit-line kind-${line.line.kind}${line.line.local ? " local" : ""}`;
        const path = svg(
          "path",
          { d: line.d, "stroke-width": line.width },
          cls,
        );
        path.dataset.line = line.id;
        wires.appendChild(path);
        const hit = svg("path", { d: line.d }, "wiring-hit unit-hit");
        hit.addEventListener("click", (e) =>
          pick({ type: "line", id: line.id }, e),
        );
        wires.appendChild(hit);
        line.label.lines.forEach((value, index) => {
          const label = svg(
            "text",
            {
              x: line.label.x,
              y:
                line.label.y +
                (index - (line.label.lines.length - 1) / 2) * 12 +
                3,
            },
            "wiring-capacity named unit-rate",
          );
          label.textContent = value;
          texts.appendChild(label);
        });
      });

      scroller.appendChild(root);
      applyZoom();
      applySelection();
    }

    function applySelection() {
      if (!root) return;
      root
        .querySelectorAll(".selected")
        .forEach((node) => node.classList.remove("selected"));
      if (!selection) return;
      const attribute = {
        device: "data-device",
        unit: "data-unit",
        line: "data-line",
      }[selection.type];
      if (!attribute) return;
      root.querySelectorAll(`[${attribute}]`).forEach((node) => {
        if (node.getAttribute(attribute) === selection.id)
          node.classList.add("selected");
      });
    }

    return {
      show(model, mode) {
        shape = layout(model, mode);
        draw();
      },
      select(next) {
        selection = next;
        applySelection();
      },
      zoomBy(factor) {
        zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom * factor));
        applyZoom();
      },
      fit() {
        if (!shape || !scroller.clientWidth) return;
        zoom = Math.min(
          1,
          Math.max(MIN_ZOOM, scroller.clientWidth / shape.width),
        );
        applyZoom();
      },
      element: scroller,
    };
  }

  wiring.unitLayout = layout;
  wiring.rateLabel = rateLabel;
  wiring.unitDiagram = unitDiagram;
})();
