import {
  $,
  h,
  arr,
  api,
  link,
  heading,
  panel,
  empty,
  notice,
  field,
  select,
  submit,
  metadata,
  bindForm,
  asset,
  icon,
  table,
  date,
  pagination,
} from './ui.js';

function outputKind(record) {
  if (record.config?.legacy_unverified || !record.config?.process || !record.config?.evidence)
    return '旧输出 · 工艺依据未核实';
  return record.config?.output_kind === 'process_test'
    ? '工艺测试样稿'
    : '按工艺参数生成 · 待工厂确认';
}
function productionRecords(data, id, query) {
  if (data.unavailable)
    return notice('当前服务尚未提供生产历史接口；请等待服务更新后读取已有输出。', 'warning');
  return (
    table(
      ['输出记录', '实际规格', '工艺与依据', '性质 / 文件'],
      arr(data.items).map((record) => {
        const config = record.config || {};
        return [
          `<strong>输出 #${record.id}</strong><p>${date(record.created_at)}</p><small>源图案资产 #${record.source_asset_id}</small>`,
          `${config.width_cm || '—'} × ${config.height_cm || '—'} cm<p>${config.dpi || '—'} DPI · ${record.width || config.pixel_width || '—'} × ${record.height || config.pixel_height || '—'} px</p><small>出血 ${config.bleed_mm ?? '—'} mm · ${h(config.format || '')} / ${h(config.color_space || '')}</small>`,
          `<strong>${h(config.process || '未记录工艺')}</strong><p>${h(config.evidence || '未记录来源依据')}</p>${config.preset_id ? `<small>规格方案 #${config.preset_id} · v${config.preset_revision || config.preset_snapshot?.revision || '—'}</small>` : ''}`,
          `<p>${h(outputKind(record))}</p><a class="text-link" href="${asset(record.asset_id)}" download>下载该次文件 →</a>`,
        ];
      }),
      '还没有生产输出记录',
    ) + pagination(data, '/production/' + id, Object.fromEntries(query))
  );
}

export async function productionPage(ctx, id) {
  const d = await api(`/api/designs/${id}`),
    components = arr(d.sku?.components || d.config?.components);
  if (d.mode === 'supplier')
    return {
      title: '生产文件',
      context: d.product,
      html:
        heading(
          '02 / PRODUCTION OUTPUT',
          '当前任务用于实物展示',
          '供应商中性实物母稿保留商品外观，它不是平面印刷源。',
          link('/studio/' + id, '返回工作室'),
        ) + notice('请使用原创完整构图或循环花型任务中的实际平面母稿准备生产文件。', 'warning'),
    };
  const arts = arr(d.assets).filter((a) =>
    ['tile', 'master', 'pattern', 'repeat'].includes(a.role?.split(':').pop()),
  );
  const presets = arr(ctx.config.production_presets),
    first = arts.find((a) => String(a.id) === ctx.query.get('design_asset_id')) || arts[0];
  const history = await api(
    `/api/designs/${id}/production?` +
      new URLSearchParams({ page: ctx.query.get('page') || 1, page_size: 24 }),
  ).catch((error) => {
    if ([404, 405].includes(error.status)) return { items: [], unavailable: true };
    throw error;
  });
  return {
    title: '生产文件',
    context: d.product,
    html:
      heading(
        '02 / PRODUCTION OUTPUT',
        '把设计落到实际尺寸。',
        '选择明确的组件母稿、真实规格与生产尺度，输出记录保留资产和参数依据。',
        link(`/studio/${id}`, '返回工作室'),
      ) +
      `<div class="two-column">${panel(
        '生产规格',
        `<form id="production-form" class="panel-body" data-track data-revision="${d.revision}"><div class="form-grid">${select('确定的图案资产', 'design_asset_id', [{ value: '', label: '请选择母稿或循环单元' }, ...arts.map((a) => ({ value: a.id, label: `${a.label || a.role} · 关联 #${a.id}` }))], first?.id || '')}${select('组件', 'component_key', [{ value: '', label: '选择实际组件' }, ...components.map((c) => ({ value: c.key, label: c.name }))], first?.meta?.component_key || '')}${select('生产规格方案', 'preset_id', [{ value: '', label: '本次填写' }, ...presets.map((p) => ({ value: p.id, label: `${p.name} · v${p.revision}${p.enabled ? '' : ' · 已停用'}`, disabled: !p.enabled }))], '')}${select(
          '输出格式',
          'format',
          [
            { value: 'png', label: 'PNG · RGB' },
            { value: 'tiff', label: 'TIFF · RGB' },
          ],
          'png',
        )}${field('输出组件宽 / cm', 'width_cm', '', { type: 'number', min: 0.1, step: 'any', required: true })}${field('输出组件高 / cm', 'height_cm', '', { type: 'number', min: 0.1, step: 'any', required: true })}${field('DPI', 'dpi', 300, { type: 'number', min: 72, max: 1200, required: true })}${field('四周出血 / mm', 'bleed_mm', 0, { type: 'number', min: 0, step: 'any' })}</div><section class="form-section"><h3>本次工艺与依据</h3><div class="form-grid">${select(
          '文件用途',
          'output_kind',
          [
            { value: '', label: '请选择本次实际用途' },
            { value: 'factory', label: '按真实组件和工艺参数准备' },
            { value: 'process_test', label: '工艺测试样稿 · 不代表可直接投产' },
          ],
          '',
        )}${field('工艺名称', 'process', '', { required: true, placeholder: '按供应商或测试方案的实际工艺填写' })}${field('工艺规格来源与依据', 'evidence', '', { textarea: true, rows: 3, required: true, cls: 'span-all', help: '填写真实规格文件、供应商说明或明确测试要求的位置，不使用默认虚构工艺。' })}</div><p class="muted small" id="production-preset-note">本次手动填写，保存到这次输出。选择方案会带入其工艺与依据，输出冻结该方案修订。</p></section><div id="production-repeat" class="form-section"><h3>循环单元的生产尺度</h3><div class="form-grid three">${field('单元实际宽 / cm', 'print_width_cm', '', { type: 'number', min: 0.1, step: 'any' })}${field('单元实际高 / cm', 'print_height_cm', '', { type: 'number', min: 0.1, step: 'any' })}${select(
          '排列方式',
          'repeat_mode',
          [
            { value: 'none', label: '完整构图，不循环' },
            { value: 'basic', label: '规则平铺' },
            { value: 'half-drop', label: '半落位循环' },
          ],
          'none',
        )}</div><p class="muted small">修改单元尺寸会改变输出密度。原母稿保留，生产记录保存本次使用的实际参数。</p></div><div id="production-dimensions" class="inline-notice" style="margin-top:22px">填写实际尺寸后核对输出像素。</div><div class="form-actions">${submit('生成生产文件', 'download')}</div></form>`,
      )}<aside class="stack">${panel(
        '图案与依据',
        `<div class="panel-body">${first ? `<img id="production-preview" src="${asset(first.asset_id)}" alt="生产图案" style="width:100%;max-height:280px;object-fit:contain;background:#f4f1f7;border-radius:6px">` : empty('没有可生产的图案', '先完成真实母稿或循环单元生成。', '', 'image')}${metadata(
          [
            ['商品', h(d.product?.name || d.product_id)],
            ['SKU', h(d.sku?.code || d.sku_id)],
            ['设计版本', 'v' + h(d.revision)],
            ['实际尺寸来源', '当前 SKU 的真实组件'],
          ],
        )}<div id="production-source-details"></div></div>`,
      )}${notice('DPI 元数据不会增加原图细节。实际分辨率、尺寸与色彩工艺需要一起核对；缺少条件时不会标为可生产。', 'warning')}<div id="production-result"></div></aside></div>` +
      panel('历史生产输出', productionRecords(history, id, ctx.query)),
    bind() {
      const form = $('#production-form'),
        input = (name) => $(`[name=${name}]`, form);
      const chosen = () => arts.find((a) => String(a.id) === input('design_asset_id').value);
      const dimensions = () => {
        const width = Number(input('width_cm').value),
          height = Number(input('height_cm').value),
          dpi = Number(input('dpi').value),
          bleed = Number(input('bleed_mm').value || 0);
        $('#production-dimensions').textContent =
          width && height && dpi
            ? `预计输出 ${Math.round(((width + bleed * 0.2) / 2.54) * dpi)} × ${Math.round(((height + bleed * 0.2) / 2.54) * dpi)} px，含四周 ${bleed} mm 出血。`
            : '填写实际尺寸后核对输出像素。';
      };
      const componentSize = () => {
        const plan = arr(d.config?.component_plans).find(
          (p) => p.component_key === input('component_key').value,
        );
        const component =
          plan?.component_snapshot ||
          components.find((c) => c.key === input('component_key').value);
        input('width_cm').readOnly = !!plan;
        input('height_cm').readOnly = !!plan;
        if (component) {
          input('width_cm').value = component.width_cm || '';
          input('height_cm').value = component.height_cm || '';
        }
        dimensions();
      };
      const applySource = () => {
        const art = chosen();
        if (!art) return;
        const key = art.meta?.component_key;
        if (key) input('component_key').value = key;
        const plan = arr(d.config?.component_plans).find((p) => p.component_key === key),
          repeat = art.meta?.repeat || plan?.repeat || d.config?.repeat || {};
        const repeating = (art.meta?.pattern_mode || plan?.mode || d.mode) === 'repeat';
        input('repeat_mode').value = repeating ? repeat.mode || 'basic' : 'none';
        input('print_width_cm').value = repeat.width_cm || Number(repeat.unit_width_mm) / 10 || '';
        input('print_height_cm').value =
          repeat.height_cm || Number(repeat.unit_height_mm) / 10 || '';
        $('#production-repeat').hidden = !repeating;
        const img = $('#production-preview');
        if (img) img.src = asset(art.asset_id);
        $('#production-source-details').innerHTML = metadata([
          ['资产关联', '#' + art.id],
          [
            '实际图像',
            h(art.width || art.meta?.pixel_width || '未知') +
              ' × ' +
              h(art.height || art.meta?.pixel_height || '未知') +
              ' px',
          ],
          ['组件归属', h(components.find((c) => c.key === key)?.name || key || '整体图案')],
          ['图案方式', repeating ? '循环花型' : '完整构图'],
        ]);
        componentSize();
      };
      input('design_asset_id').addEventListener('change', applySource);
      input('component_key').addEventListener('change', () => {
        const key = input('component_key').value,
          match = arts.find((a) => a.meta?.component_key === key);
        if (match) {
          input('design_asset_id').value = match.id;
          applySource();
        } else componentSize();
      });
      input('preset_id').addEventListener('change', () => {
        const preset = presets.find((p) => String(p.id) === input('preset_id').value),
          conf = preset?.spec || preset?.config || {};
        for (const key of ['dpi', 'bleed_mm', 'format', 'process', 'evidence'])
          if (conf[key] !== undefined)
            input(key).value = key === 'format' ? String(conf[key]).toLowerCase() : conf[key];
        if (!input('component_key').value)
          for (const key of ['width_cm', 'height_cm'])
            if (conf[key] !== undefined) input(key).value = conf[key];
        $('#production-preset-note').textContent = preset
          ? `继承方案 ${preset.name} v${preset.revision}。组件尺寸仍以当前设计的真实规格为准；输出保留方案快照与本次明确工艺。`
          : '本次手动填写，不绑定规格方案。';
        dimensions();
      });
      form.addEventListener('input', dimensions);
      form.addEventListener('draftrestored', dimensions);
      applySource();
      bindForm('#production-form', async (form, v) => {
        const art = chosen();
        if (!art) throw new Error('请选择确定的母稿或循环单元。');
        if (art.meta?.component_key && art.meta.component_key !== v.component_key)
          throw new Error('所选图案与组件不一致，请选择该组件实际生成的母稿。');
        if (!v.output_kind) throw new Error('请选择真实工艺参数文件或明确的测试样稿用途。');
        if (!v.process.trim() || !v.evidence.trim())
          throw new Error('请补齐真实工艺名称和规格依据。');
        const preset = presets.find((p) => String(p.id) === v.preset_id);
        const body = {
          preset_id: preset?.id || null,
          preset_revision: preset?.revision || null,
          process: v.process.trim(),
          evidence: v.evidence.trim(),
          output_kind: v.output_kind,
          design_asset_id: art.id,
          asset_id: art.asset_id,
          component_key: v.component_key,
          width_cm: Number(v.width_cm),
          height_cm: Number(v.height_cm),
          dpi: Number(v.dpi),
          bleed_mm: Number(v.bleed_mm),
          format: v.format,
          repeat_mode: v.repeat_mode,
          print_width_cm: Number(v.print_width_cm) || null,
          print_height_cm: Number(v.print_height_cm) || null,
        };
        const output = await api(`/api/designs/${id}/production`, { method: 'POST', body });
        ctx.saved(
          form,
          output.output_kind === 'process_test' || output.config?.output_kind === 'process_test'
            ? '工艺测试样稿已生成，尚未验证工厂适用性'
            : '工艺参数文件已生成，仍需工厂核对',
        );
        const aid = output.asset_id || output.id;
        $('#production-result').innerHTML = panel(
          outputKind(output),
          `<div class="panel-body"><div class="file-tile">${icon('file')}<div><h3>${h(output.name || '生产文件')}</h3><p>${h(output.width || output.config?.pixel_width || '')} × ${h(output.height || output.config?.pixel_height || '')} px</p></div><a class="button primary small" href="${asset(aid)}" target="_blank">下载文件</a></div>${notice('文件已按记录参数生成，不代表已获工厂批准或可直接投产。', 'warning')}${output.warning ? notice(h(output.warning), 'warning') : ''}${link('/production/' + id, '查看已保存输出历史 →', 'text-link')}</div>`,
        );
      });
    },
  };
}
