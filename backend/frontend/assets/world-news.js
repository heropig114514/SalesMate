/** 职责：全球洞察的活动筛选、联动详情、行程导出、邀约草稿与行业资讯。
 * 实现：URL 保存筛选/选择，金额和客户均为显式演示；资讯沿用 NewsFeed 校验与详情路由；操作只导出本地日历或可编辑草稿。
 * 关联：聊天 Markdown 模块依赖使用统一缓存版本；0919 界面及共享语言资源统一缓存版本；导航资源使用账号清空版本以更新缓存；共享语言/API 资源随需求界面统一版本；world.html、world-news.css、world-map.js；world-events.js 活动与 world-demo.js 新闻为演示数据源。
 * 目录：text、eventName、countryName、money、daysUntil、visibleEvents、updateURL、selectEvent、renderEvents、renderDetail、renderNews、renderRoute、foldCalendarLine、calendarText、calendarText.escape、downloadItinerary、inviteDraft、receiveWorldNews、start。
 * 变量索引：$ 为 DOM 查询；feed 为资讯快照；state 为类型/时间/地区/选择/视角；map 为地图实例；today 为实际当天日期；categories 为资讯分类；DEMO_* 为导入数据。
 */
import { language } from "./i18n.js?v=20260921-product";
import { escapeHtml as e } from "./api.js?v=20260921-product";
import { mountWorkspace } from "./workspace.js?v=20260921-markdown";
import { WorldMap } from "./world-map.js?v=20260921-product";
import {
  DEMO_EVENTS,
  DEMO_COUNTRIES,
  NEWS_CATEGORIES,
} from "./world-events.js?v=20260921-product";
import { NewsFeed } from "./world-feed.js?v=20260921-product";
import { DEMO_NEWS } from "./world-demo.js?v=20260921-product";
const $ = (id) => document.getElementById(id);
const feed = new NewsFeed("demo", DEMO_NEWS);
const today = new Date();
today.setHours(0, 0, 0, 0);
const categories = NEWS_CATEGORIES;
let map = null;
const state = {
  type: "all",
  time: "all",
  country: "all",
  selected: null,
  view: "global",
};
/** 功能：选择文案。输入：zh/en。输出：文本。逻辑：沿用语言设置。约束：不翻译用户内容。 */
function text(zh, en) {
  return language === "en" ? en : zh;
}
/** 功能：活动名称。输入：item。输出：文本。逻辑：使用演示数据双语标题。约束：无修改。 */
function eventName(item) {
  return text(item.title, item.en);
}
/** 功能：地区名称。输入：code。输出：名称。逻辑：固定白名单映射。约束：未知保持标识。 */
function countryName(code) {
  const country = DEMO_COUNTRIES.find((v) => v.code === code);
  return country ? text(country.name, country.en) : code;
}
/** 功能：展示金额。输入：amount。输出：明确 SGD 金额。逻辑：国际化格式。约束：仅用于演示快照，不换汇。 */
function money(amount) {
  return (
    "SGD " +
    new Intl.NumberFormat(language === "en" ? "en-SG" : "zh-CN", {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(amount)
  );
}
/** 功能：计算自然日倒计时。输入：date 为 ISO 日期。输出：整数。逻辑：日期按本地日历读取。约束：负值表示过去。 */
function daysUntil(date) {
  return Math.round((new Date(date + "T00:00:00") - today) / 86400000);
}
/** 功能：筛选活动。输入：includeCountry 是否启用国家条件。输出：数组。逻辑：类型与时间交集，季度包含起止边界且排除已结束活动。约束：不评分、不排名。 */
function visibleEvents(includeCountry = true) {
  const quarterEnd = new Date(
    today.getFullYear(),
    Math.floor(today.getMonth() / 3) * 3 + 3,
    0,
    23,
    59,
    59,
  );
  return DEMO_EVENTS.filter(
    (item) =>
      (state.type === "all" || item.type === state.type) &&
      (!includeCountry ||
        state.country === "all" ||
        item.country === state.country) &&
      (state.time === "all" ||
        (new Date(item.end + "T23:59:59") >= today &&
          (state.time === "30"
            ? daysUntil(item.date) <= 30
            : new Date(item.date + "T00:00:00") <= quarterEnd))),
  );
}
/** 功能：保存筛选 URL。输入：当前 state。输出：无。逻辑：白名单状态写入查询项。约束：不包含私人资料。 */
function updateURL() {
  const query = new URLSearchParams();
  for (const key of ["type", "time", "country", "view"])
    if (state[key] !== "all" && state[key] !== "global")
      query.set(key, state[key]);
  if (state.selected) query.set("event", state.selected);
  history.replaceState(null, "", "/world/" + (query.size ? "?" + query : ""));
}
/** 功能：选择活动。输入：id。输出：无。逻辑：限当前筛选结果，地图/列表/详情同步。约束：不创建行程或发送邀请。 */
function selectEvent(id) {
  if (!visibleEvents().some((v) => v.id === id)) return;
  state.selected = id;
  updateURL();
  renderEvents();
}
/** 功能：刷新联动界面。输入：state。输出：无。逻辑：失效选择移至当前第一项；空列表清空详情，地区数基于类型和时间条件。约束：筛选不改变地图视角。 */
function renderEvents() {
  const items = visibleEvents();
  if (!items.some((v) => v.id === state.selected))
    state.selected = items[0]?.id || null;
  $("event-count").textContent = items.length;
  $("map-count").textContent = text(
    `${items.length} 场活动 · 演示商机`,
    `${items.length} events · Demo pipeline`,
  );
  $("region-filters").innerHTML =
    `<button data-country="all" aria-pressed="${state.country === "all"}">${text("全部地区", "All regions")}<span>${visibleEvents(false).length}</span></button>` +
    DEMO_COUNTRIES.map(
      (country) =>
        `<button data-country="${country.code}" aria-pressed="${state.country === country.code}">${e(countryName(country.code))}<span>${visibleEvents(false).filter((v) => v.country === country.code).length}</span></button>`,
    ).join("");
  $("event-list").innerHTML = items.length
    ? items
        .map(
          (item) =>
            `<button class="event-card" type="button" data-select="${item.id}" aria-pressed="${state.selected === item.id}"><span class="event-date"><b>${item.date.slice(8)}</b><small>${item.date.slice(5, 7)} / ${item.date.slice(0, 4)}</small></span><span class="event-card-main"><span class="event-type">${item.type === "exhibition" ? text("展会", "Exhibition") : text("销售活动", "Sales event")}</span><strong>${e(eventName(item))}</strong><span>${e(item.city)} · ${e(money(item.amount))}</span></span><span aria-hidden="true">↗</span></button>`,
        )
        .join("")
    : `<div class="insights-empty"><h3>${text("当前筛选下暂无活动", "No events match these filters")}</h3><p>${text("可以切换时间窗或地区继续查看。", "Try a different date range or region.")}</p><button id="reset-filters" class="secondary">${text("清除筛选", "Clear filters")}</button></div>`;
  $("reset-filters")?.addEventListener("click", () => {
    state.type = "all";
    state.time = "all";
    state.country = "all";
    $("event-type").value = "all";
    $("event-time").value = "all";
    renderEvents();
    updateURL();
  });
  renderDetail(items.find((v) => v.id === state.selected));
  map?.setItems(items, state.selected);
  updateURL();
}
/** 功能：展示活动完整判断。输入：item 或 undefined。输出：无。逻辑：标题、倒计时、金额、原因、现场和建议动作分区。约束：所有业务信息明确是演示，空筛选不保留旧详情。 */
function renderDetail(item) {
  if (!item) {
    $("event-detail").innerHTML =
      `<div class="insights-empty">${text("选择一个活动，查看详情。", "Select an event to see details.")}</div>`;
    return;
  }
  const days = daysUntil(item.date),
    countdown =
      days < 0
        ? text("已开始 / 已结束", "Started / past")
        : days === 0
          ? text("今天", "Today")
          : text(`${days} 天后`, `In ${days} days`);
  $("event-detail").innerHTML =
    `<div class="event-detail-head"><span class="event-type">${item.type === "exhibition" ? text("展会", "Exhibition") : text("销售活动", "Sales event")}</span><span class="event-countdown">${countdown}</span></div><h2>${e(eventName(item))}</h2><p class="event-location">⊕ ${e(item.city)}</p><p class="event-period">${item.date} — ${item.end}</p><div class="event-value"><span>${text("关联在手商机 · 演示", "Related pipeline · Demo")}</span><strong>${e(money(item.amount))}</strong><small>${e(item.customers.join(" · "))}</small></div><section><h3><span>01</span>${text("为什么值得去", "Why attend")}</h3><p>${e(language === "en" ? `Meet ${item.customers.join(" and ")} to review their demo inspection projects and confirm requirements.` : item.why)}</p></section><section><h3><span>02</span>${text("现场情况", "On site")}</h3><ul>${(language === "en" ? ["Customer purchasing and engineering teams in this demo scenario.", "Technical discussions and application demonstrations."] : item.onsite).map((v) => `<li>${e(v)}</li>`).join("")}<li>${text("报名截止", "Registration closes")}: ${item.deadline}</li></ul></section><section><h3><span>03</span>${text("建议动作", "Suggested actions")}</h3><ul>${(language === "en" ? [`Arrange a meeting with ${item.customers[0]}.`, "Prepare specifications and a validation plan."] : item.actions).map((v) => `<li>${e(v)}</li>`).join("")}</ul></section><div class="event-actions"><button id="add-itinerary" class="primary" type="button">${text("＋ 加入行程", "＋ Add to itinerary")}</button><button id="create-invite" class="secondary" type="button">${text("生成客户邀约邮件", "Draft customer invitation")}</button></div><p class="event-action-note">${text("行程导出为日历文件；邀约生成后由你审阅。", "Export a calendar file or review an invitation draft.")}</p>`;
  $("add-itinerary").onclick = () => downloadItinerary(item);
  $("create-invite").onclick = () => inviteDraft(item);
}
/** 功能：显示近十四天资讯。输入：feed 快照及真实 today。输出：无。逻辑：只取真实日期范围内前四条并标记分类/发布时间。约束：演示日期不随刷新伪装成新消息。 */
function renderNews() {
  const items = feed
    .list()
    .filter((item) => {
      const days = daysUntil(item.published_at.slice(0, 10));
      return days <= 0 && days >= -14;
    })
    .slice(0, 4);
  $("industry-news").innerHTML = items.length
    ? items
        .map(
          (item) =>
            `<a class="industry-news-card" href="/world/news/${encodeURIComponent(item.id)}/"><span class="news-category">${e((categories[item.industry] || ["行业", "Industry"])[language === "en" ? 1 : 0])}</span><h3>${e(item.title)}</h3><time datetime="${e(item.published_at)}">${e(item.published_at.slice(0, 10))}</time><span class="news-arrow" aria-hidden="true">↗</span></a>`,
        )
        .join("")
    : `<p class="muted">${text("当前演示快照在近 14 天内没有资讯。", "No demo news within the last 14 days.")}</p>`;
}
/** 功能：恢复地图查询或新闻详情。输入：location。输出：无。逻辑：同源详情沿用 NewsFeed 记录；地图恢复白名单筛选。约束：未知消息明确显示缺失。 */
function renderRoute() {
  const match = location.pathname.match(/^\/world\/news\/([a-z0-9-]+)\/$/);
  $("world-explorer").hidden = Boolean(match);
  $("news-detail").hidden = !match;
  if (match) {
    const item = feed.get(match[1]);
    $("news-detail").innerHTML =
      `<a href="/world/">← ${text("返回全球洞察", "Back to Global Insights")}</a>` +
      (item
        ? `<article><p class="eyebrow">${text("演示资讯", "DEMO NEWS")} · ${item.published_at.slice(0, 10)}</p><h1>${e(item.title)}</h1><p class="article-lead">${e(item.summary)}</p>${item.body.map((v) => `<p>${e(v)}</p>`).join("")}<p class="muted">${text("虚构演示，没有真实报道来源。", "Fictional example, without a real reporting source.")}</p></article>`
        : `<h1>${text("消息未找到", "News not found")}</h1>`);
    return;
  }
  const query = new URLSearchParams(location.search);
  state.type = ["exhibition", "sales"].includes(query.get("type"))
    ? query.get("type")
    : "all";
  state.time = ["30", "quarter"].includes(query.get("time"))
    ? query.get("time")
    : "all";
  state.country = DEMO_COUNTRIES.some((c) => c.code === query.get("country"))
    ? query.get("country")
    : "all";
  state.view = ["apac", "europe"].includes(query.get("view"))
    ? query.get("view")
    : "global";
  state.selected = query.get("event");
  $("event-type").value = state.type;
  $("event-time").value = state.time;
  renderEvents();
  renderNews();
}
/** 功能：折叠日历内容行。输入：line 为已转义字符串。输出：带 CRLF 空格续行的文本。
 * 逻辑：按 Unicode 字符累计 UTF-8 字节，不切断多字节字符；续行空格计入 75 字节。
 * 约束：仅生成导出格式，不改变活动字段。 */
function foldCalendarLine(line) {
  const encoder = new TextEncoder(); let result = '', length = 0;
  for (const character of line) {
    const size = encoder.encode(character).length;
    if (length + size > 75) { result += '\r\n '; length = 1; }
    result += character; length += size;
  }
  return result;
}
/** 功能：构造标准 iCalendar 全天活动。输入：item。输出：ICS 字符串。逻辑：结束日使用排他次日，文本转义防止注入额外行，UTF-8 按 75 字节折行。约束：导出不代表已加入远程日历。 */
export function calendarText(item) {
  /** 功能：转义日历文本。输入：value。输出：安全字段文本。逻辑：转义换行和分隔符。约束：不用于日期字段。 */
  const escape = (value) =>
    String(value)
      .replace(/\\/g, "\\\\")
      .replace(/\r?\n/g, "\\n")
      .replace(/[,;]/g, "\\$&");
  const end = new Date(item.end + "T00:00:00Z");
  end.setUTCDate(end.getUTCDate() + 1);
  return [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//SalesMate//Demo Events//EN",
    "BEGIN:VEVENT",
    `UID:${item.id}@salesmate.example`,
    "DTSTAMP:" +
      new Date()
        .toISOString()
        .replace(/[-:]/g, "")
        .replace(/\.\d+Z$/, "Z"),
    "DTSTART;VALUE=DATE:" + item.date.replace(/-/g, ""),
    "DTEND;VALUE=DATE:" + end.toISOString().slice(0, 10).replace(/-/g, ""),
    "SUMMARY:" + escape("[Demo] " + eventName(item)),
    "LOCATION:" + escape(item.city),
    "DESCRIPTION:" +
      escape("Fictional demo event. Verify before arranging travel."),
    "END:VEVENT",
    "END:VCALENDAR",
    "",
  ].map(foldCalendarLine).join("\r\n");
}
/** 功能：导出行程文件。输入：item。输出：浏览器下载。逻辑：临时 Blob URL 下载后释放。约束：不调用外部日历、不发送邀请。 */
function downloadItinerary(item) {
  const url = URL.createObjectURL(
    new Blob([calendarText(item)], { type: "text/calendar;charset=utf-8" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = item.id + ".ics";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  $("world-status").textContent = text(
    "日历文件已导出，请导入你的日历。",
    "Calendar file exported. Import it into your calendar.",
  );
}
/** 功能：打开可编辑邀约。输入：item。输出：无。逻辑：根据活动信息生成模板草稿并标明演示，用户可复制。约束：不调用模型、不发送邮件或猜测客户邮箱。 */
function inviteDraft(item) {
  $("invite-subject").value = text(
    `邀约交流：${item.title}`,
    `Invitation: ${item.en}`,
  );
  $("invite-body").value = text(
    `您好，\n\n我们计划于 ${item.date} 在${item.city}参加“${item.title}”，希望与您预约一次交流，了解贵方当前需求并讨论产品方案。\n\n请问您是否方便参加？也欢迎告知合适的时间。\n\n期待您的回复。\n\n（此草稿基于演示活动，发送前请核对活动与客户信息。）`,
    `Hello,\n\nWe plan to attend ${item.en} in ${item.city} on ${item.date}. Would you be available to discuss your requirements and our solutions?\n\nPlease let us know a suitable time.\n\nBest regards\n\n(Draft based on a fictional demo event. Verify before sending.)`,
  );
  $("invite-status").textContent = "";
  $("invite-dialog").showModal();
}
/** 功能：接收已有资讯推送契约。输入：event。输出：feed 接收结果。逻辑：校验、版本幂等后更新近十四天列表。约束：不模拟自动推送、不改变活动筛选。 */
export function receiveWorldNews(event) {
  const result = feed.receive(event);
  renderNews();
  return result;
}
/** 功能：挂载全球洞察。输入：DOM 和 URL。输出：Promise。逻辑：绑定控件，读取本地底图，失败显示明确提示。约束：未接入真实活动服务，全部金额、客户和活动明确标为演示。 */
async function start() {
  mountWorkspace("world");
  renderRoute();
  $("event-type").onchange = (event) => {
    state.type = event.target.value;
    renderEvents();
  };
  $("event-time").onchange = (event) => {
    state.time = event.target.value;
    renderEvents();
  };
  $("region-filters").onclick = (event) => {
    const button = event.target.closest("[data-country]");
    if (button) {
      state.country = button.dataset.country;
      renderEvents();
    }
  };
  $("event-list").onclick = (event) => {
    const button = event.target.closest("[data-select]");
    if (button) selectEvent(button.dataset.select);
  };
  $("map-views").onclick = (event) => {
    const button = event.target.closest("[data-view]");
    if (!button) return;
    state.view = button.dataset.view;
    map?.setView(state.view);
    document
      .querySelectorAll("[data-view]")
      .forEach((v) => v.setAttribute("aria-pressed", String(v === button)));
    updateURL();
  };
  $("invite-close").onclick = () => $("invite-dialog").close();
  $("invite-copy").onclick = async () => {
    try {
      await navigator.clipboard.writeText(
        $("invite-subject").value + "\n\n" + $("invite-body").value,
      );
      $("invite-status").textContent = text("已复制草稿。", "Draft copied.");
    } catch (error) {
      console.error("invite_copy_failed", { name: error.name });
      $("invite-status").textContent = text(
        "复制失败，请选中文本手动复制。",
        "Copy failed. Select the text and copy it manually.",
      );
    }
  };
  window.addEventListener("popstate", renderRoute);
  if (location.pathname === "/world/")
    try {
      map = new WorldMap($("world-map"), selectEvent);
      await map.load();
      map.setView(state.view);
      map.setItems(visibleEvents(), state.selected);
      document
        .querySelectorAll("[data-view]")
        .forEach((v) =>
          v.setAttribute("aria-pressed", String(v.dataset.view === state.view)),
        );
    } catch (error) {
      console.error("insights_map_failed", { name: error.name });
      $("world-error").hidden = false;
      $("world-error").textContent = text(
        "地图加载失败。请刷新；活动列表仍可查看。",
        "Map failed to load. Refresh to try again; the event list remains available.",
      );
    }
}
void start();
