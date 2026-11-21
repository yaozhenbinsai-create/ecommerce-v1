import { $, arr, h, field, select, href } from './ui.js';

const standard = new Set([
  'sku',
  'title',
  'description',
  'bullets',
  'price',
  'stock',
  'brand',
  'parent_sku',
  'variation',
  'search_terms',
  'material',
  'color',
  'size',
  'weight_g',
]);
const explicitFields = new Set([
  'product_type',
  'listing_action',
  'parentage',
  'product_id_type',
  'product_id',
  'fulfillment_channel',
  'condition',
]);
const measureFields = {
  item_length: { source: 'length_cm', unit: 'length_unit', sourceUnit: 'cm' },
  item_width: { source: 'width_cm', unit: 'width_unit', sourceUnit: 'cm' },
  item_weight: { source: 'weight_g', unit: 'weight_unit', sourceUnit: 'g' },
  package_weight: { source: 'package_weight_g', unit: 'package_weight_unit', sourceUnit: 'g' },
};
const fieldLabels = {
  product_type: '产品类型',
  listing_action: '平台处理动作',
  parentage: '父子关系',
  product_id_type: '商品识别码类型',
  product_id: '商品识别码',
  fulfillment_channel: '履约渠道',
  condition: '商品状况',
  model_name: '型号名称',
  model_number: '型号编码',
  manufacturer: '制造商',
  quantity: '销售件数',
  item_length: '商品长边',
  length_unit: '长边单位',
  item_width: '商品短边',
  width_unit: '短边单位',
  item_weight: '商品重量',
  weight_unit: '商品重量单位',
  package_weight: '包装重量',
  package_weight_unit: '包装重量单位',
  package_length: '包装长度',
  package_length_unit: '包装长度单位',
  package_width: '包装宽度',
  package_width_unit: '包装宽度单位',
  package_height: '包装高度',
  package_height_unit: '包装高度单位',
  fabric: '面料组成',
  filling: '填充材料',
  origin_country: '原产国',
  handling_days: '备货天数',
  dangerous_goods_regulation: '危险品法规声明',
  currency: '目标币种',
};
const nativeLabels = {
  'Special Features': '产品特点',
  'Item Shape': '商品形状',
  'Fill Material': '填充材料',
  Pattern: '图案',
  'Unit Count': '计量数量',
  'Unit Count Type': '计量单位',
  'Cover Material': '外层材料',
  'List Price': '参考划线价',
  'Shipping Template (US)': '美国配送模板',
  'Product Compliance Certificate': '合规证明',
  'Country of Origin': '原产国',
  'Batteries Required': '是否需要电池',
  'Batteries Included': '是否包含电池',
};
const commonFields = new Set([...Object.keys(fieldLabels)]);
const standardLabels = {
  title: '商品标题',
  description: '商品描述',
  bullets: '商品卖点',
  price: '销售价格',
  stock: '可销售库存',
  brand: '品牌',
  parent_sku: '父体 SKU',
  variation: '变体主题',
  images: '设计图片',
  template: '原生模板',
  dependencies: '上游关联版本',
  material: '材质',
  color: '颜色',
  size: '尺寸',
  weight_g: '重量',
  search_terms: '搜索词',
};
export function normalizeListingIssues(issues, template = null, currentListing = null) {
  const config = template?.config || {};
  const grouped = new Map();
  for (const issue of arr(issues)) {
    let key = String(issue.field || '').replace(/^(?:fields\.)?attributes\./, '');
    const mapped = Object.entries(config.mapping || {}).find(
      ([, column]) => String(column) === key,
    );
    if (mapped) key = mapped[0];
    const column = config.mapping?.[key] || issue.column;
    const native =
      arr(config.columns).find((item) => String(item.column) === String(column))?.label ||
      arr(config.native_rules?.rules).find((item) => String(item.column) === String(column))
        ?.label ||
      '';
    const label =
      standardLabels[key] ||
      fieldLabels[key] ||
      nativeLabels[native] ||
      (key.startsWith('images') || /^image_|^detail_image_/.test(key)
        ? '设计图片'
        : native
          ? '模板字段 · ' + native
          : '需要补齐的资料');
    const listingId = issue.listing_id || currentListing?.id;
    const destination = issue.family_id
      ? '/families/' + issue.family_id
      : listingId
        ? '/listings/' + listingId
        : null;
    const focus = key === 'images.main' || /^image_|^detail_image_/.test(key) ? 'images' : key;
    const target =
      key === 'template' && currentListing
        ? href('/settings/templates', { target_id: currentListing.target_id })
        : destination
          ? href(destination, { focus })
          : issue.href;
    const groupKey = [issue.family_id || '', listingId || '', key].join(':');
    if (!grouped.has(groupKey))
      grouped.set(groupKey, {
        ...issue,
        field: key,
        label,
        nativeLabel: native,
        href: target,
        messages: [],
      });
    const entry = grouped.get(groupKey);
    if (!entry.messages.includes(issue.message))
      entry.messages.push(issue.message || '请补齐对应资料');
  }
  return [...grouped.values()].map((item) => {
    const specific = item.messages.filter(
      (message) => !['缺少模板必填字段', '原生模板定义的必填字段缺失'].includes(message),
    );
    return { ...item, message: (specific.length ? specific : [item.messages[0]]).join('；') };
  });
}
export function labelListingError(error, template) {
  if (Array.isArray(error.fields)) error.fields = normalizeListingIssues(error.fields, template);
  return error;
}
function choicesFor(rules, productType) {
  const groups = arr(rules)
    .filter((r) => r.strict_enum)
    .map((rule) => {
      if (Array.isArray(rule.enums)) return rule.enums;
      const byType = rule.enums || {};
      return arr(byType[productType] || (productType ? [] : Object.values(byType).flat()));
    })
    .filter((values) => values.length);
  if (!groups.length) return [];
  return [...new Set(groups[0].map(String))].filter((value) =>
    groups.every((values) => values.map(String).includes(value)),
  );
}
export function templateAttributes(templates, listing, schema) {
  const extra = new Map(),
    known = new Set([...standard, ...schema.map((s) => s.key)]);
  for (const [key, value] of Object.entries(listing.fields?.attributes || {}))
    if (
      !known.has(key) &&
      templates.some((template) => Object.hasOwn(template.config?.mapping || {}, key))
    )
      extra.set(key, {
        key,
        label: key,
        value: key === 'currency' ? listing.target?.currency || '' : value,
        required: false,
        templates: [],
        enumRules: [],
      });
  for (const template of templates) {
    const config = template.config || {};
    for (const [key, column] of Object.entries(config.mapping || {})) {
      if (
        known.has(key) ||
        /^image(?:_url|_\d+)$/.test(key) ||
        /^detail_image_\d+$/.test(key) ||
        /^bullet_\d+$/.test(key)
      )
        continue;
      const label = arr(config.columns).find((c) => c.column === column)?.label || key,
        previous = extra.get(key);
      const value =
        key === 'currency'
          ? listing.target?.currency || ''
          : (listing.fields?.attributes?.[key] ??
            listing.fields?.[key] ??
            (explicitFields.has(key) ? '' : listing.sku?.facts?.[key]) ??
            '');
      const enumRules = [
        ...arr(previous?.enumRules),
        ...arr(config.native_rules?.rules).filter(
          (rule) => String(rule.column) === String(column) && rule.strict_enum,
        ),
      ];
      extra.set(key, {
        key,
        label: fieldLabels[key] || nativeLabels[label] || label,
        nativeLabel: label,
        activeIssue: arr(listing.diagnostics).find(
          (issue) =>
            issue.field === key && (!issue.template_id || issue.template_id === template.id),
        ),
        value,
        required:
          previous?.required ||
          arr(config.required).includes(key) ||
          arr(config.official_required).includes(key),
        templates: [...arr(previous?.templates), template.name],
        enumRules,
        measurement: measureFields[key]
          ? {
              ...measureFields[key],
              sourceValue: listing.sku?.facts?.[measureFields[key].source],
            }
          : null,
      });
    }
  }
  const productType =
    extra.get('product_type')?.value || listing.fields?.attributes?.product_type || '';
  return [...extra.values()].map((item) => ({
    ...item,
    allowedValues: choicesFor(item.enumRules, productType),
  }));
}
function itemHtml(item, value = item.value) {
  const label =
      item.label + (item.activeIssue ? ' · 当前待补' : item.required ? ' · 模板必填' : ''),
    name = 'platform_' + item.key;
  const choices = arr(item.allowedValues),
    invalid = value !== '' && choices.length && !choices.includes(String(value));
  const control = choices.length
    ? select(
        label,
        name,
        [
          { value: '', label: '请选择模板允许值' },
          ...choices.map((value) => ({ value, label: value })),
        ],
        value,
      )
    : field(label, name, typeof value === 'object' ? JSON.stringify(value) : value, {
        readonly: item.key === 'currency',
      });
  const measurementHelp = item.measurement
    ? `<small class="template-field-help" data-measurement-help="${h(item.key)}">${measurementText(item, '')}</small>`
    : '';
  return (
    control +
    (item.nativeLabel && item.nativeLabel !== item.label
      ? `<small class="template-native-label">${h(item.nativeLabel)}</small>`
      : '') +
    `<small class="template-field-help">${item.key === 'currency' ? '由目标店铺币种确定' : choices.length ? '原生模板允许值 · ' + h([...new Set(item.templates)].join('、')) : item.templates.length ? '依据：' + h([...new Set(item.templates)].join('、')) : '保留的店铺独立属性'}</small>${measurementHelp}${invalid ? '<small class="text-error">当前保存值不在此产品类型的允许值中，请重新选择。</small>' : ''}`
  );
}
function measurementText(item, selectedUnit) {
  const measure = item.measurement;
  if (typeof measure.sourceValue !== 'number' || !Number.isFinite(measure.sourceValue)) {
    return '源资料没有可直接换算的精确数值。请补足实际依据，再填写平台数值和允许单位。';
  }
  return h(
    `真实源值 ${measure.sourceValue} ${measure.sourceUnit}。${selectedUnit ? `已选平台单位 ${selectedUnit}；此数值留空时由服务端换算，原始事实保持不变。` : '先选择模板允许的单位；此数值可留空，由服务端按实际源值换算。'}`,
  );
}
export function renderTemplateAttributes(items) {
  const filled = (item) => item.value !== '' && item.value !== null && item.value !== undefined;
  const priority = items.filter(
    (item) => item.required || item.activeIssue || filled(item) || commonFields.has(item.key),
  );
  const optional = items.filter((item) => !priority.includes(item));
  priority.sort(
    (a, b) =>
      Number(!!b.activeIssue) - Number(!!a.activeIssue) ||
      Number(!!b.required) - Number(!!a.required) ||
      Number(filled(b)) - Number(filled(a)),
  );
  const render = (item) =>
    `<div data-template-field="${h(item.key)}" data-field="${h(item.key)}">${itemHtml(item)}</div>`;
  return `<div class="span-all"><p class="muted small template-group-note">优先显示当前缺项、必填、已填写和常用字段。保存或重新校验后更新条件要求。</p><div class="form-grid" id="template-priority-fields">${priority.map(render).join('')}</div>${optional.length ? `<details class="template-other-fields"><summary>其他模板字段 · ${optional.length} 项</summary><p class="muted small">保留所有模板字段。仅在实际适用时填写；空白不代表已确认“不适用”。</p><div class="form-grid">${optional.map(render).join('')}</div></details>` : ''}</div>`;
}
export function exposeTemplateIssues(form, issues) {
  const priority = $('#template-priority-fields', form);
  if (!priority) return;
  for (const issue of arr(issues)) {
    const node = $(`[data-template-field="${CSS.escape(issue.field || '')}"]`, form);
    if (node && node.closest('details')) priority.append(node);
  }
}
export function attachTemplateEnums(form, items) {
  const refreshMeasurements = () => {
    for (const item of items) {
      if (!item.measurement) continue;
      const node = $(`[data-measurement-help="${CSS.escape(item.key)}"]`, form);
      if (node)
        node.innerHTML = measurementText(
          item,
          $(`[name="platform_${CSS.escape(item.measurement.unit)}"]`, form)?.value || '',
        );
    }
  };
  const refresh = () => {
    const productType = $('[name=platform_product_type]', form)?.value || '';
    for (const item of items) {
      if (!arr(item.enumRules).length) continue;
      const node = $(`[data-template-field="${CSS.escape(item.key)}"]`, form);
      if (!node) continue;
      const value = $('[name]', node)?.value ?? item.value;
      item.allowedValues = choicesFor(item.enumRules, productType);
      node.innerHTML = itemHtml(item, value);
    }
    refreshMeasurements();
  };
  form.addEventListener('change', (event) => {
    if (event.target?.name === 'platform_product_type') refresh();
    else if (event.target?.name?.startsWith('platform_')) refreshMeasurements();
  });
  form.addEventListener('draftrestored', refresh);
  refreshMeasurements();
}
export function readTemplateAttributes(items, values) {
  return Object.fromEntries(
    items.map((item) => [item.key, values['platform_' + item.key] ?? item.value]),
  );
}
