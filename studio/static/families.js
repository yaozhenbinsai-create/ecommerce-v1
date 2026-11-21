import {
  $,
  $$,
  h,
  arr,
  api,
  heading,
  panel,
  notice,
  field,
  select,
  button,
  submit,
  table,
  link,
  badge,
  pagination,
  bindForm,
  bindAction,
  int,
  errorBox,
} from './ui.js';

const targets = (items) => [
  { value: '', label: '请选择目标店铺' },
  ...arr(items).map((t) => ({ value: t.id, label: `${t.name} · ${t.platform} / ${t.site}` })),
];
const childIds = (family) =>
  arr(family?.listing_ids).length
    ? family.listing_ids
    : arr(family?.children).map((row) => row.listing_id || row.id);
function templateChoices(template, field, productType) {
  if (field === 'product_type') return arr(template?.config?.native_rules?.product_types);
  const column = template?.config?.mapping?.[field];
  const rule = arr(template?.config?.native_rules?.rules).find(
    (r) => r.column === column && r.strict_enum,
  );
  const values = arr(rule?.enums?.[productType]);
  return field === 'listing_action' ? values.filter((value) => value !== 'Delete') : values;
}

export async function familiesPage(ctx) {
  const data = await api('/api/families?' + ctx.query.toString());
  const targetId = int(ctx.query.get('target_id')),
    incoming = int(ctx.query.get('listing_id'));
  return {
    title: '父子变体分组',
    html:
      heading(
        '03 / VARIATION FAMILIES',
        '把真实规格组织成平台变体。',
        '父体只承担平台关联；真实规格、售价、库存和商品标识继续由各个子刊登维护。',
        link('/listings', '返回刊登', 'button secondary', { target_id: targetId }) +
          link('/families/new', '建立父子分组', 'button primary', {
            target_id: targetId,
            listing_id: incoming,
          }),
      ) +
      (incoming
        ? notice(`正在为刊登 #${incoming} 选择分组。打开已有分组后保存，或建立一个新分组。`)
        : '') +
      `<form id="family-filter" class="toolbar"><div class="filters">${select('目标店铺', 'target_id', targets(ctx.config.targets), targetId || '')}<button class="button secondary" type="submit">筛选</button></div></form>` +
      panel(
        '已保存的父子关系',
        table(
          ['逻辑父体', '目标 / 原生模板', '变体主题', '实际子刊登', '修订', '操作'],
          arr(data.items).map((family) => [
            `<strong>${h(family.title || family.fields?.title || family.parent_sku)}</strong><p class="mono">${h(family.parent_sku)}</p>`,
            `${h(ctx.config.targets.find((t) => t.id === family.target_id)?.name || '店铺 #' + family.target_id)}<p>模板 #${family.template_id}</p>`,
            `${h(family.product_type)}<p>${h(family.variation)}</p>`,
            `${family.child_count ?? childIds(family).length} 份真实刊登`,
            `v${family.revision}`,
            link('/families/' + family.id, incoming ? '加入并核对 →' : '编辑分组 →', 'text-link', {
              listing_id: incoming,
            }),
          ]),
          '尚未建立父子变体分组',
        ) + pagination(data, '/families', { target_id: targetId, listing_id: incoming }),
      ),
    bind() {
      bindForm('#family-filter', async (_form, values) =>
        ctx.go('/families', { ...values, listing_id: incoming }),
      );
    },
  };
}

export async function familyEditorPage(ctx, id = null) {
  const family = id ? await api('/api/families/' + id) : null;
  const targetId = family?.target_id || int(ctx.query.get('target_id'));
  const templates = targetId
    ? arr((await api(`/api/templates?target_id=${targetId}&page_size=100`)).items)
    : [];
  if (family?.template && !templates.some((t) => t.id === family.template.id))
    templates.push(family.template);
  const existingFields = family?.fields || {};
  const selectionKey = `family-children:${id || 'new'}:${family?.revision || 0}:${targetId || ''}`;
  const stored = sessionStorage.getItem(selectionKey);
  const selected = new Set(stored ? JSON.parse(stored) : childIds(family));
  const incoming = int(ctx.query.get('listing_id'));
  if (incoming) selected.add(incoming);
  const selectedTemplateId = family?.template_id || int(ctx.query.get('template_id'));
  const initialTemplate = templates.find((t) => t.id === selectedTemplateId);
  const productTypes = templateChoices(initialTemplate, 'product_type');
  const variants = templateChoices(initialTemplate, 'variation', family?.product_type || '');
  const actions = templateChoices(initialTemplate, 'listing_action', family?.product_type || '');
  return {
    title: family ? '编辑父子分组' : '建立父子分组',
    html:
      heading(
        '03 / LOGICAL PARENT',
        family ? family.parent_sku : '建立明确的父子关系',
        '逻辑父体没有实物、售价、库存或商品识别码。选择至少两个属于同一店铺的真实子刊登。',
        link('/families', '返回分组', 'button secondary', { target_id: targetId }) +
          (family
            ? link('/exports', '完整分组进入导出', 'button primary', {
                target_id: targetId,
                family_id: family.id,
                template_id: family.template_id,
              })
            : ''),
      ) +
      `<div class="two-column"><div>${panel('父体与变体规则', `<form id="family-form" class="panel-body" data-track data-revision="${family?.revision || ''}"><div class="form-grid">${select('目标店铺', 'target_id', targets(ctx.config.targets), targetId || '', family ? 'disabled' : '')}${select('对应原生模板', 'template_id', [{ value: '', label: targetId ? '请选择实际模板' : '先选择目标店铺' }, ...templates.map((t) => ({ value: t.id, label: `${t.name} · v${t.revision}${t.config?.native_rules?.adapter === 'amazon_pillow_us_v1' ? '' : ' · 父子行待适配'}`, disabled: t.config?.native_rules?.adapter !== 'amazon_pillow_us_v1' }))], selectedTemplateId || '')}${field('稳定的父体 SKU 标识', 'parent_sku', family?.parent_sku || '', { required: true, readonly: !!family, placeholder: '由当前店铺明确指定，不使用真实子 SKU' })}<div id="family-type">${select('产品类型', 'product_type', [{ value: '', label: '请选择原生模板类型' }, ...productTypes.map((value) => ({ value, label: value }))], family?.product_type || '')}</div><div id="family-variation">${select('平台允许的变体主题', 'variation', [{ value: '', label: '先选择产品类型' }, ...variants.map((value) => ({ value, label: value }))], family?.variation || '')}</div><div id="family-action">${select('本次平台处理动作', 'listing_action', [{ value: '', label: '请选择原生模板允许值' }, ...actions.map((value) => ({ value, label: value }))], family?.listing_action || '')}</div>${field('父体展示标题', 'title', family?.title || existingFields.title || '', { required: true, cls: 'span-all' })}${field('共同品牌', 'brand', family?.brand || existingFields.brand || '', { required: true })}${field('共同材质表达（确有依据时）', 'material', existingFields.attributes?.material || '')}${field('父体描述', 'description', existingFields.description || '', { textarea: true, rows: 4, cls: 'span-all' })}${field('共同卖点（每行一条）', 'bullets', arr(existingFields.bullets).join('\n'), { textarea: true, rows: 3, cls: 'span-all' })}</div><div id="family-template-scope" style="margin-top:18px"></div><section class="form-section"><div class="row-between"><h3>选择真实子刊登</h3><span class="pill">已选 <strong id="family-selected-count">${selected.size}</strong> 份</span></div><p class="muted small">选择会跨页保留。保存前需要至少两份实际子刊登；各自售价、库存和变体值在刊登资料中维护。</p><div id="family-children"></div></section><div class="form-actions">${submit(family ? '保存分组修订' : '建立逻辑父体')}</div></form>`)}</div><aside class="stack">${panel('关系与导出方式', `<div class="panel-body"><ol class="step-list"><li><span class="step-number">1</span><div><strong>父体是平台关系记录</strong><span>不创建商品档案，不伪造实际 SKU 或库存。</span></div></li><li><span class="step-number">2</span><div><strong>子体保留真实业务身份</strong><span>颜色、尺寸、设计图和商业值继续属于子刊登。</span></div></li><li><span class="step-number">3</span><div><strong>父体与全部子体同批输出</strong><span>导出时选择完整分组，冻结父子关系和各子刊登修订。</span></div></li></ol></div>`)}${family?.diagnostics?.length ? panel('当前分组待处理项', `<div class="panel-body">${family.diagnostics.map((issue) => notice(h(issue.message || issue) + (/^#\/(listings|families)\/\d+/.test(issue.href || '') ? `<p><a class="text-link" href="${h(issue.href)}">前往对应资料 →</a></p>` : ''), 'warning')).join('')}</div>`) : ''}</aside></div>`,
    bind() {
      const form = $('#family-form');
      let childPage = 1;
      const remember = () => {
        sessionStorage.setItem(selectionKey, JSON.stringify([...selected]));
        $('#family-selected-count').textContent = selected.size;
      };
      const currentTemplate = () =>
        templates.find((t) => String(t.id) === $('[name=template_id]', form).value);
      const updateRules = (preserve = true) => {
        const template = currentTemplate();
        const previousType = preserve ? $('[name=product_type]', form)?.value : '';
        $('#family-type').innerHTML = select(
          '产品类型',
          'product_type',
          [
            { value: '', label: '请选择原生模板类型' },
            ...templateChoices(template, 'product_type').map((value) => ({ value, label: value })),
          ],
          previousType || '',
        );
        const updateDependent = () => {
          const type = $('[name=product_type]', form).value;
          for (const [key, selector, label] of [
            ['variation', '#family-variation', '平台允许的变体主题'],
            ['listing_action', '#family-action', '本次平台处理动作'],
          ]) {
            const previous = $('[name=' + key + ']', form)?.value || '';
            $(selector).innerHTML = select(
              label,
              key,
              [
                { value: '', label: '请选择原生模板允许值' },
                ...templateChoices(template, key, type).map((value) => ({ value, label: value })),
              ],
              previous,
            );
          }
        };
        $('[name=product_type]', form).addEventListener('change', updateDependent);
        updateDependent();
        $('#family-template-scope').innerHTML = !template
          ? notice('先选择模板；父子关系必须使用该模板实际支持的产品类型和主题。')
          : template.config?.native_rules?.adapter !== 'amazon_pillow_us_v1'
            ? notice(
                '当前模板尚无可执行的父子变体规则，请在模板管理中读取或适配原生规则。',
                'warning',
              )
            : notice(
                `使用 ${h(template.name)} 的已保存规则；保存与导出仍会校验各个子体的实际属性。`,
              );
      };
      async function loadChildren() {
        const host = $('#family-children');
        if (!targetId) {
          host.innerHTML = notice('选择目标店铺后显示可关联的真实子刊登。');
          return;
        }
        host.innerHTML =
          '<div class="page-loading"><span class="spinner"></span><p>读取子刊登</p></div>';
        try {
          const data = await api(
            `/api/listings?target_id=${targetId}&page=${childPage}&page_size=24`,
          );
          host.innerHTML =
            table(
              ['选择', '实际商品 / SKU', '颜色 / 尺寸', '刊登修订', '资料'],
              arr(data.items).map((child) => [
                `<input type="checkbox" name="family_child" value="${child.id}" ${selected.has(child.id) ? 'checked' : ''} aria-label="选择刊登 ${child.id}">`,
                `<strong>${h(child.name || '商品 #' + child.product_id)}</strong><p class="mono">${h(child.sku_code || 'SKU #' + child.sku_id)}</p>`,
                h(
                  [child.fields?.attributes?.color, child.fields?.attributes?.size]
                    .filter(Boolean)
                    .join(' · ') || '在子刊登中核对',
                ),
                `v${child.revision}`,
                link('/listings/' + child.id, '查看刊登 →', 'text-link'),
              ]),
              '此店铺还没有实际子刊登',
            ) +
            `<div class="pagination"><span>共 ${data.total || 0} 份刊登 · 第 ${childPage} 页</span><div>${button('← 上页', 'family-prev', 'secondary small', childPage > 1 ? '' : 'disabled')}${button('下页 →', 'family-next', 'secondary small', childPage * 24 < Number(data.total) ? '' : 'disabled')}</div></div>`;
          $$('[name=family_child]', host).forEach((input) =>
            input.addEventListener('change', () => {
              if (input.checked) selected.add(Number(input.value));
              else selected.delete(Number(input.value));
              remember();
            }),
          );
          bindAction(
            'family-prev',
            async () => {
              childPage--;
              await loadChildren();
            },
            host,
          );
          bindAction(
            'family-next',
            async () => {
              childPage++;
              await loadChildren();
            },
            host,
          );
        } catch (error) {
          host.innerHTML = errorBox(error);
        }
      }
      if (!family)
        $('[name=target_id]', form).addEventListener('change', (event) =>
          ctx.go('/families/new', { target_id: event.target.value }),
        );
      $('[name=template_id]', form).addEventListener('change', () => updateRules(false));
      form.addEventListener('draftrestored', () => updateRules(true));
      updateRules(true);
      remember();
      loadChildren();
      bindForm('#family-form', async (form, values) => {
        if (
          !targetId ||
          !values.template_id ||
          !values.product_type ||
          !values.variation ||
          !values.listing_action
        )
          throw new Error('请选择店铺、原生模板、产品类型、变体主题与处理动作。');
        if (selected.size < 2) throw new Error('一个父子分组至少需要两个真实子刊登。');
        const payload = {
          ...(family ? { revision: family.revision } : {}),
          target_id: targetId,
          template_id: Number(values.template_id),
          parent_sku: values.parent_sku.trim(),
          product_type: values.product_type,
          variation: values.variation,
          listing_action: values.listing_action,
          listing_ids: [...selected],
          fields: {
            ...existingFields,
            title: values.title,
            brand: values.brand,
            description: values.description,
            bullets: values.bullets
              .split('\n')
              .map((v) => v.trim())
              .filter(Boolean),
            attributes: { ...(existingFields.attributes || {}), material: values.material },
          },
        };
        const saved = await api('/api/families' + (id ? '/' + id : ''), {
          method: id ? 'PUT' : 'POST',
          body: payload,
        });
        sessionStorage.removeItem(selectionKey);
        ctx.saved(form, '父子分组已保存，导出时选择完整分组');
        ctx.go('/families/' + saved.id);
        if (id) ctx.reload();
      });
    },
  };
}
