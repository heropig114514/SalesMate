/** Responsibility: Browse approved shared synthetic experiment data and related sources within the existing site.
 * Implementation: GET browsing and idempotent Tool maintenance share the Session; URLs retain related primary keys; bodies render as plain text.
 * Relationships: Header cache version reflects removal of the experiment entry; uses sales.experiments, experiment_writes, experiments.html, and product-header.js. No external actions.
 * Directory: getJson, message, tableUrl, recordTitle, showRecord, editRecord, saveRecord, deleteRecord, mutateRecord, loadRows, route, start.
 * Variable index: ui locates DOM elements; state stores batch, page, current record, and request generation.
 */
import './product-header.js?v=20260922-nav';
import { request } from './api.js?v=20260921-product';

const ui = id => document.getElementById(id);
const state = { batch: null, table: null, page: 1, sequence: 0, rows: [], editing: null, busy: false, pending: null };

/** Function: Read a same-origin experiment endpoint. Inputs: url is a relative path. Outputs: Parsed JSON.
 * Logic: Reuse the shared requester's session, cache version, and error interpretation. Constraints: Never create sessions or retry failed requests. */
async function getJson(url) {
  return request(url.slice('/api/v1/'.length));
}

/** Function: Display status. Inputs: text and error flag. Outputs: None.
 * Logic: Write plain text into the aria-live region. Constraints: Never execute server content as HTML. */
function message(text, error = false) {
  ui('experiment-status').textContent = text;
  ui('experiment-status').toggleAttribute('data-error', error);
}

/** Function: Build a table-query path. Inputs: label is the model name. Outputs: A same-origin API path.
 * Logic: Encode batch and model path segments. Constraints: The server independently checks its allowlist. */
function tableUrl(label) {
  return `/api/v1/experiments/${encodeURIComponent(state.batch.batch)}/${encodeURIComponent(label)}/`;
}

/** Function: Select a short record title. Inputs: row is an experiment projection. Outputs: A title string.
 * Logic: Try common business-name fields in order, otherwise show model/primary key. Constraints: Truncate list titles only; retain full details. */
function recordTitle(row) {
  const fields = row.fields;
  return String(fields.title || fields.name || fields.company_name || fields.subject || fields.number || fields.tool || fields.username || fields.source || fields.content || fields.event || fields.email || fields.address || `${state.table.name} ${row.pk}`).slice(0, 160);
}

/** Function: Show details, maintenance actions, and relation navigation. Inputs: row is a loaded record. Outputs: A dialog and available file-download links.
 * Logic: Link foreign keys to exact primary-key queries according to model structure; destination pages show no results when authorized records are unavailable.
 * Constraints: Show maintenance buttons only with server write capability; do not expand records outside the manifest. */
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

/** Function: Build a create/edit form. Inputs: row is an existing record, or null for creation. Outputs: Field controls and edit state.
 * Logic: Show only catalog-declared writable fields, use primary keys for relations, and retain structured JSON text; creation may omit defaulted fields.
 * Constraints: The browser does not submit ownership, primary keys, or system fields; validation remains server-side. */
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

/** Function: Submit shared-record maintenance from a form. Inputs: event is the form event. Outputs: Close the dialog on success; retain input on failure.
 * Logic: Parse by field type, submit only changed fields on updates, and use the old fingerprint to prevent overwriting concurrent edits.
 * Constraints: No automatic retries, swallowed errors, or protected-field changes. */
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

/** Function: Confirm and delete the current shared record. Inputs: row is the observed target. Outputs: Close details on success; show the reason on failure.
 * Logic: Ask confirmation for the specific record; the server rejects deletion when references exist. Constraints: No automatic cascades or bulk deletion. */
async function deleteRecord(row) {
  if (state.busy || !window.confirm(`删除这条共享虚构记录？\n${recordTitle(row)}\n${row.pk}\n此操作会影响所有账号。`)) return;
  try { await mutateRecord('delete', row); ui('record-dialog').close(); }
  catch (error) { ui('record-owner').textContent = error.message; }
}

/** Function: Call the shared maintenance endpoint and refresh manifest counts. Inputs: operation, nullable prior row, and optional business data.
 * Outputs: Refresh directory/list on success. Logic: Generate an idempotency UUID per logical submission, reuse it for manual resubmission with identical arguments, and retain actor/version in the Tool service.
 * Constraints: Keep the page on failure, never automatically replay unknown outcomes, and disable buttons during requests. */
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

/** Function: Read and render one page of the current table. Inputs: state and search controls. Outputs: Record table, count, and pagination state.
 * Logic: Request generations prevent stale responses from overwriting new selections; build DOM nodes using plain text. Constraints: Clear stale records on failure to avoid implying success. */
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

/** Function: Respond to model and foreign-key routes. Inputs: location.hash and catalog state. Outputs: Table title, structure, and list.
 * Logic: Allow declared catalog models only; changing tables clears pagination and text filters. Constraints: No automatic fallback for unknown models. */
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

/** Function: Load the experiment catalog and bind events. Inputs: Current Session. Outputs: Batch catalog and initial table.
 * Logic: Read fixed approved batches; offer the original workspace entry after authentication failure; never automatically expose future batches.
 * Constraints: Maintenance uses the shared Tool API and CSRF, without model calls or external actions. */
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
