import {
  $,
  $$,
  h,
  arr,
  api,
  select,
  field,
  button,
  submit,
  modal,
  closeModal,
  bindForm,
  bindAction,
  notice,
  metadata,
  link,
  imageRoleLabel,
} from './ui.js';

const keys = [
  ['route_count', '创意路线数'],
  ['sales_count', '销售 / 效果图数'],
  ['detail_count', '详情图数'],
];
const sourceLabel = (source) => source?.source || '系统初始值';
const stateLabel = (state) =>
  ({ clear: '明确清空', not_applicable: '不适用', inherit: '继承', value: '有效值' })[state] ||
  '有效值';
export function configurationSourcesHtml(config = {}) {
  const labels = {
    route_count: '创意路线数',
    sales_count: '销售图数量',
    detail_count: '详情图数量',
    repeat: '循环单元参数',
    style: '风格与场景',
    palette: '配色方向',
    scene: '场景方向',
    output_size: '图像规格',
    components: '真实组件',
    component_plans: '组件设计方案',
    text_model_id: '文本模型',
    image_model_id: '图片模型',
    reference_asset_id: '修改参考图',
    selected_route: '采用路线',
  };
  const sources = Object.entries(config._sources || {});
  return sources.length
    ? `<details class="configuration-source-details"><summary>查看冻结参数的来源与覆盖范围</summary>${metadata(sources.map(([key, source]) => [labels[key] || '其他设计参数', h(sourceLabel(source)) + ' · ' + h(stateLabel(source.state))]))}</details>`
    : '<p class="muted small">此版本没有保存逐项参数来源。</p>';
}
export function attachInheritance(ctx, form, product, initialOverrides = null) {
  let resolved = null,
    sequence = 0,
    initialApplied = false;
  const sourcesBox = document.createElement('div');
  sourcesBox.className = 'configuration-sources';
  const output = $('[name=route_count]', form)?.closest('.form-section');
  if (output) {
    output.append(sourcesBox);
    sourcesBox.innerHTML =
      '<div class="row-between" style="margin-top:15px"><span class="muted small" id="configuration-source-caption">正在读取参数来源</span>' +
      button('管理商品 / SKU 设计偏好', 'design-preferences', 'quiet small') +
      '</div>';
    sourcesBox.insertAdjacentHTML(
      'beforeend',
      `<div class="form-section configuration-style"><div class="row-between"><h3>风格与场景偏好</h3>${button('恢复风格继承', 'restore-style', 'quiet small')}</div><div class="form-grid">${select(
        '本次取值方式',
        'task_style_state',
        [
          { value: 'inherit', label: '继承有效偏好' },
          { value: 'value', label: '本次任务指定' },
          { value: 'clear', label: '明确清空' },
          { value: 'not_applicable', label: '本次不适用' },
        ],
        'inherit',
      )}${field('风格与场景', 'task_style', '', { textarea: true, rows: 3, help: '只约束创意表达，不改变真实商品身份、材质或尺寸。' })}</div><small class="parameter-source" data-style-source>正在读取风格来源</small></div>`,
    );
  }
  for (const [key] of keys) {
    const input = $(`[name=${key}]`, form);
    if (!input) continue;
    const row = document.createElement('small');
    row.className = 'parameter-source';
    row.innerHTML = `<span data-parameter-source="${key}">读取来源中</span> <button type="button" class="text-link small" data-restore-parameter="${key}">恢复继承</button>`;
    input.closest('.field').append(row);
    input.addEventListener('input', () => showSources());
  }
  const repeatFields = ['repeat_mode', 'repeat_width', 'repeat_height'];
  const repeatHost = $('#repeat-config', form);
  if (repeatHost)
    repeatHost.insertAdjacentHTML(
      'beforeend',
      `<div class="parameter-source"><span data-repeat-source>正在读取循环参数来源</span>${button('恢复循环参数继承', 'restore-repeat', 'quiet small')}</div>`,
    );
  repeatFields.forEach((name) =>
    $(`[name=${name}]`, form)?.addEventListener('input', () => showSources()),
  );
  const values = () =>
    Object.fromEntries(keys.map(([key]) => [key, Number($(`[name=${key}]`, form)?.value || 0)]));
  const repeat = () => ({
    mode: $('[name=repeat_mode]', form)?.value || 'basic',
    width_cm: Number($('[name=repeat_width]', form)?.value || 24),
    height_cm: Number($('[name=repeat_height]', form)?.value || 24),
    offset: 0.5,
  });
  function showSources() {
    for (const [key] of keys) {
      const node = $(`[data-parameter-source=${key}]`, form);
      if (node)
        node.textContent =
          resolved && Number($(`[name=${key}]`, form).value) === Number(resolved.effective[key])
            ? '继承：' + sourceLabel(resolved.sources[key])
            : '本次任务覆盖';
    }
    const caption = $('#configuration-source-caption', form);
    if (caption)
      caption.textContent = resolved
        ? '修改只影响当前任务；可恢复继承或保存为常用偏好。'
        : '先选择实际 SKU，才能解析适用的参数。';
    const inherited = resolved?.effective?.repeat;
    const actual = repeat();
    const sameRepeat =
      inherited &&
      actual.mode === inherited.mode &&
      actual.width_cm === Number(inherited.width_cm ?? Number(inherited.unit_width_mm) / 10) &&
      actual.height_cm === Number(inherited.height_cm ?? Number(inherited.unit_height_mm) / 10);
    const repeatNote = $('[data-repeat-source]', form);
    if (repeatNote)
      repeatNote.textContent = sameRepeat
        ? '继承：' + sourceLabel(resolved.sources.repeat)
        : inherited
          ? '当前任务覆盖循环参数；上级来自 ' + sourceLabel(resolved.sources.repeat)
          : '上级未设置循环方案；当前尺寸为本次明确参数。';
    const restoreRepeat = $('[data-action=restore-repeat]', form);
    if (restoreRepeat) restoreRepeat.disabled = !inherited;
    const styleState = $('[name=task_style_state]', form)?.value || 'inherit';
    const style = $('[name=task_style]', form);
    if (style) style.readOnly = styleState !== 'value';
    const styleNote = $('[data-style-source]', form);
    if (styleNote)
      styleNote.textContent =
        styleState === 'inherit'
          ? resolved?.sources?.style
            ? `继承：${sourceLabel(resolved.sources.style)} · ${stateLabel(resolved.sources.style.state)}`
            : '上级未指定风格，保留未知，不自动添加风格要求。'
          : `本次任务覆盖 · ${stateLabel(styleState)}`;
  }
  const restoreStyle = () => {
    $('[name=task_style_state]', form).value = 'inherit';
    $('[name=task_style]', form).value =
      typeof resolved?.effective?.style === 'string' ? resolved.effective.style : '';
    form.dispatchEvent(new Event('input', { bubbles: true }));
    showSources();
  };
  bindAction('restore-style', restoreStyle, form);
  bindAction(
    'restore-repeat',
    () => {
      const value = resolved?.effective?.repeat;
      if (!value) return;
      $('[name=repeat_mode]', form).value = value.mode || 'basic';
      $('[name=repeat_width]', form).value = value.width_cm ?? Number(value.unit_width_mm) / 10;
      $('[name=repeat_height]', form).value = value.height_cm ?? Number(value.unit_height_mm) / 10;
      form.dispatchEvent(new Event('input', { bubbles: true }));
      showSources();
    },
    form,
  );
  $('[name=task_style_state]', form)?.addEventListener('change', () => {
    if ($('[name=task_style_state]', form).value === 'inherit') restoreStyle();
    else showSources();
  });
  form.addEventListener('draftrestored', showSources);
  async function load(force = false) {
    const sku = $('[name=sku_id]', form)?.value;
    if (!sku) {
      resolved = null;
      showSources();
      return;
    }
    const token = ++sequence;
    const params = new URLSearchParams({ mode: $('[name=mode]', form)?.value || 'large' });
    const preset = $('[name=preset_id]', form)?.value;
    if (preset) params.set('preset_id', preset);
    try {
      const next = await api(`/api/skus/${sku}/design-configuration?${params}`);
      if (token !== sequence || !form.isConnected) return;
      resolved = next;
      const hasDraft = sessionStorage.getItem(
        `woven-draft:${location.hash.split('&focus=')[0]}:${form.id}`,
      );
      if (force || !hasDraft) {
        $('[name=task_style_state]', form).value = 'inherit';
        $('[name=task_style]', form).value =
          typeof next.effective.style === 'string' ? next.effective.style : '';
        for (const [key] of keys)
          if (next.effective[key] !== undefined)
            $(`[name=${key}]`, form).value = next.effective[key];
        if (next.effective.repeat) {
          const r = next.effective.repeat;
          $('[name=repeat_mode]', form).value = r.mode || 'basic';
          $('[name=repeat_width]', form).value = r.width_cm || 24;
          $('[name=repeat_height]', form).value = r.height_cm || 24;
        }
        if (!force && !initialApplied && initialOverrides) {
          const originalStyle = initialOverrides.style;
          if (originalStyle !== undefined) {
            $('[name=task_style_state]', form).value =
              typeof originalStyle === 'object' && originalStyle
                ? originalStyle.state || 'value'
                : 'value';
            $('[name=task_style]', form).value =
              typeof originalStyle === 'string' ? originalStyle : originalStyle?.value || '';
          }
          for (const [key] of keys) {
            const raw = initialOverrides[key];
            const value =
              raw && typeof raw === 'object'
                ? raw.state === 'value'
                  ? raw.value
                  : undefined
                : raw;
            if (value !== undefined) $(`[name=${key}]`, form).value = value;
          }
          const raw = initialOverrides.repeat;
          const r = raw?.state === 'value' ? raw.value : raw?.state ? null : raw;
          if (r) {
            $('[name=repeat_mode]', form).value = r.mode || 'basic';
            $('[name=repeat_width]', form).value = r.width_cm || Number(r.unit_width_mm) / 10 || 24;
            $('[name=repeat_height]', form).value =
              r.height_cm || Number(r.unit_height_mm) / 10 || 24;
          }
          initialApplied = true;
        }
      }
      showSources();
    } catch (error) {
      const caption = $('#configuration-source-caption', form);
      if (caption) caption.textContent = '参数来源读取失败：' + error.message;
    }
  }
  $$('[data-restore-parameter]', form).forEach((el) =>
    el.addEventListener('click', () => {
      const key = el.dataset.restoreParameter;
      if (resolved) {
        $(`[name=${key}]`, form).value = resolved.effective[key];
        form.dispatchEvent(new Event('input', { bubbles: true }));
        showSources();
      }
    }),
  );
  for (const name of ['sku_id', 'mode', 'preset_id'])
    $(`[name=${name}]`, form)?.addEventListener('change', () => load(true));
  bindAction(
    'design-preferences',
    async () => {
      const sid = Number($('[name=sku_id]', form)?.value);
      if (!product?.id || !sid) {
        ctx.toast('先选择商品与实际 SKU。', 'error');
        return;
      }
      await editPreferences(ctx, product.id, sid, () => load(true));
    },
    form,
  );
  load();
  return () => {
    const result = {};
    const styleState = $('[name=task_style_state]', form)?.value || 'inherit';
    result.style =
      styleState === 'value'
        ? { state: 'value', value: $('[name=task_style]', form).value }
        : { state: styleState };
    const actual = values();
    for (const [key] of keys) {
      if (resolved && Number(actual[key]) === Number(resolved.effective[key]))
        result[key] = { state: 'inherit' };
      else result[key] = actual[key];
    }
    const r = repeat(),
      inherited = resolved?.effective?.repeat;
    result.repeat =
      inherited &&
      r.mode === inherited.mode &&
      r.width_cm === Number(inherited.width_cm) &&
      r.height_cm === Number(inherited.height_cm)
        ? { state: 'inherit' }
        : r;
    return result;
  };
}

async function editPreferences(ctx, productId, skuId, onSaved) {
  let currentScope = 'sku';
  let record = await api(`/api/preferences/sku/${skuId}`);
  const render = () => {
    const values = record.value || {};
    return `<form id="design-preferences-form"><div class="form-grid">${select(
      '应用范围',
      'scope',
      [
        { value: 'sku', label: '当前实际 SKU 的后续任务' },
        { value: 'product', label: '当前商品的后续任务' },
      ],
      currentScope,
    )}<div>${notice('保存偏好不修改真实商品属性，也不改写正在执行的设计。')}</div>${keys
      .map(([key, label]) => {
        const value = values[key];
        const state = value === undefined || value?.state === 'inherit' ? 'inherit' : 'value';
        const number = typeof value === 'object' ? value?.value : value;
        return `<div class="form-grid">${select(
          label + ' · 取值方式',
          'state_' + key,
          [
            { value: 'inherit', label: '继承上级方案' },
            { value: 'value', label: '使用此范围指定值' },
          ],
          state,
        )}${field(label, key, number ?? '', { type: 'number', min: key === 'detail_count' ? 0 : 1, max: key === 'route_count' ? 5 : key === 'sales_count' ? 12 : 3 })}</div>`;
      })
      .join('')}${select(
      '风格偏好 · 取值方式',
      'style_state',
      [
        { value: 'inherit', label: '继承上级方案' },
        { value: 'value', label: '指定风格偏好' },
        { value: 'clear', label: '明确清空风格' },
        { value: 'not_applicable', label: '此范围不适用' },
      ],
      values.style?.state || (typeof values.style === 'string' ? 'value' : 'inherit'),
    )}${field('风格与场景', 'style', typeof values.style === 'object' ? values.style.value || '' : values.style || '', { textarea: true, rows: 3 })}</div><div class="form-actions"><span class="muted small">偏好修订 v${record.revision}</span>${submit('保存偏好')}</div></form>`;
  };
  const dialog = modal('设计偏好与继承', render());
  const bind = () => {
    const form = $('#design-preferences-form', dialog);
    $('[name=scope]', form).addEventListener('change', async (e) => {
      try {
        currentScope = e.target.value;
        record = await api(
          `/api/preferences/${currentScope}/${currentScope === 'sku' ? skuId : productId}`,
        );
        $('.modal-content', dialog).innerHTML = render();
        bind();
      } catch (error) {
        ctx.toast(error.message, 'error');
      }
    });
    bindForm(
      '#design-preferences-form',
      async (_form, v) => {
        const value = { ...record.value };
        for (const [key] of keys)
          value[key] =
            v['state_' + key] === 'inherit'
              ? { state: 'inherit' }
              : { state: 'value', value: Number(v[key]) };
        value.style =
          v.style_state === 'value' ? { state: 'value', value: v.style } : { state: v.style_state };
        await api(
          `/api/preferences/${currentScope}/${currentScope === 'sku' ? skuId : productId}`,
          { method: 'PUT', body: { revision: record.revision, value } },
        );
        closeModal();
        ctx.toast('设计偏好已保存，后续任务按新规则继承');
        onSaved();
      },
      dialog,
    );
  };
  bind();
}

function paletteHtml(palette) {
  const colors = [],
    notes = [];
  const roleNames = {
    background: '背景色',
    primary: '主色',
    secondary: '辅助色',
    accent: '点缀色',
    motifs: '图案配色',
    foreground: '前景色',
    base: '底色',
    highlights: '强调色',
    rule: '配色规则',
    notes: '配色说明',
    description: '配色说明',
  };
  const validHex = (value) =>
    typeof value === 'string' && /^#[0-9a-f]{3}(?:[0-9a-f]{3})?$/i.test(value.trim())
      ? value.trim().toUpperCase()
      : null;
  const visit = (value, context = '', key = '') => {
    if (value === null || value === undefined) return;
    if (Array.isArray(value)) {
      value.forEach((item) => visit(item, context, key));
      return;
    }
    if (typeof value === 'object') {
      const hex = validHex(value.hex || value.hex_code || value.color || value.value);
      if (hex || value.name || value.label) {
        colors.push({ name: String(value.name || value.label || context || hex), hex });
        for (const noteKey of ['rule', 'notes', 'description'])
          if (typeof value[noteKey] === 'string') notes.push(value[noteKey]);
        if (hex) return;
      }
      for (const [childKey, child] of Object.entries(value)) {
        if (['name', 'label'].includes(childKey)) continue;
        visit(child, roleNames[childKey] || childKey, childKey);
      }
      return;
    }
    const text = String(value),
      hex = validHex(text);
    if (['rule', 'notes', 'description'].includes(key)) notes.push(text);
    else colors.push({ name: hex ? context || hex : context ? context + '：' + text : text, hex });
  };
  visit(palette);
  return `<div class="route-palette" aria-label="本路线配色">${colors.map((color) => `<div class="route-color">${color.hex ? `<span class="route-color-swatch" style="background:${color.hex}" aria-hidden="true"></span>` : ''}<span><strong>${h(color.name)}</strong>${color.hex ? `<small>${h(color.hex)}</small>` : ''}</span></div>`).join('')}</div>${[...new Set(notes)].map((note) => `<p class="muted small">${h(note)}</p>`).join('')}`;
}

export function planHtml(design) {
  const revision = design.config?.image_revision;
  if (revision) {
    const originalId = revision.source_design_id || design.parent_id;
    return `<section class="image-revision-brief"><h3>修改目标 · ${h(imageRoleLabel(revision.role))}</h3><p class="small subtle" style="white-space:pre-wrap;margin:14px 0">${h(revision.instruction || '此版本未记录修改要求')}</p>${notice('仅修改所选图片；其余资产沿用原版本。原设计继续保留，已有刊登不会自动改绑。')}${originalId ? link('/studio/' + originalId, '查看原设计 #' + originalId, 'text-link', { asset_id: revision.source_design_asset_id }) : ''}<p class="muted small" style="margin-top:12px">${design.status === 'completed' ? '本次单图修改已完成，没有另外生成整套创意路线或图组计划。' : '本次只执行所选图片的修改步骤，进度与结果见当前任务。'}</p></section>`;
  }
  const result = design.job?.result || {},
    steps = arr(design.job?.steps);
  const routeData = steps.find((s) => s.key === 'routes')?.result || result.routes || {};
  const imageData = steps.find((s) => s.key === 'planning')?.result || result.plan || {};
  const routes = arr(routeData.routes),
    shots = arr(imageData.pages || imageData.shots);
  const selected = Number(design.config?.selected_route ?? routeData.recommended_index ?? 0);
  let body = '';
  if (routes.length)
    body += `<div class="three-column">${routes.map((r, i) => `<section class="plan-route ${selected === i ? 'selected' : ''}"><span class="pill">路线 ${i + 1}${selected === i ? ' · 当前采用' : ''}</span><h3>${h(r.name || r.title || r.theme || '创意方向')}</h3><p>${h(r.story || r.description || r.concept || '')}</p>${r.composition ? `<p><strong>构图</strong> ${h(r.composition)}</p>` : ''}${r.palette ? paletteHtml(r.palette) : ''}</section>`).join('')}</div>`;
  if (shots.length)
    body += `<ol class="step-list">${shots.map((s, i) => `<li><span class="step-number">${i + 1}</span><div><strong>${h(s.title || s.name || { main: '销售主图' }[s.role] || (/detail/.test(s.role) ? '完整详情图' : '销售图 ' + (i + 1)))}</strong>${s.copy ? `<p class="small subtle">${h(typeof s.copy === 'string' ? s.copy : JSON.stringify(s.copy))}</p>` : ''}<span>${h(s.visual || s.description || s.purpose || s.prompt || '')}</span></div></li>`).join('')}</ol>`;
  return (
    body ||
    `<p class="muted small">${design.job ? '创意与图组计划将随实际任务步骤保存；尚未取得计划时不展示虚构方案。' : '启动任务后生成真实创意与图组计划。'}</p>`
  );
}
