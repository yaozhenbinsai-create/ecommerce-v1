import {
  $,
  $$,
  h,
  arr,
  api,
  modal,
  button,
  notice,
  table,
  badge,
  date,
  errorBox,
  bindAction,
} from './ui.js';

const labels = {
  name: '名称',
  enabled: '使用状态',
  config: '配置',
  components: '可用组件',
  attributes: '适用属性',
  key: '字段标识',
  label: '显示名称',
  type: '数据类型',
  unit: '单位',
  options: '允许选项',
  choices: '允许选项',
  platform: '平台',
  kind: '研究范围',
  query: '默认关键词',
  locale: '地区 / 语言提示',
  contact: '联系人',
  notes: '资料说明',
  mode: '设计方式',
  route_count: '创意路线数',
  sales_count: '销售图数',
  detail_count: '详情图数',
  style: '风格与场景',
  repeat: '循环方案',
  width_cm: '宽 / cm',
  height_cm: '高 / cm',
  unit_width_mm: '单元宽 / mm',
  unit_height_mm: '单元高 / mm',
  dpi: 'DPI',
  bleed_mm: '出血 / mm',
  format: '输出格式',
  process: '工艺名称',
  evidence: '工艺依据',
  design_defaults: '设计默认值',
  source_category: '来源品类',
  cross_category: '跨品类参考',
  limit: '收集数量',
};
const typeNames = {
  text: '文本',
  number: '小数',
  integer: '整数',
  choice: '选项',
  boolean: '是 / 否',
  supplier: '供应商展示',
  large: '完整构图',
  repeat: '循环花型',
  basic: '规则平铺',
  'half-drop': '半落位',
  market: '电商平台研究',
  social: '社交内容研究',
  hotspot: '开放热点',
};
function semantic(snapshot) {
  return {
    name: snapshot.name,
    enabled: !!snapshot.enabled,
    config: snapshot.config || snapshot.spec || {},
  };
}
function flatten(value, path = '', result = new Map()) {
  if (Array.isArray(value)) {
    if (value.every((v) => v && typeof v === 'object' && !Array.isArray(v) && v.key))
      for (const item of value)
        flatten(item, path + ' › ' + (item.label || item.name || item.key), result);
    else result.set(path, value);
  } else if (value && typeof value === 'object') {
    const entries = Object.entries(value);
    if (!entries.length) result.set(path, {});
    for (const [key, item] of entries)
      flatten(item, path ? path + ' › ' + (labels[key] || key) : labels[key] || key, result);
  } else result.set(path, value);
  return result;
}
function valueHtml(value, path) {
  if (value === undefined) return '<span class="muted">未设置</span>';
  if (value === null || value === '') return '<span class="muted">空值</span>';
  if (typeof value === 'boolean')
    return value
      ? path === '使用状态'
        ? '用于新工作'
        : '是'
      : path === '使用状态'
        ? '停用，历史保留'
        : '否';
  if (Array.isArray(value))
    return value.length
      ? `<ul class="history-values">${value.map((v) => `<li>${valueHtml(v, path)}</li>`).join('')}</ul>`
      : '<span class="muted">空列表</span>';
  if (typeof value === 'object') {
    const pairs = Object.entries(value);
    return pairs.length
      ? pairs
          .map(
            ([k, v]) =>
              `<div><span class="muted">${h(labels[k] || k)}：</span>${valueHtml(v, k)}</div>`,
          )
          .join('')
      : '<span class="muted">无配置项</span>';
  }
  return h(typeNames[value] || value).replace(/\n/g, '<br>');
}
export function configurationDiff(before, after) {
  const old = flatten(semantic(before)),
    now = flatten(semantic(after));
  return [...new Set([...old.keys(), ...now.keys()])].map((path) => ({
    path,
    before: old.get(path),
    after: now.get(path),
    changed: JSON.stringify(old.get(path)) !== JSON.stringify(now.get(path)),
  }));
}
function diffTable(rows) {
  return table(
    ['字段', '所选历史内容', '当前内容', '变化'],
    rows.map((row) => [
      h(row.path),
      valueHtml(row.before, row.path),
      valueHtml(row.after, row.path),
      badge(
        row.before === undefined ? 'active' : row.after === undefined ? 'missing' : 'registered',
        row.before === undefined ? '当前新增' : row.after === undefined ? '当前已移除' : '内容变化',
      ),
    ]),
    '内容一致',
  );
}

export async function openConfigurationHistory(ctx, kind, item) {
  const fresh = await api('/api/config');
  let current = arr(fresh[kind]).find((x) => x.id === item.id) || item;
  let page = 1,
    selected = null,
    data = null,
    restored = false;
  const cache = new Map();
  const dialog = modal(
    '配置历史 · ' + current.name,
    '<div class="page-loading"><span class="spinner"></span><p>读取已保存的修订</p></div>',
  );
  dialog.classList.add('history-dialog');
  dialog.addEventListener(
    'close',
    () => {
      dialog.classList.remove('history-dialog');
      if (restored) ctx.reload();
    },
    { once: true },
  );
  async function load() {
    data = await api(`/api/config/${kind}/${item.id}/history?page=${page}&page_size=10`);
    for (const version of arr(data.items)) cache.set(version.revision, version);
    if (!selected)
      selected = arr(data.items).find((v) => v.revision !== current.revision) || arr(data.items)[0];
    render();
  }
  function render() {
    const rows = selected ? configurationDiff(selected.snapshot, current) : [],
      changed = rows.filter((r) => r.changed),
      same = rows.filter((r) => !r.changed);
    $('.modal-content', dialog).innerHTML =
      `<div class="configuration-history"><aside class="history-versions"><p class="muted small">共 ${data.total || 0} 个修订</p>${arr(
        data.items,
      )
        .map(
          (version) =>
            `<button class="history-version ${selected?.revision === version.revision ? 'selected' : ''}" type="button" data-history-revision="${version.revision}"><strong>v${version.revision}${version.revision === current.revision ? ' · 当前' : ''}</strong><span>${date(version.created_at)}</span></button>`,
        )
        .join(
          '',
        )}<div class="row" style="margin-top:14px">${button('←', 'history-prev', 'secondary small', page > 1 ? '' : 'disabled')}${button('→', 'history-next', 'secondary small', page * 10 < Number(data.total) ? '' : 'disabled')}</div></aside><section class="history-comparison"><div class="row-between"><h3>${selected ? 'v' + selected.revision : '历史'} → 当前 v${current.revision}</h3><span class="pill">${changed.length} 个字段有变化</span></div><p class="muted small" style="margin:10px 0 18px">左侧选择历史修订，与当前有效内容逐项比较。已启动任务仍保留各自冻结的配置。</p>${changed.length ? diffTable(changed) : notice('所选内容与当前配置一致。')}${
        same.length
          ? `<details class="history-unchanged"><summary class="text-link">查看 ${same.length} 个相同字段</summary>${table(
              ['字段', '保存内容'],
              same.map((r) => [h(r.path), valueHtml(r.after, r.path)]),
            )}</details>`
          : ''
      }<div class="form-errors"></div><div class="form-actions"><p class="muted small">采用历史内容会创建新的修订，原历史记录保持原样。</p>${button(selected ? '将 v' + selected.revision + ' 内容保存为新修订' : '采用历史内容', 'restore-configuration', 'primary', selected && changed.length ? '' : 'disabled')}</div></section></div>`;
    $$('[data-history-revision]', dialog).forEach(
      (el) =>
        (el.onclick = () => {
          selected = cache.get(Number(el.dataset.historyRevision));
          render();
        }),
    );
    bindAction(
      'history-prev',
      async () => {
        page--;
        await load();
      },
      dialog,
    );
    bindAction(
      'history-next',
      async () => {
        page++;
        await load();
      },
      dialog,
    );
    bindAction(
      'restore-configuration',
      async () => {
        const snapshot = selected.snapshot;
        try {
          const result = await api(`/api/config/${kind}/${item.id}`, {
            method: 'PUT',
            body: {
              revision: current.revision,
              name: snapshot.name,
              enabled: !!snapshot.enabled,
              config: snapshot.config || snapshot.spec || {},
            },
          });
          current = result;
          restored = true;
          ctx.invalidateConfig();
          ctx.saved(null, `历史内容已保存为 v${result.revision}，旧修订仍保留`);
          page = 1;
          await load();
        } catch (error) {
          $('.form-errors', dialog).innerHTML = errorBox(error);
        }
      },
      dialog,
    );
  }
  try {
    await load();
  } catch (error) {
    $('.modal-content', dialog).innerHTML = errorBox(error);
  }
}
