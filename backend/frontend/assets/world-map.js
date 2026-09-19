/**
 * 职责：将世界消息坐标绘制为可访问的 Leaflet 气泡。
 * 实现：本地 GeoJSON 底图、经纬线和按当前屏幕距离聚合的 HTML 标记；缩放后重新分组。
 * 关联：world-news.js 提供筛选后快照与点击回调；不读取业务 API、不请求在线瓦片。
 * 目录：clusterNews、WorldMap、WorldMap.constructor、WorldMap.load、WorldMap.reset、WorldMap.setItems、WorldMap.draw、WorldMap.focusGroup。
 * 变量索引：WorldMap.map 为 Leaflet 实例，layer 为标记层，items 为快照，selectedId/highlightId 为视觉状态，onSelect 为页面回调。
 */
import { INDUSTRIES } from './world-feed.js';

/** 功能：按当前屏幕空间聚合邻近消息。输入：items 快照、map 地图。
 * 输出：数组，每组保留消息列表与代表坐标。逻辑：稳定输入顺序，112×56 像素邻域避免文字相互覆盖。
 * 约束：仅影响展示，不去重/删改消息；同坐标消息始终可通过分组列表逐条访问。 */
export function clusterNews(items, map) {
  const groups = [];
  for (const item of items) {
    const latlng = [item.location.latitude, item.location.longitude];
    const point = map.latLngToContainerPoint(latlng);
    const group = groups.find(entry => Math.abs(entry.point.x - point.x) < 112 && Math.abs(entry.point.y - point.y) < 56);
    if (group) group.items.push(item);
    else groups.push({ point, latlng, items: [item] });
  }
  return groups;
}

/** 功能：管理世界地图与消息标记。
 * 逻辑：固定本地底图并按缩放聚合，页面更新消息不重置当前视角。
 * 约束：需要本地 Leaflet；资源失败由调用者显示，不替换成其他地图源。 */
export class WorldMap {
  /** 功能：初始化地图。输入：element 地图容器、onSelect 消息组回调。
   * 输出：实例。逻辑：使用经纬度投影和有界拖动，小屏可缩小至全世界。
   * 约束：不启用自动定位；点击回调不会发起业务写操作。 */
  constructor(element, onSelect) {
    if (!window.L) throw new Error('地图组件未能加载，请刷新页面。');
    this.onSelect = onSelect;
    this.items = [];
    this.selectedId = null;
    this.highlightId = null;
    this.map = L.map(element, { crs: L.CRS.EPSG4326, minZoom: -1, maxZoom: 5, zoomSnap: 0.25, zoomDelta: 0.5,
      zoomControl: false, attributionControl: true, maxBounds: [[-85, -190], [85, 190]], maxBoundsViscosity: 0.8 });
    L.control.zoom({ position: 'bottomright' }).addTo(this.map);
    this.map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>');
    this.map.attributionControl.addAttribution('<a href="https://www.naturalearthdata.com" target="_blank" rel="noopener noreferrer">Natural Earth</a>');
    this.layer = L.layerGroup().addTo(this.map);
    this.map.on('zoomend', () => this.draw());
    this.map.on('resize', () => this.draw());
    this.reset();
  }
  /** 功能：读取并绘制地图资源。输入：固定同源资源。
   * 输出：Promise；HTTP、解析或超时失败抛 Error。逻辑：先加载国界，再绘制经纬网。
   * 约束：无在线瓦片、无重试；底图不代表业务消息覆盖范围。 */
  async load() {
    const response = await fetch('/static/world-countries.geojson', { signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`地图资源加载失败（HTTP ${response.status}）。`);
    const data = await response.json();
    if (data.type !== 'FeatureCollection' || !Array.isArray(data.features)) throw new Error('地图资源格式无效。');
    L.geoJSON(data, { interactive: false, style: { color: '#f6f8ef', weight: 0.7, fillColor: '#cbd5be', fillOpacity: 1 } }).addTo(this.map);
    for (let lat = -60; lat <= 60; lat += 30) L.polyline([[lat, -180], [lat, 180]], { interactive: false, color: '#769083', opacity: 0.12, weight: 1, dashArray: '2 7' }).addTo(this.map);
    for (let lng = -180; lng <= 180; lng += 30) L.polyline([[-80, lng], [80, lng]], { interactive: false, color: '#769083', opacity: 0.12, weight: 1, dashArray: '2 7' }).addTo(this.map);
    console.info('world_map_ready', { features: data.features.length });
  }
  /** 功能：恢复全球视角。输入：当前容器尺寸。输出：无。
   * 逻辑：按固定世界范围适配尺寸。约束：仅显式按钮/初始化调用，不因推送打断视角。 */
  reset() { this.map.invalidateSize(); this.map.fitBounds([[-58, -168], [78, 180]], { padding: [24, 36], animate: false }); }
  /** 功能：更新标记数据。输入：items、selectedId、highlightId。
   * 输出：无。逻辑：记录当前可见快照后重绘。约束：不移动地图、不读取来源链接。 */
  setItems(items, selectedId = null, highlightId = null) {
    this.items = items; this.selectedId = selectedId; this.highlightId = highlightId; this.draw();
  }
  /** 功能：绘制可键盘操作的气泡。输入：实例快照和地图缩放。
   * 输出：无。逻辑：纯文本构造 DOM，聚合展示计数，点击把完整组交回页面。
   * 约束：不插入新闻 HTML；缩放只重绘标记，不改变消息选择。 */
  draw() {
    this.layer.clearLayers();
    for (const group of clusterNews(this.items, this.map)) {
      const first = group.items[0], places = new Set(group.items.map(item => item.location.name));
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'news-pin';
      button.style.setProperty('--pin-color', INDUSTRIES[first.industry].color);
      button.classList.toggle('selected', group.items.some(item => item.id === this.selectedId));
      button.classList.toggle('incoming', group.items.some(item => item.id === this.highlightId));
      button.dataset.newsIds = group.items.map(item => item.id).join(' ');
      button.setAttribute('aria-label', `${[...places].join('、')}，${group.items.length} 条消息，查看摘要`);
      const dot = document.createElement('span'); dot.className = 'pin-dot'; dot.setAttribute('aria-hidden', 'true');
      const label = document.createElement('span'); label.textContent = places.size > 1 ? `${places.size} 地动态` : first.location.name.split(' · ')[0];
      const count = document.createElement('b'); count.textContent = group.items.length;
      button.append(dot, label, count);
      const marker = L.marker(group.latlng, { keyboard: false, icon: L.divIcon({ html: button, className: 'news-marker', iconSize: [130, 40], iconAnchor: [65, 48] }) });
      marker.on('click', () => this.onSelect(group.items, button));
      marker.addTo(this.layer);
    }
  }
  /** 功能：放大消息组。输入：items 组内消息。输出：无。
   * 逻辑：多坐标适配范围，同坐标聚合仍保留逐条列表。
   * 约束：由用户显式触发，不自动打开其中任一详情。 */
  focusGroup(items) { this.map.fitBounds(items.map(item => [item.location.latitude, item.location.longitude]), { padding: [90, 90], maxZoom: 3, animate: false }); }
}
