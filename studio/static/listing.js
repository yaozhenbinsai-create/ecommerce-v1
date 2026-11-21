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
  check,
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
  int,
  intent,
  progress,
  asset,
  thumb,
  errorBox,
  formValues,
  imageRoleLabel,
} from './ui.js';
import { openPublicImages } from './listing-public-images.js';
import { skuPicker, designPicker } from './listing-picker.js';
import { categorySchema, dynamicFactFields, readDynamicFacts } from './catalog-ui.js';
import {
  templateAttributes,
  renderTemplateAttributes,
  readTemplateAttributes,
  attachTemplateEnums,
  exposeTemplateIssues,
  normalizeListingIssues,
  labelListingError,
} from './listing-fields.js';

const targetOptions = (targets) => [
  { value: '', label: '请选择店铺与市场' },
  ...arr(targets).map((t) => ({ value: t.id, label: `${t.name} · ${t.platform} / ${t.site}` })),
];
const productOptions = (products) => [
  { value: '', label: '请选择商品' },
  ...arr(products).map((p) => ({ value: p.id, label: p.name })),
];
const fieldNames = {
  title: '商品标题',
  description: '商品描述',
  bullets: '卖点',
  price: '售价',
  stock: '库存',
  brand: '品牌',
  parent_sku: '父 SKU',
  variation: '变体主题',
  images: '设计图片',
  template: '平台原生模板',
  dependencies: '上游版本',
};
const roleLabel = imageRoleLabel;
const returnHref = (value) => (value?.startsWith('#') ? value : '#' + (value || '/listings'));

async function newListingModal(ctx, products) {
  const initialProductId = int(ctx.query.get('product_id'));
  let skuControl, designControl;
  modal(
    '准备新的店铺刊登',
    `<form id="new-listing-form"><div class="form-grid">${select('目标商品', 'product_id', productOptions(products), initialProductId || '')}${select('目标店铺', 'target_id', targetOptions(ctx.config.targets), ctx.query.get('target_id') || '')}</div><div id="listing-sku-field"></div><div id="listing-design-field"></div><p class="muted small" style="margin-top:15px">选择真实 SKU 后，只列出属于该规格的设计。选择跨页保留；也可暂不绑定设计，先保存文案草稿。</p><div class="form-actions">${submit('建立刊登草稿', 'arrow')}</div></form>`,
    (root) => {
      const form = $('#new-listing-form', root);
      const chooseProduct = async (productId, allowSeed = false) => {
        skuControl?.dispose();
        designControl?.dispose();
        const skuHost = $('#listing-sku-field', form),
          designHost = $('#listing-design-field', form);
        designHost.innerHTML =
          '<input type="hidden" name="design_id" value=""><p class="muted small">先选择实际 SKU，再选择其设计版本。</p>';
        if (!productId) {
          skuHost.innerHTML =
            '<input type="hidden" name="sku_id" value=""><p class="muted small">先选择目标商品。</p>';
          return;
        }
        let selected = null;
        if (allowSeed && ctx.query.get('sku_id')) {
          selected = await api('/api/skus/' + ctx.query.get('sku_id'));
          if (Number(selected.product_id) !== Number(productId))
            throw new Error('传入的 SKU 不属于目标商品。');
        }
        const onSku = (sku) => {
          designControl?.dispose();
          if (sku) designControl = designPicker(designHost, productId, sku.id);
        };
        skuControl = skuPicker(skuHost, productId, onSku, selected);
        if (selected) onSku(selected);
      };
      $('[name=product_id]', form).addEventListener('change', (event) =>
        chooseProduct(Number(event.target.value)).catch((error) =>
          ctx.toast(error.message, 'error'),
        ),
      );
      chooseProduct(initialProductId, true).catch((error) => {
        $('#listing-sku-field', form).innerHTML = errorBox(error);
      });
      bindForm(
        '#new-listing-form',
        async (_f, values) => {
          if (!values.product_id || !values.sku_id || !values.target_id)
            throw new Error('请选择商品、实际 SKU 和目标店铺。');
          const created = await api('/api/listings', {
            method: 'POST',
            body: {
              product_id: Number(values.product_id),
              sku_id: Number(values.sku_id),
              target_id: Number(values.target_id),
              design_id: int(values.design_id),
            },
          });
          skuControl?.dispose();
          designControl?.dispose();
          closeModal();
          ctx.go('/listings/' + created.id);
        },
        root,
      );
    },
  );
}

export async function listingsPage(ctx) {
  const [data, products] = await Promise.all([
    api('/api/listings?' + ctx.query.toString()),
    api('/api/products?page_size=100'),
  ]);
  const product = arr(products.items).find((p) => String(p.id) === ctx.query.get('product_id'));
  return {
    title: '刊登工作区',
    context: product,
    html:
      heading(
        '03 / LISTING WORKSPACE',
        '把产品准备好，交给市场。',
        '同一商品的店铺资料分别保存，真实事实和设计版本保持可追溯。',
        link('/exports', `${icon('download')}导出批次`) +
          link('/families', '父子变体分组', 'button secondary', {
            target_id: ctx.query.get('target_id'),
          }) +
          button(`${icon('plus')}准备刊登`, 'new-listing', 'primary'),
      ) +
      `<form class="toolbar" id="listing-filter"><div class="filters">${select('目标店铺', 'target_id', targetOptions(ctx.config.targets), ctx.query.get('target_id') || '')}${select('商品', 'product_id', productOptions(products.items), ctx.query.get('product_id') || '')}<button class="button secondary" type="submit">筛选</button></div><span class="muted small">${number(data.total)} 份刊登</span></form>` +
      panel(
        '店铺刊登草稿',
        table(
          ['商品 / SKU', '店铺与市场', '图片', '待补项', '状态 / 版本', '操作'],
          arr(data.items).map((l) => [
            `<div class="table-product">${thumb(l.images?.[0]?.asset_id, l.name)}<div><strong>${h(l.name || l.product_name || '商品 #' + l.product_id)}</strong><p class="mono">${h(l.sku_code || 'SKU #' + l.sku_id)}</p></div></div>`,
            h(
              l.target_name ||
                ctx.config.targets?.find((t) => t.id === l.target_id)?.name ||
                l.target_id,
            ),
            `${arr(l.images).length}<span class="muted small"> 个槽位</span>`,
            arr(l.diagnostics).length
              ? `<span class="badge missing">${l.diagnostics.length} 项待补</span>`
              : badge('ready'),
            `${l.stale ? badge('stale') : badge(l.status)}<p>v${l.revision}</p>`,
            link(`/listings/${l.id}`, '编辑资料 →', 'text-link'),
          ]),
          '还没有店铺刊登草稿',
        ) + pagination(data, '/listings', Object.fromEntries(ctx.query)),
      ),
    bind() {
      bindAction('new-listing', () => newListingModal(ctx, products.items));
      bindForm('#listing-filter', async (_f, v) => ctx.go('/listings', v));
    },
  };
}

function diagnosticHtml(l, issues, template = null) {
  issues = normalizeListingIssues(issues, template, l);
  return issues.length
    ? issues
        .map((issue) => {
          const destination = issue.href || href('/listings/' + l.id, { focus: issue.field });
          return `<div class="diagnostic-item"><strong>${h(issue.label || fieldNames[issue.field] || issue.field)}</strong><p>${h(issue.message)}</p><a href="${destination}" class="text-link">定位并处理 →</a></div>`;
        })
        .join('')
    : `<div style="padding:12px 0">${badge('ready', '当前本地校验无缺项')}<p class="muted small" style="margin-top:10px">导出时还会按所选模板与实际图片重新核对。</p></div>`;
}
function imageEditors(images, template) {
  const entries = arr(images).map((image, index) => ({ image, index, slots: [] }));
  const main = ['main', 'hero', 'sales-1', 'sales_1']
    .map((role) => entries.find((entry) => entry.image.role === role))
    .find(Boolean);
  const rank = (entry) => Number(String(entry.image.role).match(/(\d+)$/)?.[1] || 1000);
  const gallery = entries
    .filter(
      (entry) => entry !== main && /^(sales(?:[-_]\d+)?|gallery.*|scene.*)$/.test(entry.image.role),
    )
    .sort((a, b) => rank(a) - rank(b));
  const details = entries
    .filter((entry) => /^detail/.test(entry.image.role))
    .sort((a, b) => rank(a) - rank(b));
  if (main) main.slots = ['image_url', 'image_1'];
  gallery.forEach((entry, index) => {
    entry.slots = ['image_' + (index + 2)];
  });
  details.forEach((entry, index) => {
    entry.slots = ['detail_image_' + (index + 1)];
  });
  const ordered = [...(main ? [main] : []), ...gallery, ...details];
  ordered.push(...entries.filter((entry) => !ordered.includes(entry)));
  const mapping = template?.config?.mapping || {};
  return arr(images).length
    ? ordered
        .map(({ image, index: i, slots }) => {
          const exported = slots.filter((slot) => Object.hasOwn(mapping, slot));
          return `<div class="image-row" data-field="images.${i}.url">${thumb(image.asset_id, roleLabel(image.role))}<div><div class="row-between"><h4>${h(roleLabel(image.role))} · ${h(image.role)} · 资产 #${h(image.asset_id)}</h4>${exported.length ? badge(image.url_status, image.url_status === 'verified' ? '地址与资产一致' : image.url_status === 'failed' ? '验证失败' : '待验证') : '<span class="pill">本模板不输出</span>'}</div>${exported.length ? `<p class="muted small">输出槽位：${h(exported.map((slot) => slot + ' → ' + mapping[slot]).join(' / '))}</p>${field('平台可访问的 HTTPS 图片地址', `image_url_${i}`, image.url || '', { type: 'url', placeholder: 'https://…' })}` : `<p class="muted small">${template ? '仅保留设计资产，本模板不输出，也不要求验证公开地址。' : '先登记并选择原生模板，才能确定输出槽位。'}</p><input type="hidden" name="image_url_${i}" value="${h(image.url || '')}">`}<input type="hidden" name="image_role_${i}" value="${h(image.role)}"><small>绑定设计图片 #${h(image.design_asset_id)}${exported.length && image.verification_error ? ' · ' + h(image.verification_error) : ''}</small></div></div>`;
        })
        .join('')
    : empty(
        '尚未绑定设计图片',
        '从同一 SKU 的已完成设计绑定销售图，图片才会与正确版本关联。',
        '',
        'image',
      );
}
function copySuggestionHtml(job, revision) {
  if (!job) return '';
  if (job.status === 'succeeded') {
    const result = job.result || {};
    const suggestion = result.suggestion || result;
    return `<div class="inline-notice"><div><strong>文案建议已生成</strong><p>${result.listing_revision !== undefined && result.listing_revision !== revision ? '建议基于较早的刊登修订，请先核对差异。' : '建议保留在任务结果中，采用后仍需保存当前表单。'}</p></div></div><div class="copy-suggestion"><h3>${h(suggestion.title || '标题待确认')}</h3><p class="small subtle" style="white-space:pre-wrap;margin-top:10px">${h(suggestion.description || '')}</p><ul class="small subtle">${arr(
      suggestion.bullets,
    )
      .map((x) => `<li>${h(x)}</li>`)
      .join(
        '',
      )}</ul><div class="row">${button('采用到当前表单', 'apply-copy', 'secondary small')}${link('/jobs', '查看生成任务 →', 'text-link', { job_id: job.id })}</div></div>`;
  }
  return `${badge(job.status)}${progress(job)}${job.error ? notice(h(job.error), job.status === 'unknown' ? 'warning' : 'error') : ''}`;
}
export async function listingPage(ctx, id) {
  let listing = await api(`/api/listings/${id}`);
  const fields = listing.fields || {};
  const schema = categorySchema(ctx.config, listing.product || {});
  const templateResponse = await api('/api/templates?target_id=' + listing.target_id);
  const templates = arr(templateResponse.items || templateResponse);
  const activeTemplateId =
    listing.family?.template_id ||
    listing.active_template_id ||
    fields._template_id ||
    templates.reduce((latest, template) => Math.max(latest, template.id), 0);
  const activeTemplate = templates.find(
    (template) => Number(template.id) === Number(activeTemplateId),
  );
  const extraAttributes = templateAttributes(
    activeTemplate ? [activeTemplate] : [],
    listing,
    schema,
  );
  const images = arr(listing.images);
  const target = listing.target || {};
  let copyJob = ctx.query.get('copy_job')
    ? await api(`/api/jobs/${ctx.query.get('copy_job')}`)
    : null;
  return {
    title: '刊登编辑',
    context: listing.product,
    html:
      heading(
        '03 / LISTING EDITOR',
        listing.product?.name || listing.name || '刊登资料',
        `${target.name || '目标店铺'} · ${target.platform || ''} / ${target.site || ''} · ${target.language || ''}`,
        link('/listings', '刊登队列', 'button secondary', { product_id: listing.product_id }) +
          link('/families', '加入父子分组', 'button secondary', {
            target_id: listing.target_id,
            listing_id: id,
          }) +
          link('/exports', `${icon('download')}进入导出`, 'button secondary', {
            target_id: listing.target_id,
            listing_id: id,
            template_id: activeTemplateId,
          }) +
          `<button class="button primary" type="submit" form="listing-form">${icon('check')}保存草稿</button>`,
      ) +
      `${listing.stale ? notice('当前商品、设计或店铺设置已有新版本。请比较上游资料，再明确刷新当前草稿关联。', 'warning') : ''}<div class="split-editor"><aside class="record-nav"><div class="sku-mini"><strong>${h(listing.sku?.label || '当前规格')}</strong><p class="mono">${h(listing.sku?.code || listing.sku_code)}</p></div><a href="#listing-copy" class="active" data-scroll="listing-copy">商品文案</a><a href="#listing-pricing" data-scroll="listing-pricing">价格与变体</a><a href="#listing-images" data-scroll="listing-images">图片与版本</a><a href="#listing-attributes" data-scroll="listing-attributes">真实属性</a></aside><div class="stack">${panel(
        '平台刊登资料',
        `<form id="listing-form" class="panel-body" data-track data-revision="${listing.revision}"><section class="form-section" data-field="template"><h3>当前使用的原生模板</h3>${select('刊登与图片校验模板', 'listing_template_id', [{ value: '', label: templates.length ? '请选择模板' : '此店铺尚未登记模板' }, ...templates.map((template) => ({ value: template.id, label: `${template.name} · v${template.revision}` }))], activeTemplateId || '', listing.family ? 'disabled' : '')}<p class="muted small" id="template-selection-note" style="margin-top:10px">${listing.family ? `此刊登跟随父子分组模板。${link('/families/' + listing.family.id, '到分组修改 →', 'text-link')}` : '下方字段与图片槽位按当前已保存模板显示。'}</p></section><section class="form-section" id="listing-copy"><div class="row-between"><h3>商品文案</h3>${button(`${icon('spark')}保存并生成文案建议`, 'generate-copy', 'quiet small')}</div><div id="copy-job-result" style="margin:15px 0">${copySuggestionHtml(copyJob, listing.revision)}</div><div class="form-grid">${field('目标语言商品标题', 'title', fields.title || '', { cls: 'span-all', placeholder: '符合目标站点表达的商品标题' })}${field('商品卖点（每行一条）', 'bullets', arr(fields.bullets).join('\n'), { textarea: true, rows: 5, cls: 'span-all', placeholder: '每条卖点使用一行，不补造材质、认证或性能。' })}${field('商品描述', 'description', fields.description || '', { textarea: true, rows: 7, cls: 'span-all' })}${field('品牌', 'brand', fields.brand || '')}${field('搜索词', 'search_terms', fields.search_terms || '')}</div></section><section class="form-section" id="listing-pricing"><h3>价格、库存与平台变体</h3><div class="form-grid">${field(`售价 / ${target.currency || '目标币种'}`, 'price', fields.price ?? '', { type: 'number', min: 0, step: 'any' })}${field('可销售库存', 'stock', fields.stock ?? '', { type: 'number', min: 0, step: 1 })}${field('父 SKU（适用时）', 'parent_sku', fields.parent_sku || '')}${field('平台变体主题', 'variation', typeof fields.variation === 'string' ? fields.variation : fields.variation?.theme || '', { placeholder: '使用平台模板允许的变体主题' })}</div><p class="muted small" style="margin-top:10px">内部与供应商 SKU 保持稳定，平台字段按当前店铺和模板准备。</p></section><section class="form-section" id="listing-images" data-field="images"><div class="row-between"><h3>图片与设计版本</h3><div class="row">${button('更换设计版本', 'replace-design', 'quiet small')}${button('准备公开图片地址', 'open-public-images', 'secondary small', activeTemplate ? '' : 'disabled')}</div></div><p class="muted small" style="margin-top:8px">已绑定设计 ${listing.design_id ? '#' + listing.design_id : '—'}。每个图片地址须与绑定资产内容一致。</p><div id="listing-image-editors">${imageEditors(images, activeTemplate)}</div>${images.length ? `<div class="row" style="margin-top:15px">${button('保存并验证图片地址', 'verify-images', 'secondary small')}<span class="muted small">由服务端读取图片并核对内容</span></div>` : ''}</section><section class="form-section" id="listing-attributes"><h3>平台属性表达</h3><p>原始事实来自 SKU；以下为当前平台独立使用的属性值。未知属性保持空白。</p><div class="form-grid">${field('材质', 'attribute_material', fields.attributes?.material ?? listing.sku?.facts?.material ?? '')}${field('颜色', 'attribute_color', fields.attributes?.color ?? listing.sku?.facts?.color ?? '')}${field('尺寸', 'attribute_size', fields.attributes?.size ?? listing.sku?.facts?.size ?? '')}${field('重量 / g', 'attribute_weight_g', fields.attributes?.weight_g ?? listing.sku?.facts?.weight_g ?? '', { type: 'number', min: 0, step: 'any' })}${dynamicFactFields(schema, { ...listing.sku?.facts, ...fields.attributes }, 'attribute_')}</div></section>${extraAttributes.length ? `<section class="form-section"><h3>当前模板的附加字段</h3><p>按已登记原生模板映射整理。必填依据来自对应模板，没有资料的事实仍需回到来源补齐。</p><div class="form-grid">${renderTemplateAttributes(extraAttributes)}</div></section>` : ''}<section class="form-section" data-field="dependencies"><h3>关联版本</h3>${metadata(
          [
            ['商品事实', `v${h(listing.product?.revision)}`],
            ['SKU 事实', `v${h(listing.sku?.revision)}`],
            [
              '设计',
              listing.design ? `#${listing.design.id} · v${listing.design.revision}` : '未绑定',
            ],
            ['当前草稿', 'v' + h(listing.revision)],
          ],
        )}<div class="row" style="margin-top:12px">${button('比较后刷新关联版本', 'refresh-dependencies', 'secondary small')}${link(`/products/${listing.product_id}`, '查看商品事实 →', 'text-link')}</div></section><div class="form-actions listing-save-actions"><span class="muted small">未知售价、库存可保留空白，缺项由导出校验提示</span>${submit('保存刊登资料')}</div></form>`,
      )}</div><aside class="stack">${panel(
        '当前店铺与模板',
        `<div class="panel-body">${metadata([
          [
            '原生模板',
            activeTemplate ? h(activeTemplate.name) + ' · v' + activeTemplate.revision : '未登记',
          ],
          ['平台', h(target.platform)],
          ['店铺', h(target.name)],
          ['站点', h(target.site)],
          ['经营模式', h(target.mode || '未设')],
          ['类目', h(target.category || '未设')],
          ['语言 / 币种', h([target.language, target.currency].filter(Boolean).join(' / '))],
        ])}${link('/settings/templates', '查看原生模板 →', 'text-link', { target_id: listing.target_id })}</div>`,
      )}${panel('需要补齐的资料', `<div class="panel-body" id="listing-diagnostics">${diagnosticHtml(listing, arr(listing.diagnostics), activeTemplate)}</div>`, button('重新校验', 'validate-listing', 'quiet small'))}${panel(
        '真实 SKU 依据',
        `<div class="panel-body">${listing.sku?.original_asset_id ? `<img src="${asset(listing.sku.original_asset_id)}" alt="原始商品图" style="max-height:160px;width:100%;object-fit:contain;margin-bottom:12px">` : ''}${metadata(
          [
            ['供应商货号', h(listing.sku?.provenance?.identity?.supplier_code || '—')],
            ['材质', h(listing.sku?.facts?.material || '未知')],
            ['尺寸', h(listing.sku?.facts?.size || '未知')],
            ['颜色', h(listing.sku?.facts?.color || '未知')],
          ],
        )}${link(`/products/${listing.product_id}`, '查看完整档案 →', 'text-link')}</div>`,
      )}</aside></div>`,
    bind() {
      const form = $('#listing-form');
      const templateSelector = $('[name=listing_template_id]', form);
      templateSelector?.addEventListener('change', () => {
        $('#template-selection-note').textContent =
          Number(templateSelector.value) === Number(activeTemplateId)
            ? '下方字段与图片槽位按当前已保存模板显示。'
            : '已选择另一份模板。保存草稿后加载该模板的字段和图片要求；已填写内容保留。';
      });
      attachTemplateEnums(form, extraAttributes);
      form.addEventListener('input', (event) => {
        if (!/^image_url_\d+$/.test(event.target?.name || '')) return;
        const index = Number(event.target.name.slice(10));
        if (event.target.value !== (images[index]?.url || '')) {
          const badge = event.target.closest('.image-row')?.querySelector('.badge');
          if (badge) {
            badge.className = 'badge missing';
            badge.textContent = '地址已修改 · 待保存验证';
          }
        }
      });
      bindAction('open-public-images', async () => {
        if (Number(templateSelector?.value) !== Number(activeTemplateId))
          throw new Error('请先保存模板选择，再准备该模板的图片。');
        await openPublicImages(ctx, listing, activeTemplateId, (records) => {
          let applied = 0;
          for (const item of records) {
            const index = images.findIndex((image) =>
              item.design_asset_id
                ? Number(image.design_asset_id) === Number(item.design_asset_id)
                : Number(image.asset_id) === Number(item.asset_id) && image.role === item.role,
            );
            const input = $(`[name=image_url_${index}]`, form);
            if (!input || input.type !== 'url') continue;
            input.value = item.url;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            applied++;
          }
          return applied;
        });
      });
      $$('[data-scroll]').forEach((el) =>
        el.addEventListener('click', (e) => {
          e.preventDefault();
          $('#' + el.dataset.scroll).scrollIntoView({ behavior: 'smooth', block: 'start' });
          $$('[data-scroll]').forEach((x) => x.classList.toggle('active', x === el));
        }),
      );
      const collect = () => {
        const v = formValues(form);
        const attrs = {
          ...(listing.fields.attributes || {}),
          ...readDynamicFacts(schema, v, 'attribute_'),
          ...readTemplateAttributes(extraAttributes, v),
          material: v.attribute_material,
          color: v.attribute_color,
          size: v.attribute_size,
          weight_g: int(v.attribute_weight_g),
        };
        const savedFields = {
          ...listing.fields,
          _template_id: listing.family?.template_id || int(v.listing_template_id) || null,
          title: v.title,
          description: v.description,
          bullets: v.bullets
            .split('\n')
            .map((x) => x.trim())
            .filter(Boolean),
          price: int(v.price),
          stock: int(v.stock),
          brand: v.brand,
          parent_sku: v.parent_sku,
          variation: v.variation,
          search_terms: v.search_terms,
          attributes: attrs,
        };
        const savedImages = images.map((image, i) => ({
          ...image,
          url: v[`image_url_${i}`] || '',
          role: v[`image_role_${i}`] || image.role,
        }));
        return { revision: listing.revision, fields: savedFields, images: savedImages };
      };
      let saving = false;
      const save = async (extra = {}, show = true) => {
        if (saving) throw new Error('正在保存当前草稿，请等待本次保存完成。');
        saving = true;
        try {
          listing = await api(`/api/listings/${id}`, {
            method: 'PUT',
            body: { ...collect(), ...extra },
          });
          form.dataset.revision = listing.revision;
          if (show) ctx.saved(form, '刊登资料已保存');
          $('#listing-diagnostics').innerHTML = diagnosticHtml(listing, arr(listing.diagnostics));
          exposeTemplateIssues(form, listing.diagnostics);
          return listing;
        } finally {
          saving = false;
        }
      };
      bindForm('#listing-form', async () => {
        await save();
        ctx.reload();
      });
      const bindApply = () =>
        bindAction('apply-copy', () => {
          const suggestion = copyJob?.result?.suggestion || copyJob?.result || {};
          for (const name of ['title', 'description', 'search_terms'])
            if (suggestion[name] !== undefined) $(`[name=${name}]`, form).value = suggestion[name];
          if (Array.isArray(suggestion.bullets))
            $('[name=bullets]', form).value = suggestion.bullets.join('\n');
          form.dispatchEvent(new Event('input', { bubbles: true }));
          ctx.toast('文案建议已采用到表单，请核对后保存');
        });
      bindApply();
      bindAction('generate-copy', async () => {
        await save();
        const job = await api(`/api/listings/${id}/copy`, {
          method: 'POST',
          body: { intent_key: intent(`copy-${id}-${listing.revision}`) },
        });
        ctx.go(`/listings/${id}`, { copy_job: job.id });
      });
      bindAction('verify-images', async () => {
        await save();
        listing = await api(`/api/listings/${id}/images/verify`, {
          method: 'POST',
          body: {
            revision: listing.revision,
            template_id:
              listing.active_template_id ||
              listing.family?.template_id ||
              listing.fields._template_id,
          },
        });
        ctx.toast('图片地址验证已完成，请查看每个图片槽位结果');
        ctx.reload();
      });
      bindAction('validate-listing', async () => {
        const result = await api(`/api/listings/${id}/validate`, { method: 'POST' });
        $('#listing-diagnostics').innerHTML = diagnosticHtml(
          listing,
          arr(result.issues),
          activeTemplate,
        );
        exposeTemplateIssues(form, result.issues);
        ctx.toast(
          result.ready ? '当前已保存资料通过本地校验' : '已定位需要补齐的资料',
          result.ready ? 'success' : 'error',
        );
      });
      bindAction('refresh-dependencies', () =>
        modal(
          '核对并刷新上游关联',
          `<div class="stack">${notice('此操作采用最新商品、SKU 和店铺修订；已有文案与图片不会自动重写。若设计仍引用旧事实，请先更换设计版本。', 'warning')}${metadata(
            [
              ['当前商品事实', 'v' + h(listing.product?.revision)],
              ['当前 SKU 事实', 'v' + h(listing.sku?.revision)],
              [
                '当前设计',
                listing.design ? `#${listing.design.id} · v${listing.design.revision}` : '未绑定',
              ],
            ],
          )}<div class="form-actions">${button('保存并刷新关联', 'confirm-refresh', 'primary')}</div></div>`,
          (root) =>
            bindAction(
              'confirm-refresh',
              async () => {
                await save({ refresh_dependencies: true });
                closeModal();
                ctx.reload();
              },
              root,
            ),
        ),
      );
      bindAction('replace-design', async () => {
        const selected = listing.design_id ? await api('/api/designs/' + listing.design_id) : null;
        modal(
          '更换当前 SKU 的设计版本',
          `<form id="replace-design-form"><div id="replace-design-picker"></div>${notice('更换后，图片槽位重新绑定新设计的实际成果，公开图片地址需要重新准备。历史导出仍保留原版本。', 'warning')}<div class="form-actions">${submit('保存并采用此设计', 'layers')}</div></form>`,
          (root) => {
            const picker = designPicker(
              $('#replace-design-picker', root),
              listing.product_id,
              listing.sku_id,
              selected,
            );
            bindForm(
              '#replace-design-form',
              async (_form, values) => {
                if (!values.design_id) throw new Error('请选择设计。');
                await save({ design_id: Number(values.design_id), refresh_dependencies: true });
                picker.dispose();
                closeModal();
                ctx.reload();
              },
              root,
            );
          },
        );
      });
      if (copyJob && ['queued', 'running'].includes(copyJob.status))
        return ctx.bindRefresh(async () => {
          copyJob = await api(`/api/jobs/${copyJob.id}`);
          $('#copy-job-result').innerHTML = copySuggestionHtml(copyJob, listing.revision);
          bindApply();
          if (!['queued', 'running'].includes(copyJob.status)) return;
        }, 4000);
    },
  };
}

export async function exportsPage(ctx) {
  const tid = int(ctx.query.get('target_id'));
  const [history, listings, templates, families] = await Promise.all([
    api(
      '/api/exports?' +
        new URLSearchParams({
          ...(tid ? { target_id: tid } : {}),
          page: ctx.query.get('page') || 1,
        }),
    ),
    tid
      ? api(
          '/api/listings?' +
            new URLSearchParams({
              target_id: tid,
              page_size: 24,
              page: ctx.query.get('listing_page') || 1,
            }),
        )
      : Promise.resolve({ items: [] }),
    tid
      ? api('/api/templates?' + new URLSearchParams({ target_id: tid, page_size: 100 }))
      : Promise.resolve({ items: [] }),
    tid
      ? api(
          '/api/families?' +
            new URLSearchParams({
              target_id: tid,
              page: ctx.query.get('family_page') || 1,
              page_size: 24,
            }),
        ).catch((error) => {
          if (error.status === 404) return { items: [], unavailable: true };
          throw error;
        })
      : Promise.resolve({ items: [] }),
  ]);
  const templateItems = arr(templates.items || templates);
  const selectedId = int(ctx.query.get('listing_id'));
  const selectionKey = 'export-selection:' + tid;
  const selectedListings = new Set(JSON.parse(sessionStorage.getItem(selectionKey) || '[]'));
  if (selectedId) selectedListings.add(selectedId);
  const familySelectionKey = 'export-family-selection:' + tid;
  const selectedFamilies = new Set(JSON.parse(sessionStorage.getItem(familySelectionKey) || '[]'));
  const selectedFamilyId = int(ctx.query.get('family_id'));
  if (selectedFamilyId) selectedFamilies.add(selectedFamilyId);
  const templateSelectionKey = 'export-template-selection:' + tid;
  const templateSelection =
    ctx.query.get('template_id') ||
    sessionStorage.getItem(templateSelectionKey) ||
    templateItems[0]?.id ||
    '';
  const familySection =
    tid && !families.unavailable
      ? `<section class="form-section"><div class="row-between"><h3>完整父子变体分组</h3>${link('/families', '管理分组 →', 'text-link', { target_id: tid })}</div><p class="muted small">选择一个分组会输出逻辑父体和全部真实子刊登。重复勾选的子刊登仅输出一次；已分组的子体不能单独拆批导出。</p>${table(
          ['选择', '逻辑父 SKU', '模板 / 主题', '子刊登', '操作'],
          arr(families.items).map((family) => [
            `<input type="checkbox" name="family_ids" value="${family.id}" ${selectedFamilies.has(family.id) ? 'checked' : ''} aria-label="导出分组 ${h(family.parent_sku)}">`,
            `<strong>${h(family.title || family.parent_sku)}</strong><p class="mono">${h(family.parent_sku)}</p>`,
            `模板 #${family.template_id}<p>${h(family.variation)}</p>`,
            family.child_count,
            link('/families/' + family.id, '核对关系 →', 'text-link'),
          ]),
          '此店铺尚无父子分组',
        )}<div class="pagination"><span>已选 <strong id="export-family-count">${selectedFamilies.size}</strong> 个完整分组</span><div>${Number(families.page || 1) > 1 ? link('/exports', '← 分组上页', 'button secondary small', { target_id: tid, family_page: Number(families.page) - 1 }) : ''}${Number(families.page || 1) * 24 < Number(families.total || 0) ? link('/exports', '分组下页 →', 'button secondary small', { target_id: tid, family_page: Number(families.page || 1) + 1 }) : ''}</div></div></section>`
      : families.unavailable
        ? notice('当前服务暂不能读取父子分组。独立刊登仍可按现有规则校验。', 'warning')
        : '';
  const target = arr(ctx.config.targets).find((t) => t.id === tid);
  return {
    title: '导出与回执',
    html:
      heading(
        '03 / EXPORT DELIVERY',
        '从已保存资料，生成平台文件。',
        '每个批次冻结模板、SKU 事实、刊登和图片版本。导出文件与平台处理结果分别记录。',
        link('/settings/templates', '管理原生模板'),
      ) +
      `<div class="two-column">${panel(
        '建立导出批次',
        `<form id="export-form" class="panel-body"><div class="form-grid">${select('目标店铺', 'target_id', targetOptions(ctx.config.targets), tid || '')}${select('原生模板版本', 'template_id', [{ value: '', label: tid ? '请选择已登记模板' : '先选择目标店铺' }, ...templateItems.map((t) => ({ value: t.id, label: `${t.name} · v${t.revision}` }))], templateSelection)}</div>${target ? `<div class="row" style="margin-top:15px"><span class="pill">${h(target.platform)}</span><span class="pill">${h(target.site)}</span><span class="muted small">${h(target.category || '类目待设')} · ${h(target.currency || '')}</span></div>` : ''}${tid && !templateItems.length ? notice(`这个店铺还没有原生模板。${link('/settings/templates', '配置实际平台模板 →', 'text-link', { target_id: tid })}`, 'warning') : ''}<div class="section-heading"><h2>选择本次导出的 SKU 刊登</h2><span class="muted small">仅使用已保存修订</span></div>${table(
          ['选择', '商品 / SKU', '修订', '本地缺项', '操作'],
          arr(listings.items).map((l) => [
            `<input type="checkbox" name="listing_ids" value="${l.id}" ${selectedListings.has(l.id) ? 'checked' : ''} aria-label="选择 ${h(l.sku_code || l.id)}">`,
            `<strong>${h(l.name || '商品 #' + l.product_id)}</strong><p class="mono">${h(l.sku_code || 'SKU #' + l.sku_id)}</p>`,
            `v${l.revision}`,
            arr(l.diagnostics).length
              ? badge('missing', l.diagnostics.length + ' 项待补')
              : badge('ready'),
            link(`/listings/${l.id}`, '编辑 →', 'text-link'),
          ]),
          tid ? '此店铺还没有刊登草稿' : '先选择目标店铺',
        )}${tid ? `<div class="pagination"><span>共 ${listings.total || 0} 份刊登 · 已选 <strong id="export-selection-count">${selectedListings.size}</strong> 份</span><div>${Number(listings.page || 1) > 1 ? link('/exports', '← 上页', 'button secondary small', { target_id: tid, listing_page: Number(listings.page) - 1 }) : ''}${Number(listings.page || 1) * Number(listings.page_size || 24) < Number(listings.total || 0) ? link('/exports', '下页 →', 'button secondary small', { target_id: tid, listing_page: Number(listings.page || 1) + 1 }) : ''}</div></div>` : ''}${familySection}<div class="form-actions">${button('清空选择', 'clear-export-selection', 'quiet small')}${submit('校验并生成原生文件', 'download')}</div></form>`,
      )}<aside>${panel('交付状态', `<div class="panel-body"><ol class="step-list"><li><span class="step-number">1</span><div><strong>文件已生成</strong><span>符合所选原生模板结构，保留完整导出快照。</span></div></li><li><span class="step-number">2</span><div><strong>平台处理结果已确认</strong><span>绑定实际处理报告及可核对的提交批次依据。</span></div></li><li><span class="step-number">3</span><div><strong>商品发布结果已确认</strong><span>以平台对应商品的实际状态为依据。</span></div></li></ol><p class="muted small" style="margin-top:15px">三个状态各自有证据，生成文件不代表平台已经导入。</p></div>`)}</aside></div><div class="section-heading"><h2>导出批次记录</h2><span class="muted small">历史快照保持原样</span></div>` +
      panel(
        '已保存批次',
        table(
          ['批次', '目标店铺', '输出行数', '状态', '生成时间', '操作'],
          arr(history.items).map((b) => [
            `<strong>批次 #${b.id}</strong>`,
            h(
              b.snapshot?.target?.name ||
                ctx.config.targets?.find((t) => t.id === b.target_id)?.name ||
                b.target_id,
            ),
            arr(b.snapshot?.rows).length || arr(b.snapshot?.listings).length,
            badge(b.status, b.status === 'file_generated' ? '文件已生成' : null),
            date(b.created_at),
            link(`/exports/${b.id}`, '查看文件与回执 →', 'text-link'),
          ]),
          '尚未生成导出批次',
        ) + pagination(history, '/exports', { target_id: tid }),
      ),
    bind() {
      const form = $('#export-form');
      const remember = () => {
        sessionStorage.setItem(selectionKey, JSON.stringify([...selectedListings]));
        sessionStorage.setItem(familySelectionKey, JSON.stringify([...selectedFamilies]));
        if ($('#export-selection-count'))
          $('#export-selection-count').textContent = selectedListings.size;
        if ($('#export-family-count'))
          $('#export-family-count').textContent = selectedFamilies.size;
      };
      remember();
      $$('[name=listing_ids]', form).forEach((el) =>
        el.addEventListener('change', () => {
          if (el.checked) selectedListings.add(Number(el.value));
          else selectedListings.delete(Number(el.value));
          remember();
        }),
      );
      $$('[name=family_ids]', form).forEach((el) =>
        el.addEventListener('change', () => {
          if (el.checked) selectedFamilies.add(Number(el.value));
          else selectedFamilies.delete(Number(el.value));
          remember();
        }),
      );
      bindAction('clear-export-selection', () => {
        selectedListings.clear();
        selectedFamilies.clear();
        $$('[name=listing_ids]', form).forEach((el) => (el.checked = false));
        $$('[name=family_ids]', form).forEach((el) => (el.checked = false));
        remember();
      });
      $('[name=target_id]', form).addEventListener('change', (e) =>
        ctx.go('/exports', { target_id: e.target.value }),
      );
      $('[name=template_id]', form).addEventListener('change', (event) =>
        sessionStorage.setItem(templateSelectionKey, event.target.value),
      );
      bindForm('#export-form', async (form, v) => {
        const selected = [...selectedListings];
        if (!v.target_id || !v.template_id) throw new Error('请选择目标店铺和原生模板。');
        if (!selected.length && !selectedFamilies.size)
          throw new Error('请选择独立刊登或完整父子分组。');
        let result;
        try {
          result = await api('/api/exports', {
            method: 'POST',
            body: {
              target_id: Number(v.target_id),
              template_id: Number(v.template_id),
              listing_ids: selected,
              family_ids: [...selectedFamilies],
              intent_key: intent('export'),
            },
          });
        } catch (error) {
          throw labelListingError(
            error,
            templateItems.find((item) => Number(item.id) === Number(v.template_id)),
          );
        }
        sessionStorage.removeItem(selectionKey);
        sessionStorage.removeItem(familySelectionKey);
        ctx.saved(null, '原生文件已生成，平台结果尚待确认');
        ctx.go(`/exports/${result.id}`);
      });
    },
  };
}

export async function exportPage(ctx, id) {
  const batch = await api(`/api/exports/${id}`);
  const snapshot = batch.snapshot || {};
  const rows = arr(snapshot.rows || snapshot.listings);
  const receipts = arr(batch.receipts);
  return {
    title: '导出批次 #' + id,
    html:
      heading(
        'EXPORT BATCH / #' + id,
        '导出批次 #' + id,
        `${snapshot.target?.name || '店铺 #' + batch.target_id} · ${date(batch.created_at)} · 冻结 ${rows.length} 条平台数据行`,
        link('/exports', '返回导出批次', 'button secondary', { target_id: batch.target_id }) +
          `<a class="button primary" href="${asset(batch.asset_id)}?download=true">${icon('download')}下载原生文件</a>`,
      ) +
      `<div class="row" style="margin-bottom:22px">${badge(batch.status, batch.status === 'file_generated' ? '文件已生成' : null)}<span class="muted small">平台导入与发布状态依据实际回执分别确认</span></div><div class="two-column"><div class="stack">${panel(
        '冻结的 SKU 与版本',
        table(
          ['平台输出行', '商品 / SKU', '刊登修订', '事实 / 图片', '当前资料'],
          rows.map((row, index) => {
            if (row.row_type === 'parent') {
              const family = row.family || {};
              return [
                row.row ?? index + 1,
                `<strong>${h(family.title || family.fields?.title || row.values?.title || '逻辑父体')}</strong><p class="mono">${h(family.parent_sku || row.values?.sku || '')}</p>${badge('registered', '逻辑父体')}`,
                `分组 v${h(family.revision || '—')}`,
                '平台关系行；没有实物 SKU、售价或库存',
                family.id
                  ? link('/families/' + family.id, '查看当前分组 →', 'text-link')
                  : '冻结父体记录',
              ];
            }
            const listing = row.listing || row;
            const sku = row.sku || {};
            return [
              row.row ?? index + 1,
              `<strong>${h(row.product?.name || row.values?.title || listing.fields?.title || '商品')}</strong><p class="mono">${h(sku.code || row.sku_code || listing.sku_id)}</p>`,
              `v${h(listing.revision)}`,
              `<span class="small">SKU v${h(sku.revision || '—')}</span><p>${arr(listing.images).length} 个冻结图片版本</p>`,
              listing.id ? link(`/listings/${listing.id}`, '查看当前草稿 →', 'text-link') : '—',
            ];
          }),
          '批次没有记录',
        ),
      )}${panel(
        '处理报告历史',
        receipts.length
          ? receipts
              .map(
                (r) =>
                  `<div class="panel-body" style="border-bottom:1px solid var(--line)"><div class="row-between"><h3>处理报告 #${r.id}</h3>${badge(r.status, r.status === 'awaiting_batch_evidence' ? '待核对批次归属' : null)}</div><p class="muted small" style="margin-top:8px">${date(r.created_at)} · 平台批次参考：${h(r.batch_reference || '未提供')}</p>${r.parsed?.reason ? notice(h(r.parsed.reason), 'warning') : ''}<p class="small subtle" style="margin:12px 0">已对应 ${r.parsed?.matched_count || 0} 条 SKU；对应 SKU 不自动证明是本批次上传结果。</p><details><summary class="text-link">查看逐行报告内容</summary>${table(
                    ['报告行', 'SKU', '归属刊登', '报告原值'],
                    arr(r.parsed?.rows).map((row) => [
                      row.row,
                      h(row.sku || '—'),
                      row.listing_id ? '#' + row.listing_id : '未匹配',
                      `<span class="small">${h(
                        Object.entries(row.raw || {})
                          .map(([k, v]) => k + ': ' + v)
                          .join(' · '),
                      )}</span>`,
                    ]),
                    '报告中没有数据行',
                  )}</details><p style="margin-top:13px"><a class="text-link" href="${asset(r.asset_id)}?download=true">下载原始处理报告 ↗</a></p></div>`,
              )
              .join('')
          : empty(
              '尚未附加平台处理报告',
              '上传平台下载的原始报告，保留文件与对应关系。',
              '',
              'file',
            ),
      )}</div><aside class="stack">${panel(
        '模板与交付依据',
        `<div class="panel-body">${metadata([
          ['平台', h(snapshot.target?.platform)],
          [
            '店铺 / 站点',
            h([snapshot.target?.name, snapshot.target?.site].filter(Boolean).join(' / ')),
          ],
          ['原生模板', h(snapshot.template?.name || batch.template_id)],
          ['模板版本', 'v' + h(snapshot.template?.revision || '—')],
          [
            '模板指纹',
            `<span class="mono">${h(snapshot.template_hash?.slice(0, 20) || '—')}…</span>`,
          ],
        ])}</div>`,
      )}${panel('附加平台处理报告', `<form id="receipt-form" class="panel-body">${field('平台提交批次参考', 'batch_reference', '', { placeholder: '记录平台可核对的提交编号' })}${field('平台原始处理报告', 'file', '', { type: 'file', accept: '.xlsx,.csv,.tsv', required: true, help: '保留原文件，不通过改写 SKU 或状态制造成功。' })}<div class="form-actions">${submit('解析并保留报告', 'upload')}</div></form>`)}</aside></div>`,
    bind() {
      bindForm('#receipt-form', async (form) => {
        await api(`/api/exports/${id}/receipts`, { method: 'POST', body: new FormData(form) });
        ctx.saved(null, '处理报告已保留，请核对批次归属');
        ctx.reload();
      });
    },
  };
}
