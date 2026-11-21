import {
  $,
  $$,
  h,
  arr,
  api,
  href,
  link,
  icon,
  heading,
  panel,
  empty,
  notice,
  field,
  select,
  button,
  submit,
  thumb,
  table,
  badge,
  date,
  number,
  pagination,
  metadata,
  bindForm,
  bindAction,
  modal,
  closeModal,
  upload,
  int,
  progress,
  jsonObject,
} from './ui.js';
import {
  categorySchema,
  dynamicFactFields,
  readDynamicFacts,
  provenanceSummary,
  supplierImportReferences,
} from './catalog-ui.js';

const options = (items, first = '请选择') => [
  { value: '', label: first },
  ...arr(items)
    .filter((x) => x.enabled !== false)
    .map((x) => ({ value: x.id, label: x.name })),
];
const categoryName = (config, id) =>
  arr(config.categories).find((x) => Number(x.id) === Number(id))?.name || '未设品类';
const descriptionText = (product) =>
  typeof product.description === 'string'
    ? product.description
    : product.description?.notes || product.description?.description || '';
const workflow = (current = 1) =>
  `<div class="workflow-ribbon">${['真实商品资料', '研究与设计', '刊登准备', '导出与回执'].map((label, i) => `<span class="workflow-step ${current === i + 1 ? 'current' : current > i + 1 ? 'done' : ''}"><span>${current > i + 1 ? '✓' : i + 1}</span>${label}</span>`).join('')}</div>`;

function productModal(ctx, product = null) {
  modal(
    product ? '编辑商品档案' : '建立商品档案',
    `<form id="product-form"><div class="form-errors"></div><div class="form-grid">${field('商品名称', 'name', product?.name || '', { required: true, cls: 'span-all', placeholder: '给这款商品一个清楚的名字' })}${select('产品品类', 'category_id', options(ctx.config.categories), product?.category_id || '')}${select(
      '商品来源',
      'origin',
      [
        { value: 'supplier', label: '供应商真实商品' },
        { value: 'original', label: '原创产品' },
      ],
      product?.origin || 'original',
      product ? 'disabled' : '',
    )}${field('商品说明', 'description', product ? descriptionText(product) : '', { textarea: true, cls: 'span-all', placeholder: '记录产品用途或定位；规格和材质请在 SKU 中维护。' })}</div>${!ctx.config.categories?.length ? notice(`还没有产品品类。${link('/settings/categories', '先配置品类', 'text-link')}`) : ''}<div class="form-actions">${submit(product ? '保存商品' : '创建并完善规格', 'plus')}</div></form>`,
    (root) =>
      bindForm(
        '#product-form',
        async (form, v) => {
          const body = {
            name: v.name,
            category_id: int(v.category_id),
            description: {
              ...(product?.description && typeof product.description === 'object'
                ? product.description
                : {}),
              notes: v.description,
            },
          };
          const result = product
            ? await api(`/api/products/${product.id}`, {
                method: 'PUT',
                body: { ...body, revision: product.revision },
              })
            : await api('/api/products', { method: 'POST', body: { ...body, origin: v.origin } });
          closeModal();
          ctx.saved(null, product ? '商品资料已保存' : '商品已创建，请添加实际 SKU');
          ctx.go(`/products/${result.id || product.id}`);
          if (product) ctx.reload();
        },
        root,
      ),
  );
}
function productRows(items, ctx) {
  return arr(items).map((p) => [
    `<div class="table-product">${thumb(p.original_asset_id || p.cover_asset_id, p.name)}<div>${link(`/products/${p.id}`, h(p.name), '')}<p>${h(p.origin === 'supplier' ? '供应商商品' : '原创产品')} · #${h(p.id)}</p></div></div>`,
    `<span class="subtle">${h(categoryName(ctx.config, p.category_id))}</span>`,
    `${number(p.sku_count)}<span class="muted small"> 个规格</span>`,
    `${number(p.design_count)}<span class="muted small"> 份设计</span>`,
    `${number(p.listing_count)}<span class="muted small"> 份刊登</span>`,
    `<span class="muted small">${date(p.updated_at || p.created_at)}</span>`,
    link(`/products/${p.id}`, '打开档案 →', 'text-link'),
  ]);
}
export async function overviewPage(ctx) {
  const data = await api('/api/dashboard');
  const counts = data.counts || {};
  return {
    title: '工作台',
    html:
      heading(
        'YOUR PRODUCT WORKSPACE',
        '让每一份灵感，走到成品。',
        '从当前工作继续。商品事实、设计成果和刊登资料在这里连成一条路径。',
        link('/products', `${icon('box')}商品档案`) +
          button(`${icon('plus')}建立商品`, 'new-product', 'primary'),
      ) +
      `<div class="summary-strip">${[
        ['商品档案', counts.products, '款', 'box', '管理产品与实际规格'],
        ['设计任务', counts.designs, '份', 'spark', '从任务书到完整成果'],
        ['刊登草稿', counts.listings, '份', 'list', '按目标店铺完善资料'],
        ['导出批次', counts.exports, '批', 'download', '保留版本与处理结果'],
      ]
        .map(
          ([label, value, unit, glyph, hint]) =>
            `<div class="summary-item">${icon(glyph)}<div class="label">${label}</div><div class="value">${number(value)}<small>${unit}</small></div><div class="hint">${hint}</div></div>`,
        )
        .join('')}</div>` +
      workflow() +
      `<div class="two-column"><div class="stack">${panel('需要继续的工作', arr(data.attention).length ? `<div class="attention-list">${data.attention.map((item) => `<div class="attention-item">${icon('warning')}<div><h3>${h(item.title)}</h3><p>${h(item.reason)}</p></div><a class="text-link" href="${h(item.href?.startsWith('#') ? item.href : '#' + (item.href || '/products'))}">继续处理 →</a></div>`).join('')}</div>` : empty('没有待处理事项', '创建商品、导入供应商资料或开启一项产品研究后，待办会随实际进度出现。', link('/imports', '导入供应商资料', 'button secondary')))}${panel('最近的商品档案', table(['商品', '品类', 'SKU', '设计', '刊登', '最近更新', ''], productRows(data.recent_products, ctx), '还没有商品档案'), link('/products', '查看全部 →', 'text-link'))}</div>${panel('任务动态', arr(data.recent_jobs).length ? `<div class="job-list">${data.recent_jobs.map((job) => `<div class="job-list-item"><div class="row-between"><h3>${h(job.stage || job.kind || '业务任务')}</h3>${badge(job.status)}</div><p>#${h(job.id)} · ${date(job.updated_at || job.created_at)}</p>${progress(job)}${job.error ? `<p class="text-error">${h(job.error)}</p>` : ''}</div>`).join('')}</div>` : empty('暂时没有运行中的任务', '采集、设计和文案任务可以离开页面后继续执行。', '', 'clock'), link('/jobs', '任务中心 →', 'text-link'))}</div>`,
    bind() {
      bindAction('new-product', () => productModal(ctx));
    },
  };
}
export async function productsPage(ctx) {
  const params = Object.fromEntries(ctx.query);
  delete params.product_id;
  const data = await api('/api/products?' + new URLSearchParams(params));
  return {
    title: '商品档案',
    html:
      heading(
        '01 / PRODUCT INFORMATION',
        '商品档案',
        '保留每款商品的真实规格、来源和设计进度。',
        link('/imports', `${icon('upload')}供应商导入`) +
          button(`${icon('plus')}建立商品`, 'new-product', 'primary'),
      ) +
      `<form class="toolbar" id="product-filter"><div class="filters">${field('搜索商品', 'q', ctx.query.get('q') || '', { cls: 'search-field', placeholder: '商品名称、关键词' })}${select('品类', 'category_id', options(ctx.config.categories, '全部品类'), ctx.query.get('category_id') || '')}<button class="button secondary" type="submit">筛选</button></div><span class="muted small">共 ${number(data.total)} 款商品</span></form>` +
      panel(
        '全部商品',
        table(
          ['商品', '品类', 'SKU', '设计', '刊登', '最近更新', ''],
          productRows(data.items, ctx),
          '还没有商品档案',
        ) + pagination(data, '/products', params),
      ),
    bind() {
      bindAction('new-product', () => productModal(ctx));
      bindForm('#product-filter', async (_f, v) => ctx.go('/products', v));
    },
  };
}

function componentRow(part = {}) {
  return `<div class="component-row">${field('组件名称', 'component_name', part.name || '', { placeholder: '如：被套正面' })}${field('数量', 'component_quantity', part.quantity ?? 1, { type: 'number', min: 1, step: 1 })}${field('宽 / cm', 'component_width', part.width_cm ?? '', { type: 'number', min: 0.01, step: 'any' })}${field('高 / cm', 'component_height', part.height_cm ?? '', { type: 'number', min: 0.01, step: 'any' })}${select(
    '印花方式',
    'component_mode',
    [
      { value: 'large', label: '完整大图' },
      { value: 'repeat', label: '循环小花' },
      { value: 'solid', label: '纯色' },
    ],
    part.print_mode || 'large',
  )}<button class="icon-button" type="button" data-remove-component aria-label="移除组件">${icon('close')}</button><input type="hidden" name="component_key" value="${h(part.key || '')}"/></div>`;
}
function componentsFrom(form) {
  return $$('.component-row', form)
    .map((row, i) => ({
      key: $('[name=component_key]', row).value || `component-${i + 1}`,
      name: $('[name=component_name]', row).value.trim(),
      quantity: Number($('[name=component_quantity]', row).value || 1),
      width_cm: int($('[name=component_width]', row).value),
      height_cm: int($('[name=component_height]', row).value),
      print_mode: $('[name=component_mode]', row).value,
    }))
    .filter((x) => x.name);
}
function skuModal(ctx, product, sku = null) {
  const facts = sku?.facts || {};
  const schema = categorySchema(ctx.config, product, facts);
  modal(
    sku ? '编辑实际 SKU' : '添加实际 SKU',
    `<form id="sku-form"><div class="form-grid">${field('内部 SKU 编码', 'code', sku?.code || '', { required: !sku, readonly: !!sku, placeholder: '稳定的唯一编号，如 LINEN-200-PURPLE' })}${field('规格名称', 'label', sku?.label || '', { required: true, placeholder: '如：紫色 · 200 × 230 cm' })}${field('材质', 'material', facts.material || '')}${field('颜色', 'color', facts.color || '')}${field('尺寸 / 规格', 'size', facts.size || '')}${field('原始重量 / g', 'weight_g', facts.weight_g ?? '', { type: typeof facts.weight_g === 'string' && !/^\d+(\.\d+)?$/.test(facts.weight_g) ? 'text' : 'number', min: 0, step: 'any', help: '保留来源中的约数和范围，不改造成精确事实。' })}${field(facts.price_provenance_status === 'suggested' ? '来源建议价（非店铺售价）' : '来源销售价', 'price', facts.price ?? '', { type: typeof facts.price === 'string' && !/^\d+(\.\d+)?$/.test(facts.price) ? 'text' : 'number', min: 0, step: 'any' })}${field('库存', 'stock', facts.stock ?? '', { type: 'number', min: 0, step: 1 })}${dynamicFactFields(schema, facts)}${field('供应商原图', 'original_file', '', { type: 'file', accept: 'image/*', cls: 'span-all', help: '用于对应 SKU 的母稿，保持真实商品身份。' })}</div>${sku?.original_asset_id ? `<div class="row" style="margin-top:15px">${thumb(sku.original_asset_id, '当前原图', 'large')}<span class="muted small">当前原图 · 未选新文件时继续使用</span></div>` : ''}<section class="form-section" style="margin-top:22px"><div class="row-between"><h3>实际组件与可印尺寸</h3>${button(`${icon('plus')}添加组件`, 'add-component', 'quiet small')}</div><p class="muted small" style="margin-top:6px">组件和数量来自真实商品；没有依据的尺寸留空。</p><div id="component-rows">${arr(sku?.components).map(componentRow).join('')}</div></section><div class="form-actions">${submit(sku ? '保存 SKU' : '添加 SKU')}</div></form>`,
    (root) => {
      const bindRemove = () =>
        $$('[data-remove-component]', root).forEach(
          (el) => (el.onclick = () => el.closest('.component-row').remove()),
        );
      bindRemove();
      bindAction(
        'add-component',
        () => {
          $('#component-rows', root).insertAdjacentHTML('beforeend', componentRow());
          bindRemove();
        },
        root,
      );
      bindForm(
        '#sku-form',
        async (form, v) => {
          const newFacts = { ...facts, ...readDynamicFacts(schema, v) };
          for (const name of ['material', 'color', 'size', 'weight_g', 'price', 'stock'])
            newFacts[name] = ['weight_g', 'price', 'stock'].includes(name)
              ? v[name] === ''
                ? null
                : Number.isFinite(Number(v[name]))
                  ? Number(v[name])
                  : v[name]
              : v[name];
          const file = $('[name=original_file]', form).files[0];
          let original = sku?.original_asset_id || null;
          if (file) original = (await upload(file)).id;
          const body = {
            label: v.label,
            facts: newFacts,
            components: componentsFrom(form),
            original_asset_id: original,
          };
          let result;
          if (sku)
            result = await api(`/api/skus/${sku.id}`, {
              method: 'PUT',
              body: { ...body, revision: sku.revision },
            });
          else {
            result = await api(`/api/products/${product.id}/skus`, {
              method: 'POST',
              body: { ...body, code: v.code },
            });
            if (original && !result.original_asset_id)
              await api(`/api/skus/${result.id}`, {
                method: 'PUT',
                body: { ...body, revision: result.revision },
              });
          }
          closeModal();
          ctx.saved(null, 'SKU 事实已保存');
          ctx.reload();
        },
        root,
      );
    },
  );
}
export async function productPage(ctx, id) {
  const product = await api(`/api/products/${id}`);
  const tab = ctx.query.get('tab') || 'skus';
  const skuData = ['skus', 'imports'].includes(tab)
    ? await api(`/api/products/${id}/skus?page=${ctx.query.get('page') || 1}&page_size=24`)
    : null;
  const sourceData = await api(
    '/api/sources?' +
      new URLSearchParams({
        product_id: id,
        page: tab === 'sources' ? ctx.query.get('page') || 1 : 1,
        page_size: tab === 'sources' ? 24 : 1,
      }),
  );
  const skus = arr(skuData?.items || product.skus),
    sources = tab === 'sources' ? arr(sourceData.items) : arr(product.sources),
    importReferences = supplierImportReferences(skus),
    designs = arr(product.designs),
    listings = arr(product.listings);
  let body;
  if (tab === 'skus') {
    body = panel(
      '实际销售规格',
      table(
        ['SKU / 原图', '已知事实', '实际组件', '来源依据', '操作'],
        skus.map((s) => [
          `<div class="table-product">${thumb(s.original_asset_id, s.label)}<div><strong>${h(s.label || s.code)}</strong><p class="mono">${h(s.code)}</p></div></div>`,
          `<strong>${h(s.facts?.material || '材质待补')}</strong><p>${h([s.facts?.color, s.facts?.size].filter(Boolean).join(' · ') || '颜色 / 尺寸待补')}</p>`,
          arr(s.components).length
            ? arr(s.components)
                .map(
                  (x) =>
                    `<div class="small">${h(x.name)} × ${h(x.quantity)}<p>${h(x.width_cm || '—')} × ${h(x.height_cm || '—')} cm</p></div>`,
                )
                .join('')
            : '<span class="muted small">组件待补</span>',
          provenanceSummary(s),
          `<div class="row">${button('编辑', 'edit-sku', 'quiet small', `data-id="${s.id}"`)}${link('/designs/new', '建立设计 →', 'text-link', { product_id: id, sku_id: s.id })}</div>`,
        ]),
        '尚未添加实际 SKU',
        'sku-records',
      ) +
        (skus.length
          ? ''
          : notice('商品档案已建立。添加实际规格及原图后，设计才能准确对应商品。')) +
        pagination(skuData, `/products/${id}`, { tab: 'skus' }),
      button(`${icon('plus')}添加 SKU`, 'new-sku', 'secondary small'),
    );
  } else if (tab === 'imports') {
    body = panel(
      '供应商原始资料',
      `<div class="panel-body">${notice('这里展示当前 SKU 页所追溯的真实导入记录。研究灵感单独保存；供应商资料继续保留原批次、工作表和字段依据。')}${importReferences.length ? importReferences.map((batch) => `<section class="source-origin-record"><div class="row-between"><h3>供应商批次 #${batch.id}</h3>${link('/imports/' + batch.id, '打开原始批次 →', 'button secondary small')}</div><p class="muted small">${h([...batch.positions].join('；'))}</p><div class="stack">${[...batch.skus.values()].map((s) => `<div><strong>${h(s.label || s.code)}</strong><p class="mono">${h(s.code)}</p>${provenanceSummary(s)}</div>`).join('')}</div></section>`).join('') : empty('当前 SKU 页没有供应商导入记录', '人工录入的事实不会被标成供应商出处。')}<div style="margin-top:18px">${pagination(skuData, `/products/${id}`, { tab: 'imports' })}</div></div>`,
    );
  } else if (tab === 'sources') {
    body = panel(
      '与商品关联的研究和灵感',
      table(
        ['来源', '类型 / 平台', '提炼元素', '操作'],
        sources.map((s) => [
          h(s.title),
          `${h(s.kind)} · ${h(s.platform)}`,
          h(typeof s.elements === 'string' ? s.elements : JSON.stringify(s.elements || [])),
          link(`/sources/${s.id}`, '查看来源 →', 'text-link', { product_id: id }),
        ]),
        '尚无研究或灵感记录',
      ) + pagination(sourceData, `/products/${id}`, { tab: 'sources' }),
      link('/research', '去研究 →', 'text-link', { product_id: id }),
    );
  } else if (tab === 'designs') {
    body = panel(
      '关联设计',
      table(
        ['设计', '方式', '状态', '版本', '操作'],
        designs.map((d) => [
          `<strong>${h(d.brief?.slice?.(0, 45) || '设计任务 #' + d.id)}</strong><p>#${d.id}</p>`,
          h(
            { supplier: '供应商展示', large: '原创大印花', repeat: '原创小印花' }[d.mode] || d.mode,
          ),
          badge(d.status) + (d.stale ? badge('stale') : ''),
          `v${h(d.revision)}`,
          link(`/studio/${d.id}`, '进入工作室 →', 'text-link'),
        ]),
        '尚未建立设计任务',
      ),
      link('/designs/new', '建立设计 →', 'text-link', { product_id: id }),
    );
  } else {
    body = panel(
      '店铺刊登',
      table(
        ['刊登', '目标店铺', '状态', '修订', '操作'],
        listings.map((l) => [
          `#${l.id} · ${h(skus.find((s) => s.id === l.sku_id)?.label || l.sku_id)}`,
          h(
            l.target?.name ||
              ctx.config.targets?.find((t) => t.id === l.target_id)?.name ||
              l.target_id,
          ),
          badge(l.status) + (l.stale ? badge('stale') : ''),
          `v${h(l.revision)}`,
          link(`/listings/${l.id}`, '编辑刊登 →', 'text-link'),
        ]),
        '尚未准备刊登资料',
      ),
      link('/listings', '进入刊登工作区 →', 'text-link', { product_id: id }),
    );
  }
  return {
    title: product.name,
    context: product,
    html:
      heading(
        'PRODUCT DOSSIER / #' + id,
        product.name,
        descriptionText(product) || '同一份商品事实贯穿研究、设计与刊登。',
        button('编辑商品', 'edit-product') +
          link('/designs/new', `${icon('spark')}建立设计`, 'button primary', { product_id: id }),
      ) +
      workflow() +
      `<div class="row" style="margin-bottom:22px"><span class="pill">${h(categoryName(ctx.config, product.category_id))}</span><span class="pill">${product.origin === 'supplier' ? '供应商商品' : '原创产品'}</span><span class="muted small">事实版本 v${h(product.revision)} · ${date(product.created_at)}</span></div>${importReferences.length ? `<div class="import-origin-strip"><span>供应商出处</span>${importReferences.map((batch) => link('/imports/' + batch.id, '原导入批次 #' + batch.id, 'text-link')).join('')}${link(`/products/${id}`, '查看字段位置 →', 'text-link', { tab: 'imports' })}</div>` : ''}<div class="tabs">${[['skus', 'SKU 与事实', product.sku_count ?? skus.length], ...(product.origin === 'supplier' ? [['imports', '供应商出处', null]] : []), ['sources', '研究与灵感', sourceData.total ?? sources.length], ['designs', '设计记录', product.design_count ?? designs.length], ['listings', '店铺刊登', product.listing_count ?? listings.length]].map(([key, label, count]) => link(`/products/${id}`, `${label}${count === null ? '' : ` <span class="muted">${count}</span>`}`, tab === key ? 'active' : '', { tab: key })).join('')}</div>` +
      body,
    bind() {
      bindAction('edit-product', () => productModal(ctx, product));
      bindAction('new-sku', () => skuModal(ctx, product));
      bindAction('edit-sku', (el) =>
        skuModal(
          ctx,
          product,
          skus.find((x) => String(x.id) === el.dataset.id),
        ),
      );
    },
  };
}

export async function importsPage(ctx) {
  const data = await api('/api/imports?' + ctx.query.toString());
  return {
    title: '供应商导入',
    html:
      heading(
        '01 / SUPPLIER IMPORT',
        '从真实资料，建立商品。',
        '保留原表、原图与行号。重复导入维持 SKU 身份，冲突在对应行处理。',
      ) +
      `<div class="two-column">${panel('导入新的供应商文件', `<form id="upload-import" class="panel-body"><div class="form-grid">${select('供应商', 'supplier_id', options(ctx.config.suppliers), ctx.query.get('supplier_id') || '')}${link('/settings/suppliers', '管理供应商 →', 'text-link')}</div><div class="drop-zone" style="margin-top:20px">${icon('upload')}<h3>选择供应商产品表</h3><p>支持 XLSX / CSV，嵌入原图随源表保存。</p><input type="file" name="file" accept=".xlsx,.csv" required aria-label="供应商产品表"></div><div class="form-actions">${submit('解析并预览', 'arrow')}</div></form>`)}${panel('一次导入的完整过程', `<div class="panel-body"><ol class="step-list"><li><span class="step-number">1</span><div><strong>解析原表与原图</strong><span>保留文件指纹、工作表和原始行号。</span></div></li><li><span class="step-number">2</span><div><strong>核对字段映射与归属</strong><span>区分商品名称、供应商货号、规格及真实属性。</span></div></li><li><span class="step-number">3</span><div><strong>按实际 SKU 入库</strong><span>新增、更新和冲突逐行记录；可从批次回到商品。</span></div></li></ol></div>`)}</div><div class="section-heading"><h2>导入批次</h2><span class="muted small">保留原资料与处理进度</span></div>` +
      panel(
        '历史导入',
        table(
          ['原始文件', '供应商', '状态', '导入时间', '操作'],
          arr(data.items).map((b) => [
            `<strong>${h(b.name || b.filename || '供应商文件')}</strong><p>#${b.id}</p>`,
            h(
              b.supplier_name ||
                ctx.config.suppliers?.find((x) => x.id === b.supplier_id)?.name ||
                '未指定',
            ),
            badge(b.status),
            date(b.created_at),
            link(`/imports/${b.id}`, '打开批次 →', 'text-link'),
          ]),
          '暂无导入批次',
        ) + pagination(data, '/imports'),
      ),
    bind() {
      bindForm('#upload-import', async (form) => {
        const body = new FormData(form);
        if (!body.get('supplier_id')) throw new Error('请先选择供应商，确保货号身份能够稳定识别。');
        const result = await api('/api/imports', { method: 'POST', body });
        ctx.saved(null, '文件已解析');
        ctx.go(`/imports/${result.id}`);
      });
    },
  };
}
const mappingGroups = [
  [
    '商品身份与来源',
    [
      ['product_name', '商品名称'],
      ['sku_code', '供应商原货号'],
      ['label', '规格名称'],
      ['brand', '品牌'],
      ['supplier_category', '供应商品类'],
      ['color', '颜色'],
      ['size', '原始尺寸说明'],
      ['barcode', 'GTIN / UPC / EAN'],
      ['source_asin', '来源 ASIN'],
      ['manufacturer', '制造商'],
    ],
  ],
  [
    '成本与销售资料',
    [
      ['cost_price', '供货成本'],
      ['cost_currency', '供货币种'],
      ['unit', '计价单位'],
      ['price', '已确认销售价（显式映射）'],
      ['currency', '销售币种'],
      ['stock', '库存'],
    ],
  ],
  [
    '材质、组件与尺寸',
    [
      ['material', '材料组成'],
      ['material_composition', '详细材料组成'],
      ['fabric', '面料成分'],
      ['filling', '填充物成分'],
      ['length_cm', '商品长度 / cm'],
      ['width_cm', '商品宽度 / cm'],
      ['height_cm', '商品高度 / cm'],
      ['natural_height_cm', '自然高度 / cm'],
      ['compressed_height_cm', '受压高度 / cm'],
      ['weight_g', '商品总重 / g'],
      ['weight', '无单位重量原文'],
      ['filling_weight_g', '填充重量 / g'],
      ['quantity', '销售件数'],
      ['contents', '套装组成'],
    ],
  ],
  [
    '包装、产地与说明',
    [
      ['origin_country', '原产国'],
      ['origin_place', '详细产地'],
      ['package_size', '包装尺寸'],
      ['package_weight_g', '包装重量 / g'],
      ['features', '产品特点与用途'],
      ['source_notes', '源表补充说明'],
      ['image', '图片地址或文件名'],
    ],
  ],
];
const mappingKeys = mappingGroups.flatMap(([, fields]) => fields);
function mappingSections(columns, mapping) {
  return mappingGroups
    .map(
      ([name, keys], index) =>
        `<details class="mapping-section" ${index < 2 ? 'open' : ''}><summary><strong>${h(name)}</strong><span>${keys.filter(([key]) => mapping[key]).length} / ${keys.length} 项已映射</span></summary><div class="mapping-grid">${keys.map(([key, label]) => select(label, `map_${key}`, [{ value: '', label: '未提供 / 不映射' }, ...columns], mapping[key] || '')).join('')}</div></details>`,
    )
    .join('');
}
function importParsePanel(data) {
  const locked = arr(data.rows).some((r) => r.sku_id);
  return `<details class="panel" style="margin-bottom:20px"><summary class="panel-heading" style="cursor:pointer"><h2>源表解析区域</h2><span class="muted small">${locked ? '已有关联商品，保留原始定位' : '需要时选择工作表、表头与数据起始行'}</span></summary><form id="reparse-import" class="panel-body"><div class="form-grid three">${select('工作表', 'sheet', [{ value: '', label: '自动识别' }, ...arr(data.sheets).map((s) => (typeof s === 'object' ? { value: s.name || s.sheet, label: s.name || s.sheet } : { value: s, label: s }))], data.config?.sheet || '')}${field('表头行', 'header_row', data.config?.header_row || '', { type: 'number', min: 1, placeholder: '留空自动识别' })}${field('首个数据行', 'data_row', data.config?.data_row || '', { type: 'number', min: 2, placeholder: '留空自动识别' })}</div><p class="muted small" style="margin-top:12px">只在原表解析区域不正确时重新识别。已关联商品的来源行保持不变。</p><div class="form-actions">${button('按此区域重新解析', 'reparse-source', 'secondary', locked ? 'disabled' : '')}</div></form></details>`;
}
function mappingOriginHtml(data) {
  const labels = {
    automatic: '按源表列名自动匹配',
    supplier_default: '继承供应商默认映射',
    batch_override: '当前批次独立覆盖',
  };
  return `<div class="mapping-origin"><strong>${h(labels[data.mapping_source] || '当前批次映射')}</strong>${data.supplier ? `<span>${h(data.supplier.name)} · 供应商配置 v${data.supplier.revision}${data.mapping_default_revision ? ' · 映射依据 v' + data.mapping_default_revision : ''}</span>` : ''}<p>应用并导入只处理本批资料。保存为供应商默认后，后续新文件可以复用；缺失列仍需逐项核对。</p>${arr(
    data.mapping_issues,
  )
    .map((issue) =>
      notice(
        h(issue.message || issue) +
          (issue.field
            ? `<label class="check"><input type="checkbox" name="ignore_missing_fields" value="${h(issue.field)}"><span>本批忽略此缺失字段（保留未知）</span></label>`
            : ''),
        'warning',
      ),
    )
    .join('')}</div>`;
}
export async function importPage(ctx, id) {
  const data = await api(`/api/imports/${id}?${ctx.query.toString()}`);
  const columns = arr(data.columns).map((c) =>
    typeof c === 'string'
      ? { value: c, label: c }
      : { value: c.name || c.key || c.column, label: c.name || c.label || c.column },
  );
  const rows = arr(data.rows?.items || data.rows);
  const mapping = data.mapping || {};
  const rowHtml = rows.map((row) => {
    const raw = row.raw || {};
    return [
      h(row.row_no ?? row.id),
      `<div class="raw-cell" title="${h(JSON.stringify(raw))}">${h(
        Object.values(raw)
          .filter((x) => x != null && x !== '')
          .slice(0, 5)
          .join(' · '),
      )}</div>`,
      arr(row.images)
        .map((image) => thumb(image.asset_id || image.id || image, '源表图片'))
        .join('') || '<span class="muted small">—</span>',
      row.issues?.length
        ? arr(row.issues)
            .map(
              (issue) =>
                `<p class="${issue.severity === 'info' ? 'muted' : 'text-error'}">${h(typeof issue === 'string' ? issue : issue.message)}</p>${issue.current !== undefined || issue.incoming !== undefined ? `<div class="conflict-values"><span>现值：${h(issue.current ?? '未知')}</span><span>导入值：${h(issue.incoming ?? '未知')}</span></div>` : ''}`,
            )
            .join('')
        : badge(row.status || 'parsed'),
      row.sku_id
        ? `${link(`/products/${row.product_id}`, '查看商品 →', 'text-link')}<p>SKU #${row.sku_id}</p>`
        : '<span class="muted small">待入库</span>',
      row.issues?.length && (row.sku_id || row.status === 'conflict')
        ? select(
            '冲突处理',
            `decision_${row.id || row.row_no}`,
            row.sku_id
              ? [
                  { value: 'keep', label: '保留现有值' },
                  { value: 'update', label: '使用这次导入值' },
                ]
              : [
                  { value: '', label: '待核对身份' },
                  { value: 'create_new', label: '确认是新的实际规格' },
                ],
            row.sku_id ? 'keep' : '',
          )
        : '<span class="muted small">—</span>',
    ];
  });
  return {
    title: '导入批次 #' + id,
    html:
      heading(
        'IMPORT BATCH / #' + id,
        data.name || data.filename || '供应商文件',
        '把原始列映射为业务字段。空值保留未知，有原货号时按供应商与货号去重；缺少货号时保留来源内部身份，不伪造供应商编码。',
        link('/imports', '返回导入批次'),
      ) +
      `<div class="row" style="margin-bottom:20px">${badge(data.status)}<span class="muted small">${h(data.summary?.row_count || data.summary?.total || data.total || rows.length)} 行资料</span></div>${importParsePanel(data)}<form id="apply-import" data-track data-draft-clean-if-unchanged data-revision="${h(data.revision || 1)}">${panel('字段与商品归属', `<div class="panel-body"><div class="form-grid"><div>${select('目标品类', 'category_id', options(ctx.config.categories), data.category_id || '')}${link('/settings/categories', '管理品类，完成后返回 →', 'text-link', { return_to: '/imports/' + id })}</div>${field('没有商品列时使用的名称', 'product_name', data.product_name || '', { placeholder: '按原表分组时可留空' })}</div><div class="section-heading"><h2>源表列 → 商品字段</h2><span class="muted small">供货成本与销售价格分别保存</span></div><div id="mapping-origin">${mappingOriginHtml(data)}</div>${mappingSections(columns, mapping)}<div class="form-actions">${button('保存为此供应商默认映射', 'save-supplier-mapping', 'secondary', data.supplier ? '' : 'disabled')}<span class="muted small">只保存映射，不执行商品导入</span></div></div>`)}<div class="section-heading"><h2>原始行与处理结果</h2><span class="muted small">冲突不会自动覆盖已编辑事实</span></div>${panel('行预览', table(['原始行', '原始内容', '原图', '校验 / 状态', '归属', '冲突决策'], rowHtml, '原文件没有可处理的数据行') + pagination(data.pagination || { total: rows.length }, '/imports/' + id))}<div class="form-actions"><span class="muted small">已完成行再次提交保持同一身份</span>${submit('应用映射并导入', 'check')}</div></form>`,
    bind() {
      bindAction('reparse-source', async () => {
        const v = Object.fromEntries(new FormData($('#reparse-import')));
        const config = { revision: data.revision };
        if (v.sheet) config.sheet = v.sheet;
        if (v.header_row) config.header_row = Number(v.header_row);
        if (v.data_row) config.data_row = Number(v.data_row);
        await api(`/api/imports/${id}/reparse`, { method: 'POST', body: config });
        ctx.toast('源表区域已重新解析');
        ctx.reload();
      });
      bindAction('save-supplier-mapping', async () => {
        const form = $('#apply-import');
        const values = Object.fromEntries(new FormData(form));
        const mapping = Object.fromEntries(
          mappingKeys.map(([key]) => [key, values['map_' + key]]).filter(([, value]) => value),
        );
        const saved = await api('/api/imports/' + id + '/save-mapping-default', {
          method: 'POST',
          body: {
            revision: data.revision,
            supplier_revision: data.supplier.revision,
            category_id: int(values.category_id),
            mapping,
          },
        });
        Object.assign(data, saved);
        form.dataset.revision = data.revision;
        $('#mapping-origin').innerHTML = mappingOriginHtml(data);
        const pendingImportOptions = [...form.elements].some((element) =>
          element.name === 'product_name'
            ? element.value !== (data.product_name || '')
            : element.name?.startsWith('decision_')
              ? element.value !==
                [...element.options].find((option) => option.defaultSelected)?.value
              : element.name === 'ignore_missing_fields' && element.checked,
        );
        if (pendingImportOptions) {
          form.dispatchEvent(new Event('input', { bubbles: true }));
          ctx.saved(null, '默认映射已保存；尚未应用的导入选项已暂存本机');
        } else {
          ctx.saved(form, '供应商默认映射已保存；本次未重新执行商品导入');
        }
      });
      bindForm('#apply-import', async (form, v) => {
        const map = Object.fromEntries(
          mappingKeys.map(([key]) => [key, v[`map_${key}`]]).filter(([, value]) => value),
        );
        const decisions = Object.fromEntries(
          Object.entries(v)
            .filter(([k]) => k.startsWith('decision_'))
            .map(([k, value]) => [k.slice(9), value]),
        );
        const result = await api(`/api/imports/${id}/apply`, {
          method: 'POST',
          body: {
            revision: data.revision,
            mapping: map,
            category_id: int(v.category_id),
            product_name: v.product_name,
            decisions,
            ignore_missing_fields: new FormData(form).getAll('ignore_missing_fields'),
          },
        });
        ctx.saved(form, `导入已处理${result.summary?.conflicts ? '，冲突已保留' : ''}`);
        ctx.reload();
      });
    },
  };
}
