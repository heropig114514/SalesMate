/** 职责：在既有网站中浏览获准共享的虚构实验数据及关联来源。
 * 实现：GET 浏览与带幂等键的 Tool 维护共用 Session；URL 保存关联主键；正文以纯文本呈现。
 * 关联：顶栏使用移除实验入口后的缓存版本；sales.experiments 与 experiment_writes、experiments.html、product-header.js；不执行外部动作。
 * 目录：getJson、message、tableUrl、recordTitle、showRecord、editRecord、saveRecord、deleteRecord、mutateRecord、loadRows、route、start。
 * 变量索引：ui 为 DOM 定位函数；state 保存批次、页码、当前记录及请求代次。
 */
import './product-header.js?v=20260922-nav';
import { request } from './api.js?v=20260921-product';

const ui = id => document.getElementById(id);
const state = { batch: null, table: null, page: 1, sequence: 0, rows: [], editing: null, busy: false, pending: null };

/** 功能：读取同源实验接口。输入：url 相对路径。输出：解析后的 JSON。
 * 逻辑：复用统一请求器的会话、缓存版本和错误解释。约束：不创建会话、不重试失败请求。 */
async function getJson(url) {
  return request(url.slice('/api/v1/'.length));
}

/** 功能：显示状态。输入：text 提示文本、error 是否错误。输出：无。
 * 逻辑：使用纯文本写入 aria-live 区域。约束：不把服务端内容作为 HTML 执行。 */
function message(text, error = false) {
  ui('experiment-status').textContent = text;
  ui('experiment-status').toggleAttribute('data-error', error);
}

/** 功能：构造表查询路径。输入：label 模型名。输出：同源 API 路径。
 * 逻辑：编码批次与模型路径段。约束：服务端另行核验白名单。 */
function tableUrl(label) {
  return `/api/v1/experiments/${encodeURIComponent(state.batch.batch)}/${encodeURIComponent(label)}/`;
}

/** 功能：从记录字段选择简短标题。输入：row 实验投影。输出：标题字符串。
 * 逻辑：依次选择常见业务名称，未命名记录显示模型及主键。约束：仅截断列表标题，详情保留完整内容。 */
function recordTitle(row) {
  const fields = row.fields;
  return String(fields.title || fields.name || fields.company_name || fields.subject || fields.number || fields.tool || fields.username || fields.source || fields.content || fields.event || fields.email || fields.address || `${state.table.name} ${row.pk}`).slice(0, 160);
}

/** 功能：展示详情、维护入口和关联跳转。输入：row 已读取记录。输出：弹窗及可用的文件下载链接。
 * 逻辑：外键按模型结构链接到精确主键查询；缺少授权记录时由目标页显示空结果。
 * 约束：仅按服务端 write 能力显示维护按钮，不展开非清单记录。 */
function showRecord(row) {
  ui('record-title').textContent = recordTitle(row);
  ui('record-owner').textContent = `虚构 · 归属：${row.owner.username}（${row.owner.id}） · ${row.batch} · ${row.read_only ? '只读' : '全体登录账号可维护'}`;
  ui('edit-record').hidden = !state.table.write.update;
  ui('delete-record').hidden = !state.table.write.delete;
  ui('edit-record').onclick = () => { ui('record-dialog').close(); editRecord(row); };
  ui('delete-record').onclick = () => deleteRecord(row);
  ui('record-json').textContent = JSON.stringify(row.fields, null, 2);
  const links = ui('record-links');
  links.replaceChildren();
  for (const field of state.table.fields) {
    if (!field.relation || !state.batch.tables.some(table => table.model === field.relation) || row.fields[field.name] == null) continue;
    const link = document.createElement('a');
    link.href = '#' + new URLSearchParams({ table: field.relation, pk: String(row.fields[field.name]) });
    link.textContent = `${field.name} → ${field.relation}`;
    link.onclick = () => ui('record-dialog').close();
    links.append(link);
  }
  const download = ui('record-download');
  download.hidden = !['sales.Attachment', 'accounts.SetupDocument'].includes(state.table.model);
  download.href = `${tableUrl(state.table.model)}${encodeURIComponent(row.pk)}/download/`;
  ui('record-dialog').showModal();
}

/** 功能：构建新增或编辑表单。输入：row 现有记录，null 表示新增。输出：字段控件及编辑状态。
 * 逻辑：只展示目录声明的可写字段，关系使用主键，JSON 保持结构化文本；新增允许省略有默认值字段。
 * 约束：归属、主键和系统字段不由浏览器提交，最终校验在服务端。 */
function editRecord(row = null) {
  state.editing = row;
  state.pending = null;
  ui('editor-title').textContent = `${row ? '修改' : '新增'}共享虚构${state.table.name}`;
  ui('editor-status').textContent = '归属保持不变；修改会记录当前操作者。关联字段须使用同批次记录的主键。';
  const container = ui('editor-fields'); container.replaceChildren();
  for (const field of state.table.write.fields) {
    const label = document.createElement('label');
    label.textContent = `${field.name}${field.required ? ' *' : ''}${field.relation ? ` → ${field.relation}` : ''}`;
    const input = document.createElement(field.type === 'JSONField' || field.type === 'TextField' ? 'textarea' : 'input');
    input.name = field.name; input.dataset.type = field.type;
    input.placeholder = field.nullable ? '留空使用空值' : field.required ? '必填' : '新增时留空使用默认值';
    if (field.type === 'BooleanField') input.placeholder = 'true / false';
    if (field.choices.length) input.placeholder = field.choices.map(choice => choice[0]).join(' / ');
    const value = row?.fields[field.name];
    input.value = value == null ? '' : field.type === 'JSONField' ? JSON.stringify(value, null, 2) : String(value);
    if (field.max_length) input.maxLength = field.max_length;
    label.append(input); container.append(label);
  }
  ui('editor-dialog').showModal();
}

/** 功能：提交表单中的共享记录维护。输入：event 表单事件。输出：成功关闭弹窗，失败保留用户输入。
 * 逻辑：按字段类型解析，更新只提交变化字段，旧指纹防止覆盖并发修改。
 * 约束：不自动重试，错误不吞没，不修改受保护字段。 */
async function saveRecord(event) {
  event.preventDefault();
  if (state.busy) return;
  try {
    const data = {};
    for (const field of state.table.write.fields) {
      const input = ui('editor-form').elements.namedItem(field.name);
      const raw = input.value;
      const before = state.editing?.fields[field.name];
      const original = before == null ? '' : field.type === 'JSONField' ? JSON.stringify(before, null, 2) : String(before);
      if (state.editing && raw === original) continue;
      if (!state.editing && raw === '' && !field.required) continue;
      let value = raw;
      if (raw === '' && field.nullable) value = null;
      else if (field.type === 'JSONField') value = JSON.parse(raw);
      else if (field.type === 'BooleanField') {
        if (!['true', 'false'].includes(raw)) throw new Error(`${field.name} 须为 true 或 false`);
        value = raw === 'true';
      } else if (field.type.includes('Integer') || field.type === 'FloatField') {
        if (!raw.trim() || !Number.isFinite(Number(raw))) throw new Error(`${field.name} 须为数字`);
        value = Number(raw);
      }
      data[field.name] = value;
    }
    await mutateRecord(state.editing ? 'update' : 'create', state.editing, data);
    ui('editor-dialog').close();
  } catch (error) { ui('editor-status').textContent = error.message; }
}

/** 功能：确认并删除当前共享记录。输入：row 已读取的目标。输出：成功关闭详情，失败显示原因。
 * 逻辑：要求用户确认具体记录，服务端拒绝有引用的删除。约束：不自动级联、不批量删除。 */
async function deleteRecord(row) {
  if (state.busy || !window.confirm(`删除这条共享虚构记录？\n${recordTitle(row)}\n${row.pk}\n此操作会影响所有账号。`)) return;
  try { await mutateRecord('delete', row); ui('record-dialog').close(); }
  catch (error) { ui('record-owner').textContent = error.message; }
}

/** 功能：调用统一维护接口并刷新清单数量。输入：operation 操作，row 可空旧记录，data 可选业务字段。
 * 输出：成功后刷新目录与列表。逻辑：每次逻辑提交生成幂等 UUID，相同参数的手动再提交复用，Tool 服务保留操作者及版本。
 * 约束：失败保留原页面，不自动重放未知结果；按钮在请求期间禁用。 */
async function mutateRecord(operation, row, data) {
  state.busy = true;
  ui('save-record').disabled = true; ui('delete-record').disabled = true;
  try {
    const args = { batch: state.batch.batch, model: state.table.model };
    if (row) Object.assign(args, { pk: row.pk, expected: row.fingerprint });
    if (data !== undefined) args.data = data;
    const signature = JSON.stringify({ operation, args });
    if (state.pending?.signature !== signature) state.pending = { signature, key: crypto.randomUUID() };
    await request('agent-tools/call/', { method: 'POST', data: { name: `experiments.${operation}`, arguments: args, idempotency_key: state.pending.key } });
    const catalog = await getJson('/api/v1/experiments/');
    state.batch = catalog.batches.find(batch => batch.batch === args.batch);
    state.table = state.batch.tables.find(table => table.model === args.model);
    ui('batch-name').textContent = `${state.batch.batch} · ${state.batch.total} 条记录`;
    for (const link of ui('experiment-tables').children) link.lastChild.textContent = String(state.batch.tables.find(table => table.model === link.dataset.model).count);
    await loadRows();
    message('共享虚构数据已保存，操作已记录。');
  } finally { state.busy = false; ui('save-record').disabled = false; ui('delete-record').disabled = false; }
}

/** 功能：读取并绘制当前表的一页。输入：state 与搜索控件。输出：记录表、总量和分页状态。
 * 逻辑：请求代次阻止旧响应覆盖新选择；DOM 节点按纯文本构建。约束：失败清除旧记录以免误认成功。 */
async function loadRows() {
  const sequence = ++state.sequence;
  const routeQuery = new URLSearchParams(location.hash.slice(1));
  const query = new URLSearchParams({ page: String(state.page), page_size: '50', q: ui('search-query').value, owner: ui('owner-query').value });
  if (routeQuery.has('pk')) query.set('pk', routeQuery.get('pk'));
  message('正在读取并核验实验数据…');
  ui('experiment-rows').replaceChildren();
  try {
    const data = await getJson(tableUrl(state.table.model) + '?' + query);
    if (sequence !== state.sequence) return;
    state.rows = data.results;
    for (const row of state.rows) {
      const tr = document.createElement('tr');
      for (const value of [recordTitle(row), `${row.owner.username}（${row.owner.id}）`, row.pk]) {
        const td = document.createElement('td'); td.textContent = value; tr.append(td);
      }
      const td = document.createElement('td');
      const button = document.createElement('button'); button.type = 'button'; button.textContent = '查看详情';
      button.onclick = () => showRecord(row); td.append(button); tr.append(td);
      ui('experiment-rows').append(tr);
    }
    ui('row-count').textContent = `${data.count} 条`;
    ui('page-label').textContent = `${data.page} / ${Math.max(1, Math.ceil(data.count / data.page_size))}`;
    ui('previous-page').disabled = state.page <= 1;
    ui('next-page').disabled = state.page * data.page_size >= data.count;
    message(data.count ? '当前记录已按导入清单核验。所有记录仅供实验使用。' : '当前筛选下没有已开放的实验记录。');
  } catch (error) {
    if (sequence !== state.sequence) return;
    ui('row-count').textContent = '读取失败';
    ui('previous-page').disabled = true; ui('next-page').disabled = true;
    message(error.message, true);
  }
}

/** 功能：响应模型及外键路由。输入：location.hash 与目录状态。输出：表标题、结构、列表。
 * 逻辑：只允许目录已声明的模型；切换表清除分页和文本筛选。约束：未知模型不自动回退。 */
function route() {
  if (!state.batch) return;
  ui('record-dialog').close(); ui('editor-dialog').close();
  const params = new URLSearchParams(location.hash.slice(1));
  const model = params.get('table') || 'crm.Company';
  state.table = state.batch.tables.find(table => table.model === model);
  state.page = 1;
  ui('search-query').value = ''; ui('owner-query').value = '';
  if (!state.table) { ++state.sequence; ui('experiment-rows').replaceChildren(); message('该表未开放。', true); return; }
  ui('table-title').textContent = state.table.name;
  ui('table-name').textContent = `${state.table.model} · ${state.table.table}`;
  ui('table-schema').textContent = JSON.stringify(state.table.fields, null, 2);
  ui('create-record').hidden = !state.table.write.create;
  for (const link of ui('experiment-tables').children) link.toggleAttribute('aria-current', link.dataset.model === model);
  loadRows();
}

/** 功能：加载实验目录并挂载事件。输入：当前 Session。输出：批次目录与初始表。
 * 逻辑：读取固定已批准批次；登录失败提供原工作台入口；不自动开放未来批次。
 * 约束：维护使用统一 Tool API 和 CSRF，不调用模型、不执行外部动作。 */
async function start() {
  try {
    const catalog = await getJson('/api/v1/experiments/');
    state.batch = catalog.batches[0];
    if (!state.batch) { message('当前没有已开放的实验批次。'); return; }
    ui('experiment-content').hidden = false;
    ui('batch-owner').textContent = `批次归属：${state.batch.owner.username}`;
    ui('batch-name').textContent = `${state.batch.batch} · ${state.batch.total} 条记录`;
    ui('experiment-notice').textContent = state.batch.notice;
    ui('table-count').textContent = String(state.batch.tables.length);
    const download = ui('export-batch'); download.hidden = false;
    download.href = `/api/v1/experiments/${encodeURIComponent(state.batch.batch)}/export/`;
    for (const table of state.batch.tables) {
      const link = document.createElement('a'); link.dataset.model = table.model;
      link.href = '#' + new URLSearchParams({ table: table.model });
      const title = document.createElement('span'); title.textContent = table.name;
      const count = document.createElement('span'); count.textContent = String(table.count);
      link.append(title, count); ui('experiment-tables').append(link);
    }
    ui('experiment-filter').onsubmit = event => { event.preventDefault(); state.page = 1; loadRows(); };
    ui('clear-filter').onclick = () => { history.replaceState(null, '', '#' + new URLSearchParams({ table: state.table.model })); route(); };
    ui('previous-page').onclick = () => { state.page--; loadRows(); };
    ui('next-page').onclick = () => { state.page++; loadRows(); };
    ui('close-record').onclick = () => ui('record-dialog').close();
    ui('create-record').onclick = () => editRecord();
    ui('close-editor').onclick = () => ui('editor-dialog').close();
    ui('editor-form').onsubmit = saveRecord;
    window.addEventListener('hashchange', route);
    route();
  } catch (error) {
    message(error.message, true); ui('login-link').hidden = false;
  }
}
start();
