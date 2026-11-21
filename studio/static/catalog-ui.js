import { h, arr, field, select, table, link, asset } from './ui.js';

const builtIn = new Set(['material', 'color', 'size', 'weight_g', 'price', 'stock']);
const sourceLabels = {
  cost_price: '供货成本',
  cost_currency: '供货币种',
  currency: '来源销售币种',
  brand: '品牌',
  supplier_category: '供应商品类',
  material_composition: '详细材料组成',
  fabric: '面料成分',
  filling: '填充物成分',
  length_cm: '商品长度 / cm',
  width_cm: '商品宽度 / cm',
  height_cm: '商品高度 / cm',
  natural_height_cm: '自然高度 / cm',
  compressed_height_cm: '受压高度 / cm',
  filling_weight_g: '填充重量 / g',
  quantity: '销售件数',
  contents: '套装组成',
  unit: '计价单位',
  origin_country: '原产国',
  origin_place: '详细产地',
  package_size: '包装尺寸',
  package_weight_g: '包装重量 / g',
  barcode: 'GTIN / UPC / EAN',
  source_asin: '来源 ASIN',
  manufacturer: '制造商',
  features: '产品特点与用途',
  source_notes: '源表补充说明',
  weight: '原始重量说明',
  image: '原始图片信息',
};
export function categorySchema(config, product, facts = {}) {
  const category = arr(config.categories).find((x) => Number(x.id) === Number(product.category_id));
  const defined = arr((category?.spec || category?.config || {}).attributes).filter(
    (x) => x.key && !builtIn.has(x.key),
  );
  const keys = new Set(defined.map((x) => x.key));
  for (const [key, value] of Object.entries(facts))
    if (!builtIn.has(key) && !keys.has(key) && !/(?:_status|_provenance|_revision)$/.test(key)) {
      defined.push({
        key,
        label: sourceLabels[key] || key,
        type: typeof value === 'number' ? 'number' : 'text',
      });
    }
  return defined;
}
export function dynamicFactFields(schema, facts, prefix = 'fact_') {
  return schema
    .map((a) => {
      const label = (a.label || a.key) + (a.unit ? ' / ' + a.unit : '');
      const value = facts[a.key];
      if (a.type === 'boolean')
        return select(
          label,
          prefix + a.key,
          [
            { value: '', label: '未知 / 未提供' },
            { value: 'true', label: '是' },
            { value: 'false', label: '否' },
          ],
          value === true ? 'true' : value === false ? 'false' : '',
        );
      if (a.type === 'choice') {
        const choices = arr(a.options || a.choices).map((x) =>
          typeof x === 'object'
            ? { value: x.value ?? x.key ?? x.label, label: x.label ?? x.value ?? x.key }
            : { value: x, label: x },
        );
        return select(
          label,
          prefix + a.key,
          [{ value: '', label: '未知 / 未提供' }, ...choices],
          value ?? '',
        );
      }
      return field(label, prefix + a.key, value ?? '', {
        type: ['number', 'integer'].includes(a.type) ? 'number' : 'text',
        step: a.type === 'integer' ? 1 : 'any',
        help: '来自当前品类属性定义；没有真实依据时留空。',
      });
    })
    .join('');
}
export function readDynamicFacts(schema, values, prefix = 'fact_') {
  return Object.fromEntries(
    schema.map((a) => {
      const raw = values[prefix + a.key];
      let value =
        raw === '' || raw === undefined
          ? null
          : a.type === 'boolean'
            ? raw === 'true'
            : ['number', 'integer'].includes(a.type)
              ? Number(raw)
              : raw;
      if (value !== null && a.type === 'choice') {
        const match = arr(a.options || a.choices)
          .map((x) => (typeof x === 'object' ? (x.value ?? x.key ?? x.label) : x))
          .find((x) => String(x) === String(raw));
        if (match !== undefined) value = match;
      }
      return [a.key, value];
    }),
  );
}
export function supplierImportReferences(skus) {
  const batches = new Map();
  for (const sku of skus) {
    for (const value of Object.values(sku.provenance || {})) {
      if (!value || typeof value !== 'object' || !value.import_id) continue;
      const id = Number(value.import_id);
      if (!batches.has(id)) batches.set(id, { id, skus: new Map(), positions: new Set() });
      const batch = batches.get(id);
      batch.skus.set(sku.id, sku);
      if (value.sheet || value.row)
        batch.positions.add(`${value.sheet || '原表'} · 行 ${value.row || '未知'}`);
    }
  }
  return [...batches.values()];
}
export function provenanceSummary(sku) {
  const p = sku.provenance || {},
    identity = p.identity || {};
  const sources = Object.entries(p).filter(
    ([key, value]) => value && typeof value === 'object' && value.import_id && key !== 'identity',
  );
  const manual = arr(p.manual_fields);
  const top =
    identity.kind === 'internal_source_identity'
      ? '来源内部身份（无供应商货号）'
      : identity.supplier_code
        ? '供应商货号 ' + identity.supplier_code
        : sources.length
          ? '供应商源表'
          : '人工录入';
  const batches = supplierImportReferences([sku]);
  return `<span class="small subtle">${h(top)}</span>${batches.length ? `<div class="row" style="margin-top:7px">${batches.map((b) => link('/imports/' + b.id, '打开原导入批次 #' + b.id, 'text-link')).join('')}</div>` : ''}${identity.note ? `<p>${h(identity.note)}</p>` : ''}${
    sources.length || manual.length
      ? `<details style="margin-top:7px"><summary class="text-link small">查看字段依据</summary><div style="min-width:280px;margin-top:9px">${table(
          ['字段', '原始位置', '原值'],
          sources.map(([key, value]) => [
            `${h(key)}${manual.includes(key) ? '<p>后续人工编辑</p>' : ''}`,
            `${link('/imports/' + value.import_id, '批次 #' + value.import_id, 'text-link')}<p>${h(value.sheet || '')} · 行 ${h(value.row)} / 列 ${h(value.column || value.header)}</p>`,
            h(value.raw ?? '未知'),
          ]),
          '暂无导入字段',
        )}${manual.length ? `<p class="muted small">人工填写：${h(manual.join('、'))}</p>` : ''}</div></details>`
      : ''
  }`;
}
