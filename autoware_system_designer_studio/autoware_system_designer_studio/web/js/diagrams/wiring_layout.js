// Wiring layout: the devices of a wiring model as boxes in canvas coordinates
// and its connections as axis-aligned wires. Devices hosting environments form
// the core row. Switches and routers joining several of them are rails above
// it, with the devices reached only through a rail in a row over the rails.
// Buses and links between core devices run in lanes under the core row. Every
// other device hangs below the core device it is wired to, and the devices
// wired through it are stacked under it.
(function () {
  const SD = window.SD;
  const wiring = (SD.wiring ||= {});

  // Geometry in canvas pixels.
  const G = {
    margin: 28,
    charW: 6.1,
    colGap: 30,
    leafGap: 12,
    indent: 16,
    stackGap: 8,
    stackLabel: 22,
    aboveGap: 16,
    slotMin: 26,
    slotPad: 12,
    groupGap: 10,
    coreMinW: 150,
    leafMinW: 88,
    lineH: 13,
    headH: 36,
    portZone: 30,
    railLabel: 18,
    railStep: 26,
    laneGap: 22,
    rowGap: 40,
    partBar: 10,
    partBox: 24,
    portStep: 14,
  };

  const ROW_CORE = "core";
  const ROW_BELOW = "below";
  const ROW_ABOVE = "above";
  const ROW_RAIL = "rail";

  const textW = (value, bold) => Math.ceil((value || "").length * G.charW * (bold ? 1.12 : 1));
  const shortDefinition = (name) => (name || "").replace(/\.device$/, "");

  // First address of an interface, with the count of the others.
  function addressLabel(iface) {
    const addresses = (iface?.attachments || []).map((a) => a.address).filter(Boolean);
    if (!addresses.length) return "";
    return addresses.length > 1 ? `${addresses[0]} +${addresses.length - 1}` : addresses[0];
  }

  function deviceAddresses(device) {
    const addresses = [
      ...new Set(device.interfaces.flatMap((i) => i.attachments.map((a) => a.address))),
    ].filter(Boolean);
    if (addresses.length <= 2) return addresses.join(" ");
    return `${addresses[0]} +${addresses.length - 1}`;
  }

  function layout(model) {
    const { byId, owner } = model;
    const tops = model.devices.filter((d) => owner(d.id) === d.id);
    const ownersOf = (c) => [...new Set(c.endpoints.map((e) => owner(e.device)))];
    const external = model.connections.filter((c) => !c.internal && ownersOf(c).length > 1);
    const internal = model.connections.filter((c) => !external.includes(c));
    const isLink = (c) => c.kind === wiring.KIND_LINK && c.endpoints.length === 2;

    const byOwner = new Map(tops.map((d) => [d.id, []]));
    external.forEach((c) => ownersOf(c).forEach((id) => byOwner.get(id)?.push(c)));

    // -- rows ------------------------------------------------------------

    const hosting = tops.filter(
      (d) => d.role === "ecu" || wiring.deviceEnvironments(model, d).length,
    );
    const coreSet = new Set(
      (hosting.length ? hosting : tops.filter((d) => !wiring.isInfra(d))).map((d) => d.id),
    );
    const place = new Map();
    tops.forEach((d) => {
      if (coreSet.has(d.id)) place.set(d.id, { row: ROW_CORE, depth: 0 });
    });
    tops.forEach((d) => {
      if (coreSet.has(d.id) || !wiring.isInfra(d)) return;
      const linkedCore = new Set(
        byOwner
          .get(d.id)
          .filter(isLink)
          .flatMap(ownersOf)
          .filter((id) => coreSet.has(id)),
      );
      if (linkedCore.size !== 1) place.set(d.id, { row: ROW_RAIL, depth: 0 });
    });
    const children = new Map();
    const queue = tops.filter((d) => coreSet.has(d.id)).map((d) => d.id);
    while (queue.length) {
      const id = queue.shift();
      byOwner.get(id).forEach((c) => {
        ownersOf(c).forEach((other) => {
          if (other === id || place.has(other)) return;
          place.set(other, {
            row: ROW_BELOW,
            parent: id,
            via: c,
            depth: place.get(id).depth + 1,
          });
          if (!children.has(id)) children.set(id, []);
          children.get(id).push(other);
          queue.push(other);
        });
      });
    }
    tops.forEach((d) => {
      if (!place.has(d.id)) place.set(d.id, { row: ROW_ABOVE, depth: 0 });
    });
    const rowOf = (id) => place.get(id).row;
    const inRow = (row) => tops.filter((d) => rowOf(d.id) === row);
    const kids = (id) => children.get(id) || [];

    // -- leaf boxes ------------------------------------------------------

    const peripheralsOf = (id) => model.peripherals.filter((p) => owner(p.device) === id);
    const boxes = new Map();
    function leafBox(device) {
      const lines = [{ text: device.name, cls: "name" }];
      const sub = shortDefinition(device.definition) || device.hardware || device.role;
      if (sub) lines.push({ text: sub, cls: "sub" });
      const addresses = deviceAddresses(device);
      if (addresses) lines.push({ text: addresses, cls: "address" });
      peripheralsOf(device.id).forEach((p) => {
        const units = [...new Set(p.hosts.map((h) => h.compute_unit))].filter(Boolean);
        lines.push({
          text: `⇄ ${units.length ? units.join(", ") : p.name}`,
          cls: "peripheral",
        });
      });
      const w = Math.max(G.leafMinW, ...lines.map((l) => textW(l.text, l.cls === "name") + 18));
      return { device, lines, w, h: 10 + lines.length * G.lineH + 4 };
    }
    tops
      .filter((d) => rowOf(d.id) !== ROW_CORE && rowOf(d.id) !== ROW_RAIL)
      .forEach((d) => boxes.set(d.id, leafBox(d)));

    const subtreeW = (id) => {
      const own = boxes.get(id).w;
      const below = kids(id);
      if (!below.length) return own;
      return Math.max(own, G.indent + Math.max(...below.map(subtreeW)) + G.stackLabel);
    };
    const subtreeH = (id) =>
      kids(id).reduce((sum, kid) => sum + G.stackGap + subtreeH(kid), boxes.get(id).h);

    // -- core boxes: slots of the connections leaving each edge ------------

    const slotKey = (c, index) => `${c.id}#${index}`;
    const slotOf = new Map();
    function coreBox(device) {
      const parts = model.parts.get(device.id) || [];
      const partIds = parts.map((p) => p.id);
      // Interface of the device wired to one of its parts.
      const ifaceToPart = new Map();
      internal.forEach((c) => {
        const own = c.endpoints.find((e) => e.device === device.id && e.interface);
        const part = c.endpoints.find((e) => partIds.includes(e.device));
        if (own && part) ifaceToPart.set(own.interface, { part: part.device, conn: c.id });
      });
      const top = [];
      const bottom = [];
      const wired = new Set();
      byOwner.get(device.id).forEach((c) => {
        c.endpoints.forEach((e, index) => {
          if (owner(e.device) !== device.id) return;
          const ownEnd = e.device === device.id;
          if (ownEnd && e.interface) wired.add(e.interface);
          const through = ownEnd ? ifaceToPart.get(e.interface)?.part : null;
          const part = ownEnd ? through || null : e.device;
          const iface = ownEnd ? device.interfaces.find((i) => i.name === e.interface) : null;
          const label = ownEnd && !through ? e.interface || "" : "";
          const sub = ownEnd && !through ? addressLabel(iface) : "";
          const slot = {
            conn: c,
            index,
            part,
            label,
            sub,
            w: Math.max(G.slotMin, textW(label) + G.slotPad, textW(sub) + G.slotPad),
          };
          slotOf.set(slotKey(c, index), slot);
          const others = ownersOf(c).filter((id) => id !== device.id);
          if (isLink(c) && rowOf(others[0]) === ROW_RAIL) {
            slot.side = "top";
            top.push(slot);
            return;
          }
          slot.side = "bottom";
          bottom.push(slot);
          const hung = kids(device.id).filter((kid) => place.get(kid).via === c);
          // The first device hung by the connection sits under its port, the others in columns beside it.
          hung.forEach((kid, i) => {
            const w = subtreeW(kid) + G.leafGap;
            if (i) bottom.push({ column: kid, part, w, side: "bottom" });
            else {
              slot.child = kid;
              slot.w = Math.max(slot.w, w);
            }
          });
        });
      });
      const groupIndex = (slot) => partIds.indexOf(slot.part);
      bottom.sort((a, b) => groupIndex(a) - groupIndex(b));

      const groups = parts.map((part) => {
        const uplinks = [...ifaceToPart]
          .filter(([name, to]) => to.part === part.id && !wired.has(name))
          .map(([name, to]) => {
            const iface = device.interfaces.find((i) => i.name === name);
            const sub = addressLabel(iface);
            return {
              conn: to.conn,
              label: name,
              sub,
              w: Math.max(textW(name), textW(sub)) + G.slotPad,
            };
          });
        const caption = [part.name, shortDefinition(part.definition)].filter(Boolean).join(" · ");
        const slotsW = bottom.filter((s) => s.part === part.id).reduce((sum, s) => sum + s.w, 0);
        const need = Math.max(
          60,
          textW(caption) + 10,
          uplinks.reduce((sum, u) => sum + u.w, 0),
        );
        if (slotsW < need) {
          const filler = { part: part.id, w: need - slotsW, side: "bottom" };
          const last = bottom.map(groupIndex).lastIndexOf(partIds.indexOf(part.id));
          if (last < 0) {
            const after = bottom.findIndex((s) => groupIndex(s) > partIds.indexOf(part.id));
            bottom.splice(after < 0 ? bottom.length : after, 0, filler);
          } else bottom.splice(last + 1, 0, filler);
        }
        return {
          part,
          caption,
          uplinks,
          h: wiring.isInfra(part) ? G.partBar : G.partBox,
        };
      });

      let bottomW = 0;
      bottom.forEach((slot, i) => {
        if (i && slot.part !== bottom[i - 1].part) bottomW += G.groupGap;
        slot.offset = bottomW;
        bottomW += slot.w;
      });
      let topW = 0;
      top.forEach((slot) => {
        slot.offset = topW;
        topW += slot.w;
      });
      const sub =
        [shortDefinition(device.definition), device.hardware].filter(Boolean).join(" · ") ||
        device.role;
      const topZone = top.length ? G.portZone : 6;
      const partH = Math.max(0, ...groups.map((g) => g.h));
      const labelled = bottom.some((s) => !s.part && s.label);
      const bottomZone = groups.length ? G.portZone + partH + 24 : labelled ? G.portZone : 8;
      return {
        device,
        core: true,
        top,
        bottom,
        groups,
        sub,
        topW,
        bottomW,
        topZone,
        w: Math.max(
          G.coreMinW,
          topW + 20,
          bottomW + 20,
          textW(device.name, true) + 110,
          textW(sub) + 24,
        ),
        h: topZone + G.headH + bottomZone,
      };
    }
    inRow(ROW_CORE).forEach((d) => boxes.set(d.id, coreBox(d)));

    // -- positions ---------------------------------------------------------

    function placeSubtree(id, x0) {
      boxes.get(id).x = x0;
      kids(id).forEach((kid) => placeSubtree(kid, x0 + G.indent));
    }
    function stackSubtree(id) {
      const box = boxes.get(id);
      let y0 = box.y + box.h + G.stackGap;
      kids(id).forEach((kid) => {
        boxes.get(kid).y = y0;
        stackSubtree(kid);
        y0 += subtreeH(kid) + G.stackGap;
      });
    }

    let x = G.margin;
    inRow(ROW_CORE).forEach((d) => {
      const box = boxes.get(d.id);
      box.x = x;
      const bottomX = x + (box.w - box.bottomW) / 2;
      box.bottom.forEach((slot) => {
        slot.x0 = bottomX + slot.offset;
        slot.cx = slot.x0 + slot.w / 2;
        const hung = slot.child || slot.column;
        if (!hung) return;
        const x0 = slot.x0 + (slot.w - subtreeW(hung)) / 2;
        placeSubtree(hung, x0);
        slot.cx = x0 + boxes.get(hung).w / 2;
      });
      const topX = x + (box.w - box.topW) / 2;
      box.top.forEach((slot) => {
        slot.x0 = topX + slot.offset;
        slot.cx = slot.x0 + slot.w / 2;
      });
      x += box.w + G.colGap;
    });
    const coreRight = Math.max(G.margin, x - G.colGap);

    const above = inRow(ROW_ABOVE);
    const aboveW =
      above.reduce((sum, d) => sum + boxes.get(d.id).w, 0) +
      G.aboveGap * Math.max(0, above.length - 1);
    x = G.margin + Math.max(0, (coreRight - G.margin - aboveW) / 2);
    above.forEach((d) => {
      boxes.get(d.id).x = x;
      x += boxes.get(d.id).w + G.aboveGap;
    });
    const aboveRight = above.length ? x - G.aboveGap : G.margin;

    // Lanes of the horizontal runs: buses, and links joining two placed devices.
    const hangs = (c) =>
      isLink(c) &&
      ownersOf(c).some(
        (id) => place.get(id).via === c && ownersOf(c).includes(place.get(id).parent),
      );
    const toRail = (c) => isLink(c) && ownersOf(c).some((id) => rowOf(id) === ROW_RAIL);
    const laned = external.filter((c) => !toRail(c) && !hangs(c));
    const upperLanes = laned.filter((c) => ownersOf(c).every((id) => rowOf(id) === ROW_ABOVE));
    const lowerLanes = laned.filter((c) => !upperLanes.includes(c));
    const laneY = new Map();

    let y = G.margin;
    if (above.length) {
      const aboveH = Math.max(...above.map((d) => boxes.get(d.id).h));
      above.forEach((d) => (boxes.get(d.id).y = y));
      y += aboveH + 10;
      upperLanes.forEach((c) => {
        y += G.laneGap;
        laneY.set(c.id, y);
      });
      y += 14;
    }
    const rails = inRow(ROW_RAIL).map((device) => {
      y += G.railLabel;
      const rail = { device, y, xs: [] };
      y += G.railStep;
      return rail;
    });
    const railOf = new Map(rails.map((rail) => [rail.device.id, rail]));
    if (rails.length) y += 6;

    const core = inRow(ROW_CORE);
    const coreH = Math.max(0, ...core.map((d) => boxes.get(d.id).h));
    core.forEach((d) => {
      const box = boxes.get(d.id);
      box.y = y;
      box.h = coreH;
    });
    y += coreH;
    if (lowerLanes.length) {
      y += 6;
      lowerLanes.forEach((c) => {
        y += G.laneGap;
        laneY.set(c.id, y);
      });
    }
    const hung = inRow(ROW_BELOW).filter((d) => place.get(d.id).depth === 1);
    if (hung.length) {
      y += G.rowGap;
      hung.forEach((d) => {
        boxes.get(d.id).y = y;
        stackSubtree(d.id);
      });
      y += Math.max(...hung.map((d) => subtreeH(d.id)));
    }

    // -- box contents --------------------------------------------------------

    const texts = [];
    const partBoxes = [];
    const innerStubs = [];
    const badgeAnchors = new Map();
    const say = (text, x0, y0, cls, anchor = "start", owner0 = null) =>
      texts.push({ text, x: x0, y: y0, cls, anchor, device: owner0 });

    boxes.forEach((box, id) => {
      if (!box.core) {
        const cx = box.x + box.w / 2;
        box.lines.forEach((line, i) =>
          say(line.text, cx, box.y + 19 + i * G.lineH, line.cls, "middle", id),
        );
        badgeAnchors.set(id, { x: cx, y: box.y + box.h + 12, anchor: "middle" });
        return;
      }
      const bottomY = box.y + box.h;
      box.top.forEach((slot) => {
        slot.fromY = box.y;
        say(slot.label, slot.cx, box.y + 13, "port", "middle", id);
        say(slot.sub, slot.cx, box.y + 25, "address", "middle", id);
      });
      say(box.device.name, box.x + 10, box.y + box.topZone + 18, "name core", "start", id);
      say(box.sub, box.x + 10, box.y + box.topZone + 32, "sub", "start", id);
      badgeAnchors.set(id, {
        x: box.x + box.w - 10,
        y: box.y + box.topZone + 18,
        anchor: "end",
      });
      const partBottom = bottomY - 20;
      const partRect = new Map();
      box.groups.forEach((group) => {
        const slots = box.bottom.filter((s) => s.part === group.part.id);
        const x0 = slots[0].x0 + 4;
        const x1 = slots[slots.length - 1].x0 + slots[slots.length - 1].w - 4;
        const rect = {
          id: group.part.id,
          parent: id,
          device: group.part,
          x: x0,
          y: partBottom - group.h,
          w: x1 - x0,
          h: group.h,
          bar: group.h === G.partBar,
        };
        partRect.set(group.part.id, rect);
        partBoxes.push(rect);
        say(group.caption, x0, bottomY - 7, "sub", "start", group.part.id);
        group.uplinks.forEach((uplink, i) => {
          const cx = x0 + (rect.w * (i + 0.5)) / group.uplinks.length;
          say(uplink.label, cx, rect.y - 20, "port", "middle", id);
          say(uplink.sub, cx, rect.y - 9, "address", "middle", id);
          innerStubs.push({
            x: cx,
            y1: rect.y - 6,
            y2: rect.y,
            device: id,
            connection: uplink.conn,
          });
        });
      });
      box.bottom.forEach((slot) => {
        const rect = slot.part ? partRect.get(slot.part) : null;
        slot.fromY = rect ? rect.y + rect.h : bottomY;
        if (!slot.conn) return;
        if (slot.label) say(slot.label, slot.cx, bottomY - 17, "port", "middle", id);
        if (slot.sub) say(slot.sub, slot.cx, bottomY - 6, "address", "middle", id);
      });
      box.top
        .filter((slot) => slot.part && partRect.has(slot.part))
        .forEach((slot) => (slot.fromY = partRect.get(slot.part).y));
    });

    // -- wires ---------------------------------------------------------------

    // Ports of a leaf box: the connection it hangs by at its centre, the others beside it.
    const leafPorts = new Map();
    function leafPort(id, c) {
      const box = boxes.get(id);
      const row = rowOf(id);
      if (!leafPorts.has(id)) {
        const via = place.get(id).via;
        const own = byOwner
          .get(id)
          .filter((other) => !kids(id).some((kid) => place.get(kid).via === other));
        leafPorts.set(
          id,
          [...own].sort((a, b) => (b === via) - (a === via)),
        );
      }
      const order = leafPorts.get(id);
      const index = Math.max(0, order.indexOf(c));
      if (row === ROW_ABOVE) {
        return {
          x: box.x + (box.w * (index + 1)) / (order.length + 1),
          y: box.y + box.h,
          dir: "up",
        };
      }
      const step = Math.ceil(index / 2) * G.portStep * (index % 2 ? -1 : 1);
      return { x: box.x + box.w / 2 + step, y: box.y, dir: "down" };
    }

    function anchor(c, index) {
      const endpoint = c.endpoints[index];
      const id = owner(endpoint.device);
      const row = rowOf(id);
      if (row === ROW_RAIL) return { rail: railOf.get(id), device: endpoint.device, owner: id };
      if (row === ROW_CORE) {
        const slot = slotOf.get(slotKey(c, index));
        return {
          x: slot.cx,
          y: slot.fromY,
          dir: slot.side === "top" ? "down" : "up",
          device: endpoint.device,
          owner: id,
        };
      }
      return { ...leafPort(id, c), device: endpoint.device, owner: id };
    }

    const wires = [];
    let connectors = 0;
    const rightEdge = Math.max(
      coreRight,
      aboveRight,
      ...[...boxes.values()].map((box) => box.x + box.w),
    );
    external.forEach((c) => {
      const wire = {
        id: c.id,
        connection: c,
        segments: [],
        ends: [],
        label: null,
      };
      const line = (x1, y1, x2, y2) => {
        if (x1 !== x2 || y1 !== y2) wire.segments.push({ x1, y1, x2, y2 });
      };
      const end = (a, x0 = a.x, y0 = a.y, dir = a.dir) =>
        wire.ends.push({ device: a.device, owner: a.owner, x: x0, y: y0, dir });
      const anchors = c.endpoints.map((_, index) => anchor(c, index));
      const capacity = wiring.formatBitrate(c.bitrate);

      if (toRail(c)) {
        const [first, second] = anchors;
        if (first.rail && second.rail) {
          const cx = rightEdge + 24 + connectors++ * G.portStep;
          line(cx, first.rail.y, cx, second.rail.y);
          [first, second].forEach((a) => {
            a.rail.xs.push(cx);
            end(a, cx, a.rail.y, a === first ? "up" : "down");
          });
          wire.label = { x: cx + 4, y: (first.rail.y + second.rail.y) / 2 + 4, text: capacity };
        } else {
          const railEnd = first.rail ? first : second;
          const other = first.rail ? second : first;
          railEnd.rail.xs.push(other.x);
          line(other.x, other.y, other.x, railEnd.rail.y);
          end(other);
          end(railEnd, other.x, railEnd.rail.y, other.y > railEnd.rail.y ? "up" : "down");
          wire.label = {
            x: other.x + 4,
            y: railEnd.rail.y + (other.y > railEnd.rail.y ? 13 : -6),
            text: capacity,
          };
        }
      } else if (hangs(c)) {
        const childIndex = anchors.findIndex((a) => place.get(a.owner).via === c);
        const child = anchors[childIndex];
        const parent = anchors[1 - childIndex];
        if (rowOf(parent.owner) === ROW_CORE) {
          line(parent.x, parent.y, child.x, child.y);
          end(parent);
          end(child);
          wire.label = { x: child.x + 4, y: child.y - 5, text: capacity };
        } else {
          const above = boxes.get(parent.owner);
          const box = boxes.get(child.owner);
          const trunk = above.x + G.indent / 2;
          const mid = box.y + box.h / 2;
          line(trunk, above.y + above.h, trunk, mid);
          line(trunk, mid, box.x, mid);
          end(parent, trunk, above.y + above.h, "up");
          end(child, box.x, mid, "right");
          wire.label = { x: box.x + box.w + 4, y: mid + 3, text: capacity };
        }
      } else {
        const laneAt = laneY.get(c.id);
        const xs = [];
        anchors.forEach((a) => {
          if (a.rail) return;
          xs.push(a.x);
          line(a.x, a.y, a.x, laneAt);
          end(a);
        });
        if (xs.length) {
          let x1 = Math.min(...xs);
          let x2 = Math.max(...xs);
          if (x1 === x2) {
            x1 -= 24;
            x2 += 24;
          }
          line(x1, laneAt, x2, laneAt);
          wire.run = { x1, x2, y: laneAt };
          const named = isLink(c)
            ? capacity
            : [c.name, c.type === wiring.KIND_NETWORK ? "" : c.type, capacity]
                .filter(Boolean)
                .join(" · ");
          wire.label = isLink(c)
            ? { x: (x1 + x2) / 2, y: laneAt - 4, text: named, anchor: "middle" }
            : { x: x1 + 4, y: laneAt - 5, text: named, name: true };
        }
      }
      wires.push(wire);
    });

    const railBars = rails.map((rail) => {
      const xs = rail.xs.length ? rail.xs : [G.margin, G.margin + 120];
      const x1 = Math.min(...xs) - 16;
      const x2 = Math.max(...xs) + 16;
      const label = [
        rail.device.name,
        shortDefinition(rail.device.definition) || rail.device.hardware,
      ]
        .filter(Boolean)
        .join(" · ");
      badgeAnchors.set(rail.device.id, { x: x2, y: rail.y - 7, anchor: "end" });
      return { id: rail.device.id, device: rail.device, x1, x2, y: rail.y, label };
    });

    // Points where a vertical run passes over the horizontal run of another wire or a rail.
    const horizontals = [
      ...railBars.map((bar) => ({ id: bar.id, x1: bar.x1, x2: bar.x2, y: bar.y })),
      ...wires.flatMap((wire) =>
        wire.segments
          .filter((s) => s.y1 === s.y2)
          .map((s) => ({
            id: wire.id,
            x1: Math.min(s.x1, s.x2),
            x2: Math.max(s.x1, s.x2),
            y: s.y1,
          })),
      ),
    ];
    const crossings = [];
    wires.forEach((wire) => {
      const touched = new Set(wire.ends.map((e) => e.owner));
      wire.segments
        .filter((s) => s.x1 === s.x2)
        .forEach((s) => {
          const top = Math.min(s.y1, s.y2);
          const bottom = Math.max(s.y1, s.y2);
          horizontals.forEach((h) => {
            if (h.id === wire.id || touched.has(h.id)) return;
            if (h.y > top && h.y < bottom && s.x1 > h.x1 && s.x1 < h.x2)
              crossings.push({ x: s.x1, y: h.y });
          });
        });
    });

    const deviceBoxes = [...boxes.entries()].map(([id, box]) => ({
      id,
      device: box.device,
      row: rowOf(id),
      x: box.x,
      y: box.y,
      w: box.w,
      h: box.h,
    }));
    const width =
      Math.max(
        rightEdge + (connectors ? 24 + connectors * G.portStep : 0),
        ...railBars.map((bar) => bar.x2),
      ) + G.margin;
    return {
      width,
      height: y + G.margin + 14,
      devices: deviceBoxes,
      parts: partBoxes,
      rails: railBars,
      texts: texts.filter((t) => t.text),
      innerStubs,
      wires,
      crossings,
      badgeAnchors,
    };
  }

  wiring.layout = layout;
  wiring.layoutGeometry = G;
})();
