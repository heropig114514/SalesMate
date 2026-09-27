/** Responsibility: Project news/events and their source amounts onto a real world map.
 * Implementation: Local Natural Earth GeoJSON highlights followed customer countries. Fixed-size solid event and dashed news markers display the selected record’s source amount and qualifier; unrelated amounts are never summed. Centers remain at geographic anchors.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; world-news.js supplies filters/selection callbacks, and backend customer country codes control highlights. No online tiles.
 * Directory: WorldMap, WorldMap.constructor, WorldMap.load, WorldMap.setView, WorldMap.setCountries, WorldMap.setItems, WorldMap.draw, WorldMap.destroy.
 * Variable index: LOCATION_DIAMETER defines a fixed location-bubble diameter and does not encode financial value; WorldMap.map is Leaflet; layer contains news/event markers; items/selectedId describes current display; onSelect is the callback; countries contains highlighted country names; view is the current perspective; resizeObserver watches container dimensions.
 */
import { language } from "./i18n.js?v=20260921-product";
import { sourceAmountText } from "./world-signals.js?v=20260927-timeline";
const LOCATION_DIAMETER = 18;
/** Function: Manage the map and accessible news/event bubbles. Logic: Filtering preserves perspective; explicit perspective changes update center/zoom together. Constraints: Business records come from the API; never locate the user. */
export class WorldMap {
  /** Function: Initialize the map. Inputs: element and onSelect callback. Outputs: An instance. Logic: Real geographic projection and local map data. Constraints: Missing Leaflet raises an explicit error. */
  constructor(element, onSelect) {
    if (!window.L) throw new Error("Map library unavailable");
    this.onSelect = onSelect;
    this.items = [];
    this.selectedId = null;
    this.countries = new Set();
    this.map = L.map(element, {
      crs: L.CRS.EPSG4326,
      minZoom: -1,
      maxZoom: 7,
      zoomSnap: 0.25,
      zoomControl: false,
      scrollWheelZoom: false,
    });
    L.control.zoom({ position: "bottomright" }).addTo(this.map);
    this.map.attributionControl.setPrefix("Leaflet");
    this.map.attributionControl.addAttribution("Natural Earth");
    this.layer = L.layerGroup().addTo(this.map);
    this.view = "global";
    this.setView(this.view);
    this.resizeObserver = new ResizeObserver(() => {
      this.map.invalidateSize({ pan: false });
      this.setView(this.view);
    });
    this.resizeObserver.observe(element);
    window.addEventListener("pagehide", event => { if (!event.persisted) this.destroy(); });
  }
  /** Function: Load country boundaries. Inputs: Fixed same-origin GeoJSON. Outputs: Promise. Logic: Match database customer countries and apply theme colors; mark Singapore at its actual position on low-resolution maps. Constraints: No alternative source or retry on failure. */
  async load() {
    const response = await fetch("/static/world-countries.geojson", {
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) throw new Error(`Map HTTP ${response.status}`);
    const data = await response.json(),
      theme = getComputedStyle(document.documentElement);
    const accent = theme.getPropertyValue("--accent").trim(),
      land = theme.getPropertyValue("--map-land").trim(),
      border = theme.getPropertyValue("--map-border").trim();
    if (data.type !== "FeatureCollection") throw new Error("Invalid map data");
    L.geoJSON(data, {
      interactive: false,
      style: (feature) => ({
        color: border,
        weight: 0.8,
        fillColor: this.countries.has(feature.properties.name) ? accent : land,
        fillOpacity: this.countries.has(feature.properties.name) ? 0.5 : 1,
      }),
    }).addTo(this.map);
    if (this.countries.has("Singapore")) L.circleMarker([1.352, 103.819], {
      radius: 3,
      color: accent,
      fillOpacity: 0.8,
      interactive: false,
    }).addTo(this.map);
    for (let lat = -60; lat <= 60; lat += 30)
      L.polyline(
        [
          [lat, -180],
          [lat, 180],
        ],
        { color: border, weight: 0.5, opacity: 0.5, interactive: false },
      ).addTo(this.map);
    this.layer.bringToFront?.();
    this.draw();
    console.info("insights_map_ready", { features: data.features.length });
  }
  /** Function: Switch world/Asia-Pacific/Europe perspective. Inputs: view. Outputs: None. Logic: fitBounds controls center and zoom together. Constraints: Display changes only. */
  setView(view) {
    this.view = view;
    const bounds = {
      global: [
        [-55, -165],
        [75, 175],
      ],
      apac: [
        [-15, 75],
        [55, 155],
      ],
      europe: [
        [35, -12],
        [62, 40],
      ],
    };
    this.map.fitBounds(bounds[view] || bounds.global, {
      padding: [20, 20],
      animate: false,
    });
  }
  /** Function: Release map resources. Inputs: Instance observer/map. Outputs: None. Logic: Disconnect resize observation and map events when leaving without entering the back-forward cache. Constraints: No data changes. */
  destroy() {
    this.resizeObserver.disconnect();
    this.map.remove();
  }
  /** Function: Set customer countries. Inputs: codes. Outputs: None. Logic: Match ISO English names to map-specific names. Constraints: Set before load; never infer customer countries from events. */
  setCountries(codes) {
    const names = new Intl.DisplayNames(['en'], { type: 'region' });
    const aliases = { US: 'United States of America', KR: 'South Korea', KP: 'North Korea', RU: 'Russia', CZ: 'Czechia' };
    this.countries = new Set(codes.map(code => aliases[code] || names.of(code)));
  }
  /** Function: Update filtered news/events. Inputs: items and selectedId. Outputs: None. Logic: Redraw layers while retaining perspective. Constraints: No data changes. */
  setItems(items, selectedId) {
    this.items = items;
    this.selectedId = selectedId;
    this.draw();
  }
  /** Function: Draw location-grouped events. Inputs: Instance source records and selection. Outputs: Accessible Leaflet markers.
   * Logic: Use fixed geographic markers; show the selected record’s amount and list each grouped record in the tooltip separately. Constraints: Do not sum grants, fees, budgets, or currencies; null never becomes zero. */
  draw() {
    this.layer.clearLayers();
    const groups = new Map();
    for (const item of this.items) {
      const key = `${item.country}:${item.lat}:${item.lng}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    for (const items of groups.values()) {
      const item = items.find((v) => v.id === this.selectedId) || items[0];
      const amount = item.amount, size = LOCATION_DIAMETER;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "event-pin";
      button.classList.toggle("news-pin", item.kind === "news");
      button.classList.toggle("amount-missing", amount === null);
      button.classList.toggle("multiple", items.length > 1);
      button.dataset.eventId = item.id;
      button.style.setProperty("--bubble-size", `${size}px`);
      button.classList.toggle(
        "selected",
        items.some((v) => v.id === this.selectedId),
      );
      const name = language === "en" ? item.en : item.title;
      const amountText = sourceAmountText(item);
      button.title = items.map(row => `${row.title}: ${sourceAmountText(row)}`).join('\n');
      button.setAttribute(
        "aria-label",
        `${name}, ${amountText.replaceAll('\n', ', ')}, ${items.length}`,
      );
      const bubble = document.createElement("span");
      bubble.className = "event-bubble";
      bubble.textContent = items.length > 1 ? String(items.length) : "";
      const label = document.createElement("span");
      label.className = "event-pin-label";
      label.textContent = item.city;
      const value = document.createElement("span");
      value.className = "event-pin-amount";
      value.textContent = amountText;
      label.append(value);
      button.append(bubble, label);
      button.addEventListener("click", () => this.onSelect(item.id));
      L.marker([item.lat, item.lng], {
        keyboard: false,
        icon: L.divIcon({
          html: button,
          className: "event-marker",
          iconSize: [100, 80],
          iconAnchor: [50, 40],
        }),
      }).addTo(this.layer);
    }
  }
}
