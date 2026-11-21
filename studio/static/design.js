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
  pagination,
  metadata,
  bindForm,
  bindAction,
  modal,
  closeModal,
  int,
  intent,
  progress,
  steps,
  asset,
  thumbnail,
  thumbnailFallback,
  thumb,
  errorBox,
} from './ui.js';
import { attachInheritance, planHtml, configurationSourcesHtml } from './design-config.js';
import { skuPicker } from './listing-picker.js';
import { attachComponentPlans, componentPlanSummary } from './component-plans.js';

const modeName = { supplier: '供应商展示', large: '原创大印花', repeat: '原创小印花' };
const roleName = {
  main: '商品主图',
  'repeat-preview': '拼接预览',
  master: '母稿',
  tile: '循环单元',
  sale: '销售图',
  sales: '销售图',
  hero: '商品主图',
  detail: '详情图',
  preview: '拼接预览',
  repeat_preview: '拼接预览',
  production: '生产文件',
  mockup: '效果图',
};
const canReviseRole = (role) => role === 'main' || /^(sales|detail)-[1-9]\d*$/.test(role || '');
const isSales = (role) =>
  ['main', 'sale', 'sales', 'hero', 'mockup', 'detail'].includes(role) ||
  /^(sales|detail)-/.test(role || '');
const assetKind = (art) => art?.role?.split(':').pop();
const assetRole = (art) =>
  (art.meta?.component_key ? (art.meta?.component_name || art.meta.component_key) + ' · ' : '') +
  (roleName[assetKind(art)] ||
    (/^sales-/.test(art.role) ? '销售图' : /^detail-/.test(art.role) ? '详情图' : null) ||
    art.label ||
    art.role ||
    '设计成果');
const designTitle = (d) =>
  (typeof d.brief === 'string' ? d.brief : d.brief?.title)?.slice(0, 50) || `设计任务 #${d.id}`;
const productOptions = (items) => [
  { value: '', label: '请选择目标商品' },
  ...arr(items).map((p) => ({ value: p.id, label: p.name })),
];
const intentFor = (key) => {
  let value = sessionStorage.getItem(key);
  if (!value) {
    value = intent(key);
    sessionStorage.setItem(key, value);
  }
  return value;
};

export async function designsPage(ctx) {
  const pid = ctx.query.get('product_id');
  const data = await api('/api/designs?' + ctx.query.toString());
  const products = await api('/api/products?page_size=100');
  const product = arr(products.items).find((p) => String(p.id) === pid);
  return {
    title: '设计任务',
    context: product,
    html:
      heading(
        '02 / DESIGN PIPELINE',
        '设计任务',
        '从明确的任务书出发，保留来源、真实 SKU、母稿和每一步成果。',
        link('/designs/new', `${icon('plus')}建立设计任务`, 'button primary', { product_id: pid }),
      ) +
      `<form class="toolbar" id="design-filter"><div class="filters">${select('目标商品', 'product_id', productOptions(products.items), pid || '')}${select('任务状态', 'status', [{ value: '', label: '全部状态' }, ...['draft', 'queued', 'running', 'completed', 'failed', 'unknown'].map((x) => ({ value: x, label: { draft: '未启动', queued: '排队中', running: '进行中', completed: '图组已生成', failed: '失败待续', unknown: '结果待核对' }[x] }))], ctx.query.get('status') || '')}<button class="button secondary" type="submit">筛选</button></div><span class="muted small">成果按任务版本保留</span></form>` +
      panel(
        '设计队列',
        table(
          ['任务 / 商品', '设计方式', '状态', '事实版本', '建立时间', '操作'],
          arr(data.items).map((d) => [
            `<strong>${h(designTitle(d))}</strong><p>${h(d.product_name || d.product?.name || '商品 #' + d.product_id)} · SKU #${h(d.sku_id)}</p>`,
            h(modeName[d.mode] || d.mode),
            badge(d.status) + (d.stale ? ' ' + badge('stale') : ''),
            `v${h(d.input_revision?.sku || d.revision)}`,
            date(d.created_at),
            link(`/studio/${d.id}`, '进入工作室 →', 'text-link'),
          ]),
          '还没有设计任务',
        ) + pagination(data, '/designs', Object.fromEntries(ctx.query)),
      ),
    bind() {
      bindForm('#design-filter', async (_f, v) => ctx.go('/designs', v));
    },
  };
}

export async function newDesignPage(ctx) {
  const editId = int(ctx.query.get('edit_id'));
  const editing = editId ? await api('/api/designs/' + editId) : null;
  if (editing && (editing.status !== 'draft' || editing.job_id))
    throw new Error('此任务已经冻结，请返回工作室复制新版本。');
  const initial = editing?.config || {};
  const products = await api('/api/products?page_size=100');
  const pid = editing?.product_id || int(ctx.query.get('product_id'));
  const product = pid ? await api(`/api/products/${pid}`) : null;
  if (product && !arr(products.items).some((p) => p.id === product.id))
    products.items.push(product);
  const skus = arr(product?.skus);
  let draftSku = null;
  try {
    const draftRoute = (
      globalThis.location?.hash || href('/designs/new', Object.fromEntries(ctx.query))
    ).split('&focus=')[0];
    const draft = JSON.parse(
      sessionStorage.getItem(`woven-draft:${draftRoute}:design-brief`) || 'null',
    );
    if (draft && String(draft.revision || '') === String(editing?.revision || ''))
      draftSku = int(arr(draft.fields).find((item) => item.name === 'sku_id')?.value);
  } catch {}
  const requestedSku = draftSku || editing?.sku_id || int(ctx.query.get('sku_id'));
  if (requestedSku && !skus.some((s) => s.id === requestedSku)) {
    const selected = await api('/api/skus/' + requestedSku);
    if (selected.product_id !== pid)
      throw new Error('当前 SKU 不属于所选商品，请从商品档案重新进入。');
    skus.push(selected);
  }
  const sid = requestedSku || skus[0]?.id;
  const selectedSources = new Set(
    editing?.source_ids ||
      (ctx.query.get('source_ids') || '').split(',').filter(Boolean).map(Number),
  );
  const mode =
    editing?.mode ||
    ctx.query.get('mode') ||
    (product?.origin === 'supplier' ? 'supplier' : 'large');
  const sources = arr(product?.sources);
  for (const sourceId of selectedSources)
    if (!sources.some((s) => s.id === sourceId)) {
      try {
        sources.push(await api('/api/sources/' + sourceId));
      } catch {}
    }
  const presets = arr(ctx.config.design_presets);
  return {
    title: '设计任务书',
    context: product,
    html:
      heading(
        '02 / DESIGN BRIEF',
        editing ? '编辑草稿任务书' : '先把这次设计说清楚。',
        editing
          ? '保存会重新核对当前事实与配置，保留明确的任务覆盖；启动前仍可继续编辑。'
          : '真实商品、来源依据和输出方案会在启动时冻结，作为整条设计链的依据。',
        editing
          ? link('/studio/' + editId, '返回工作室', 'button secondary')
          : link('/designs', '返回设计任务', 'button secondary', { product_id: pid }),
      ) +
      `<div class="two-column"><div>${panel(
        '设计任务书',
        `<form id="design-brief" class="panel-body" data-track data-revision="${editing?.revision || ''}"><section class="form-section"><h3><span class="step-number">1</span>选择实际商品与 SKU</h3><div class="form-grid">${select('目标商品', 'product_id', productOptions(products.items), pid || '')}</div><div id="design-sku-picker"><input type="hidden" name="sku_id" value="${sid || ''}"></div>${product && !skus.length ? notice(`当前商品尚未添加实际 SKU。${link(`/products/${pid}`, '完善商品规格 →', 'text-link')}`, 'warning') : ''}</section><section class="form-section"><h3><span class="step-number">2</span>设计方式与表达方向</h3><div class="form-grid">${select(
          '设计方式',
          'mode',
          Object.entries(modeName).map(([value, label]) => ({ value, label })),
          mode,
        )}${select('常用设计方案', 'preset_id', [{ value: '', label: '本次自定义' }, ...presets.map((p) => ({ value: p.id, label: p.name }))], initial._overrides?.preset_id || initial.preset_id || '')}${field('设计任务与创意方向', 'brief', editing?.brief || ctx.query.get('brief') || '', { textarea: true, rows: 6, required: true, cls: 'span-all', placeholder: '写清主题、情绪、配色、构图和使用场景。供应商展示保留实物身份；原创设计写清怎样应用灵感。' })}</div><div id="repeat-config" ${mode === 'repeat' ? '' : 'hidden'} style="margin-top:18px"><div class="form-grid three">${select(
          '循环方式',
          'repeat_mode',
          [
            { value: 'basic', label: '规则平铺' },
            { value: 'half-drop', label: '半落位循环' },
          ],
          'basic',
        )}${field('循环单元宽 / cm', 'repeat_width', 24, { type: 'number', min: 0.1, step: 'any' })}${field('循环单元高 / cm', 'repeat_height', 24, { type: 'number', min: 0.1, step: 'any' })}</div><p class="muted small" style="margin-top:10px">循环单元、3×3 拼接和生产展开共用这组参数；实际组件尺寸读取 SKU。</p></div></section><section class="form-section" id="component-plan-editor"></section><section class="form-section"><h3><span class="step-number">3</span>交付方案</h3><div class="form-grid three">${field('创意路线数', 'route_count', mode === 'supplier' ? 1 : 3, { type: 'number', min: 1, max: 5 })}${field('销售 / 效果图数', 'sales_count', mode === 'supplier' ? 7 : 4, { type: 'number', min: 1, max: 12 })}${field('详情图数', 'detail_count', mode === 'supplier' ? 1 : 0, { type: 'number', min: 0, max: 3 })}</div><p class="muted small" style="margin-top:10px">这里的数量控制本次实际输出。设计方案可以在业务配置中复用。</p></section><section class="form-section"><h3><span class="step-number">4</span>引用来源</h3>${sources.length ? `<div class="stack" style="gap:12px">${sources.map((s) => check(s.title, 'source_ids', s.id, selectedSources.has(s.id))).join('')}</div>` : notice(`此商品还没有采用来源。${link('/research', '去研究 →', 'text-link', { product_id: pid })} 原创任务也可以直接填写自己的创意。`)}</section><div class="form-actions">${submit('保存任务书', 'file')}${button('保存并启动设计 →', 'save-and-start', 'primary')}</div></form>`,
      )}</div><aside class="stack">${panel('当前商品事实', product ? `<div class="panel-body"><h3>${h(product.name)}</h3><div id="sku-fact-preview" style="margin-top:14px"></div>${link(`/products/${pid}`, '回到商品档案 →', 'text-link')}</div>` : empty('还没有选择商品', '选择目标后，真实规格和原图会显示在这里。', '', 'box'))}${panel('设计如何继续', `<div class="panel-body"><ol class="step-list"><li><span class="step-marker">${icon('file')}</span><div><strong>任务书冻结输入</strong><span>SKU 事实、来源与配置版本一起保留。</span></div></li><li><span class="step-marker">${icon('spark')}</span><div><strong>路线 → 母稿 → 完整图组</strong><span>每张图继承明确的视觉依据，成功成果可继续使用。</span></div></li><li><span class="step-marker">${icon('arrow')}</span><div><strong>生产与刊登分别交付</strong><span>生产读取真实尺寸；刊登绑定实际图片版本。</span></div></li></ol></div>`)}</aside></div>`,
    bind() {
      const form = $('#design-brief');
      const skuChoice = pid
        ? skuPicker(
            $('#design-sku-picker', form),
            pid,
            (sku) => {
              if (sku && !skus.some((item) => item.id === sku.id)) skus.push(sku);
            },
            skus.find((sku) => sku.id === sid) || null,
          )
        : null;
      if (editing) $('[name=product_id]', form).disabled = true;
      else
        $('[name=product_id]', form).addEventListener('change', (e) =>
          ctx.go('/designs/new', { product_id: e.target.value }),
        );
      const showSku = () => {
        const s = skus.find((x) => x.id === Number($('[name=sku_id]', form).value));
        const node = $('#sku-fact-preview');
        if (node)
          node.innerHTML = s
            ? `${s.original_asset_id ? `<img src="${asset(s.original_asset_id)}" alt="${h(s.label)}" style="max-height:180px;width:100%;object-fit:contain;margin-bottom:12px">` : ''}${metadata(
                [
                  ['规格', h(s.label)],
                  ['SKU', `<span class="mono">${h(s.code)}</span>`],
                  ['材质', h(s.facts?.material || '未知')],
                  ['尺寸', h(s.facts?.size || '未知')],
                  [
                    '组件',
                    h(
                      arr(s.components)
                        .map((x) => `${x.name} × ${x.quantity}`)
                        .join('、') || '待补',
                    ),
                  ],
                ],
              )}`
            : '<p class="muted small">请选择实际 SKU。</p>';
      };
      showSku();
      $('[name=sku_id]', form).addEventListener('change', showSku);
      $('[name=mode]', form).addEventListener('change', (e) => {
        $('#repeat-config').hidden = e.target.value !== 'repeat';
        $('[name=sales_count]', form).value = e.target.value === 'supplier' ? 7 : 4;
        $('[name=detail_count]', form).value = e.target.value === 'supplier' ? 1 : 0;
      });
      $('[name=preset_id]', form).addEventListener('change', (e) => {
        const p = presets.find((x) => String(x.id) === e.target.value);
        const c = p?.config || p?.spec;
        if (!c) return;
        for (const key of ['route_count', 'sales_count', 'detail_count'])
          if (c[key] !== undefined) $(`[name=${key}]`, form).value = c[key];
        if (c.repeat) {
          $('[name=repeat_mode]', form).value = c.repeat.mode || 'basic';
          $('[name=repeat_width]', form).value = c.repeat.width_cm || 24;
          $('[name=repeat_height]', form).value = c.repeat.height_cm || 24;
        }
      });
      bindAction('save-and-start', () => {
        form.dataset.start = 'true';
        form.requestSubmit();
      });
      const inheritedOverrides = attachInheritance(
        ctx,
        form,
        product,
        editing?.config?._overrides || editing?.config,
      );
      const readComponentPlans = attachComponentPlans(form, skus, initial.component_plans || []);
      bindForm('#design-brief', async (form, v) => {
        if (!(editing?.product_id || v.product_id) || !v.sku_id)
          throw new Error('请选择商品和实际 SKU。');
        const sku = skus.find((x) => x.id === Number(v.sku_id));
        const config = {
          ...(editing?.config?._overrides || {}),
          ...inheritedOverrides(),
          components: sku?.components || [],
          component_plans: readComponentPlans(),
          preset_id: int(v.preset_id),
        };
        const d = await api(editing ? '/api/designs/' + editId : '/api/designs', {
          method: editing ? 'PUT' : 'POST',
          body: {
            ...(editing ? { revision: editing.revision } : {}),
            product_id: editing?.product_id || Number(v.product_id),
            sku_id: Number(v.sku_id),
            mode: v.mode,
            brief: v.brief,
            source_ids: new FormData(form).getAll('source_ids').map(Number),
            config,
          },
        });
        ctx.saved(form, '设计任务书已保存');
        if (form.dataset.start === 'true') {
          try {
            await api(`/api/designs/${d.id}/start`, {
              method: 'POST',
              body: { intent_key: intentFor(`start-design-${d.id}`) },
            });
          } catch (error) {
            ctx.toast(error.message, 'error');
          }
        }
        ctx.go(`/studio/${d.id}`);
      });
      return () => skuChoice?.dispose();
    },
  };
}

function selectedAsset(arts, key) {
  return (
    arts.find((x) => String(x.id) === String(key)) || arts.find((x) => isSales(x.role)) || arts[0]
  );
}
function jobErrorNotice(job) {
  if (!job?.error) return '';
  const step = arr(job.steps).find((item) => ['unknown', 'failed'].includes(item.status));
  const stage = step?.label || job.stage || '当前步骤';
  const modelBridge =
    /CodexBridgeError/.test(job.error) ||
    (step?.request_ref?.protocol === 'codex' && job.status === 'unknown');
  const message = modelBridge
    ? `${stage}：模型连接未返回可确认结果。${job.status === 'unknown' ? '已暂停后续生成，需先核对原请求；已保存成果继续保留。' : '已保存成果继续保留，可在任务详情检查失败步骤。'}`
    : `${stage}：${job.error}`;
  return (
    notice(h(message), job.status === 'unknown' ? 'warning' : 'error') +
    (modelBridge
      ? `<details class="job-technical-error"><summary>查看服务原始错误</summary><p>${h(job.error)}</p></details>`
      : '')
  );
}
function bindReconcile(ctx, job) {
  bindAction('reconcile-job', () => {
    const unknown = arr(job?.steps).filter((step) => step.status === 'unknown');
    modal(
      '核对原请求的实际结果',
      unknown.length
        ? `<form id="reconcile-job-form">${notice('只检查已经发出的请求和本机已保存证据，不发送新的生成请求。结果仍未明确时继续保留 unknown。')}${select(
            '需要核对的原步骤',
            'step_key',
            unknown.map((step) => ({ value: step.key, label: step.label || step.key })),
            unknown[0].key,
          )}<div class="form-actions">${submit('核对原请求', 'search')}</div><div id="reconcile-result"></div></form>`
        : notice('没有可定位的未知步骤，请保留当前任务并检查任务记录中的原请求身份。', 'warning'),
      (root) => {
        bindForm(
          '#reconcile-job-form',
          async (_form, values) => {
            const result = await api(`/api/jobs/${job.id}/reconcile`, {
              method: 'POST',
              body: { step_key: values.step_key },
            });
            const evidence = result.reconciliation || {};
            $('#reconcile-result', root).innerHTML =
              notice(
                h(
                  evidence.message ||
                    {
                      known_no_output:
                        '已确认原请求没有产生可用输出。返回任务后，可明确接续失败步骤。',
                      restored: '已恢复原请求的回包。没有新发请求，请返回任务核对成果。',
                      pending: '原请求仍在进行，继续保留结果待核对状态。',
                      insufficient_evidence: '现有证据不足以确定结果，继续保留原请求。',
                    }[evidence.outcome] ||
                    '已完成一次原请求核对，请检查最新任务状态。',
                ),
                ['pending', 'insufficient_evidence'].includes(evidence.outcome)
                  ? 'warning'
                  : 'info',
              ) + button('返回最新任务状态', 'return-reconciled-job', 'secondary');
            bindAction(
              'return-reconciled-job',
              () => {
                closeModal();
                ctx.reload();
              },
              root,
            );
          },
          root,
        );
      },
    );
  });
}
function studioJob(job) {
  if (!job) return '';
  return `<div class="studio-taskbar">${badge(job.status)}${progress(job)}<div class="row">${job.status === 'unknown' ? button('核对原请求', 'reconcile-job', 'secondary small') : ''}${['failed'].includes(job.status) ? button('继续未完成步骤', 'resume-design', 'secondary small') : ''}${['running', 'queued'].includes(job.status) ? button('停止后续步骤', 'cancel-design', 'quiet small') : ''}${link('/jobs', '任务详情 →', 'text-link', { job_id: job.id })}</div>${jobErrorNotice(job)}</div>`;
}
function assetButtons(arts, selected) {
  return arts
    .map(
      (a) =>
        `<button type="button" class="asset-select ${a.id === selected?.id ? 'selected' : ''}" data-art-id="${a.id}">${thumb(a.asset_id, a.label)}<span>${h(a.label || assetRole(a))}<small>${h(assetRole(a))} · v${h(a.version || 1)}</small></span></button>`,
    )
    .join('');
}
async function previewPattern(stage, art, view, repeat, component) {
  stage.innerHTML = '<span class="spinner"></span>';
  if (!art) {
    stage.innerHTML = empty(
      '还没有可用图案',
      '任务生成母稿后，可在这里查看循环单元和实际尺度。',
      '',
      'image',
    );
    return;
  }
  const img = new Image();
  img.src = asset(art.asset_id);
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = () => reject(new Error('图案文件暂时无法读取。'));
  });
  if (!stage.isConnected) return;
  const canvas = document.createElement('canvas');
  const tileW = Math.max(0.1, Number(repeat.width_cm || 24)),
    tileH = Math.max(0.1, Number(repeat.height_cm || 24));
  const width = view === 'repeat' ? tileW * 3 : Number(component?.width_cm || tileW * 3),
    height = view === 'repeat' ? tileH * 3 : Number(component?.height_cm || tileH * 3);
  const ratio = Math.min(900 / width, 760 / height);
  canvas.width = Math.max(1, Math.round(width * ratio));
  canvas.height = Math.max(1, Math.round(height * ratio));
  canvas.setAttribute(
    'aria-label',
    view === 'repeat' ? '真实图案的 3×3 拼接预览' : '按物理尺度排列的组件平面预览',
  );
  const c = canvas.getContext('2d');
  c.fillStyle = '#fff';
  c.fillRect(0, 0, canvas.width, canvas.height);
  const w = tileW * ratio,
    hh = tileH * ratio;
  for (let y = -1; y < Math.ceil(canvas.height / hh) + 1; y++)
    for (let x = -1; x < Math.ceil(canvas.width / w) + 1; x++) {
      const dx = x * w;
      const dy = y * hh + (repeat.mode === 'half-drop' && Math.abs(x + 1) % 2 ? hh * 0.5 : 0);
      c.drawImage(img, dx, dy, w, hh);
    }
  stage.replaceChildren(canvas);
}

export async function studioPage(ctx, id) {
  const d = await api(`/api/designs/${id}`);
  const arts = arr(d.assets);
  const product = d.product;
  const job = d.job;
  const savedView = JSON.parse(sessionStorage.getItem(`studio-view:${id}`) || '{}');
  let current = selectedAsset(arts, ctx.query.get('asset_id') || savedView.asset_id);
  let displayed = current;
  let zoom = Number(savedView.zoom) || 100;
  let view =
    ctx.query.get('view') ||
    savedView.view ||
    (arts.some((a) => isSales(a.role)) ? 'product' : d.mode === 'repeat' ? 'tile' : 'master');
  const repeat = d.config?.repeat || { mode: 'basic', width_cm: 24, height_cm: 24 };
  const components = arr(d.sku?.components || d.config?.components);
  const componentPlans = arr(d.config?.component_plans);
  const hasRepeat = d.mode === 'repeat' || componentPlans.some((p) => p.mode === 'repeat');
  const targets = arr(ctx.config.targets).filter((x) => x.enabled !== false);
  return {
    title: '设计工作室',
    context: product,
    className: 'studio-page',
    html:
      heading(
        '02 / DESIGN STUDIO',
        product?.name || designTitle(d),
        `${modeName[d.mode] || d.mode} · ${d.sku?.label || 'SKU #' + d.sku_id} · 设计 #${id} / v${d.revision}`,
        link('/designs', '设计任务', 'button secondary', { product_id: d.product_id }) +
          (d.parent_id
            ? link('/studio/' + d.parent_id, '原版本 #' + d.parent_id, 'button secondary')
            : '') +
          button(`${icon('layers')}复制新版本`, 'fork-design') +
          (d.status === 'draft'
            ? link('/designs/new', '编辑任务书', 'button secondary', { edit_id: d.id })
            : '') +
          (d.status === 'draft'
            ? button(`${icon('spark')}启动设计`, 'start-design', 'primary')
            : button(
                `${icon('arrow')}用于刊登`,
                'design-to-listing',
                'primary',
                arts.some(
                  (a) =>
                    ['main', 'sale', 'sales', 'hero', 'mockup'].includes(a.role) ||
                    /^sales-/.test(a.role || ''),
                )
                  ? ''
                  : 'disabled',
              )),
      ) +
      `${d.stale ? notice('商品事实或未冻结任务的配置已更新。当前设计保留原输入版本；请更新任务书或建立新版本后处理受影响内容。', 'warning') : d.configuration_changed ? notice('当前配置已有新版，本任务继续使用冻结配置。复制新版本时可采用最新设置。') : ''}<div id="studio-job">${studioJob(job)}</div><div class="studio-layout"><aside class="studio-assets"><header>当前版本的成果</header><div class="studio-sidebody" id="studio-asset-list">${arts.length ? assetButtons(arts, current) : '<p class="muted small" style="line-height:1.9">设计任务开始后，母稿和成品会按步骤出现在这里。</p>'}</div><header>商品事实</header><div class="studio-sidebody">${metadata(
        [
          ['SKU', h(d.sku?.code || '—')],
          ['材质', h(d.sku?.facts?.material || '未知')],
          ['规格', h(d.sku?.facts?.size || d.sku?.label || '未知')],
          ['事实修订', 'v' + h(d.input_revision?.sku || d.revision)],
        ],
      )}${link(`/products/${d.product_id}`, '查看事实 →', 'text-link')}</div></aside><div><section class="canvas-shell"><div class="canvas-toolbar"><div class="canvas-tabs">${[
        ['product', '成品'],
        ['master', '母稿'],
        ['tile', '循环单元'],
        ['repeat', '3×3 拼接'],
        ['scale', '实尺度'],
      ]
        .filter(([key]) => hasRepeat || ['product', 'master'].includes(key))
        .map(
          ([key, label]) =>
            `<button type="button" data-view="${key}" class="${view === key ? 'active' : ''}">${label}</button>`,
        )
        .join(
          '',
        )}</div><div class="row"><label class="canvas-zoom-label"><span id="canvas-zoom-value">${zoom}%</span><input id="canvas-zoom" type="range" min="50" max="200" step="10" value="${zoom}" aria-label="画布缩放，100% 为适合画布"></label>${button('原尺寸 ↗', 'open-full', 'quiet small', current ? '' : 'disabled')}</div></div><div class="canvas-stage" id="design-canvas"></div><div class="canvas-caption"><span id="canvas-description">${h(current?.label || '设计成果预览')}</span><span id="canvas-size">${current?.width ? `${current.width} × ${current.height} px` : '等待真实成果'}</span></div><div class="canvas-actionbar">${button('修改当前销售图', 'revise-current-image', 'secondary small', 'disabled')}<span class="muted small" id="single-image-note">完整设计完成后，可分别修改销售图和详情图。</span></div></section><div class="studio-filmstrip" id="studio-filmstrip">${arts.map((a) => `<button class="film-frame ${a.id === current?.id ? 'selected' : ''}" type="button" data-art-id="${a.id}"><img src="${thumbnail(a.asset_id, 160)}" ${thumbnailFallback(a.asset_id)} alt="${h(a.label)}"><span>${h(a.label || assetRole(a))}</span></button>`).join('')}</div>${panel('设计任务与来源', `<div class="panel-body">${d.config?.image_revision ? `<details><summary class="text-link">查看原设计任务书</summary><p class="small subtle" style="white-space:pre-wrap;margin-top:12px">${h(d.brief)}</p></details>` : `<p class="small subtle" style="white-space:pre-wrap">${h(d.brief)}</p>`}<div class="section-heading"><h2>${d.config?.image_revision ? '本次单图修改' : '实际执行计划'}</h2></div>${configurationSourcesHtml(d.config)}${planHtml(d)}${arr(d.sources).length ? `<div class="row" style="margin-top:15px">${d.sources.map((s) => link(`/sources/${s.id}`, h(s.title), 'pill', { product_id: d.product_id })).join('')}</div>` : ''}<div class="row" style="margin-top:15px"><span class="muted small">设计修订 v${d.revision} · 创建于 ${date(d.created_at)}</span></div></div>`)}</div><aside class="studio-inspector"><header>设计参数与交付</header><form id="studio-config" data-track data-revision="${d.revision}"><section class="inspector-section" ${d.mode === 'supplier' && !components.length ? 'hidden' : ''}><h3>应用组件</h3>${select('当前预览组件', 'component_key', [{ value: '', label: '未选择组件' }, ...components.map((x) => ({ value: x.key, label: x.name }))], savedView.component_key || current?.meta?.component_key || '', 'data-transient')}${!components.length ? `<p style="margin-top:10px">实际组件尺寸待补。${link(`/products/${d.product_id}`, '完善规格 →', 'text-link')}</p>` : ''}</section>${
        d.mode === 'repeat'
          ? `<section class="inspector-section"><h3>循环花型</h3><div class="form-grid">${field('单元宽 / cm', 'repeat_width', repeat.width_cm, { type: 'number', min: 0.1, step: 'any' })}${field('单元高 / cm', 'repeat_height', repeat.height_cm, { type: 'number', min: 0.1, step: 'any' })}</div><div style="margin-top:13px">${select(
              '排列方式',
              'repeat_mode',
              [
                { value: 'basic', label: '规则平铺' },
                { value: 'half-drop', label: '半落位循环' },
              ],
              repeat.mode || 'basic',
            )}</div><p style="margin-top:11px">调整即时影响平面预览。保存后建立新版本；已完成成果保持原参数。</p></section>`
          : ''
      }${componentPlans.length ? `<section class="inspector-section"><h3>组件设计方案</h3>${componentPlanSummary(componentPlans)}</section>` : ''}<section class="inspector-section"><h3>输出方案</h3>${metadata(
        [
          ['创意路线', h(d.config?.route_count || 1) + ' 条'],
          ['销售 / 效果图', h(d.config?.sales_count || 0) + ' 张'],
          ['详情图', h(d.config?.detail_count || 0) + ' 张'],
          ['已保存成果', arts.length + ' 份'],
          ['出图数量来源', h(d.config?._sources?.sales_count?.source || '当前任务')],
          ['花型参数来源', h(d.config?._sources?.repeat?.source || '当前任务')],
        ],
      )}</section><section class="inspector-section"><div class="stack" style="gap:8px">${d.mode === 'repeat' ? submit('保存为新版本', 'layers') : ''}${d.mode === 'supplier' ? '<p class="muted small">供应商实物展示不作为平面印刷源；生产需要独立的原创母稿或循环单元。</p>' : link(`/production/${id}`, `${icon('download')}准备生产文件`, 'button secondary full')}${current ? `<a class="button quiet full" href="${asset(current.asset_id)}" target="_blank" id="download-current">${icon('image')}打开当前原图</a>` : ''}</div></section></form></aside></div>`,
    bind() {
      const form = $('#studio-config');
      const readRepeat = () => ({
        mode: $('[name=repeat_mode]', form)?.value || repeat.mode,
        width_cm: Number($('[name=repeat_width]', form)?.value || repeat.width_cm),
        height_cm: Number($('[name=repeat_height]', form)?.value || repeat.height_cm),
        offset: 0.5,
      });
      const remember = () =>
        sessionStorage.setItem(
          `studio-view:${id}`,
          JSON.stringify({
            view,
            zoom,
            asset_id: current?.id,
            component_key: $('[name=component_key]', form)?.value,
          }),
        );
      const applyZoom = () => {
        const stage = $('#design-canvas'),
          media = $('img,canvas', stage);
        $('#canvas-zoom-value').textContent = zoom + '%';
        if (!media) return;
        const width = media.naturalWidth || media.width,
          height = media.naturalHeight || media.height;
        if (!width || !height) return;
        const stageStyle = getComputedStyle(stage);
        const availableWidth = Math.max(
          1,
          stage.clientWidth -
            parseFloat(stageStyle.paddingLeft) -
            parseFloat(stageStyle.paddingRight),
        );
        const availableHeight = Math.max(
          1,
          stage.clientHeight -
            parseFloat(stageStyle.paddingTop) -
            parseFloat(stageStyle.paddingBottom),
        );
        const fit = Math.min(availableWidth / width, availableHeight / height);
        media.style.width = Math.max(1, (width * fit * zoom) / 100) + 'px';
        media.style.height = 'auto';
        media.style.maxWidth = 'none';
        media.style.maxHeight = 'none';
        media.style.margin = 'auto';
        media.style.flexShrink = '0';
        stage.style.alignItems = 'flex-start';
        stage.style.justifyContent = 'flex-start';
      };
      const canvasResize = new ResizeObserver(applyZoom);
      canvasResize.observe($('#design-canvas'));
      $('#canvas-zoom').addEventListener('input', (e) => {
        zoom = Number(e.target.value);
        remember();
        applyZoom();
      });
      const renderCanvas = async () => {
        remember();
        const stage = $('#design-canvas');
        $$('[data-view]').forEach((el) => el.classList.toggle('active', el.dataset.view === view));
        $$('[data-art-id]').forEach((el) =>
          el.classList.toggle('selected', String(current?.id) === el.dataset.artId),
        );
        const componentKey = $('[name=component_key]', form).value;
        const scoped = componentKey
          ? arts.filter((a) => a.meta?.component_key === componentKey)
          : arts.filter((a) => !a.meta?.component_key);
        let shown = current;
        if (['tile', 'repeat', 'scale'].includes(view))
          shown = scoped.find((a) => assetKind(a) === 'tile');
        else if (view === 'master')
          shown =
            scoped.find((a) => assetKind(a) === 'master') ||
            scoped.find((a) => assetKind(a) === 'tile');
        else if (view === 'product' && !isSales(shown?.role))
          shown = arts.find((a) => isSales(a.role));
        displayed = shown;
        const reviseButton = $('[data-action=revise-current-image]');
        if (reviseButton)
          reviseButton.disabled =
            d.status !== 'completed' || d.stale || !canReviseRole(shown?.role);
        const revisionNote = $('#single-image-note');
        if (revisionNote)
          revisionNote.textContent = d.stale
            ? '商品事实已变化，请复制采用最新事实的完整设计版本。'
            : d.status !== 'completed'
              ? '完整图组尚未完成。等待任务完成后可逐张修改；母稿和循环单元使用复制新版本。'
              : !canReviseRole(shown?.role)
                ? '切换到具体销售图或详情图可单张修改；母稿和循环单元需复制完整新版本。'
                : '修改会创建新版本，仅替换当前角色；其他图片沿用，原刊登仍保持原绑定。';
        const download = $('#download-current');
        if (download && shown) download.href = asset(shown.asset_id);
        $('#canvas-description').textContent =
          view === 'scale'
            ? '按真实尺度排列的平面可印区域'
            : view === 'repeat'
              ? '真实图案 3×3 拼接预览'
              : shown?.label || '尚未生成对应成果';
        $('#canvas-size').textContent = shown?.width
          ? `${shown.width} × ${shown.height} px`
          : '等待真实成果';
        if (!shown) {
          stage.innerHTML = empty(
            view === 'product' ? '销售效果图尚未生成' : '当前还没有设计成果',
            d.status === 'draft'
              ? '启动设计后，真实母稿和输出图会按阶段保存。'
              : '已完成的步骤会保留，未完成部分可在任务中查看原因。',
            '',
            'image',
          );
          return;
        }
        if (view === 'repeat' || view === 'scale') {
          const component = components.find((x) => x.key === $('[name=component_key]', form).value);
          if (view === 'scale' && (!component?.width_cm || !component?.height_cm)) {
            stage.innerHTML = empty(
              '先选择有实际尺寸的组件',
              '补全商品组件尺寸后，才能判断实际花型密度。',
              link(`/products/${d.product_id}`, '完善实际尺寸'),
            );
            return;
          }
          try {
            await previewPattern(
              stage,
              shown,
              view,
              shown.meta?.repeat ||
                componentPlans.find((p) => p.component_key === component?.key)?.repeat ||
                readRepeat(),
              component,
            );
            applyZoom();
          } catch (error) {
            stage.innerHTML = errorBox(error);
          }
        } else {
          stage.innerHTML = `<img src="${asset(shown.asset_id)}" alt="${h(shown.label || assetRole(shown))}">`;
          $('img', stage).addEventListener('load', applyZoom);
        }
      };
      renderCanvas();
      $$('[data-view]').forEach((el) =>
        el.addEventListener('click', () => {
          view = el.dataset.view;
          renderCanvas();
        }),
      );
      $$('[data-art-id]').forEach((el) =>
        el.addEventListener('click', () => {
          current = arts.find((a) => String(a.id) === el.dataset.artId);
          if (current?.meta?.component_key)
            $('[name=component_key]', form).value = current.meta.component_key;
          view = isSales(current?.role)
            ? 'product'
            : assetKind(current) === 'tile'
              ? 'tile'
              : assetKind(current) === 'repeat-preview'
                ? 'repeat'
                : 'master';
          renderCanvas();
          const download = $('#download-current');
          if (download) download.href = asset(current.asset_id);
        }),
      );
      form.addEventListener('input', renderCanvas);
      form.addEventListener('change', renderCanvas);
      form.addEventListener('draftrestored', renderCanvas);
      bindAction('revise-current-image', () => {
        const selected = displayed;
        if (d.status !== 'completed' || d.stale || !canReviseRole(selected?.role)) {
          ctx.toast('只有完整设计中的销售图或详情图支持单张修改。', 'error');
          return;
        }
        const requestIntent = intent('revise-image');
        modal(
          '修改当前销售图',
          `<form id="revise-image-form"><div class="revision-image-context">${thumb(selected.asset_id, selected.label, 'large')}<div><h3>${h(selected.label || assetRole(selected))}</h3><p class="muted small">设计 #${d.id} / v${d.revision} · ${h(selected.role)}</p></div></div>${field('这张图需要怎样修改', 'instruction', '', { textarea: true, rows: 6, required: true, placeholder: '例如：保留商品、配色和版式，将左上文字调整为……；或说明需要改变的背景、视角和留白。' })}${notice('本次只重新生成当前角色的一张图，新版本沿用其余已保存成果。原设计和已有刊登保持原样；新图完成后再决定是否用于刊登。')}<div class="form-actions">${submit('创建单图修改任务', 'spark')}</div></form>`,
          (root) =>
            bindForm(
              '#revise-image-form',
              async (form, v) => {
                if (!v.instruction.trim()) throw new Error('请填写具体修改要求。');
                const result = await api(`/api/designs/${id}/revise-image`, {
                  method: 'POST',
                  body: {
                    revision: d.revision,
                    design_asset_id: selected.id,
                    instruction: v.instruction.trim(),
                    intent_key: requestIntent,
                  },
                });
                if (!result.design_id)
                  throw new Error('任务已返回但缺少新设计标识，请到任务中心核对。');
                closeModal();
                ctx.saved(null, '单图修改任务已建立，原设计继续保留');
                ctx.go('/studio/' + result.design_id);
              },
              root,
            ),
        );
      });
      bindAction('open-full', () => {
        if (displayed) window.open(asset(displayed.asset_id), '_blank', 'noopener');
      });
      bindAction('start-design', async () => {
        await api(`/api/designs/${id}/start`, {
          method: 'POST',
          body: { intent_key: intentFor(`start-design-${id}`) },
        });
        ctx.toast('设计任务已启动');
        ctx.reload();
      });
      bindReconcile(ctx, job);
      bindAction('resume-design', async () => {
        await api(`/api/jobs/${job.id}/resume`, { method: 'POST' });
        ctx.reload();
      });
      bindAction('cancel-design', async () => {
        await api(`/api/jobs/${job.id}/cancel`, { method: 'POST' });
        ctx.toast('已停止后续步骤；已提交请求仍保留结果');
        ctx.reload();
      });
      const fork = () =>
        modal(
          '建立新的设计版本',
          `<form id="fork-form">${field('修改说明 / 新设计方向', 'brief', d.brief, { textarea: true, rows: 7, required: true })}${displayed ? check('沿用当前成果作为修改参考', 'use_current_reference', displayed.asset_id, false) : ''}<p class="muted small" style="margin-top:13px">默认以 SKU 原图或原创任务重新建立母稿。仅勾选时引用当前成果；原设计和历史成果继续保留。这会建立完整设计任务，不代表仅重做某一张图。</p><div class="form-actions">${submit('建立新版本', 'layers')}</div></form>`,
          (root) =>
            bindForm(
              '#fork-form',
              async (_f, v) => {
                const result = await api(`/api/designs/${id}/fork`, {
                  method: 'POST',
                  body: {
                    brief: v.brief,
                    config: {
                      ...(d.config?._overrides || d.config),
                      repeat: readRepeat(),
                      reference_asset_id: { state: 'clear' },
                    },
                    ...(v.use_current_reference
                      ? { asset_id: Number(v.use_current_reference) }
                      : {}),
                  },
                });
                closeModal();
                ctx.go(`/studio/${result.id}`);
              },
              root,
            ),
        );
      bindAction('fork-design', fork);
      bindForm('#studio-config', async (form) => {
        const result = await api(`/api/designs/${id}/fork`, {
          method: 'POST',
          body: {
            brief: d.brief,
            config: {
              ...(d.config?._overrides || d.config),
              repeat: readRepeat(),
              reference_asset_id: { state: 'clear' },
            },
          },
        });
        ctx.saved(form, '已建立采用新参数的设计版本');
        ctx.go(`/studio/${result.id}`);
      });
      bindAction('design-to-listing', () =>
        modal(
          '把设计成果用于刊登',
          `<form id="design-listing-form">${select('目标店铺与市场', 'target_id', [{ value: '', label: '请选择' }, ...targets.map((t) => ({ value: t.id, label: `${t.name} · ${t.platform} / ${t.site}` }))], '')}${!targets.length ? notice(`先配置真实目标店铺。${link('/settings/targets', '店铺配置 →', 'text-link')}`) : ''}<p class="muted small" style="margin-top:15px">保存时绑定当前 SKU、设计版本和实际图像角色，刊登可追溯回这份设计。</p><div class="form-actions">${submit('建立刊登并继续', 'arrow')}</div></form>`,
          (root) =>
            bindForm(
              '#design-listing-form',
              async (form, v) => {
                if (!v.target_id) throw new Error('请选择目标店铺。');
                try {
                  const result = await api(`/api/designs/${id}/listing`, {
                    method: 'POST',
                    body: { target_id: Number(v.target_id) },
                  });
                  closeModal();
                  ctx.go(`/listings/${result.id}`);
                } catch (error) {
                  if (error.status === 409 && error.fields?.listing_id) {
                    const existing = error.fields.listing_id;
                    form.insertAdjacentHTML(
                      'beforeend',
                      notice(
                        `此 SKU 已有对应店铺刊登。${link(`/listings/${existing}`, '打开现有刊登并更换设计 →', 'text-link')}`,
                        'warning',
                      ),
                    );
                    return;
                  }
                  throw error;
                }
              },
              root,
            ),
        ),
      );
      const stopRefresh =
        job && ['queued', 'running'].includes(job.status)
          ? ctx.bindRefresh(async () => {
              const fresh = await api(`/api/designs/${id}`);
              if (
                fresh.assets?.length !== arts.length ||
                fresh.job?.status !== job.status ||
                fresh.job?.stage !== job.stage
              ) {
                ctx.reload();
                return;
              }
            }, 3500)
          : null;
      return () => {
        canvasResize.disconnect();
        stopRefresh?.();
      };
    },
  };
}

export async function artworksPage(ctx) {
  const data = await api('/api/artworks?' + ctx.query.toString());
  const products = await api('/api/products?page_size=100');
  const product = arr(products.items).find((p) => String(p.id) === ctx.query.get('product_id'));
  return {
    title: '设计成果',
    context: product,
    html:
      heading(
        '02 / ASSET LIBRARY',
        '每一份成果，都有来处。',
        '按商品、用途和版本查看真实母稿与成品，继续生产或刊登。',
        link('/designs', '查看设计任务'),
      ) +
      `<form class="toolbar" id="asset-filter"><div class="filters">${select('商品', 'product_id', productOptions(products.items), ctx.query.get('product_id') || '')}${select('资产用途', 'role', [{ value: '', label: '全部用途' }, ...Object.entries(roleName).map(([value, label]) => ({ value, label }))], ctx.query.get('role') || '')}<button class="button secondary" type="submit">筛选</button></div><span class="muted small">${data.total || 0} 份成果</span></form><div style="height:20px"></div>${arr(data.items).length ? `<div class="asset-grid">${data.items.map((a) => `<article class="asset-card"><a class="asset-visual" href="${href('/studio/' + a.design_id, { asset_id: a.id })}"><img src="${thumbnail(a.asset_id, 640)}" ${thumbnailFallback(a.asset_id)} alt="${h(a.label)}" loading="lazy">${badge('draft', assetRole(a))}</a><div class="asset-body"><h3>${h(a.label || assetRole(a))}</h3><p>${h(a.product_name || '商品 #' + a.product_id)} · v${h(a.version || 1)}</p><p>${a.width || '—'} × ${a.height || '—'} px · ${date(a.created_at)}</p><div class="asset-actions">${link('/studio/' + a.design_id, '打开工作室 →', 'text-link', { asset_id: a.id })}<a href="${asset(a.asset_id)}" class="text-link" target="_blank">原图 ↗</a></div></div></article>`).join('')}</div>` : panel('真实设计资产', empty('还没有保存的设计成果', '设计任务生成的母稿、销售图和详情图会按版本出现在这里。', link('/designs', '进入设计任务'), 'image'))}${pagination(data, '/artworks', Object.fromEntries(ctx.query))}`,
    bind() {
      bindForm('#asset-filter', async (_f, v) => ctx.go('/artworks', v));
    },
  };
}

export { productionPage } from './production.js';

export async function jobsPage(ctx) {
  const data = await api('/api/jobs?' + ctx.query.toString());
  const selected = ctx.query.get('job_id');
  const job = selected ? await api(`/api/jobs/${selected}`) : null;
  const routeFor = (j) =>
    ['design', 'image_revision'].includes(j.kind)
      ? `/studio/${j.entity_id}`
      : j.kind === 'copy'
        ? `/listings/${j.entity_id}`
        : j.kind === 'research'
          ? '/research'
          : j.kind === 'backup'
            ? '/settings/backups'
            : null;
  return {
    title: '任务中心',
    html:
      heading(
        'WORKSPACE / JOBS',
        '任务中心',
        '每个任务保留输入、步骤与请求状态。成功成果继续使用，未知结果先核对原请求。',
      ) +
      `<form id="job-filter" class="toolbar"><div class="filters">${select('状态', 'status', [{ value: '', label: '全部状态' }, ...['queued', 'running', 'succeeded', 'failed', 'unknown', 'cancelled'].map((value) => ({ value, label: { queued: '等待执行', running: '进行中', succeeded: '已完成', failed: '失败', unknown: '结果待核对', cancelled: '已取消' }[value] }))], ctx.query.get('status') || '')}<button class="button secondary" type="submit">筛选</button></div></form>` +
      panel(
        '任务记录',
        table(
          ['任务', '当前步骤', '状态', '更新时间', '操作'],
          arr(data.items).map((j) => [
            `<strong>#${j.id} · ${h({ design: '设计生成', image_revision: '单图修改', copy: '刊登文案', research: '来源研究', backup: '工作资料备份' }[j.kind] || j.kind)}</strong>`,
            h(j.stage),
            badge(j.status),
            date(j.updated_at || j.created_at),
            link('/jobs', '查看步骤 →', 'text-link', { job_id: j.id }),
          ]),
          '暂无业务任务',
        ) + pagination(data, '/jobs', Object.fromEntries(ctx.query)),
      ) +
      (job
        ? `<div style="height:22px"></div>${panel(`任务 #${job.id} · 步骤与恢复`, `<div class="panel-body"><div class="row-between">${badge(job.status)}${routeFor(job) ? link(routeFor(job), '返回业务对象 →', 'text-link', job.kind === 'research' ? { job_id: job.id } : {}) : ''}</div>${progress(job)}${jobErrorNotice(job)}${job.status === 'unknown' ? notice('请求已发出但结果尚未核对。当前任务禁止自动重发，避免重复生成和重复消费。', 'warning') : ''}${steps(job)}<div class="form-actions">${job.status === 'unknown' ? button('核对原请求', 'reconcile-job', 'primary') : ''}${job.status === 'failed' ? button('继续未完成步骤', 'resume-job', 'primary') : ''}${['queued', 'running'].includes(job.status) ? button('停止领取后续步骤', 'cancel-job', 'secondary') : ''}</div></div>`)}`
        : ''),
    bind() {
      bindForm('#job-filter', async (_f, v) => ctx.go('/jobs', v));
      bindReconcile(ctx, job);
      bindAction('resume-job', async () => {
        await api(`/api/jobs/${job.id}/resume`, { method: 'POST' });
        ctx.reload();
      });
      bindAction('cancel-job', async () => {
        await api(`/api/jobs/${job.id}/cancel`, { method: 'POST' });
        ctx.reload();
      });
      if (job && ['queued', 'running'].includes(job.status))
        return ctx.bindRefresh(() => ctx.reload(), 5000);
    },
  };
}
