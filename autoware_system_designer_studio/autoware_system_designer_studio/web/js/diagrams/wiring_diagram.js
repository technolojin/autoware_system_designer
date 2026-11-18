// Wiring diagram: a wiring layout drawn as SVG, with the wires of a time sync
// domain or of a peripheral's hosts lit over the dimmed rest, and the clicked
// device or connection reported to its host.
(function () {
  const SD = window.SD;
  const wiring = (SD.wiring ||= {});
  const NS = "http://www.w3.org/2000/svg";

  // Colour tokens the lit domains cycle through.
  const DOMAIN_COLOURS = 5;
  const ARROW = 5;
  const MIN_ZOOM = 0.3;
  const MAX_ZOOM = 2;

  function svg(tag, attributes = {}, className = "") {
    const node = document.createElementNS(NS, tag);
    if (className) node.setAttribute("class", className);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }

  // Wires and markers a view lights: `lit` maps connection ids to a colour
  // index, `arrows` are wire ends pointing into a device, `badges` the notes
  // per device id, `devices` the marked device ids.
  function syncOverlay(model, layout, domainName) {
    const overlay = { lit: new Map(), arrows: [], badges: new Map(), devices: new Set() };
    const wires = new Map(layout.wires.map((wire) => [wire.id, wire]));
    model.timeSync.forEach((domain, index) => {
      if (domainName && domain.name !== domainName) return;
      const colour = index % DOMAIN_COLOURS;
      if (domain.grandmaster) {
        const id = model.owner(domain.grandmaster.device);
        if (!overlay.badges.has(id)) overlay.badges.set(id, []);
        overlay.badges.get(id).push({
          text: `GM ${domain.name}${domain.source ? ` ← ${domain.source}` : ""}`,
          colour,
        });
        overlay.devices.add(id);
      }
      domain.members.forEach((member) => {
        overlay.devices.add(model.owner(member.device));
        const path = member.path || [];
        path.forEach((id) => {
          if (!overlay.lit.has(id)) overlay.lit.set(id, colour);
        });
        const wire = wires.get(path[path.length - 1]);
        const end =
          wire?.ends.find((e) => e.device === member.device) ||
          wire?.ends.find((e) => e.owner === model.owner(member.device));
        if (end) overlay.arrows.push({ ...end, colour });
      });
    });
    return overlay;
  }

  function peripheralOverlay(model, peripheralName) {
    const overlay = { lit: new Map(), arrows: [], badges: new Map(), devices: new Set() };
    model.peripherals.forEach((peripheral) => {
      if (peripheralName && peripheral.name !== peripheralName) return;
      overlay.devices.add(model.owner(peripheral.device));
      peripheral.hosts.forEach((host) => {
        if (host.device) overlay.devices.add(model.owner(host.device));
        (host.path || []).forEach((id) => overlay.lit.set(id, 0));
      });
    });
    return overlay;
  }

  function wiringDiagram(container, { onSelect } = {}) {
    const scroller = document.createElement("div");
    scroller.className = "wiring-scroll";
    container.appendChild(scroller);
    let model = null;
    let layout = null;
    let root = null;
    let zoom = 1;
    let selection = null;
    let view = { mode: "connections", domain: "", peripheral: "", capacity: true };

    function applyZoom() {
      if (!root) return;
      root.setAttribute("width", layout.width * zoom);
      root.setAttribute("height", layout.height * zoom);
    }

    function pick(type, id, event) {
      event.stopPropagation();
      onSelect?.({ type, id });
    }

    function draw() {
      scroller.replaceChildren();
      root = svg("svg", { viewBox: `0 0 ${layout.width} ${layout.height}` }, "wiring-diagram");
      root.addEventListener("click", () => onSelect?.(null));
      const layers = {};
      ["boxes", "runs", "halos", "stubs", "hits", "texts", "overlay"].forEach((name) => {
        layers[name] = svg("g", {}, `wiring-layer-${name}`);
        root.appendChild(layers[name]);
      });

      layout.devices.forEach((box) => {
        const rect = svg(
          "rect",
          { x: box.x, y: box.y, width: box.w, height: box.h, rx: 3 },
          `wiring-device role-${box.device.role} row-${box.row}`,
        );
        rect.dataset.device = box.id;
        rect.addEventListener("click", (e) => pick("device", box.id, e));
        layers.boxes.appendChild(rect);
      });
      layout.parts.forEach((part) => {
        const rect = svg(
          "rect",
          { x: part.x, y: part.y, width: part.w, height: part.h, rx: 3 },
          `wiring-part${part.bar ? " bar" : ""}`,
        );
        rect.dataset.device = part.id;
        rect.addEventListener("click", (e) => pick("device", part.id, e));
        layers.boxes.appendChild(rect);
      });
      layout.innerStubs.forEach((stub) => {
        const line = svg(
          "line",
          { x1: stub.x, y1: stub.y1, x2: stub.x, y2: stub.y2 },
          "wiring-wire type-ethernet",
        );
        line.dataset.connection = stub.connection;
        layers.stubs.appendChild(line);
      });
      layout.rails.forEach((rail) => {
        const line = svg(
          "line",
          { x1: rail.x1, y1: rail.y, x2: rail.x2, y2: rail.y },
          "wiring-rail",
        );
        line.dataset.device = rail.id;
        line.addEventListener("click", (e) => pick("device", rail.id, e));
        layers.runs.appendChild(line);
        const label = svg("text", { x: rail.x1 + 4, y: rail.y - 7 }, "wiring-text name");
        label.textContent = rail.label;
        layers.texts.appendChild(label);
      });

      layout.wires.forEach((wire) => {
        const c = wire.connection;
        const cls = `wiring-wire type-${c.type.replace(/[^\w-]/g, "-")} kind-${c.kind}`;
        const title = `${c.name ? `${c.name}: ` : ""}${c.endpoints
          .map((e) => (e.interface ? `${e.device}.${e.interface}` : e.device))
          .join(" — ")}`;
        wire.segments.forEach((s) => {
          const horizontal = s.y1 === s.y2;
          const attributes = { x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2 };
          const line = svg("line", attributes, `${cls}${horizontal && wire.run ? " run" : ""}`);
          line.dataset.connection = wire.id;
          (horizontal ? layers.runs : layers.stubs).appendChild(line);
          const hit = svg("line", attributes, "wiring-hit");
          hit.addEventListener("click", (e) => pick("connection", wire.id, e));
          const tip = svg("title");
          tip.textContent = title;
          hit.appendChild(tip);
          layers.hits.appendChild(hit);
        });
        if (wire.label) {
          const label = svg(
            "text",
            {
              x: wire.label.x,
              y: wire.label.y,
              "text-anchor": wire.label.anchor || "start",
            },
            `wiring-capacity${wire.label.name ? " named" : ""}`,
          );
          label.dataset.connection = wire.id;
          // An unknown capacity keeps its place for the rate shown there.
          label.textContent = wire.label.text || "–";
          layers.texts.appendChild(label);
        }
      });
      layout.crossings.forEach((point) =>
        layers.halos.appendChild(
          svg(
            "line",
            { x1: point.x - 5, y1: point.y, x2: point.x + 5, y2: point.y },
            "wiring-halo",
          ),
        ),
      );
      layout.texts.forEach((item) => {
        const label = svg(
          "text",
          { x: item.x, y: item.y, "text-anchor": item.anchor },
          `wiring-text ${item.cls}`,
        );
        label.textContent = item.text;
        layers.texts.appendChild(label);
      });

      scroller.appendChild(root);
      applyZoom();
      applyView();
      applySelection();
    }

    function applyView() {
      if (!root) return;
      const overlayLayer = root.querySelector(".wiring-layer-overlay");
      overlayLayer.replaceChildren();
      let overlay = null;
      if (view.mode === "sync") overlay = syncOverlay(model, layout, view.domain);
      if (view.mode === "peripherals") overlay = peripheralOverlay(model, view.peripheral);
      root.classList.toggle("dimmed", !!overlay);
      root.classList.toggle("no-capacity", !view.capacity);
      root.querySelectorAll(".wiring-wire[data-connection]").forEach((line) => {
        const colour = overlay?.lit.get(line.dataset.connection);
        line.classList.toggle("lit", colour !== undefined);
        if (colour === undefined) delete line.dataset.colour;
        else line.dataset.colour = colour;
      });
      root.querySelectorAll("[data-device]").forEach((node) => {
        node.classList.toggle("marked", !!overlay?.devices.has(model.owner(node.dataset.device)));
      });
      if (!overlay) return;
      overlay.arrows.forEach((arrow) => {
        const sign = arrow.dir === "down" ? -1 : 1;
        const back =
          arrow.dir === "right"
            ? `l${-ARROW * 1.8} ${-ARROW} v${ARROW * 2}`
            : `l${-ARROW} ${sign * ARROW * 1.8} h${ARROW * 2}`;
        const tip = svg("path", { d: `M${arrow.x} ${arrow.y} ${back} z` }, "wiring-arrow");
        tip.dataset.colour = arrow.colour;
        overlayLayer.appendChild(tip);
      });
      overlay.badges.forEach((badges, id) => {
        const at = layout.badgeAnchors.get(id);
        if (!at) return;
        badges.forEach((badge, i) => {
          const label = svg(
            "text",
            { x: at.x, y: at.y + i * 12, "text-anchor": at.anchor },
            "wiring-badge",
          );
          label.dataset.colour = badge.colour;
          label.textContent = badge.text;
          overlayLayer.appendChild(label);
        });
      });
    }

    function applySelection() {
      if (!root) return;
      root.querySelectorAll(".selected").forEach((node) => node.classList.remove("selected"));
      if (!selection) return;
      const attribute = selection.type === "device" ? "data-device" : "data-connection";
      root.querySelectorAll(`[${attribute}]`).forEach((node) => {
        if (node.getAttribute(attribute) === selection.id) node.classList.add("selected");
      });
    }

    return {
      show(nextModel) {
        model = nextModel;
        layout = wiring.layout(model);
        selection = null;
        draw();
      },
      setView(next) {
        view = { ...view, ...next };
        applyView();
      },
      select(next) {
        selection = next;
        applySelection();
      },
      zoomBy(factor) {
        zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom * factor));
        applyZoom();
      },
      // Scales the drawing to the width of its container, never above its own size.
      fit() {
        if (!layout || !scroller.clientWidth) return;
        zoom = Math.min(1, Math.max(MIN_ZOOM, scroller.clientWidth / layout.width));
        applyZoom();
      },
      layout: () => layout,
    };
  }

  wiring.syncOverlay = syncOverlay;
  wiring.peripheralOverlay = peripheralOverlay;
  wiring.diagram = wiringDiagram;
})();
