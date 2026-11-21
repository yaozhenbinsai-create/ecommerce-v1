import { $, $$, h, arr, field, select, check, notice } from './ui.js';

export function attachComponentPlans(form, skus, initialPlans = []) {
  const host = $('#component-plan-editor', form);
  if (!host) return () => [];
  let activeSku = null;
  const cached = new Map();
  function read() {
    if ($('[name=mode]', form)?.value === 'supplier') return [];
    const result = [];
    for (const row of $$('[data-component-plan]', host)) {
      if (!$('[data-enable-plan]', row).checked) continue;
      const i = row.dataset.planIndex,
        mode = $(`[name=cp_${i}_mode]`, row).value;
      const plan = {
        component_key: row.dataset.componentPlan,
        mode,
        brief: $(`[name=cp_${i}_brief]`, row).value,
      };
      if (mode === 'repeat') {
        const w = Number($(`[name=cp_${i}_width]`, row).value),
          h = Number($(`[name=cp_${i}_height]`, row).value);
        if (!(w > 0 && h > 0)) throw new Error('每个循环组件都需要实际单元宽高。');
        plan.repeat = {
          mode: $(`[name=cp_${i}_repeat]`, row).value,
          unit_width_mm: w,
          unit_height_mm: h,
          width_cm: w / 10,
          height_cm: h / 10,
        };
      }
      result.push(plan);
    }
    if (result.length > 5)
      throw new Error('当前通道最多同时引用 5 份组件母稿，请将更多组件拆成明确任务。');
    return result;
  }
  function sync() {
    host.hidden = $('[name=mode]', form)?.value === 'supplier';
    for (const row of $$('[data-component-plan]', host)) {
      const enabled = $('[data-enable-plan]', row).checked;
      $('[data-plan-fields]', row).hidden = !enabled;
      $('[data-plan-repeat]', row).hidden =
        $(`[name=cp_${row.dataset.planIndex}_mode]`, row).value !== 'repeat';
    }
  }
  function render() {
    const sku = skus.find((s) => s.id === Number($('[name=sku_id]', form).value));
    if (activeSku === sku?.id) {
      sync();
      return;
    }
    if (activeSku) {
      try {
        cached.set(activeSku, read());
      } catch {}
    }
    const plans = cached.get(sku?.id) || (activeSku === null ? initialPlans : []);
    activeSku = sku?.id;
    const components = arr(sku?.components).filter((c) => c.key);
    host.innerHTML = `<h3>按真实组件分配设计</h3><p class="muted small">选择需要独立设计的组件。不同组件可以采用完整构图或循环花型；所有选中母稿共同用于成品图。未启用时沿用整体设计方案。</p>${
      components.length
        ? components
            .map((component, i) => {
              const p = plans.find((p) => p.component_key === component.key),
                r = p?.repeat || {};
              return `<section class="component-plan-row" data-component-plan="${h(component.key)}" data-plan-index="${i}"><div class="row-between">${check(component.name || component.key, `cp_${i}_enabled`, '1', !!p).replace('<input ', '<input data-enable-plan ')}<span class="muted small">实际组件 ${h(component.width_cm || '未知')} × ${h(component.height_cm || '未知')} cm</span></div><div data-plan-fields><div class="form-grid">${select(
                '设计方式',
                `cp_${i}_mode`,
                [
                  { value: 'large', label: '完整构图 · 大印花' },
                  { value: 'repeat', label: '循环花型 · 小印花' },
                ],
                p?.mode || 'large',
              )}${field('此组件的表达与用途', `cp_${i}_brief`, p?.brief || '', { textarea: true, rows: 2 })}</div><div data-plan-repeat class="form-grid three">${select(
                '循环方式',
                `cp_${i}_repeat`,
                [
                  { value: 'basic', label: '规则平铺' },
                  { value: 'half-drop', label: '半落位循环' },
                ],
                r.mode || 'basic',
              )}${field('单元实际宽 / mm', `cp_${i}_width`, r.unit_width_mm || Number(r.width_cm) * 10 || 240, { type: 'number', min: 0.1, step: 'any' })}${field('单元实际高 / mm', `cp_${i}_height`, r.unit_height_mm || Number(r.height_cm) * 10 || 240, { type: 'number', min: 0.1, step: 'any' })}</div></div></section>`;
            })
            .join('')
        : notice('当前 SKU 尚未定义真实组件。先在商品档案补充组件名称与实际宽高，再建立组件设计。')
    }`;
    host.addEventListener('change', sync);
    sync();
  }
  $('[name=sku_id]', form)?.addEventListener('change', render);
  $('[name=mode]', form)?.addEventListener('change', sync);
  form.addEventListener('draftrestored', sync);
  render();
  return read;
}

export function componentPlanSummary(plans) {
  return arr(plans)
    .map(
      (p) =>
        `<div class="component-plan-summary"><strong>${h(p.component_snapshot?.name || p.component_key)}</strong><span>${p.mode === 'repeat' ? '循环花型' : '完整构图'}${p.repeat ? ` · ${h(p.repeat.unit_width_mm || Number(p.repeat.width_cm) * 10)} × ${h(p.repeat.unit_height_mm || Number(p.repeat.height_cm) * 10)} mm` : ''}</span>${p.brief ? `<p>${h(p.brief)}</p>` : ''}</div>`,
    )
    .join('');
}
