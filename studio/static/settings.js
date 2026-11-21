import { templateEditor } from './template-editor.js';
import { openConfigurationHistory } from './configuration-history.js';
import { backupSettings } from './backups.js';
import {
  $,
  $$,
  h,
  arr,
  api,
  heading,
  panel,
  field,
  select,
  table,
  button,
  submit,
  bindForm,
  bindAction,
  modal,
  closeModal,
  notice,
  badge,
  link,
  asset,
  date,
  int,
  metadata,
} from './ui.js';

const sections = [
  ['categories', '品类与属性'],
  ['suppliers', '供应商'],
  ['sources', '信息来源'],
  ['design_presets', '设计方案'],
  ['production_presets', '生产规格'],
  ['targets', '店铺与市场'],
  ['templates', '平台模板'],
  ['models', '模型与任务分工'],
  ['storage', '存储与显示'],
  ['backups', '备份与校验'],
];
const opts = (rows, empty = '请选择') => [
  { value: '', label: empty },
  ...arr(rows).map((r) => ({ value: r.id, label: r.name })),
];
const bytes = (n) => `${(Number(n || 0) / 1024 / 1024).toFixed(2)} MB`;
const fieldsFor = {
  categories: () => '',
  suppliers: (c) =>
    field('联系人', 'contact', c.contact || '') +
    field('资料说明', 'notes', c.notes || '', { textarea: true }),
  sources: (c) =>
    select(
      '平台',
      'platform',
      [
        'Amazon',
        'Temu',
        'Alibaba.com',
        'AliExpress',
        'Pinterest',
        'Instagram',
        'TikTok',
        'YouTube',
        'Reddit',
        '开放热点',
      ],
      c.platform || 'Amazon',
    ) +
    select(
      '使用范围',
      'kind',
      [
        { value: 'market', label: '定向产品研究' },
        { value: 'social', label: '社交内容' },
        { value: 'hotspot', label: '独立热点' },
      ],
      c.kind || 'market',
    ) +
    field('默认关键词', 'query', c.query || '') +
    field('地区 / 语言提示', 'locale', c.locale || '', {
      help: '追加到查询中作为提示，不是强制的地域过滤。',
    }),
  design_presets: (c) =>
    select(
      '设计入口',
      'mode',
      [
        { value: 'supplier', label: '供应商展示' },
        { value: 'large', label: '原创大印花' },
        { value: 'repeat', label: '原创小印花' },
      ],
      c.mode || 'large',
    ) +
    field('创意路线数', 'route_count', c.route_count ?? 3, { type: 'number', min: 1, max: 5 }) +
    field('销售图数量', 'sales_count', c.sales_count ?? 4, { type: 'number', min: 1, max: 12 }) +
    field('完整详情图数量', 'detail_count', c.detail_count ?? 1, {
      type: 'number',
      min: 0,
      max: 3,
    }) +
    field('风格与场景', 'style', c.style || '', { textarea: true }) +
    select(
      '循环方式',
      'repeat_mode',
      [
        { value: 'basic', label: '基础平铺' },
        { value: 'half-drop', label: '半落位' },
      ],
      c.repeat?.mode || 'basic',
    ) +
    field('单元宽 / cm', 'repeat_width', c.repeat?.width_cm ?? 20, {
      type: 'number',
      min: 0.1,
      step: 'any',
    }) +
    field('单元高 / cm', 'repeat_height', c.repeat?.height_cm ?? 20, {
      type: 'number',
      min: 0.1,
      step: 'any',
    }),
  production_presets: (c) =>
    field('供应商工艺名称', 'process', c.process || '') +
    field('组件宽 / cm', 'width_cm', c.width_cm ?? '', { type: 'number', min: 0.1, step: 'any' }) +
    field('组件高 / cm', 'height_cm', c.height_cm ?? '', {
      type: 'number',
      min: 0.1,
      step: 'any',
    }) +
    field('DPI', 'dpi', c.dpi ?? 300, { type: 'number', min: 72, max: 1200 }) +
    field('出血 / mm', 'bleed_mm', c.bleed_mm ?? 0, {
      type: 'number',
      min: 0,
      max: 50,
      step: 'any',
    }) +
    select('实际支持格式', 'format', ['PNG', 'TIFF'], c.format || 'PNG') +
    field('工艺来源依据', 'evidence', c.evidence || '', {
      textarea: true,
      help: '记录供应商规格或文件位置；默认值不能代替真实工艺要求。',
    }),
};

function attributeRow(a = {}) {
  return `<div class="component-row attribute-row">${field('属性标识', 'attr_key', a.key || '', { placeholder: 'material' })}${field('显示名称', 'attr_label', a.label || '', { placeholder: '材质' })}${select(
    '数据类型',
    'attr_type',
    [
      { value: 'text', label: '文本' },
      { value: 'number', label: '小数' },
      { value: 'integer', label: '整数' },
      { value: 'choice', label: '选项' },
      { value: 'boolean', label: '是 / 否' },
    ],
    a.type || 'text',
  )}${field('单位', 'attr_unit', a.unit || '')}${field(
    '可选值（逗号分隔）',
    'attr_choices',
    arr(a.options || a.choices)
      .map((v) => (typeof v === 'object' ? (v.value ?? v.label) : v))
      .join(','),
  )}<button type="button" class="icon-button remove-attribute" aria-label="删除属性">×</button></div>`;
}
function directoryForm(ctx, group, item) {
  const c = item?.config || item?.spec || {};
  let extra = fieldsFor[group]?.(c) || '';
  if (group === 'categories')
    extra =
      field(
        '实际组件名称（逗号分隔）',
        'components',
        arr(c.components)
          .map((x) => (typeof x === 'string' ? x : x.name))
          .join('，'),
        { help: '这里只定义可用组件，商品的实际数量和尺寸在 SKU 档案维护。', cls: 'span-all' },
      ) +
      `<section class="span-all form-section"><div class="row-between"><h3>适用属性</h3>${button('添加属性', 'add-attribute', 'quiet small')}</div><div id="attribute-rows">${arr(c.attributes).map(attributeRow).join('')}</div></section>`;
  modal(
    item ? '编辑' + item.name : '新增' + sections.find((x) => x[0] === group)?.[1],
    `<form id="directory-form" data-track data-revision="${item?.revision || 0}"><div class="form-grid">${field('名称', 'name', item?.name || '', { required: true })}${select(
      '使用状态',
      'enabled',
      [
        { value: '1', label: '用于新工作' },
        { value: '0', label: '停用，保留历史引用' },
      ],
      item?.enabled === false || item?.enabled === 0 || item?.enabled === false ? '0' : '1',
    )}${extra}</div><div class="form-actions">${submit('保存配置')}</div></form>`,
    (root) => {
      const removers = () =>
        $$('.remove-attribute', root).forEach(
          (b) => (b.onclick = () => b.closest('.attribute-row').remove()),
        );
      removers();
      bindAction(
        'add-attribute',
        () => {
          $('#attribute-rows', root).insertAdjacentHTML('beforeend', attributeRow());
          removers();
        },
        root,
      );
      bindForm(
        '#directory-form',
        async (form, v) => {
          let config = { ...c };
          for (const [key, value] of Object.entries(v))
            if (!['name', 'enabled'].includes(key) && !key.startsWith('attr_')) config[key] = value;
          if (group === 'categories') {
            config.components = v.components
              .split(/[,，]/)
              .map((s) => s.trim())
              .filter(Boolean);
            config.attributes = $$('.attribute-row', form)
              .map((row) => ({
                key: $('[name=attr_key]', row).value.trim(),
                label: $('[name=attr_label]', row).value.trim(),
                type: $('[name=attr_type]', row).value,
                unit: $('[name=attr_unit]', row).value.trim(),
                options: $('[name=attr_choices]', row)
                  .value.split(/[,，]/)
                  .map((x) => x.trim())
                  .filter(Boolean)
                  .map((value) => {
                    const old = arr(c.attributes).find(
                      (a) => a.key === $('[name=attr_key]', row).value.trim(),
                    );
                    return (
                      arr(old?.options || old?.choices).find(
                        (option) =>
                          String(
                            typeof option === 'object' ? (option.value ?? option.label) : option,
                          ) === value,
                      ) ?? value
                    );
                  }),
              }))
              .filter((a) => a.key && a.label);
          }
          if (group === 'design_presets') {
            for (const k of ['route_count', 'sales_count', 'detail_count'])
              config[k] = Number(v[k]);
            config.repeat = {
              mode: v.repeat_mode,
              width_cm: Number(v.repeat_width),
              height_cm: Number(v.repeat_height),
            };
            delete config.repeat_mode;
            delete config.repeat_width;
            delete config.repeat_height;
          }
          if (group === 'production_presets')
            for (const k of ['width_cm', 'height_cm', 'dpi', 'bleed_mm']) config[k] = int(v[k]);
          await api(`/api/config/${group}${item ? '/' + item.id : ''}`, {
            method: item ? 'PUT' : 'POST',
            body: {
              name: v.name,
              enabled: v.enabled === '1',
              config,
              ...(item ? { revision: item.revision } : {}),
            },
          });
          ctx.invalidateConfig();
          closeModal();
          ctx.saved(null, '配置已保存，新工作可使用');
          ctx.reload();
        },
        root,
      );
    },
  );
}

function targetForm(ctx, item) {
  modal(
    item ? '编辑店铺' : '添加目标店铺',
    `<form id="target-form"><div class="form-grid">${field('店铺名称', 'name', item?.name || '', { required: true })}${select('平台', 'platform', ['Amazon', 'Temu', 'Alibaba.com', 'AliExpress'], item?.platform || 'Amazon')}${field('站点 / 市场', 'site', item?.site || '', { required: true, placeholder: '如 US' })}${field('经营模式', 'mode', item?.mode || '', { required: true, placeholder: '按实际卖家账号填写' })}${field('平台类目', 'category', item?.category || '', { required: true })}${field('语言', 'language', item?.language || 'en', { required: true })}${field('币种', 'currency', item?.currency || 'USD', { required: true })}${select(
      '状态',
      'enabled',
      [
        { value: '1', label: '启用' },
        { value: '0', label: '停用' },
      ],
      item?.enabled === 0 ? '0' : '1',
    )}</div>${notice('同一平台的不同店铺、站点和类目有独立模板与刊登资料。')}<div class="form-actions">${submit('保存店铺')}</div></form>`,
    (root) =>
      bindForm(
        '#target-form',
        async (form, v) => {
          await api('/api/targets' + (item ? '/' + item.id : ''), {
            method: item ? 'PUT' : 'POST',
            body: {
              ...v,
              currency: v.currency.toUpperCase(),
              enabled: v.enabled === '1',
              ...(item ? { revision: item.revision } : {}),
            },
          });
          ctx.invalidateConfig();
          closeModal();
          ctx.saved(null, '目标店铺已保存');
          ctx.reload();
        },
        root,
      ),
  );
}

export async function settingsPage(ctx, group) {
  if (!sections.some((x) => x[0] === group)) group = 'categories';
  let body = '',
    bind = () => {};
  const label = sections.find((x) => x[0] === group)[1];
  const subnav = `<nav class="settings-nav">${sections.map(([key, text]) => link('/settings/' + key, text, group === key ? 'active' : '')).join('')}</nav>`;
  if (fieldsFor[group]) {
    const items = arr(ctx.config[group]);
    body =
      panel(
        label,
        table(
          ['名称', '配置摘要', '当前修订', '使用状态', '操作'],
          items.map((item) => {
            const c = item.config || item.spec || {};
            const summary =
              group === 'categories'
                ? `${arr(c.components).length} 个组件 · ${arr(c.attributes).length} 个属性`
                : group === 'design_presets'
                  ? `${c.sales_count ?? '—'} 张销售图 · ${c.detail_count ?? '—'} 张详情图`
                  : group === 'production_presets'
                    ? `${c.width_cm || '—'} × ${c.height_cm || '—'} cm · ${c.dpi || '—'} DPI`
                    : c.platform || c.contact || c.notes || '尚未补充';
            return [
              `<strong>${h(item.name)}</strong>`,
              h(summary),
              `v${item.revision}`,
              group === 'sources' && item.enabled && item.capability?.supported === false
                ? badge('missing', '登记完成 · 待适配') +
                  `<p class="small muted">${h(item.capability.reason)}</p>`
                : badge(item.enabled ? 'active' : 'disabled'),
              `<div class="row">${button('编辑', 'edit-directory', 'quiet small', `data-id="${item.id}"`)}${button('变更与对照', 'directory-history', 'quiet small', `data-id="${item.id}"`)}</div>`,
            ];
          }),
          '尚无此类配置',
        ),
        button('新增' + label, 'new-directory', 'primary small'),
      ) +
      notice(
        group === 'categories'
          ? '新增类型会用于目标、商品与设计。属性默认值不代替真实商品事实。'
          : '保存配置用于新工作；已有任务保留冻结输入。名称登记和实际适配能力分别记录。',
      );
    bind = () => {
      bindAction('directory-history', (el) =>
        openConfigurationHistory(
          ctx,
          group,
          items.find((x) => String(x.id) === el.dataset.id),
        ),
      );
      bindAction('new-directory', () => directoryForm(ctx, group));
      bindAction('edit-directory', (el) =>
        directoryForm(
          ctx,
          group,
          items.find((x) => String(x.id) === el.dataset.id),
        ),
      );
    };
  } else if (group === 'targets') {
    const data = await api('/api/targets'),
      items = arr(data.items || data);
    body = panel(
      '经营目标',
      table(
        ['店铺与平台', '站点 / 模式', '类目', '语言 / 币种', '操作'],
        items.map((t) => [
          `<strong>${h(t.name)}</strong><p>${h(t.platform)}</p>`,
          `${h(t.site)}<p>${h(t.mode)}</p>`,
          h(t.category),
          `${h(t.language)} · ${h(t.currency)}`,
          button('编辑', 'edit-target', 'quiet small', `data-id="${t.id}"`) +
            link('/settings/templates', '配置模板 →', 'text-link', { target_id: t.id }),
        ]),
        '尚未配置店铺',
      ),
      button('添加店铺', 'new-target', 'primary small'),
    );
    bind = () => {
      bindAction('new-target', () => targetForm(ctx));
      bindAction('edit-target', (el) =>
        targetForm(
          ctx,
          items.find((x) => String(x.id) === el.dataset.id),
        ),
      );
    };
  } else if (group === 'templates') {
    const data = await api('/api/templates?' + ctx.query.toString());
    body =
      panel(
        '添加原生模板',
        `<form id="upload-template" class="panel-body"><div class="form-grid">${select('目标店铺组合', 'target_id', opts(ctx.config.targets), ctx.query.get('target_id') || '')}${field('平台原始模板', 'file', '', { type: 'file', required: true, accept: '.xlsx,.xlsm' })}</div><div class="form-actions">${submit('读取模板结构')}</div></form>`,
      ) +
      panel(
        '模板与支持范围',
        table(
          ['原生文件', '目标店铺', '兼容状态', '修订', '操作'],
          arr(data.items).map((t) => [
            `<strong>${h(t.name)}</strong><p>${date(t.created_at)}</p>`,
            h(ctx.config.targets.find((x) => x.id === t.target_id)?.name || t.target_id),
            badge(t.capability),
            `v${t.revision}`,
            button('字段映射', 'edit-template', 'quiet small', `data-id="${t.id}"`) +
              `<a class="text-link" href="${asset(t.asset_id)}" download>原模板</a>`,
          ]),
          '尚未导入平台模板',
        ),
      );
    bind = () => {
      bindForm('#upload-template', async (form) => {
        const data = new FormData(form);
        if (!data.get('target_id')) throw new Error('先选择目标店铺');
        data.append('config', '{}');
        const result = await api('/api/templates', { method: 'POST', body: data });
        ctx.saved(form, '原模板已保存');
        await templateEditor(ctx, result.id);
      });
      bindAction('edit-template', (el) => templateEditor(ctx, el.dataset.id));
    };
  } else if (group === 'models') {
    const settings = await api('/api/settings'),
      models = arr(ctx.config.models?.items),
      routing = settings.routing;
    const choices = (kind) => [
      { value: '', label: '使用本项目手工设置的方案' },
      ...models
        .filter((m) => m.capability === kind)
        .map((m) => ({
          value: m.id,
          label: `${m.name} · ${m.model_identifier}${m.configured ? '' : '（连接不可用）'}`,
        })),
    ];
    body =
      panel(
        '任务分工',
        `<form id="model-routing" class="panel-body" data-track data-revision="${routing.revision}"><div class="form-grid">${select('创意、策划和文案', 'text_model_id', choices('文本'), routing.value.text_model_id || '')}${select('母稿和图片', 'image_model_id', choices('图片'), routing.value.image_model_id || '')}</div>${notice('新任务冻结实际选择。已发出的请求继续沿用原服务；结果未知不会自动换模型重新生成。')}<div class="form-actions">${submit('保存任务分工')}</div></form>`,
      ) +
      panel(
        '现有模型连接',
        table(
          ['模型', '任务能力', '协议', '连接配置'],
          models.map((m) => [
            `<strong>${h(m.name)}</strong><p>${h(m.model_identifier)}</p>`,
            h(m.capability),
            h(m.protocol),
            badge(m.configured ? 'active' : 'missing', m.configured ? '已取得配置' : '连接待处理'),
          ]),
          '尚未配置本项目的模型连接',
        ),
      ) +
      (ctx.config.models?.error ? notice(h(ctx.config.models.error), 'warning') : '');
    bind = () =>
      bindForm('#model-routing', async (form, v) => {
        const value = {};
        for (const k of ['text_model_id', 'image_model_id']) if (v[k]) value[k] = Number(v[k]);
        await api('/api/settings', {
          method: 'PUT',
          body: { name: 'routing', revision: routing.revision, value },
        });
        ctx.invalidateConfig();
        ctx.saved(form, '模型分工已保存');
        ctx.reload();
      });
  } else if (group === 'backups') {
    const backup = await backupSettings(ctx);
    body = backup.body;
    bind = backup.bind;
  } else if (group === 'storage') {
    const [usage, settings] = await Promise.all([api('/api/storage'), api('/api/settings')]);
    const s = settings.storage,
      d = settings.display;
    body =
      panel(
        '当前占用',
        metadata([
          ['结构化数据', bytes(usage.database?.bytes)],
          ['正式资产记录', `${usage.assets?.count || 0} 项 · ${bytes(usage.assets?.bytes)}`],
          ['资产目录', bytes(usage.asset_files_bytes)],
          ['临时内容', bytes(usage.temporary_bytes)],
          ['运行日志', bytes(usage.logs?.bytes ?? usage.log_bytes)],
          ['恢复检查点', bytes(usage.cache?.bytes ?? usage.checkpoint_bytes)],
        ]),
        link('/settings/backups', '备份与校验 →', 'text-link'),
      ) +
      `<div class="two-column">${panel('保存与清理策略', `<form id="storage-policy" class="panel-body" data-track data-revision="${s.revision}"><div class="form-grid">${field('临时文件保留天数', 'temporary_days', s.value.temporary_days, { type: 'number', min: 1, max: 365 })}${field('未采用来源保留天数', 'source_cache_days', s.value.source_cache_days, { type: 'number', min: 1, max: 365 })}</div><div class="form-actions">${submit('保存策略')}</div></form>`)}${panel(
        '列表显示',
        `<form id="display-policy" class="panel-body" data-track data-revision="${d.revision}">${select(
          '每页条数',
          'page_size',
          [12, 24, 48, 100].map((v) => ({ value: v, label: v + ' 条' })),
          d.value.page_size || 24,
        )}<div class="form-actions">${submit('保存显示偏好')}</div></form>`,
      )}</div>` +
      panel(
        '执行到期清理',
        `<div class="panel-body">${notice(h(usage.cleanup_scope || '仅处理已到期且没有业务引用的临时内容。'))}<p class="muted small">受保护：${arr(usage.protected).map(h).join('、')}</p><div class="form-actions">${button('清理已到期的临时内容', 'cleanup', 'secondary')}</div><div id="cleanup-result"></div></div>`,
      );
    bind = () => {
      bindForm('#storage-policy', async (form, v) => {
        await api('/api/settings', {
          method: 'PUT',
          body: {
            name: 'storage',
            revision: s.revision,
            value: {
              ...s.value,
              temporary_days: Number(v.temporary_days),
              source_cache_days: Number(v.source_cache_days),
            },
          },
        });
        ctx.saved(form, '保存策略已更新');
        ctx.reload();
      });
      bindForm('#display-policy', async (form, v) => {
        await api('/api/settings', {
          method: 'PUT',
          body: {
            name: 'display',
            revision: d.revision,
            value: { page_size: Number(v.page_size) },
          },
        });
        ctx.saved(form, '显示偏好已保存');
        ctx.reload();
      });
      bindAction('cleanup', async () => {
        const result = await api('/api/storage/cleanup', { method: 'POST', body: {} });
        $('#cleanup-result').innerHTML = notice(
          `清理 ${result.removed_files ?? result.removed ?? 0} 个临时文件、${result.expired_unadopted_sources || 0} 条到期未采用来源和 ${result.completed_job_caches || 0} 份已完成任务缓存，释放 ${bytes(result.freed_bytes)}。正式引用继续保留。`,
        );
        ctx.saved(null, '到期清理已完成');
      });
    };
  }
  return {
    title: label,
    html:
      heading(
        'CONFIGURATION / WORKSPACE',
        label,
        '配置服务于实际业务。每一次变更都有明确的适用范围。',
        /^\/(imports|products)\/\d+$/.test(ctx.query.get('return_to') || '')
          ? link(ctx.query.get('return_to'), '返回刚才的工作 →', 'button primary')
          : '',
      ) + `<div class="settings-layout">${subnav}<div class="settings-content">${body}</div></div>`,
    bind,
  };
}
