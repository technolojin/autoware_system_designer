// E/E activity: the wiring of an E/E architecture the system is declared for.
// The side panel picks the architecture and what the diagram lights (the
// connections, a time sync domain or a peripheral's hosts) and lists the
// environments with their compute units; the inspector shows the selected
// device or connection.
(function () {
  const SD = window.SD;
  const { element, field, section, boundSelect, segmented, list } = SD.widgets;
  const wiring = SD.wiring;

  const VIEWS = [
    ["connections", "Connections"],
    ["sync", "Time sync"],
    ["peripherals", "Peripherals"],
  ];
  const ZOOM_STEP = 1.25;

  function row(label, value) {
    const line = element("div", "info-row");
    line.append(element("span", "info-label", label), element("span", "info-value", value));
    return line;
  }

  function card(title, ...children) {
    const box = element("div", "info-card");
    box.appendChild(element("div", "info-title", title));
    box.append(...children.filter(Boolean));
    return box;
  }

  const endpointLabel = (e) => (e.interface ? `${e.device}.${e.interface}` : e.device);
  const capacityLabel = (bitrate) => {
    const short = wiring.formatBitrate(bitrate);
    return short ? `${short}bit/s` : "not given";
  };

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
            c.endpoints.some((e) => e.device === device.id && e.interface === iface.name),
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
          element("div", "info-subtitle", id === model.mainEnvironment ? `${id} (main)` : id),
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
        if (p.component) group.appendChild(row("Component", p.component));
        p.hosts.forEach((host) =>
          group.appendChild(
            row(
              host.compute_unit || "host",
              [host.device && endpointLabel(host), host.address, host.path ? "" : "no path"]
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
        roles.push(row(domain.name, `grandmaster · ${domain.grandmaster.interface || ""}`));
      domain.members
        .filter((m) => m.device === device.id)
        .forEach((m) =>
          roles.push(row(domain.name, `slave · ${m.interface || ""}${m.path ? "" : " · no path"}`)),
        );
    });
    if (roles.length) cards.push(card("Time sync", ...roles));
    return cards;
  }

  function connectionCards(model, connection) {
    const domains = model.timeSync
      .filter((d) => d.members.some((m) => (m.path || []).includes(connection.id)))
      .map((d) => d.name);
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
        ...connection.endpoints.map((e) => row(e.device, e.interface || "any port")),
      ),
    ];
  }

  SD.workbench.registerActivity({
    id: "architecture",
    title: "E/E",
    group: "Analyze",
    order: 15,
    daemon: false,
    inspector: true,
    icon: "M3 3h5v4H3zM12 3h5v4h-5zM7.5 13h5v4h-5zM5.5 7v3h9V7M10 10v3",
    status: (store) => (store.get("architecture") ? [store.get("architecture")] : []),
    mount({ side, main, inspector }, { config, store }) {
      const records = config.eeArchitectures || [];
      const names = records.map((record) => record.name);
      store.define("architecture", names[0] || "", (v) => names.includes(v));
      store.define("wiring", VIEWS[0][0], (v) => VIEWS.some(([id]) => id === v));
      store.define("domain", "");
      store.define("peripheral", "");
      store.syncUrl();

      const frame = SD.widgets.mainView({
        empty: names.length ? "" : "No E/E architecture is declared for this system.",
      });
      main.appendChild(frame.element);
      const empty = element("p", "panel-empty", "Select a device or a connection.");
      const details = element("div", "info-panel");
      details.hidden = true;
      inspector.append(empty, details);

      let model = null;
      const diagram = wiring.diagram(frame.body, { onSelect: select });
      [
        ["−", "Zoom out", () => diagram.zoomBy(1 / ZOOM_STEP)],
        ["+", "Zoom in", () => diagram.zoomBy(ZOOM_STEP)],
        ["Fit", "Fit the width", () => diagram.fit()],
      ].forEach(([label, title, run]) => {
        const button = element("button", "panel-button", label);
        button.type = "button";
        button.title = title;
        button.addEventListener("click", run);
        frame.trailing.appendChild(button);
      });

      function select(target) {
        diagram.select(target);
        const device = target?.type === "device" ? model.byId.get(target.id) : null;
        const connection =
          target?.type === "connection" ? model.connections.find((c) => c.id === target.id) : null;
        const cards = device
          ? deviceCards(model, device)
          : connection
            ? connectionCards(model, connection)
            : [];
        details.replaceChildren(...cards);
        details.hidden = !cards.length;
        empty.hidden = !!cards.length;
      }

      const views = segmented(VIEWS, (value) => store.set("wiring", value));
      const capacity = document.createElement("input");
      capacity.type = "checkbox";
      capacity.checked = true;
      const capacityCheck = element("label", "panel-check");
      capacityCheck.append(capacity, "Capacity of each connection");
      capacity.addEventListener("change", () => diagram.setView({ capacity: capacity.checked }));
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
        } else if (view === "peripherals") {
          items = model.peripherals.map((p) => [
            p.name,
            p.device && p.device !== p.name ? `${p.name} → ${p.device}` : p.name,
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
        if (!items.some(([value]) => value === store.get(key))) store.set(key, "");
        const picker = list([["", "All"], ...items], (value) => store.set(key, value));
        picker.mark(store.get(key));
        choices.appendChild(section(view === "sync" ? "Domains" : "Peripherals", picker.element));
      }

      function showEnvironments() {
        environments.replaceChildren();
        const items = [...model.environments.values()].map((e) => [
          e.device,
          `${e.id}: ${(e.compute_units || []).join(", ") || "no unit"}`,
        ]);
        if (!items.length) return;
        const picker = list(items, (device) => select({ type: "device", id: device }));
        environments.appendChild(section("Environments", picker.element));
      }

      function showView() {
        views.mark(store.get("wiring"));
        showChoices();
        diagram.setView({
          mode: store.get("wiring"),
          domain: store.get("domain"),
          peripheral: store.get("peripheral"),
        });
      }

      function load() {
        const record = records.find((r) => r.name === store.get("architecture"));
        if (!record) return;
        model = wiring.model(record);
        frame.setTitle(model.description || model.name);
        note.textContent = model.logical
          ? "The architecture declares no connections; devices are joined by their networks."
          : "";
        note.hidden = !model.logical;
        diagram.show(model);
        select(null);
        showEnvironments();
        showView();
      }

      store.onChange("architecture", load);
      ["wiring", "domain", "peripheral"].forEach((key) => store.onChange(key, showView));
      load();
    },
  });
})();
