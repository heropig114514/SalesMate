/**
 * 职责：定义世界消息的独立数据边界，为演示和未来授权推送提供同一入口。
 * 实现：严格校验必要字段、来源链接和版本；同 ID 更新不增加计数，过期版本不覆盖新内容。
 * 关联：world-news.js 订阅 change；未来传输适配器调用 receive，不在本模块打开网络连接。
 * 目录：validateNews、validateNews.text（有界文本校验）、NewsFeed、NewsFeed.constructor、NewsFeed.list、NewsFeed.get、NewsFeed.receive。
 * 变量索引：INDUSTRIES 为行业标签与颜色；NewsFeed.mode 固定数据模式；NewsFeed.items 保存已校验快照。
 */
export const INDUSTRIES = Object.freeze({
  semiconductor: { label: '半导体', color: '#3d795d' },
  metrology: { label: '精密量测', color: '#a4773d' },
  optics: { label: '光学检测', color: '#677daf' },
  industrial: { label: '工业检测', color: '#aa6657' },
});

/** 功能：校验并复制消息快照。输入：item 为不可信对象，mode 为 demo/live。
 * 输出：规范对象；字段、坐标、时间或来源不合法时抛 TypeError。
 * 逻辑：只保留约定字段，来源只允许无凭证 HTTPS，限制文本与数组规模。
 * 约束：不抓取来源，不执行 HTML；演示与真实内容不可混合；坐标限制在本页世界视图的可见范围。 */
export function validateNews(item, mode) {
  if (!item || typeof item !== 'object' || !['demo', 'live'].includes(mode)) throw new TypeError('消息对象或数据模式无效。');
  /** 功能：校验有界非空文本。输入：value 原值、name 错误字段名、max 长度上限。
   * 输出：去除首尾空白的字符串。逻辑：先校验原始长度与类型。约束：不解析 HTML，不记录字段值。 */
  const text = (value, name, max) => {
    if (typeof value !== 'string' || !value.trim() || value.length > max) throw new TypeError(`消息 ${name} 无效。`);
    return value.trim();
  };
  const id = text(item.id, 'id', 80);
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id)) throw new TypeError('消息 id 格式无效。');
  if (!Number.isSafeInteger(item.version) || item.version < 1) throw new TypeError('消息版本必须为正整数。');
  if (!Object.hasOwn(INDUSTRIES, item.industry)) throw new TypeError('未知消息行业。');
  if (item.demo !== (mode === 'demo')) throw new TypeError('演示和真实消息不能混合。');
  const point = item.location;
  if (!point || !Number.isFinite(point.latitude) || Math.abs(point.latitude) > 85 || !Number.isFinite(point.longitude) || Math.abs(point.longitude) > 180) throw new TypeError('消息地理坐标无效。');
  if (typeof item.published_at !== 'string' || !/^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(item.published_at) || !Number.isFinite(Date.parse(item.published_at))) throw new TypeError('消息时间必须包含时区。');
  if (!Array.isArray(item.body) || !item.body.length || item.body.length > 20 || !Array.isArray(item.sources) || item.sources.length > 10) throw new TypeError('消息正文或来源格式无效。');
  const sources = item.sources.map(source => {
    const url = new URL(text(source.url, '来源地址', 2000));
    if (url.protocol !== 'https:' || url.username || url.password) throw new TypeError('来源必须为不含凭证的 HTTPS 地址。');
    return { label: text(source.label, '来源名称', 100), url: url.href };
  });
  if (mode === 'live' && sources.length === 0) throw new TypeError('真实消息必须提供可核对的来源。');
  return { id, version: item.version, industry: item.industry, demo: item.demo,
    title: text(item.title, '标题', 160), summary: text(item.summary, '摘要', 1000),
    location: { name: text(point.name, '地点', 80), latitude: point.latitude, longitude: point.longitude },
    published_at: item.published_at, body: item.body.map(value => text(value, '正文段落', 4000)), sources };
}

/** 功能：保存一个数据模式下的消息快照。
 * 逻辑：构造时整体校验，接收推送时按 ID/version 幂等更新，再发 change 事件。
 * 约束：纯内存、不持久化、不网络重连；传输鉴权与员工隔离由未来后端负责。 */
export class NewsFeed extends EventTarget {
  /** 功能：初始化消息源。输入：mode 显式数据模式，items 初始数组。
   * 输出：新实例；重复 ID 或无效快照抛错。逻辑：全部校验通过后建立索引。
   * 约束：失败不以演示数据代替；不修改传入对象。 */
  constructor(mode, items = []) {
    super();
    if (!['demo', 'live'].includes(mode)) throw new TypeError('必须指定 demo 或 live 数据模式。');
    this.mode = mode;
    const checked = items.map(item => validateNews(item, mode));
    this.items = new Map(checked.map(item => [item.id, item]));
    if (this.items.size !== checked.length) throw new TypeError('初始消息 ID 重复。');
  }
  /** 功能：读取时间倒序快照。输入：实例 items。输出：深拷贝数组。
   * 逻辑：相同时间用 ID 稳定排序。约束：调用者不能绕过 receive 修改内部版本。 */
  list() { return structuredClone([...this.items.values()].sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at) || a.id.localeCompare(b.id))); }
  /** 功能：读取消息。输入：id。输出：消息副本或 null。
   * 逻辑：直接索引。约束：不发起网络查询。 */
  get(id) { return structuredClone(this.items.get(id) ?? null); }
  /** 功能：接收一条上游消息事件。输入：event 为 {type:'news.upsert', item}。
   * 输出：新增/更新返回 true，重复/过期返回 false；同版本内容冲突抛错。
   * 逻辑：先校验，再比较版本，最后原子替换并通知视图。
   * 约束：日志仅含 ID/版本；拒绝不支持事件，不自动重试或忽略格式错误。 */
  receive(event) {
    if (event?.type !== 'news.upsert') throw new TypeError('不支持的世界消息事件。');
    const item = validateNews(event.item, this.mode), previous = this.items.get(item.id);
    if (previous && item.version <= previous.version) {
      if (item.version === previous.version && JSON.stringify(previous) !== JSON.stringify(item)) throw new TypeError('相同消息版本包含不同内容。');
      console.info('world_news_event_ignored', { id: item.id, version: item.version, currentVersion: previous.version });
      return false;
    }
    this.items.set(item.id, item);
    console.info('world_news_event_applied', { id: item.id, version: item.version, kind: previous ? 'update' : 'insert' });
    this.dispatchEvent(new CustomEvent('change', { detail: { id: item.id, inserted: !previous } }));
    return true;
  }
}
