// E/E activity: the wiring of an E/E architecture the system is declared for.
// The side panel picks the architecture, the mode whose flows are analysed and
// what the main view shows: the connections with their load, the compute
// units with one line per flow into and out of them, a time sync domain or a
// peripheral's hosts; it lists the environments with their compute units. The
// inspector shows the selected device, connection, unit, line, peripheral or
// gateway node. The focused peripheral is shared with the Explore activity,
// whose gateway nodes open here and whose node graph a gateway node opens.
(function () {
  const SD = window.SD;
  const { element, field, section, boundSelect, segmented, list } = SD.widgets;
  const wiring = SD.wiring;

  // Views of the wiring, then the compute units on a row of their own.
  const VIEWS = [
    ["connections", "Connections"],
    ["sync", "Time sync"],
    ["peripherals", "Peripherals"],
    ["units", "Units"],
  ];
  const ZOOM_STEP = 1.25;

  function row(label, value) {
    const line = element("div", "info-row");
    line.append(
      element("span", "info-label", label),
      element("span", "info-value", value),
    );
    return line;
  }

  function card(title, ...children) {
    const box = element("div", "info-card");
    box.appendChild(element("div", "info-title", title));
    box.append(...children.filter(Boolean));
    return box;
  }

  const endpointLabel = (e) =>
    e.interface ? `${e.device}.${e.interface}` : e.device;
  const capacityLabel = (bitrate) => {
    const short = wiring.formatBitrate(bitrate);
    return short ? `${short}bit/s` : "not given";
  };
  const percent = (value) =>
    typeof value === "number" ? `${Math.round(value * 100)} %` : "–";
  const endLabel = (end) =>
    end.kind === "peripheral" ? `${end.name} (peripheral)` : end.name;

  // Rate of a flow with the count of its topics whose rate is unknown.
  function flowRate(flow) {
    const known = flow.bitrate ? capacityLabel(flow.bitrate) : "";
    const unrated = flow.unrated ? `${flow.unrated} without a rate` : "";
    return [known, unrated].filter(Boolean).join(" · ") || "–";
  }

  function flowCard(model, flow) {
    const rows = [
      row("Rate", flowRate(flow)),
      flow.nodes.length && row("Gateway", flow.nodes.join(", ")),
      flow.kind === "topics" &&
        row("Way", flow.local ? "inside one device" : "over the wiring"),
      ...flow.routes.map((route, index) =>
        row(
          index ? "" : "Via",
          route.map((id) => connectionName(model, id)).join(" → ") || "–",
        ),
      ),
    ];
    const box = card(
      `${endLabel(flow.source)} → ${endLabel(flow.target)}`,
      ...rows,
    );
    flow.topics.forEach((topic) => {
      const group = element("div", "info-group");
      group.appendChild(element("div", "info-subtitle", topic.topic));
      if (topic.msgType) group.appendChild(row("Type", topic.msgType));
      group.appendChild(
        row(
          "Rate",
          [
            topic.frequency ? `${topic.frequency} Hz` : "no rate",
            topic.bitrate
              ? `${wiring.formatBitrate(topic.bitrate)}bit/s`
              : "not measured",
          ]
            .filter(Boolean)
            .join(" · "),
        ),
      );
      box.appendChild(group);
    });
    return box;
  }

  function connectionName(model, id) {
    const connection = model.connections.find((c) => c.id === id);
    return connection?.name || id;
  }

  function unitCards(model, mode, unit) {
    const environment = [...model.environments.values()].find((e) =>
      (e.compute_units || []).includes(unit),
    );
    const flows = wiring.unitFlows(model.analysis.get(mode)?.flows || [], unit);
    const listed = (title, items, end) =>
      card(
        title,
        ...(items.length
          ? items.map((flow) => {
              const group = element("div", "info-group");
              group.append(
                element("div", "info-subtitle", endLabel(flow[end])),
                row("Rate", flowRate(flow)),
              );
              return group;
            })
          : [element("p", "panel-empty", "None.")]),
      );
    return [
      card(
        unit,
        environment && row("Environment", environment.id),
        environment && row("Device", environment.device),
        environment?.provides?.length &&
          row("Provides", environment.provides.join(", ")),
      ),
      listed("In", flows.inputs, "source"),
      listed("Out", flows.outputs, "target"),
    ];
  }

  function lineCards(model, mode, id) {
    const line = wiring
      .unitLines(model.analysis.get(mode)?.flows || [])
      .find((l) => l.id === id);
    if (!line) return [];
    const cards = [line.forward, line.backward]
      .filter(Boolean)
      .map((flow) => flowCard(model, flow));
    if (line.kind === "peripheral") {
      const peripheral = model.peripherals.find((p) => p.name === line.ends[0]);
      if (peripheral?.parameters.length)
        cards.push(
          card(
            "Parameters",
            ...peripheral.parameters.map((p) =>
              row(p.name, `${p.resolved ?? "–"} (${p.value})`),
            ),
          ),
        );
    }
    return cards;
  }

  function deviceCards(model, device) {
    const cards = [];
    const identity = card(
      device.id,
      row("Role", device.role),
      device.definition && row("Device", device.definition),
      device.hardware && row("Hardware", device.hardware),
      device.parent && row("Part of", device.parent),
      device.description && row("About", device.description),
    );
    cards.push(identity);

    if (device.interfaces.length) {
      const interfaces = card("Interfaces");
      device.interfaces.forEach((iface) => {
        const group = element("div", "info-group");
        group.appendChild(
          element(
            "div",
            "info-subtitle",
            [iface.name, iface.type, wiring.formatBitrate(iface.bitrate)]
              .filter(Boolean)
              .join(" · "),
          ),
        );
        iface.attachments.forEach((a) =>
          group.appendChild(row(a.network || "address", a.address || "–")),
        );
        model.connections
          .filter((c) =>
            c.endpoints.some(
              (e) => e.device === device.id && e.interface === iface.name,
            ),
          )
          .forEach((c) =>
            group.appendChild(
              row(
                c.kind,
                c.name ||
                  c.endpoints
                    .filter((e) => e.device !== device.id)
                    .map(endpointLabel)
                    .join(", "),
              ),
            ),
          );
        interfaces.appendChild(group);
      });
      cards.push(interfaces);
    }

    const environments = wiring.deviceEnvironments(model, device);
    if (environments.length) {
      const hosted = card("Environments");
      environments.forEach((id) => {
        const environment = model.environments.get(id) || {};
        const group = element("div", "info-group");
        group.appendChild(
          element(
            "div",
            "info-subtitle",
            id === model.mainEnvironment ? `${id} (main)` : id,
          ),
        );
        group.append(
          row("Units", (environment.compute_units || []).join(", ") || "none"),
          row("Provides", (environment.provides || []).join(", ") || "–"),
          row(
            "Daemon",
            [environment.address, environment.daemon_port]
              .filter((v) => v !== null && v !== undefined)
              .join(":") || "–",
          ),
        );
        hosted.appendChild(group);
      });
      cards.push(hosted);
    }

    const bound = model.peripherals.filter((p) => p.device === device.id);
    if (bound.length) {
      const peripherals = card("Peripherals");
      bound.forEach((p) => {
        const group = element("div", "info-group");
        group.appendChild(element("div", "info-subtitle", p.name));
        if (p.interface) group.appendChild(row("Interface", p.interface));
        p.nodes.forEach((g) => group.appendChild(row("Gateway", g.node)));
        if (!p.nodes.length && p.component)
          group.appendChild(row("Component", p.component));
        p.parameters.forEach((parameter) =>
          group.appendChild(
            row(
              parameter.name,
              `${parameter.resolved ?? "–"} (${parameter.value})`,
            ),
          ),
        );
        p.hosts.forEach((host) =>
          group.appendChild(
            row(
              host.compute_unit || "host",
              [
                host.device && endpointLabel(host),
                host.address,
                host.path ? "" : "no path",
              ]
                .filter(Boolean)
                .join(" · "),
            ),
          ),
        );
        peripherals.appendChild(group);
      });
      cards.push(peripherals);
    }

    const roles = [];
    model.timeSync.forEach((domain) => {
      if (domain.grandmaster?.device === device.id)
        roles.push(
          row(
            domain.name,
            `grandmaster · ${domain.grandmaster.interface || ""}`,
          ),
        );
      domain.members
        .filter((m) => m.device === device.id)
        .forEach((m) =>
          roles.push(
            row(
              domain.name,
              `slave · ${m.interface || ""}${m.path ? "" : " · no path"}`,
            ),
          ),
        );
    });
    if (roles.length) cards.push(card("Time sync", ...roles));
    return cards;
  }

  // A peripheral: its device, its gateway nodes, each opening the node graph,
  // the values they take and the interfaces their units reach it by.
  function peripheralCards(model, mode, name, openNode) {
    const peripheral = model.peripherals.find((p) => p.name === name);
    if (!peripheral) return [];
    const analysis = model.analysis.get(mode);
    const connected = !analysis?.disconnected.includes(name);
    const gateways = card("Gateway nodes");
    if (!peripheral.nodes.length)
      gateways.appendChild(
        row("Component", peripheral.component || "none named"),
      );
    peripheral.nodes.forEach((gateway) => {
      const button = element("button", "link-button", gateway.node);
      button.type = "button";
      button.title = "Show the node in the node graph";
      button.addEventListener("click", () => openNode(gateway.node));
      const group = element("div", "info-group");
      group.append(
        button,
        row("Unit", gateway.computeUnits.join(", ") || "none"),
      );
      gateways.appendChild(group);
    });
    const cards = [
      card(
        name,
        row(
          "Device",
          peripheral.interface
            ? `${peripheral.device}.${peripheral.interface}`
            : peripheral.device,
        ),
        analysis &&
          row(
            `In ${mode}`,
            connected ? "connected" : "not connected: no gateway node",
          ),
        peripheral.description && row("About", peripheral.description),
      ),
      gateways,
    ];
    if (peripheral.parameters.length)
      cards.push(
        card(
          "Parameters",
          ...peripheral.parameters.map((p) =>
            row(p.name, `${p.resolved ?? "–"} (${p.value})`),
          ),
        ),
      );
    if (peripheral.hosts.length)
      cards.push(
        card(
          "Reached by",
          ...peripheral.hosts.map((host) =>
            row(
              host.compute_unit || "host",
              [
                host.device && endpointLabel(host),
                host.address,
                host.path ? "" : "no path",
              ]
                .filter(Boolean)
                .join(" · "),
            ),
          ),
        ),
      );
    return cards;
  }

  function connectionCards(model, mode, connection) {
    const domains = model.timeSync
      .filter((d) =>
        d.members.some((m) => (m.path || []).includes(connection.id)),
      )
      .map((d) => d.name);
    const analysis = model.analysis.get(mode);
    const load = analysis?.loads.get(connection.id);
    const carried = (load?.flows || [])
      .map((id) => analysis.flows.find((f) => f.id === id))
      .filter(Boolean);
    return [
      card(
        connection.name || connection.id,
        row("Kind", connection.kind),
        row("Type", connection.type),
        row("Capacity", capacityLabel(connection.bitrate)),
        connection.internal && row("Inside", connection.internal),
        domains.length && row("Time sync", domains.join(", ")),
      ),
      card(
        "Endpoints",
        ...connection.endpoints.map((e) =>
          row(e.device, e.interface || "any port"),
        ),
      ),
      load &&
        card(
          `Load in ${mode}`,
          row("Busiest direction", capacityLabel(load.load)),
          row("Utilization", percent(load.utilization)),
          ...carried.map((flow) => {
            const group = element("div", "info-group");
            group.append(
              element(
                "div",
                "info-subtitle",
                `${endLabel(flow.source)} → ${endLabel(flow.target)}`,
              ),
              row("Rate", flowRate(flow)),
            );
            return group;
          }),
        ),
    ].filter(Boolean);
  }

  SD.workbench.registerActivity({
    id: "architecture",
    title: "E/E",
    group: "Experimental",
    order: 50,
    daemon: false,
    inspector: true,
    icon: "M3 3h5v4H3zM12 3h5v4h-5zM7.5 13h5v4h-5zM5.5 7v3h9V7M10 10v3",
    status: (store) =>
      store.get("architecture") ? [store.get("architecture")] : [],
    mount({ side, main, inspector }, { config, store }) {
      const records = config.eeArchitectures || [];
      const names = records.map((record) => record.name);
      store.define("architecture", names[0] || "", (v) => names.includes(v));
      store.define("wiring", VIEWS[0][0], (v) =>
        VIEWS.some(([id]) => id === v),
      );
      store.define("domain", "");
      store.define("peripheral", "");
      store.syncUrl();

      const frame = SD.widgets.mainView({
        empty: names.length
          ? ""
          : "No E/E architecture is declared for this system.",
      });
      main.appendChild(frame.element);
      const empty = element(
        "p",
        "panel-empty",
        "Select a device or a connection.",
      );
      const details = element("div", "info-panel");
      details.hidden = true;
      inspector.append(empty, details);

      let model = null;
      let selected = null;
      const diagram = wiring.diagram(frame.body, { onSelect: select });
      const unitsDiagram = wiring.unitDiagram(frame.body, { onSelect: select });
      const shown = () =>
        store.get("wiring") === "units" ? unitsDiagram : diagram;
      [
        ["−", "Zoom out", () => shown().zoomBy(1 / ZOOM_STEP)],
        ["+", "Zoom in", () => shown().zoomBy(ZOOM_STEP)],
        ["Fit", "Fit the width", () => shown().fit()],
      ].forEach(([label, title, run]) => {
        const button = element("button", "panel-button", label);
        button.type = "button";
        button.title = title;
        button.addEventListener("click", run);
        frame.trailing.appendChild(button);
      });

      // Shows a gateway node in the Explore node graph.
      async function openNode(path) {
        const data = await SD.NamespaceTree.loadStructure(
          config.dataBase,
          store.get("mode"),
        );
        const entity = SD.NamespaceTree.entities(data || {}).find(
          (e) => e.entity_type === "node" && e.path === path,
        );
        if (!entity) return;
        store.set("diagram", "node_diagram");
        store.set("entity", SD.NamespaceTree.entityKey(entity));
        store.set("activity", "explore");
      }

      function select(target) {
        selected = target;
        if (target?.type === "peripheral") store.set("peripheral", target.id);
        if (target?.type === "gateway")
          store.set("peripheral", target.peripheral);
        // The connections view draws a peripheral as its device.
        const name =
          target?.type === "gateway" ? target.peripheral : target?.id;
        const peripheral = ["peripheral", "gateway"].includes(target?.type)
          ? model.peripherals.find((p) => p.name === name)
          : null;
        diagram.select(
          peripheral ? { type: "device", id: peripheral.device } : target,
        );
        unitsDiagram.select(target);
        const mode = store.get("mode");
        const device =
          target?.type === "device" ? model.byId.get(target.id) : null;
        const connection =
          target?.type === "connection"
            ? model.connections.find((c) => c.id === target.id)
            : null;
        let cards = [];
        if (device) cards = deviceCards(model, device);
        else if (connection) cards = connectionCards(model, mode, connection);
        else if (target?.type === "unit")
          cards = unitCards(model, mode, target.id);
        else if (target?.type === "line")
          cards = lineCards(model, mode, target.id);
        else if (target?.type === "peripheral")
          cards = peripheralCards(model, mode, target.id, openNode);
        else if (target?.type === "gateway")
          cards = peripheralCards(model, mode, target.peripheral, openNode);
        details.replaceChildren(...cards);
        details.hidden = !cards.length;
        empty.hidden = !!cards.length;
      }

      const views = segmented(VIEWS, (value) => store.set("wiring", value));
      views.element.classList.add("ee-views");
      const capacity = document.createElement("input");
      capacity.type = "checkbox";
      capacity.checked = true;
      const capacityCheck = element("label", "panel-check");
      capacityCheck.append(capacity, "Capacity of each connection");
      capacity.addEventListener("change", () =>
        diagram.setView({ capacity: capacity.checked }),
      );
      const loadToggle = document.createElement("input");
      loadToggle.type = "checkbox";
      loadToggle.checked = true;
      const loadCheck = element("label", "panel-check");
      loadCheck.append(loadToggle, "Load of the mode's flows");
      loadToggle.addEventListener("change", showView);
      const legend = element(
        "p",
        "panel-note",
        "Rates in bit/s of the mode's flows: ↓ from the upper unit, ↑ from the lower; +n? counts topics without a known rate. A dashed line stays inside one device.",
      );
      const note = element("p", "panel-note");
      const choices = element("div", "panel-group");
      const environments = element("div", "panel-group");
      side.append(
        field(
          "E/E architecture",
          boundSelect(
            "architecture",
            names.map((name) => [name, name]),
          ),
        ),
        views.element,
        capacityCheck,
        loadCheck,
        legend,
        note,
        choices,
        environments,
      );

      // Choices of the lit view: the domains or the peripherals, the first entry lighting all.
      function showChoices() {
        const view = store.get("wiring");
        const key = view === "sync" ? "domain" : "peripheral";
        let items = [];
        if (view === "sync") {
          items = model.timeSync.map((d) => [
            d.name,
            [d.name, d.profile, d.source ? `from ${d.source}` : d.reference]
              .filter(Boolean)
              .join(" · "),
          ]);
        } else if (view === "peripherals" || view === "units") {
          items = model.peripherals.map((p) => [
            p.name,
            p.device && p.device !== p.name
              ? `${p.name} → ${p.device}`
              : p.name,
          ]);
        }
        choices.replaceChildren();
        if (view === "connections") return;
        if (!items.length) {
          choices.appendChild(
            element(
              "p",
              "panel-empty",
              view === "sync"
                ? "The architecture declares no time sync."
                : "The architecture binds no peripheral.",
            ),
          );
          return;
        }
        if (!items.some(([value]) => value === store.get(key)))
          store.set(key, "");
        const picker = list([["", "All"], ...items], (value) =>
          store.set(key, value),
        );
        picker.mark(store.get(key));
        choices.appendChild(
          section(view === "sync" ? "Domains" : "Peripherals", picker.element),
        );
      }

      function showEnvironments() {
        environments.replaceChildren();
        const items = [...model.environments.values()].map((e) => [
          e.device,
          `${e.id}: ${(e.compute_units || []).join(", ") || "no unit"}`,
        ]);
        if (!items.length) return;
        const picker = list(items, (device) =>
          select({ type: "device", id: device }),
        );
        environments.appendChild(section("Environments", picker.element));
      }

      function showView() {
        const view = store.get("wiring");
        const analysis = model.analysis.get(store.get("mode"));
        views.mark(view);
        showChoices();
        diagram.element.hidden = view === "units";
        unitsDiagram.element.hidden = view !== "units";
        capacityCheck.hidden = view === "units";
        legend.hidden = view !== "units";
        loadCheck.hidden = view === "units" || !analysis;
        diagram.setView({
          mode: view,
          domain: store.get("domain"),
          peripheral: store.get("peripheral"),
          loads: loadToggle.checked && analysis ? analysis.loads : null,
        });
        if (view === "units") unitsDiagram.show(model, store.get("mode"));
        unitsDiagram.focus(store.get("peripheral"));
        // Units, lines and gateway ports are drawn in the units view only, connections outside it.
        const onUnits = ["unit", "line", "gateway"].includes(selected?.type);
        const kept =
          !selected ||
          selected.type === "device" ||
          selected.type === "peripheral" ||
          onUnits === (view === "units");
        select(kept ? selected : null);
      }

      function load() {
        const record = records.find(
          (r) => r.name === store.get("architecture"),
        );
        if (!record) return;
        model = wiring.model(record);
        frame.setTitle(model.description || model.name);
        note.textContent = model.logical
          ? "The architecture declares no connections; devices are joined by their networks."
          : "";
        note.hidden = !model.logical;
        diagram.show(model);
        const focused = model.peripherals.some(
          (p) => p.name === store.get("peripheral"),
        );
        select(
          focused ? { type: "peripheral", id: store.get("peripheral") } : null,
        );
        showEnvironments();
        showView();
      }

      store.onChange("architecture", load);
      // A peripheral focused here or on a gateway node elsewhere shows in the inspector.
      store.onChange("peripheral", (name) => {
        if (!model) return;
        const shown =
          selected?.type === "gateway" ? selected.peripheral : selected?.id;
        const onPeripheral = ["peripheral", "gateway"].includes(selected?.type);
        if (name && (!onPeripheral || shown !== name)) {
          if (model.peripherals.some((p) => p.name === name))
            select({ type: "peripheral", id: name });
        } else if (!name && onPeripheral) select(null);
      });
      ["wiring", "domain", "peripheral", "mode"].forEach((key) =>
        store.onChange(key, showView),
      );
      load();
    },
  });
})();
