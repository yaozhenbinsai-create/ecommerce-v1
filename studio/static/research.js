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
  safeUrl,
  external,
  asset,
  thumbnail,
  thumbnailFallback,
  jsonObject,
} from './ui.js';

const kindName = { market: '平台产品', social: '社交内容', hotspot: '开放热点' };
const platformOptions = {
  market: ['Amazon', 'Temu', 'Alibaba.com', 'AliExpress'],
  social: ['Pinterest', 'Instagram', 'TikTok', 'YouTube', 'Reddit'],
  hotspot: ['开放热点'],
};
function sourceEvidenceState(item) {
  const meta = item.meta || {};
  const imageId = item.thumbnail_asset_id || item.image_asset_id || meta.image_asset_id;
  const accessLabel =
    meta.access_status === 'read'
      ? '已读取正文摘录'
      : meta.access_status === 'search_only'
        ? '仅有搜索摘要'
        : meta.access_status === 'unavailable'
          ? '正文读取失败'
          : '正文状态未记录';
  const imageLabel =
    meta.image_status === 'verified_source_image' && imageId
      ? '已取得真实来源图'
      : imageId
        ? '本次未取得新图片，保留已有来源图'
        : meta.image_status === 'unavailable'
          ? '未取得来源图片'
          : '图片状态未记录';
  return { imageId, accessLabel, imageLabel, hasText: meta.access_status === 'read' };
}

function sourceEvidenceDetail(item) {
  const state = sourceEvidenceState(item);
  const meta = item.meta || {};
  const caution = state.hasText
    ? '以下为已保存的页面正文摘录，不代表页面全部内容。'
    : '来源登记已保留，但本次没有取得可读正文。下面若有文字，仅为已保存摘要或历史内容。';
  return `${notice(caution, state.hasText ? 'info' : 'warning')}${metadata([
    ['正文读取', h(state.accessLabel)],
    ['图片读取', h(state.imageLabel)],
  ])}${meta.access_error ? notice('正文未取得的原因：' + h(meta.access_error), 'warning') : ''}${meta.image_error ? notice('图片未取得的原因：' + h(meta.image_error), 'warning') : ''}${state.imageId ? `<figure class="source-evidence-image"><img src="${asset(state.imageId)}" alt="${h(item.title)}的实际来源图"><figcaption>${h(state.imageLabel)}</figcaption></figure>` : ''}`;
}
function savedExcerpt(item) {
  const text = String(item.excerpt || '');
  if (!text) return '<p class="muted small">尚无可用的正文摘录。</p>';
  const preview = text.slice(0, 520);
  return `<div class="source-excerpt"><p class="source-excerpt-preview">${h(preview)}${text.length > preview.length ? '…' : ''}</p>${text.length > preview.length ? `<details><summary class="text-link">展开全部已保存摘录 · ${text.length.toLocaleString('zh-CN')} 字符</summary><div class="source-excerpt-full">${h(text)}</div></details>` : ''}</div>`;
}
function sourceCard(item, selected, context = {}) {
  const state = sourceEvidenceState(item);
  const img = state.imageId;
  return `<article class="source-card ${selected ? 'selected' : ''}" data-source-card="${item.id}"><div class="source-visual">${img ? `<img src="${thumbnail(img, 640)}" ${thumbnailFallback(img)} alt="${h(item.title)}" loading="lazy">` : icon(item.kind === 'hotspot' ? 'globe' : 'image')}<span class="source-platform">${h(item.platform || kindName[item.kind] || '内容来源')}</span><input type="checkbox" name="selected_source" value="${item.id}" ${selected ? 'checked' : ''} aria-label="选择 ${h(item.title)}"></div><div class="source-body"><a href="${href('/sources/' + item.id, { product_id: context.product_id || item.product_id, return_to: context.return_to })}"><h3>${h(item.title)}</h3></a><p>${h(item.excerpt || '暂无摘要，请打开来源查看原始内容。')}</p><div class="source-evidence-status">${badge(state.hasText ? 'active' : 'missing', state.accessLabel)}<span>${h(state.imageLabel)}</span></div><div class="source-footer"><span>${h(item.source_category || kindName[item.kind] || '灵感参考')}</span><span>${item.meta?.acquisition === 'direct_url' ? '直接链接 · ' : ''}${date(item.observed_at)}</span></div><div class="source-footer">${external(item.url, '原始来源')}${link('/sources/' + item.id, '查看与提炼 →', 'text-link', { product_id: context.product_id || item.product_id, return_to: context.return_to })}</div></div></article>`;
}
export async function researchPage(ctx, hotspot = false) {
  const path = hotspot ? '/hotspots' : '/research';
  const pid = int(ctx.query.get('product_id'));
  const jobId = int(ctx.query.get('job_id'));
  const results = await Promise.all([
    api('/api/products?page_size=100'),
    jobId
      ? api(`/api/research/${jobId}`)
      : api(
          '/api/sources?' +
            new URLSearchParams({
              kind: hotspot ? 'hotspot' : ctx.query.get('kind') || '',
              ...(pid ? { product_id: pid } : {}),
              page: ctx.query.get('page') || 1,
              q: ctx.query.get('q') || '',
            }),
        ),
  ]);
  const products = arr(results[0].items);
  let selectedProduct = products.find((p) => p.id === pid);
  if (pid && !selectedProduct) {
    selectedProduct = await api('/api/products/' + pid);
    products.push(selectedProduct);
  }
  let data = results[1],
    job = data.job;
  const items = arr(data.items);
  const key = `source-selection:${hotspot ? 'hotspot' : pid || 'none'}`;
  const selected = new Set(JSON.parse(sessionStorage.getItem(key) || '[]'));
  const previous =
    JSON.parse(sessionStorage.getItem(`research-plan:${path}:${pid || ''}`) || 'null') || {};
  const sourcePlans = arr(ctx.config.sources).filter(
    (s) =>
      s.enabled !== false &&
      s.enabled !== 0 &&
      ((s.spec || s.config || {}).kind === 'hotspot') === hotspot,
  );
  const selectedKind = hotspot ? 'hotspot' : previous.kind || 'market';
  const productOptions = [
    { value: '', label: hotspot ? '选择采用后的目标产品' : '请选择研究目标商品' },
    ...products.map((p) => ({ value: p.id, label: p.name })),
  ];
  return {
    title: hotspot ? '热点发现' : '定向研究',
    context: selectedProduct,
    html:
      heading(
        hotspot ? '01 / OPEN DISCOVERY' : '01 / PRODUCT RESEARCH',
        hotspot ? '先发现值得表达的事。' : '找到下一款产品的设计线索。',
        hotspot
          ? '热点使用独立发现范围。先理解事件、情绪与表达机会，再匹配产品。'
          : '从目标商品出发，比较平台和社交来源；跨品类借鉴保留原始出处。',
        link('/products', '商品档案') + button('读取公开来源链接', 'read-source-url', 'primary'),
      ) +
      `<div class="research-layout"><aside class="research-controls">${panel(
        hotspot ? '发现范围' : '研究范围',
        `<form id="research-form" class="panel-body" data-track>${!hotspot ? select('目标商品', 'product_id', productOptions, pid || previous.product_id || '') : '<input type="hidden" name="product_id" value="">'}${select('已保存的来源方案', 'source_id', [{ value: '', label: '本次自定义范围' }, ...sourcePlans.map((s) => ({ value: s.id, label: s.name + (s.capability?.supported === false ? ' · 待适配' : ''), disabled: s.capability?.supported === false }))], previous.source_id || '')}<div id="source-plan-summary" class="muted small full-row"></div>${field(hotspot ? '关注主题 / 事件' : '关键词 / 设计方向', 'query', previous.query || '', { required: true, placeholder: hotspot ? '如：天文发现、自然情绪' : '如：梦幻紫、星光、疏朗小花', textarea: true, rows: 3 })}${
          !hotspot
            ? select(
                '研究类型',
                'kind',
                [
                  { value: 'market', label: '电商平台研究' },
                  { value: 'social', label: '社交内容研究' },
                ],
                previous.kind || 'market',
              )
            : '<input type="hidden" name="kind" value="hotspot">'
        }<div class="full-row"><p class="small subtle" style="margin-top:13px">${hotspot ? '新闻与内容渠道' : '信息来源'}</p><div class="checkbox-list" id="research-platforms">${platformOptions[selectedKind].map((name, index) => check(name, 'platforms', name, previous.platforms?.includes(name) ?? index === 0)).join('')}</div></div>${!hotspot ? `<div class="full-row" style="margin-top:10px">${check('允许跨品类参考', 'cross_category', '1', previous.cross_category || false)}</div>${field('参考来源品类', 'source_category', previous.source_category || '', { placeholder: '如：茶杯（目标仍为被套）' })}` : ''}${field('地区 / 语言提示', 'locale', previous.locale || '', { placeholder: '如：US / English', help: '作为查询提示词，不是严格的地域过滤。' })}${field('本次收集数量', 'limit', previous.limit || 12, { type: 'number', min: 1, max: 30, step: 1 })}<div class="form-actions">${submit(hotspot ? '发现热点' : '开始研究', 'search')}</div></form>`,
      )}<p class="muted small" style="padding:13px 5px;line-height:1.9">来源中的材质、尺寸和销量不自动成为目标商品事实。设计采用的是有出处的创意元素。</p></aside><div>${job ? `<div id="research-job">${panel(`研究任务 #${job.id}`, `<div class="panel-body"><div class="row-between">${badge(job.status)}<span class="muted small">${date(job.created_at)}</span></div>${progress(job)}${job.error ? notice(h(job.error), 'error') : ''}${job.status === 'unknown' ? notice('外部请求结果未知。保留原任务记录，核对后再继续。', 'warning') : ''}</div>`, link('/jobs', '查看任务 →', 'text-link'))}</div>` : ''}<div class="section-heading" style="margin-top:${job ? '24' : '0'}px"><h2>${job ? '本次收集结果' : '已保存的来源'}</h2><span class="muted small">${number(data.total ?? items.length)} 条来源 · 选择后带入设计</span></div>${!hotspot && !pid ? notice('先选择目标商品，让研究结果与后续设计有明确归属。') : ''}${items.length ? `<div class="source-grid">${items.map((item) => sourceCard(item, selected.has(item.id), { product_id: pid, return_to: href(path, Object.fromEntries(ctx.query)) })).join('')}</div>` : `<div class="panel research-empty">${empty(job && ['queued', 'running'].includes(job.status) ? '正在收集真实来源' : '还没有来源结果', job && ['queued', 'running'].includes(job.status) ? '可以离开当前页面，任务完成后会保存结果。' : '设置左侧研究范围后开始收集；原始链接、时间和提炼内容会一起保留。', '', 'search')}</div>`}${!job ? pagination(data, path, { ...Object.fromEntries(ctx.query), product_id: pid }) : ''}<div class="selection-bar" id="source-selection-bar" ${selected.size ? '' : 'hidden'}><span><strong id="selection-count">${selected.size}</strong> 条来源已选</span><div class="row">${button('清空选择', 'clear-sources', 'quiet small')}${button(`${icon('spark')}采用并建立设计`, 'adopt-sources', 'primary')}</div></div></div></div>`,
    bind() {
      const researchForm = $('#research-form');
      const renderPlatforms = (kind, plan = null) => {
        $('#research-platforms').innerHTML = (plan ? [plan.platform] : platformOptions[kind] || [])
          .map((name, i) => check(name, 'platforms', name, i === 0))
          .join('');
      };
      const choosePlan = () => {
        const item = sourcePlans.find(
          (s) => String(s.id) === $('[name=source_id]', researchForm).value,
        );
        const conf = item?.spec || item?.config;
        const summary = $('#source-plan-summary');
        if (!conf) {
          summary.textContent = '';
          renderPlatforms(hotspot ? 'hotspot' : $('[name=kind]', researchForm).value);
          return;
        }
        $('[name=kind]', researchForm).value = conf.kind || selectedKind;
        for (const name of ['query', 'locale', 'source_category', 'limit'])
          if (conf[name] !== undefined && $('[name=' + name + ']', researchForm))
            $('[name=' + name + ']', researchForm).value = conf[name];
        const cross = $('[name=cross_category]', researchForm);
        if (cross) cross.checked = !!conf.cross_category;
        renderPlatforms(conf.kind, conf);
        summary.textContent =
          item.name +
          ' · v' +
          item.revision +
          ' · ' +
          (item.capability?.reason || '启动时冻结此方案');
      };
      $('[name=source_id]', researchForm).addEventListener('change', choosePlan);
      if ($('[name=source_id]', researchForm).value) choosePlan();
      $('[name=kind]', researchForm).addEventListener('change', () => {
        $('[name=source_id]', researchForm).value = '';
        $('#source-plan-summary').textContent = '';
        renderPlatforms($('[name=kind]', researchForm).value);
      });
      bindForm('#research-form', async (form, v) => {
        const plan = sourcePlans.find((s) => String(s.id) === v.source_id);
        const platforms = plan
          ? [(plan.spec || plan.config).platform]
          : [...new FormData(form).getAll('platforms')];
        if (!platforms.length) throw new Error('请至少选择一个信息来源。');
        if (!hotspot && !v.product_id) throw new Error('请先选择目标商品。');
        const body = {
          source_id: int(v.source_id),
          locale: v.locale || '',
          kind: hotspot ? 'hotspot' : v.kind,
          product_id: hotspot ? null : int(v.product_id),
          platforms,
          query: v.query,
          source_category: v.source_category || '',
          cross_category: !!v.cross_category,
          limit: Number(v.limit || 12),
          intent_key: intent('research'),
        };
        sessionStorage.setItem(
          `research-plan:${path}:${body.product_id || ''}`,
          JSON.stringify(body),
        );
        const result = await api('/api/research', { method: 'POST', body });
        ctx.saved(form, '研究任务已建立');
        ctx.go(path, { product_id: body.product_id, job_id: result.id || result.job_id });
      });
      const update = () => {
        sessionStorage.setItem(key, JSON.stringify([...selected]));
        $('#source-selection-bar').hidden = !selected.size;
        $('#selection-count').textContent = selected.size;
      };
      $$('[name=selected_source]').forEach((input) =>
        input.addEventListener('change', () => {
          const id = Number(input.value);
          if (input.checked) selected.add(id);
          else selected.delete(id);
          input.closest('.source-card').classList.toggle('selected', input.checked);
          update();
        }),
      );
      bindAction('read-source-url', () =>
        modal(
          '读取真实公开来源',
          `<form id="source-url-form"><div class="form-grid">${!hotspot ? select('目标商品', 'product_id', productOptions, $('[name=product_id]', researchForm).value || pid || '') : '<input type="hidden" name="product_id" value="">'}${
            hotspot
              ? '<input type="hidden" name="kind" value="hotspot">'
              : select(
                  '来源类型',
                  'kind',
                  [
                    { value: 'market', label: '电商产品页面' },
                    { value: 'social', label: '社交内容页面' },
                  ],
                  $('[name=kind]', researchForm).value || 'market',
                )
          }${field('原始公开链接', 'url', '', { type: 'url', required: true, cls: 'span-all', placeholder: 'https://…', help: '保存原始链接并实际读取页面正文与可用来源图。无法访问时保留失败任务，不补造摘要。' })}${!hotspot ? field('参考来源品类', 'source_category', $('[name=source_category]', researchForm)?.value || '', { cls: 'span-all', placeholder: '来源页面自己的品类，可与目标产品不同' }) : ''}</div><div class="form-actions">${submit('读取来源并保留依据', 'search')}</div></form>`,
          (root) =>
            bindForm(
              '#source-url-form',
              async (form, v) => {
                if (!hotspot && !v.product_id) throw new Error('请选择来源关联的目标商品。');
                const body = {
                  url: v.url,
                  kind: hotspot ? 'hotspot' : v.kind,
                  product_id: hotspot ? null : Number(v.product_id),
                  source_category: v.source_category || '',
                  intent_key: intent('source-url'),
                };
                const job = await api('/api/sources/from-url', { method: 'POST', body });
                closeModal();
                ctx.saved(null, '公开链接读取任务已建立');
                ctx.go(path, { product_id: body.product_id, job_id: job.id || job.job_id });
              },
              root,
            ),
        ),
      );
      bindAction('clear-sources', () => {
        selected.clear();
        $$('[name=selected_source]').forEach((x) => {
          x.checked = false;
          x.closest('.source-card').classList.remove('selected');
        });
        update();
      });
      bindAction('adopt-sources', () => {
        modal(
          '把来源转成设计方向',
          `<form id="adopt-selection">${select('目标商品', 'product_id', productOptions, pid || '')}${field('采用哪些元素，如何适配目标产品', 'elements', '', { textarea: true, rows: 5, required: true, placeholder: '保留星光、紫色和留白；将茶杯上的细星调整为适合被套尺度的循环花型。' })}<p class="muted small" style="margin-top:12px">已选择 ${selected.size} 条来源。这里只建立创意引用，不会覆盖商品材质和尺寸。</p><div class="form-actions">${submit('保存来源并建立设计', 'arrow')}</div></form>`,
          (root) =>
            bindForm(
              '#adopt-selection',
              async (form, v) => {
                if (!v.product_id) throw new Error('请选择目标商品。');
                for (const id of selected)
                  await api(`/api/sources/${id}/adopt`, {
                    method: 'POST',
                    body: { product_id: Number(v.product_id), elements: v.elements },
                  });
                closeModal();
                ctx.saved(null, '来源已关联到目标商品');
                ctx.go('/designs/new', {
                  product_id: v.product_id,
                  source_ids: [...selected].join(','),
                  brief: v.elements,
                });
              },
              root,
            ),
        );
      });
      if (job && ['queued', 'running'].includes(job.status))
        return ctx.bindRefresh(async () => {
          const fresh = await api(`/api/research/${jobId}`);
          const node = $('#research-job');
          if (node)
            node.innerHTML = panel(
              `研究任务 #${jobId}`,
              `<div class="panel-body"><div class="row-between">${badge(fresh.job.status)}<span class="muted small">${date(fresh.job.updated_at)}</span></div>${progress(fresh.job)}</div>`,
              link('/jobs', '查看任务 →', 'text-link'),
            );
          if (!['queued', 'running'].includes(fresh.job.status)) ctx.reload();
        });
    },
  };
}

export async function sourcePage(ctx, id) {
  const data = await api(`/api/sources/${id}`);
  const products = await api('/api/products?page_size=100');
  const pid = int(ctx.query.get('product_id')) || data.product_id;
  const suppliedReturn = ctx.query.get('return_to') || '';
  const returnTo = /^#\/(research|hotspots)(?:\?|$)/.test(suppliedReturn)
    ? suppliedReturn
    : href(data.kind === 'hotspot' ? '/hotspots' : '/research', { product_id: pid });
  const product = arr(products.items).find((x) => x.id === pid);
  const savedElements =
    arr(data.adoptions).find((adoption) => adoption.product_id === pid)?.elements ?? data.elements;
  const elements =
    typeof savedElements === 'string'
      ? ['[]', '{}'].includes(savedElements.trim())
        ? ''
        : savedElements
      : Array.isArray(savedElements)
        ? savedElements.filter(Boolean).join('\n')
        : savedElements && typeof savedElements === 'object'
          ? Object.entries(savedElements)
              .map(
                ([key, value]) =>
                  `${key}：${Array.isArray(value) ? value.join('、') : String(value ?? '')}`,
              )
              .join('\n')
          : '';
  return {
    title: '来源与创意提炼',
    context: product,
    html:
      heading(
        'SOURCE / #' + id,
        '来源与创意提炼',
        '原始观察和目标商品事实分别保存。',
        `<a class="button secondary" href="${h(returnTo)}">返回原来源列表</a>`,
      ) +
      `<div class="two-column source-detail-layout"><div class="stack">${panel('原始来源', `<div class="panel-body"><div class="row" style="margin-bottom:18px"><span class="pill">${h(data.platform || '内容来源')}</span>${badge(data.kind, kindName[data.kind])}<span class="muted small">${date(data.observed_at)}</span></div><h2 class="source-detail-title">${h(data.title)}</h2><div class="source-evidence-details">${sourceEvidenceDetail(data)}</div>${savedExcerpt(data)}<div style="margin-top:22px">${external(data.url, '打开原始内容')}</div></div>`)}${panel(
        '来源记录',
        `<div class="panel-body">${metadata([
          ['来源品类', h(data.source_category || '未标注')],
          [
            '采集方式',
            h(
              data.meta?.acquisition === 'direct_url'
                ? '直接读取公开链接'
                : data.meta?.acquisition === 'automatic_search'
                  ? '自动搜索收集'
                  : '原始来源记录',
            ),
          ],
          [
            '证据范围',
            h(
              data.meta?.access_status === 'read'
                ? '本次取得的正文摘录'
                : data.meta?.evidence_level === 'url_only'
                  ? '仅原始链接'
                  : '已保存摘要/历史内容，本次正文未确认',
            ),
          ],
          ['目标商品', h(product?.name || '尚未采用')],
          ['采集时间', date(data.observed_at)],
          ['来源修订', 'v' + h(data.revision)],
          ['原始链接', external(data.url, '查看原链接')],
        ])}</div>`,
      )}${panel(
        '已采用到的商品',
        table(
          ['目标商品', '采用元素', '时间'],
          arr(data.adoptions).map((a) => [
            link(
              '/products/' + a.product_id,
              h(a.product_name || '商品 #' + a.product_id),
              'text-link',
            ),
            h(typeof a.elements === 'string' ? a.elements : JSON.stringify(a.elements || [])),
            date(a.created_at),
          ]),
          '尚未采用到商品',
        ),
      )}</div>${panel('采用到目标商品', `<form id="adopt-source" class="panel-body" data-track data-revision="${h(data.revision)}">${select('目标商品', 'product_id', [{ value: '', label: '请选择' }, ...arr(products.items).map((p) => ({ value: p.id, label: p.name }))], pid || '')}${field('提炼的元素与适配思路', 'elements', elements, { textarea: true, rows: 8, required: true, help: '写明情绪、主题、配色、构图等创意；真实属性继续引用目标商品自己的资料。' })}<div class="form-actions">${submit('保存并建立设计', 'arrow')}</div></form>`)}</div>`,
    bind() {
      bindForm('#adopt-source', async (form, v) => {
        if (!v.product_id) throw new Error('请选择目标商品。');
        await api(`/api/sources/${id}/adopt`, {
          method: 'POST',
          body: { product_id: Number(v.product_id), elements: v.elements },
        });
        ctx.saved(form, '来源与创意提炼已保存');
        ctx.go('/designs/new', { product_id: v.product_id, source_ids: id, brief: v.elements });
      });
    },
  };
}
