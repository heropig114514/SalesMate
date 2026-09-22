/** 职责：将活动与商机金额投影到真实世界地图。
 * 实现：本地 Natural Earth GeoJSON，跟进国家高亮；圆面积按所选币种金额计算，圆心固定在地理锚点；标签分币种显示已有金额，区分无所选币种金额与真正未知。
 * 关联：0919 界面及共享语言资源统一缓存版本；共享语言/API 资源随需求界面统一版本；world-news.js 提供筛选结果和选择回调；后端客户国家代码决定高亮；不请求在线瓦片。
 * 目录：WorldMap、WorldMap.constructor、WorldMap.load、WorldMap.setView、WorldMap.setCountries、WorldMap.setItems、WorldMap.draw、WorldMap.destroy。
 * 变量索引：WorldMap.map 为 Leaflet 实例；layer 为活动标记；items/selectedId 为当前展示；onSelect 为回调；countries 为高亮国家名称；view 为当前视角，resizeObserver 为容器尺寸观察器；无模块常量。
 */
import { language } from "./i18n.js?v=20260921-product";
/** 功能：管理地图与可访问活动气泡。逻辑：筛选不重置视角，显式切换视角同时变更中心与缩放。约束：业务记录来自接口，不定位用户。 */
export class WorldMap {
  /** 功能：初始化地图。输入：element 与 onSelect 回调。输出：实例。逻辑：真实地理投影及本地底图。约束：Leaflet 缺失明确报错。 */
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
  /** 功能：加载国界。输入：固定同源 GeoJSON。输出：Promise。逻辑：匹配数据库客户国家，使用主题色；新加坡在低精度底图中以实际位置标记。约束：失败不换数据源、不重试。 */
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
  /** 功能：切换世界/亚太/欧洲视角。输入：view。输出：无。逻辑：fitBounds 同时控制中心及缩放。约束：只改变显示。 */
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
  /** 功能：释放地图资源。输入：实例观察器与地图。输出：无。逻辑：不进入往返缓存时退出页面断开尺寸观察与地图事件。约束：不修改数据。 */
  destroy() {
    this.resizeObserver.disconnect();
    this.map.remove();
  }
  /** 功能：设置客户国家。输入：codes。输出：无。逻辑：使用 ISO 英文名称匹配底图，处理底图特有名称。约束：在 load 前设置，不从活动国家推断客户国家。 */
  setCountries(codes) {
    const names = new Intl.DisplayNames(['en'], { type: 'region' });
    const aliases = { US: 'United States of America', KR: 'South Korea', KP: 'North Korea', RU: 'Russia', CZ: 'Czechia' };
    this.countries = new Set(codes.map(code => aliases[code] || names.of(code)));
  }
  /** 功能：更新筛选后的活动。输入：items 和 selectedId。输出：无。逻辑：重绘图层，保持视角。约束：不修改数据。 */
  setItems(items, selectedId) {
    this.items = items;
    this.selectedId = selectedId;
    this.draw();
  }
  /** 功能：绘制按城市聚合的活动气泡。输入：实例快照，各项 map_amounts 为后端去重的分币种金额，amount 为所选 currency 的数值或 null。输出：无。逻辑：正金额直径按当前最大金额比例的平方根乘 62；所有已有币种均在标签列出，所选币种排首位，缺少所选币种单独注明；仅 map_amounts 为空时显示未知；标签不参与圆心布局。约束：无换汇、跨币种加总或金额回退；零金额保留为零，选中状态不放大金额面积。 */
  draw() {
    this.layer.clearLayers();
    const groups = new Map();
    for (const item of this.items) {
      const key = `${item.country}:${item.lat}:${item.lng}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    for (const items of groups.values()) {
      const knownAmounts = items.map(value => value.amount).filter(Number.isFinite);
      const item = items.find((v) => v.id === this.selectedId) || items[0],
        amount = knownAmounts.length ? Math.max(...knownAmounts) : null,
        size = Math.sqrt((amount ?? 0) / Math.max(1, ...this.items.map(value => value.amount).filter(Number.isFinite))) * 62;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "event-pin";
      button.classList.toggle("multiple", items.length > 1);
      button.dataset.eventId = item.id;
      button.style.setProperty("--bubble-size", `${size}px`);
      button.classList.toggle(
        "selected",
        items.some((v) => v.id === this.selectedId),
      );
      const name = language === "en" ? item.en : item.title;
      const amounts = Object.entries(item.map_amounts).sort(([a], [b]) => Number(b === item.currency) - Number(a === item.currency) || a.localeCompare(b));
      const amountText = amounts.map(([currency, value]) => `${currency} ${Number(value).toLocaleString()}`).join('\n') || (language === 'en' ? 'Amount unknown' : '金额未知');
      const currencyNote = amount === null && amounts.length ? (language === 'en' ? `No ${item.currency} amount` : `无 ${item.currency} 金额`) : '';
      button.title = `${item.city} · ${language === 'en' ? 'Local pipeline' : '当地关联商机'}: ${amountText}`;
      if (currencyNote) button.title += `\n${currencyNote}`;
      button.setAttribute(
        "aria-label",
        `${name}, ${amountText.replaceAll('\n', ', ')}, ${currencyNote ? currencyNote + ', ' : ''}${items.length}`,
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
      if (currencyNote) {
        const note = document.createElement("span");
        note.className = "event-pin-currency-note";
        note.textContent = currencyNote;
        label.append(note);
      }
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
