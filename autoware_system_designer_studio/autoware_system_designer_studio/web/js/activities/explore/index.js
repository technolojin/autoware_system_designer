// Explore activity: the diagrams as rail items, entity search, depth switch
// and image download beside the diagram canvas, with the inspector showing the
// selected element. Search
// is offered for diagrams that locate entities, the depth switch for diagrams
// that draw the hierarchy cut at a level. With a daemon, the live state of the
// launched members overlays the canvas.
(function () {
  const SD = window.SD;
  const { element, section } = SD.widgets;
  const { diagramHost, entitySearch, depthSelector, liveOverlay } =
    SD.activities.explore;

  // Inspector view: the selected element's info cards, or a prompt when nothing is selected.
  // A gateway node gets a card of its peripherals, each opening in the E/E view.
  function inspectorView(slot, openPeripheral) {
    const empty = element(
      "p",
      "panel-empty",
      "Select an element on the diagram.",
    );
    const infoPanel = element("div", "info-panel");
    infoPanel.hidden = true;
    slot.append(empty, infoPanel);
    return {
      reset() {
        infoPanel.hidden = true;
        empty.hidden = false;
      },
      show(data, type, peripherals = []) {
        infoPanel.hidden = false;
        empty.hidden = true;
        SD.InfoPanel.render(infoPanel, data, type);
        if (!peripherals.length) return;
        const card = element("div", "info-card");
        card.appendChild(element("div", "info-title", "Gateway of"));
        peripherals.forEach((name) => {
          const button = element("button", "link-button", name);
          button.type = "button";
          button.title = "Open the peripheral in the E/E view";
          button.addEventListener("click", () => openPeripheral(name));
          const row = element("div", "info-row");
          row.append(element("span", "info-label", "Peripheral"), button);
          card.appendChild(row);
        });
        infoPanel.appendChild(card);
      },
    };
  }

  // Download buttons of the shown diagram's whole drawing, one per image
  // format; disabled while the diagram exports none or a download is rendering.
  // A failed download is reported under the buttons.
  function imageSaver(host) {
    const row = element("div", "image-save");
    const buttons = ["png", "svg"].map((format) => {
      const button = element("button", "panel-button", format.toUpperCase());
      button.type = "button";
      button.title = `Save the whole diagram as a ${format.toUpperCase()} image`;
      button.addEventListener("click", async () => {
        enable(false);
        note.hidden = true;
        try {
          await host.saveImage(format);
        } catch (error) {
          console.error("explore image:", error);
          note.textContent = `Image not saved: ${error.message}`;
          note.hidden = false;
        } finally {
          enable(host.canSaveImage());
        }
      });
      row.appendChild(button);
      return button;
    });
    function enable(on) {
      buttons.forEach((button) => (button.disabled = !on));
    }
    const note = element("p", "panel-banner-error");
    note.hidden = true;
    enable(false);
    host.onLoad(() => {
      note.hidden = true;
      enable(host.canSaveImage());
    });
    return section("Image", row, note);
  }

  SD.workbench.registerActivity({
    id: "explore",
    title: "Explore",
    group: "Analyze",
    order: 10,
    daemon: false,
    inspector: true,
    views: (config) => ({
      key: "diagram",
      fallback: config.defaultDiagram,
      items: config.diagrams.map((d) => ({
        value: d.type,
        label: d.short,
        title: d.label,
        icon: d.icon,
      })),
    }),
    mount({ side, main, inspector }, { config, store }) {
      store.define("depth", "", (v) => /^[1-9]\d*$/.test(v));
      // Shared with the E/E activity: the focused peripheral and the view it opens in.
      store.define("peripheral", "");
      store.define("wiring", "");
      store.syncUrl();

      function openPeripheral(name) {
        store.set("peripheral", name);
        store.set("wiring", "units");
        store.set("activity", "architecture");
      }
      const host = diagramHost(
        main,
        inspectorView(inspector, openPeripheral),
        config,
        store,
        openPeripheral,
      );
      const search = entitySearch(config, store, (id) => host.focus(id));
      const depth = depthSelector(config, store);
      const live = liveOverlay(config, store, host, () => main.hidden);
      const sideBlocks = SD.widgets.sideSplit(side);
      sideBlocks.top.append(
        depth,
        search.element,
        live.element,
        imageSaver(host),
      );
      sideBlocks.scroll.appendChild(search.results);

      function showDiagramOptions() {
        const current = store.get("diagram");
        const diagram = config.diagrams.find((d) => d.type === current);
        search.element.hidden = !diagram?.searchable;
        sideBlocks.scroll.hidden = !diagram?.searchable;
        depth.hidden = !diagram?.layered;
      }

      // The diagram locates the store's entity when it loads and on show, and
      // drops its selection once the entity is cleared; a change while shown
      // comes from this canvas and is already in place.
      let shownKey = "";
      function followEntity() {
        const key = store.get("entity");
        if (key === shownKey) return;
        shownKey = key;
        if (!key) {
          host.clearSelection();
          return;
        }
        host.focusKey(key).catch((error) => console.warn("explore:", error));
      }
      function load() {
        shownKey = "";
        host.load().then(followEntity);
      }

      // The canvas lays out only while visible; a change made while hidden reloads on the next show.
      let stale = false;
      const reload = () => {
        if (main.hidden) stale = true;
        else load();
      };
      store.onChange("diagram", () => {
        showDiagramOptions();
        reload();
      });
      store.onChange("mode", reload);
      store.onChange("depth", () => {
        if (
          config.diagrams.find((d) => d.type === store.get("diagram"))?.layered
        )
          reload();
      });
      store.onChange("entity", (key) => {
        if (main.hidden) return;
        shownKey = key;
        if (!key) host.clearSelection();
      });
      // A selection dropped on another activity, an element that is no entity included.
      SD.on("selection.clear", () => host.clearSelection());
      // Escape drops the selection; captured ahead of the popups' own Escape,
      // so one that closes a popup does nothing here.
      document.addEventListener(
        "keydown",
        (event) => {
          if (event.key !== "Escape" || main.hidden || event.defaultPrevented)
            return;
          if (
            document.querySelector(
              ".popup-overlay.open, #settings-overlay.open",
            )
          )
            return;
          if (event.target.closest?.("input, textarea, select")) return;
          host.clearSelection();
          store.set("entity", "");
          store.set("topic", "");
        },
        true,
      );
      SD.on("themechange", host.updateTheme);

      showDiagramOptions();
      load();
      return {
        show() {
          live.show();
          if (stale) {
            stale = false;
            load();
          } else followEntity();
        },
      };
    },
  });
})();
