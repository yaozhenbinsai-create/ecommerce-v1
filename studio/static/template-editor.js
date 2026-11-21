import {
  $,
  $$,
  h,
  arr,
  api,
  modal,
  field,
  select,
  button,
  submit,
  notice,
  bindForm,
  bindAction,
  closeModal,
} from './ui.js';

const common = [
  ['sku', '平台 SKU'],
  ['title', '商品标题'],
  ['brand', '品牌'],
  ['description', '商品描述'],
  ['price', '确认售价'],
  ['stock', '可销售库存'],
  ['currency', '目标币种'],
  ['image_url', '主图地址'],
  ['parent_sku', '父 SKU'],
  ['variation', '变体主题'],
  ['product_type', '产品类型'],
  ['listing_action', '本次处理动作'],
  ['parentage', '父子关系'],
  ['product_id_type', '商品识别码类型'],
  ['product_id', '商品识别码'],
  ['fulfillment_channel', '履约渠道'],
  ['condition', '商品状况'],
  ...Array.from({ length: 8 }, (_, i) => ['image_' + (i + 1), '图片地址 ' + (i + 1)]),
  ...Array.from({ length: 5 }, (_, i) => ['bullet_' + (i + 1), '要点 ' + (i + 1)]),
];
export async function templateEditor(ctx, id) {
  const item = await api(`/api/templates/${id}`),
    c = item.config || {},
    columns = arr(c.columns),
    knownColumns = new Set(columns.map((x) => x.column));
  const keys = [
    ...new Set([...common.map((x) => x[0]), ...Object.keys(c.mapping || {}), ...arr(c.required)]),
  ];
  const nativeRules = arr(c.native_rules?.rules);
  const ruleSummary = nativeRules.length
    ? `${nativeRules.length} 项字段规则 · ${nativeRules.filter((rule) => rule.strict_enum).length} 项严格枚举 · 产品类型 ${arr(c.native_rules?.product_types).join('、') || '原生模板定义'}`
    : '尚未读取可执行的原生规则；字段映射与平台规则分别保留。';
  const columnInput = (label, name, value = '') =>
    `<label class="field"><span>${h(label)}</span><input name="${h(name)}" value="${h(value)}" list="template-native-columns" placeholder="输入列字母或选择，如 B" autocomplete="off"><small data-column-caption>${h(columns.find((x) => x.column === value)?.label || '留空不写入此字段')}</small></label>`;
  const extraRow = () =>
    `<div class="extra-template-row"><div class="form-grid">${field('业务字段标识', 'extra_key', '', { placeholder: '如 capacity_ml' })}${columnInput('对应原生列', 'extra_column')}</div><div class="row-between"><label class="check"><input type="checkbox" name="extra_required"><span>该组合必填</span></label><button type="button" class="text-link" data-remove-template-extra>移除此映射</button></div></div>`;
  modal(
    '模板字段与数据区',
    `<form id="template-form"><p class="muted small">${h(item.name)} · 修订 v${item.revision} · ${h(item.capability)}</p><section class="template-rule-summary"><div><h3>原生规则</h3><p>${h(ruleSummary)}</p><p>重新读取原文件中的字段和枚举，保存为新的模板修订；旧导出批次继续引用原快照。</p></div>${button('重新读取原生规则', 'refresh-template-rules', 'secondary small')}</section><datalist id="template-native-columns">${columns.map((x) => `<option value="${h(x.column)}">${h(x.column + ' · ' + x.label)}</option>`).join('')}</datalist><div class="form-grid" style="margin-top:16px">${select('数据工作表', 'sheet', arr(c.sheets).length ? c.sheets : [c.sheet], c.sheet)}${field('表头行号', 'header_row', c.header_row, { type: 'number', min: 1 })}${field('首个数据行号', 'data_row', c.data_row, { type: 'number', min: 2 })}</div><div class="section-heading"><h2>业务字段对应原生列</h2></div><div class="mapping-grid">${keys.map((key) => `<div>${columnInput(common.find((x) => x[0] === key)?.[1] || key, 'map_' + key, c.mapping?.[key] || '')}<label class="check"><input type="checkbox" name="required_${h(key)}" ${arr(c.required).includes(key) ? 'checked' : ''}><span>该组合必填</span></label></div>`).join('')}</div><section class="form-section"><div class="row-between"><h3>额外原生字段</h3>${button('添加字段映射', 'add-template-extra', 'secondary small')}</div><p class="muted small">字段保存到店铺刊登的独立属性。输入稳定业务标识，选择真实原生列；已存在的额外字段显示在上方。</p><div id="extra-template-mappings"></div></section>${notice('平台枚举、条件必填和变体约束由原生模板规则校验。登记字段映射本身不代表平台已验证。')}<div class="form-actions">${submit('保存模板映射')}</div></form>`,
    (root) => {
      const form = $('#template-form', root);
      const initialValues = JSON.stringify([...new FormData(form)]);
      bindAction(
        'refresh-template-rules',
        async () => {
          if (JSON.stringify([...new FormData(form)]) !== initialValues) {
            throw new Error('请先保存当前映射，再重新读取规则，避免未保存内容被覆盖。');
          }
          const result = await api(`/api/templates/${id}/refresh-rules`, {
            method: 'POST',
            body: { revision: item.revision },
          });
          closeModal();
          ctx.saved(null, `原生规则已读取为模板 v${result.revision}，旧导出批次保持原快照`);
          await ctx.reload();
          await templateEditor(ctx, id);
        },
        root,
      );
      const bindExtras = () =>
        $$('[data-remove-template-extra]', form).forEach(
          (el) => (el.onclick = () => el.closest('.extra-template-row').remove()),
        );
      bindAction(
        'add-template-extra',
        () => {
          $('#extra-template-mappings', form).insertAdjacentHTML('beforeend', extraRow());
          bindExtras();
        },
        root,
      );
      form.addEventListener('input', (event) => {
        const name = event.target?.name || '';
        if (name.startsWith('map_') || name === 'extra_column') {
          const caption = $('[data-column-caption]', event.target.closest('.field'));
          if (caption)
            caption.textContent =
              columns.find((x) => x.column === event.target.value.trim().toUpperCase())?.label ||
              (event.target.value ? '原模板中没有此列，请核对' : '留空不写入此字段');
        }
      });
      bindForm(
        '#template-form',
        async (form, v) => {
          const mapping = Object.fromEntries(
              keys
                .filter((key) => v['map_' + key]?.trim())
                .map((key) => [key, v['map_' + key].trim().toUpperCase()]),
            ),
            required = keys.filter((key) => v['required_' + key]);
          for (const row of $$('.extra-template-row', form)) {
            const key = $('[name=extra_key]', row).value.trim(),
              column = $('[name=extra_column]', row).value.trim().toUpperCase();
            if (!key && !column) continue;
            if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(key))
              throw new Error('额外字段标识请使用英文字母开头的字母、数字和下划线。');
            if (keys.includes(key) || Object.hasOwn(mapping, key))
              throw new Error(`字段 ${key} 已有映射，请在原位置修改。`);
            if (!column) throw new Error(`请为 ${key} 选择真实原生列。`);
            mapping[key] = column;
            if ($('[name=extra_required]', row).checked) required.push(key);
          }
          for (const [key, column] of Object.entries(mapping))
            if (!knownColumns.has(column))
              throw new Error(`${key} 对应的 ${column} 不在原模板列中。`);
          await api(`/api/templates/${id}`, {
            method: 'PUT',
            body: {
              revision: item.revision,
              config: {
                ...c,
                sheet: v.sheet,
                header_row: Number(v.header_row),
                data_row: Number(v.data_row),
                mapping,
                required: [...new Set(required)],
              },
            },
          });
          closeModal();
          ctx.saved(null, '模板映射已保存');
          ctx.reload();
        },
        root,
      );
    },
  );
}
