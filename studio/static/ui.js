export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => [...root.querySelectorAll(s)];
export const h = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
export const date = (value) =>
  value
    ? new Date(value).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    : '—';
export const number = (value) => Number(value || 0).toLocaleString('zh-CN');
export const arr = (value) => (Array.isArray(value) ? value : []);
export const asset = (id) => (id ? `/api/assets/${encodeURIComponent(id)}` : '');
export const thumbnail = (id, size = 320) =>
  id
    ? `/api/assets/${encodeURIComponent(id)}/thumbnail?size=${[160, 320, 640].includes(size) ? size : 320}`
    : '';
export const thumbnailFallback = (id) =>
  `onerror="${h(`this.onerror=null;this.src=${JSON.stringify(asset(id))}`)}"`;
export const imageRoleLabel = (role = '') => {
  if (['main', 'hero', 'sales-1', 'sales_1'].includes(role)) return '商品主图';
  const numbered = String(role).match(/^(sales|detail)[-_](\d+)$/);
  if (numbered) return (numbered[1] === 'sales' ? '销售图 ' : '详情图 ') + numbered[2];
  if (String(role).startsWith('detail')) return '详情图';
  if (String(role).startsWith('sales')) return '销售图';
  return role || '设计图片';
};
export const compareImageRoles = (left, right) => {
  const order = (item) => {
    const role = String(item.role || '');
    if (['main', 'hero', 'sales-1', 'sales_1'].includes(role)) return 0;
    const number = Number(role.match(/(\d+)$/)?.[1] || 1000);
    return /^sales/.test(role)
      ? 100 + number
      : /^detail/.test(role)
        ? 2000 + number
        : 4000 + number;
  };
  return order(left) - order(right);
};
export const href = (path, params = {}) =>
  `#${path}${Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '').length ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '')).toString() : ''}`;
export const link = (path, label, cls = 'button secondary', params = {}) =>
  `<a class="${cls}" href="${h(href(path, params))}">${label}</a>`;
export const icons = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  box: '<path d="m12 3 9 5-9 5-9-5 9-5Z M3 8v9l9 5 9-5V8 M12 13v9 M7.5 5.5l9 5"/>',
  search: '<circle cx="10.5" cy="10.5" r="7.5"/><path d="m16 16 5 5"/>',
  spark:
    '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z M20 2v4 M18 4h4"/>',
  image:
    '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
  upload: '<path d="M12 16V3 M7 8l5-5 5 5 M3 15v5a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-5"/>',
  download: '<path d="M12 3v13 M7 11l5 5 5-5 M3 16v4a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-4"/>',
  list: '<path d="M8 6h13 M8 12h13 M8 18h13 M3 6h1 M3 12h1 M3 18h1"/>',
  settings:
    '<path d="M4 7h16 M4 17h16"/><circle cx="8" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
  arrow: '<path d="M4 12h16 M14 6l6 6-6 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  close: '<path d="m6 6 12 12 M6 18 18 6"/>',
  plus: '<path d="M12 5v14 M5 12h14"/>',
  warning: '<path d="m12 3 10 18H2L12 3Z M12 9v5 M12 17v1"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8l10-5Z M2 12l10 5 10-5 M2 16l10 5 10-5"/>',
  file: '<path d="M13 3H5v18h14V9l-6-6Z M13 3v6h6 M8 13h8 M8 17h6"/>',
  globe:
    '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
};
export const icon = (name, cls = '') =>
  `<svg class="icon ${cls}" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.box}</svg>`;
export const statusLabels = {
  completed: '图组已生成',
  file_generated: '文件已生成',
  awaiting_batch_evidence: '待核对批次归属',
  needs_attention: '存在待处理行',
  conflict: '资料冲突',
  content_verified: '图片内容已核对',
  draft: '草稿',
  queued: '等待执行',
  running: '进行中',
  succeeded: '已完成',
  failed: '执行失败',
  unknown: '结果待核对',
  cancelled: '已取消',
  ready: '已就绪',
  missing: '待补资料',
  stale: '资料已变更',
  parsed: '待映射',
  applied: '已导入',
  partial: '部分完成',
  exported: '文件已生成',
  registered: '已登记',
  structurally_verified: '结构已核验',
  platform_verified: '平台已验证',
  pending: '待处理',
  unverified: '未验证',
  verified: '已验证',
  active: '可用',
  disabled: '已停用',
  unsupported: '待适配',
};
export const badge = (status, label) =>
  `<span class="badge ${h(status || 'draft')}">${h(label || statusLabels[status] || status || '草稿')}</span>`;
export const heading = (eyebrow, title, description = '', actions = '') =>
  `<div class="page-heading"><div><p class="eyebrow">${h(eyebrow)}</p><h1>${h(title)}</h1>${description ? `<p class="page-description">${h(description)}</p>` : ''}</div>${actions ? `<div class="heading-actions">${actions}</div>` : ''}</div>`;
export const empty = (title, description = '', action = '', type = 'box') =>
  `<div class="empty-state"><div class="empty-symbol">${icon(type)}</div><h3>${h(title)}</h3>${description ? `<p>${h(description)}</p>` : ''}${action ? `<div class="empty-actions">${action}</div>` : ''}</div>`;
export const panel = (title, body, action = '', cls = '') =>
  `<section class="panel ${cls}"><header class="panel-heading"><h2>${title}</h2>${action}</header>${body}</section>`;
export const notice = (text, type = 'info') =>
  `<div class="inline-notice ${type}">${icon(type === 'error' || type === 'warning' ? 'warning' : 'spark')}<div>${text}</div></div>`;
export const field = (label, name, value = '', options = {}) =>
  `<label class="field ${options.cls || ''}"><span>${h(label)}${options.required ? '<b class="required">*</b>' : ''}</span>${options.textarea ? `<textarea name="${h(name)}" rows="${options.rows || 4}" ${options.required ? 'required' : ''} ${options.readonly ? 'readonly' : ''} placeholder="${h(options.placeholder || '')}">${h(value)}</textarea>` : `<input name="${h(name)}" type="${h(options.type || 'text')}" value="${h(value)}" ${options.required ? 'required' : ''} ${options.readonly ? 'readonly' : ''} ${options.min !== undefined ? `min="${options.min}"` : ''} ${options.max !== undefined ? `max="${options.max}"` : ''} ${options.step ? `step="${options.step}"` : ''} placeholder="${h(options.placeholder || '')}" ${options.accept ? `accept="${h(options.accept)}"` : ''}>`}${options.help ? `<small>${h(options.help)}</small>` : ''}</label>`;
export const select = (label, name, options, value = '', attrs = '') => {
  const normalized = options.map((item) =>
    typeof item === 'object' ? item : { value: item, label: item },
  );
  if (
    value !== '' &&
    value !== null &&
    value !== undefined &&
    !normalized.some((o) => String(o.value ?? o.id ?? '') === String(value))
  )
    normalized.push({ value, label: '当前选择 · ' + value });
  return `<label class="field"><span>${h(label)}</span><select name="${h(name)}" ${attrs}>${normalized.map((o) => `<option value="${h(o.value ?? o.id ?? '')}" ${String(o.value ?? o.id ?? '') === String(value ?? '') ? 'selected' : ''} ${o.disabled ? 'disabled' : ''}>${h(o.label ?? o.name ?? '')}</option>`).join('')}</select></label>`;
};
export const check = (label, name, value, checked = false) =>
  `<label class="check"><input type="checkbox" name="${h(name)}" value="${h(value)}" ${checked ? 'checked' : ''}><span>${h(label)}</span></label>`;
export const button = (label, action, cls = 'secondary', attrs = '') =>
  `<button type="button" class="button ${cls}" data-action="${h(action)}" ${attrs}>${label}</button>`;
export const submit = (label, iconName = 'check') =>
  `<button type="submit" class="button primary">${icon(iconName)}${label}</button>`;
export const thumb = (id, label = '', cls = '') =>
  id
    ? `<img class="thumb ${cls}" src="${thumbnail(id, cls === 'large' ? 320 : 160)}" ${thumbnailFallback(id)} alt="${h(label)}" loading="lazy">`
    : `<span class="thumb no-image ${cls}">${icon('image')}</span>`;
export const table = (headers, rows, emptyText = '暂无记录', cls = '') =>
  rows.length
    ? `<div class="table-scroll ${cls}"><table class="data-table"><thead><tr>${headers.map((x) => `<th>${x}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((v, i) => `<td data-label="${h(String(headers[i] || '').replace(/<[^>]*>/g, ''))}">${v ?? '—'}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
    : empty(emptyText);
export const pagination = (data, path, params = {}) => {
  const page = Number(data.page || 1),
    size = Number(data.page_size || 24),
    total = Number(data.total || 0),
    pages = Math.max(1, Math.ceil(total / size));
  return `<div class="pagination"><span>共 ${number(total)} 条 · 第 ${page} / ${pages} 页</span><div>${page > 1 ? link(path, '← 上一页', 'button small secondary', { ...params, page: page - 1 }) : '<button class="button small secondary" disabled>← 上一页</button>'}${page < pages ? link(path, '下一页 →', 'button small secondary', { ...params, page: page + 1 }) : '<button class="button small secondary" disabled>下一页 →</button>'}</div></div>`;
};
export const metadata = (pairs) =>
  `<dl class="metadata">${pairs
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => `<div><dt>${h(k)}</dt><dd>${v ?? '—'}</dd></div>`)
    .join('')}</dl>`;
export const safeUrl = (value) => {
  try {
    const u = new URL(value);
    return ['http:', 'https:'].includes(u.protocol) ? u.href : '#';
  } catch {
    return '#';
  }
};
export const external = (url, label = '查看来源') =>
  `<a class="text-link" href="${h(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${h(label)} ↗</a>`;
export const jsonObject = (value, fallback = {}) => {
  if (value && typeof value === 'object') return value;
  try {
    return JSON.parse(value || '{}');
  } catch {
    return fallback;
  }
};
export const formValues = (form) => Object.fromEntries(new FormData(form));
export const int = (value) => (value === '' || value == null ? null : Number(value));
export const intent = (prefix) => `${prefix}-${crypto.randomUUID()}`;
export function toast(message, type = 'success') {
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.innerHTML = icon(type === 'success' ? 'check' : 'warning') + `<span>${h(message)}</span>`;
  $('#toast-region').append(node);
  setTimeout(() => node.remove(), 6500);
}
export async function api(path, options = {}) {
  if (
    (!options.method || options.method === 'GET') &&
    /^\/api\/(products|sources|designs|artworks|listings|imports|exports|jobs)(?:\?|$)/.test(path)
  ) {
    const [base, query = ''] = path.split('?');
    const params = new URLSearchParams(query);
    if (!params.has('page_size'))
      params.set(
        'page_size',
        Math.min(100, Math.max(1, Number(sessionStorage.getItem('woven-page-size')) || 24)),
      );
    path = base + '?' + params;
  }
  const init = { ...options, headers: { ...(options.headers || {}) } };
  if (init.body && !(init.body instanceof FormData)) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
  let value;
  try {
    value = await response.json();
  } catch {
    value = { error: { message: `服务返回了无法解析的响应（${response.status}）` } };
  }
  if (!response.ok) {
    const err = value.error || value.detail || value;
    const error = new Error(
      typeof err === 'string' ? err : err.message || `操作失败（${response.status}）`,
    );
    error.status = response.status;
    error.fields = err.fields;
    error.code = err.code;
    throw error;
  }
  return value;
}
export function errorBox(error) {
  const fields = Array.isArray(error.fields)
    ? error.fields
        .map(
          (item) =>
            `<li><strong>${h(item.label || item.field || '资料')}${item.listing_id ? ' · 刊登 #' + h(item.listing_id) : item.family_id ? ' · 分组 #' + h(item.family_id) : ''}</strong>：${h(item.message || '待补充')}${item.href ? ` <a class="text-link" href="${h(item.href.startsWith('#') ? item.href : '#' + item.href)}">定位处理 →</a>` : ''}</li>`,
        )
        .join('')
    : Object.entries(error.fields || {})
        .map(
          ([k, v]) =>
            `<li><strong>${h(k)}</strong>：${h(typeof v === 'object' ? JSON.stringify(v) : v)}</li>`,
        )
        .join('');
  return notice(
    `<strong>${h(error.status === 409 ? '资料已被更新，当前输入已保留' : error.message || '操作未完成')}</strong>${error.status === 409 ? '<p>重新打开最新记录并核对差异后再保存，避免覆盖其他修改。</p>' : ''}${fields ? `<ul>${fields}</ul>` : ''}`,
    'error',
  );
}
export function setFormError(form, error) {
  let box = $('.form-errors', form);
  if (!box) {
    box = document.createElement('div');
    box.className = 'form-errors';
    form.prepend(box);
  }
  box.innerHTML = errorBox(error);
  box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}
export async function busy(el, work) {
  const old = el?.disabled;
  if (el) {
    el.disabled = true;
    el.classList.add('is-busy');
    el.setAttribute('aria-busy', 'true');
  }
  try {
    return await work();
  } finally {
    if (el && el.isConnected) {
      el.disabled = old;
      el.classList.remove('is-busy');
      el.removeAttribute('aria-busy');
    }
  }
}
export function bindForm(selector, handler, root = document) {
  const form = $(selector, root);
  if (!form) return;
  let submitting = false;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (submitting) return;
    submitting = true;
    const submitter = event.submitter || $('button[type=submit]', form);
    const submitControls = [...form.elements].filter((element) => element.type === 'submit');
    const previousDisabled = submitControls.map((element) => element.disabled);
    submitControls.forEach((element) => {
      element.disabled = true;
    });
    $('.form-errors', form)?.remove();
    try {
      await busy(submitter, async () => {
        try {
          await handler(form, formValues(form));
        } catch (error) {
          setFormError(form, error);
        }
      });
    } finally {
      submitting = false;
      submitControls.forEach((element, index) => {
        if (element.isConnected) element.disabled = previousDisabled[index];
      });
    }
  });
}
export function bindAction(name, handler, root = document) {
  $$(`[data-action="${name}"]`, root).forEach((el) =>
    el.addEventListener('click', async (e) => {
      e.preventDefault();
      await busy(el, async () => {
        try {
          await handler(el, e);
        } catch (error) {
          toast(error.message, 'error');
        }
      });
    }),
  );
}
export function modal(title, content, bind) {
  const el = $('#modal');
  el.dataset.dirty = 'false';
  el.innerHTML = `<header class="modal-header"><div><p class="eyebrow">产品工作空间</p><h2 id="modal-title">${h(title)}</h2></div><button class="icon-button" type="button" data-modal-close aria-label="关闭">${icon('close')}</button></header><div class="modal-content">${content}</div>`;
  const close = () => {
    if (el.dataset.dirty !== 'true') {
      el.close();
      return;
    }
    if ($('[data-unsaved-dialog]', el)) return;
    const message = document.createElement('div');
    message.dataset.unsavedDialog = 'true';
    message.className = 'draft-notice';
    message.innerHTML =
      '<span>当前内容尚未保存，关闭会丢失本次编辑。</span><button type="button" class="text-link" data-continue-edit>继续编辑</button><button type="button" class="text-link" data-discard-edit>放弃并关闭</button>';
    $('.modal-content', el).prepend(message);
    $('[data-continue-edit]', message).onclick = () => message.remove();
    $('[data-discard-edit]', message).onclick = () => {
      el.dataset.dirty = 'false';
      el.close();
    };
    message.scrollIntoView({ block: 'nearest' });
  };
  el.oninput = () => {
    el.dataset.dirty = 'true';
  };
  el.oncancel = (event) => {
    if (el.dataset.dirty === 'true') {
      event.preventDefault();
      close();
    }
  };
  $('[data-modal-close]', el).onclick = close;
  if (!el.open) el.showModal();
  bind?.(el);
  enhanceProductSelectors(el);
  return el;
}
export const closeModal = () => {
  const el = $('#modal');
  el.dataset.dirty = 'false';
  el.close();
};
export function enhanceProductSelectors(root = document) {
  $$('select[name=product_id]', root).forEach((select) => {
    if (select.dataset.searchable || select.disabled) return;
    select.dataset.searchable = 'true';
    const field = select.closest('.field');
    if (!field) return;
    const wrapper = document.createElement('div');
    wrapper.className =
      'product-picker' + (field.classList.contains('span-all') ? ' span-all' : '');
    field.parentNode.insertBefore(wrapper, field);
    wrapper.append(field);
    const search = document.createElement('div');
    search.className = 'product-picker-search';
    search.innerHTML =
      '<input type="search" placeholder="搜索商品名称…" aria-label="搜索可选商品"><small aria-live="polite"></small>';
    wrapper.append(search);
    const input = $('input', search),
      feedback = $('small', search);
    let timer,
      request = 0;
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') event.preventDefault();
    });
    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const token = ++request,
          chosen = select.selectedOptions[0],
          value = select.value;
        feedback.textContent = '正在搜索';
        try {
          const data = await api(
            '/api/products?' + new URLSearchParams({ q: input.value, page_size: 24 }),
          );
          if (token !== request || !select.isConnected) return;
          const rows = arr(data.items);
          const options = [
            { value: '', label: '请选择商品' },
            ...rows.map((p) => ({ value: String(p.id), label: p.name })),
          ];
          if (value && !options.some((o) => o.value === value))
            options.splice(1, 0, { value, label: chosen?.textContent || '当前商品' });
          select.replaceChildren(
            ...options.map((o) => {
              const option = document.createElement('option');
              option.value = o.value;
              option.textContent = o.label;
              option.selected = o.value === value;
              return option;
            }),
          );
          feedback.textContent =
            data.total > 24
              ? `找到 ${data.total} 款，显示前 24 款；可缩小关键词。`
              : `找到 ${data.total || 0} 款商品`;
        } catch (error) {
          feedback.textContent = error.message;
        }
      }, 300);
    });
  });
}
export async function upload(file) {
  const form = new FormData();
  form.append('file', file);
  return api('/api/assets', { method: 'POST', body: form });
}
export const progress = (job) =>
  `<div class="job-progress"><div><span>${h(job.stage || statusLabels[job.status] || '等待执行')}</span><strong>${Math.min(100, Math.max(0, Number(job.progress || 0)))}%</strong></div><progress max="100" value="${Number(job.progress || 0)}"></progress></div>`;
export const steps = (job) =>
  `<ol class="step-list">${arr(job?.steps)
    .map(
      (s) =>
        `<li class="${h(s.status)}"><span class="step-marker">${icon(s.status === 'succeeded' ? 'check' : s.status === 'failed' || s.status === 'unknown' ? 'warning' : 'clock')}</span><div><strong>${h(s.label || s.key)}</strong><span>${h(statusLabels[s.status] || s.status || '待执行')}</span>${s.error ? `<p class="text-error">${h(s.error)}</p>` : ''}${s.request_ref ? `<small>请求 ${h(typeof s.request_ref === 'object' ? JSON.stringify(s.request_ref) : s.request_ref)}</small>` : ''}</div>${s.asset_id ? `<a class="text-link" href="${asset(s.asset_id)}" target="_blank">查看成果 ↗</a>` : ''}</li>`,
    )
    .join('')}</ol>`;
