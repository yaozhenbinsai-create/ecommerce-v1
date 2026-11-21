import { $, $$, h, arr, api, button, errorBox } from './ui.js';

function pagedPicker(host, options) {
  let page = 1;
  let query = '';
  let selected = options.selected || null;
  let request = 0;
  let disposed = false;
  const cache = new Map(selected ? [[Number(selected.id), selected]] : []);
  host.innerHTML = `<section class="paged-picker"><div class="row-between"><h4>${h(options.label)}</h4><span class="muted small">每页 24 条 · 最近创建在前</span></div><input type="hidden" name="${h(options.name)}" value="${h(selected?.id || '')}"><div class="picker-selected"></div><div class="picker-search"><input type="search" aria-label="${h(options.searchLabel)}" placeholder="${h(options.searchLabel)}" data-transient><button type="button" class="button secondary small" data-picker-search>搜索</button></div><div class="picker-results"></div></section>`;
  const selectedHost = $('.picker-selected', host);
  const results = $('.picker-results', host);
  const hidden = $(`input[name="${options.name}"]`, host);
  const selection = () => {
    selectedHost.innerHTML = selected
      ? `<strong>当前已选：${h(options.title(selected))}</strong>${options.optional ? '<button type="button" class="text-link" data-picker-clear>暂不绑定</button>' : ''}`
      : `<span class="muted small">${h(options.optional ? '暂不绑定设计，可先保存文案草稿' : '尚未选择实际 SKU')}</span>`;
    $('[data-picker-clear]', host)?.addEventListener('click', () => choose(null));
  };
  const choose = (item) => {
    selected = item;
    hidden.value = item?.id || '';
    selection();
    $$('[data-picker-id]', results).forEach((node) => {
      node.classList.toggle('selected', Number(node.dataset.pickerId) === Number(selected?.id));
      node.setAttribute('aria-pressed', Number(node.dataset.pickerId) === Number(selected?.id));
    });
    options.onSelect?.(item);
    hidden.dispatchEvent(new Event('input', { bubbles: true }));
    hidden.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const load = async () => {
    const token = ++request;
    results.innerHTML = '<p class="muted small">正在读取可选记录…</p>';
    try {
      const data = await options.load(page, query);
      if (disposed || token !== request || !host.isConnected) return;
      arr(data.items).forEach((item) => cache.set(Number(item.id), item));
      results.innerHTML = arr(data.items).length
        ? `<div class="picker-records">${data.items.map((item) => `<button type="button" class="picker-record ${Number(item.id) === Number(selected?.id) ? 'selected' : ''}" data-picker-id="${item.id}" aria-pressed="${Number(item.id) === Number(selected?.id)}"><strong>${h(options.title(item))}</strong><span>${h(options.detail(item))}</span></button>`).join('')}</div>`
        : '<p class="muted small">当前搜索没有匹配记录。可清空搜索或返回商品补充资料。</p>';
      results.insertAdjacentHTML(
        'beforeend',
        `<div class="pagination"><span>共 ${Number(data.total || 0)} 条 · 第 ${page} 页</span><div>${button('← 上页', 'picker-prev', 'secondary small', page > 1 ? '' : 'disabled')}${button('下页 →', 'picker-next', 'secondary small', page * 24 < Number(data.total) ? '' : 'disabled')}</div></div>`,
      );
      $$('[data-picker-id]', results).forEach((node) =>
        node.addEventListener('click', () => choose(cache.get(Number(node.dataset.pickerId)))),
      );
      $('[data-action=picker-prev]', results).onclick = () => {
        page--;
        load();
      };
      $('[data-action=picker-next]', results).onclick = () => {
        page++;
        load();
      };
    } catch (error) {
      if (!disposed && token === request && host.isConnected) results.innerHTML = errorBox(error);
    }
  };
  const search = () => {
    query = $('input[type=search]', host).value.trim();
    page = 1;
    load();
  };
  $('[data-picker-search]', host).onclick = search;
  $('input[type=search]', host).addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      search();
    }
  });
  selection();
  load();
  host.closest('form')?.addEventListener('draftrestored', async () => {
    const restored = Number(hidden.value);
    if (!restored || restored === Number(selected?.id) || !options.loadSelected) return;
    try {
      const item = cache.get(restored) || (await options.loadSelected(restored));
      if (!disposed && host.isConnected) choose(item);
    } catch (error) {
      if (!disposed && host.isConnected) results.innerHTML = errorBox(error);
    }
  });
  return {
    dispose() {
      disposed = true;
      request++;
    },
    selected: () => selected,
  };
}

export const skuPicker = (host, productId, onSelect, selected = null) =>
  pagedPicker(host, {
    name: 'sku_id',
    label: '选择真实 SKU',
    searchLabel: '搜索规格名称或内部 SKU 编码',
    selected,
    onSelect,
    title: (item) => `${item.label || '规格 #' + item.id} · ${item.code || ''}`,
    detail: (item) =>
      [item.facts?.color, item.facts?.size, item.facts?.material].filter(Boolean).join(' · ') ||
      '事实在商品档案中维护',
    load: (page, q) =>
      api(`/api/products/${productId}/skus?` + new URLSearchParams({ page, page_size: 24, q })),
    loadSelected: (id) => api('/api/skus/' + id),
  });

export const designPicker = (host, productId, skuId, selected = null, onSelect = null) =>
  pagedPicker(host, {
    name: 'design_id',
    label: '选择此 SKU 的设计版本',
    searchLabel: '搜索任务书文字或设计编号',
    selected,
    onSelect,
    optional: true,
    title: (item) =>
      `设计 #${item.id} · v${item.revision} · ${item.stale ? '事实已变化' : { completed: '图组已生成', draft: '任务书草稿', running: '生成中', failed: '执行失败', unknown: '结果待核对' }[item.status] || item.status}`,
    detail: (item) => String(item.brief || '没有任务书说明').slice(0, 130),
    load: (page, q) =>
      api(
        '/api/designs?' +
          new URLSearchParams({ product_id: productId, sku_id: skuId, page, page_size: 24, q }),
      ),
    loadSelected: (id) => api('/api/designs/' + id),
  });
