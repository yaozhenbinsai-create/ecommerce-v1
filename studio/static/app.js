import {
  $,
  $$,
  h,
  api,
  href,
  icon,
  modal,
  toast,
  errorBox,
  empty,
  link,
  enhanceProductSelectors,
} from './ui.js';
import { overviewPage, productsPage, productPage, importsPage, importPage } from './products.js';
import { researchPage, sourcePage } from './research.js';
import {
  designsPage,
  newDesignPage,
  studioPage,
  artworksPage,
  productionPage,
  jobsPage,
} from './design.js';
import { listingsPage, listingPage, exportsPage, exportPage } from './listing.js';
import { settingsPage } from './settings.js';
import { familiesPage, familyEditorPage } from './families.js';

const groups = [
  { id: 'workspace', label: '工作空间', items: [['/overview', '工作台', 'grid']] },
  {
    id: 'information',
    number: '01',
    label: '产品信息',
    items: [
      ['/products', '商品档案', 'box'],
      ['/imports', '供应商导入', 'upload'],
      ['/research', '定向研究', 'search'],
      ['/hotspots', '热点发现', 'globe'],
    ],
  },
  {
    id: 'design',
    number: '02',
    label: '产品设计',
    items: [
      ['/designs', '设计任务', 'spark'],
      ['/artworks', '设计成果', 'image'],
    ],
  },
  {
    id: 'listing',
    number: '03',
    label: '产品上架',
    items: [
      ['/listings', '刊登工作区', 'list'],
      ['/families', '父子变体分组', 'layers'],
      ['/exports', '导出与回执', 'download'],
      ['/settings/targets', '店铺与模板', 'globe'],
    ],
  },
  {
    id: 'settings',
    label: '更多工作',
    items: [
      ['/settings/categories', '业务配置', 'settings'],
      ['/settings/models', 'AI 与运行', 'layers'],
      ['/settings/storage', '存储管理', 'box'],
    ],
  },
];
const futureGroup = { id: 'future', label: '后续模块' };
const navigationStateKey = 'studio:navigation-collapsed:v1';
const collapsedGroups = (() => {
  try {
    const saved = JSON.parse(localStorage.getItem(navigationStateKey) || '{}');
    return Object.fromEntries([...groups, futureGroup].map(({ id }) => [id, saved?.[id] === true]));
  } catch {
    return {};
  }
})();
let serial = 0,
  dispose = null,
  currentProduct = null,
  configCache = null;
const main = $('#main');
function navGroup(group, items) {
  const collapsed = collapsedGroups[group.id] === true;
  return `<section class="nav-group">
    <h2><button class="nav-group-toggle" type="button" data-nav-group="${group.id}" aria-expanded="${!collapsed}" aria-controls="nav-panel-${group.id}">
      ${group.number ? `<span class="nav-group-number" aria-hidden="true">${group.number}</span>` : ''}<span class="nav-group-label">${group.label}</span>
      <svg class="nav-group-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg>
    </button></h2>
    <div class="nav-group-items" id="nav-panel-${group.id}" ${collapsed ? 'hidden' : ''}>${items}</div>
  </section>`;
}
function nav(path) {
  $('#navigation').innerHTML =
    groups
      .map((group) =>
        navGroup(
          group,
          group.items
            .map(([route, label, glyph]) => {
              const active =
                route === '/products'
                  ? path.startsWith('/products')
                  : route === '/designs'
                    ? path.startsWith('/designs') ||
                      path.startsWith('/studio') ||
                      path.startsWith('/production')
                    : route === '/settings/targets'
                      ? path.startsWith('/settings/targets') ||
                        path.startsWith('/settings/templates')
                      : route === '/settings/categories'
                        ? path.startsWith('/settings/') &&
                          !['models', 'storage', 'targets', 'templates'].some((x) =>
                            path.endsWith(x),
                          )
                        : path === route || path.startsWith(route + '/');
              const query =
                currentProduct && ['/products', '/overview'].indexOf(route) < 0
                  ? { product_id: currentProduct.id }
                  : {};
              let destination = href(route, query);
              const previous = sessionStorage.getItem('last-route:' + route);
              if (previous && route === '/products') destination = previous;
              else if (
                previous &&
                !currentProduct &&
                [
                  '/designs',
                  '/artworks',
                  '/listings',
                  '/exports',
                  '/imports',
                  '/hotspots',
                ].includes(route)
              )
                destination = previous;
              return `<a href="${destination}" class="nav-item ${active ? 'active' : ''}" ${active ? 'aria-current="page"' : ''}>${icon(glyph)}<span>${label}</span>${active ? '<span class="nav-active-dot"></span>' : ''}</a>`;
            })
            .join(''),
        ),
      )
      .join('') +
    navGroup(
      futureGroup,
      `<div class="nav-item disabled">${icon('layers')}<span>产品维护</span><small>规划中</small></div><div class="nav-item disabled">${icon('grid')}<span>产品运营</span><small>规划中</small></div>`,
    );
}
$('#navigation').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-nav-group]');
  if (!button) return;
  const collapsed = button.getAttribute('aria-expanded') === 'true';
  collapsedGroups[button.dataset.navGroup] = collapsed;
  button.setAttribute('aria-expanded', String(!collapsed));
  document.getElementById(button.getAttribute('aria-controls')).hidden = collapsed;
  try {
    localStorage.setItem(navigationStateKey, JSON.stringify(collapsedGroups));
  } catch {
    // Keep the current session usable when browser storage is unavailable.
  }
});
function context(product) {
  currentProduct = product?.id ? product : null;
  $('#product-context').innerHTML = currentProduct
    ? `<a class="context-pill" href="${href('/products/' + currentProduct.id)}"><span>当前商品</span><strong>${h(currentProduct.name)}</strong></a>`
    : '';
}
function draftKey(form) {
  return `woven-draft:${location.hash.split('&focus=')[0]}:${form.id}`;
}
function snapshot(form) {
  return [...form.elements]
    .filter(
      (el) =>
        el.name &&
        !el.hasAttribute('data-transient') &&
        !['file', 'submit', 'button'].includes(el.type),
    )
    .map((el) => ({ name: el.name, value: el.value, checked: el.checked, type: el.type }));
}
function restore(form, fields) {
  const groups = {};
  for (const f of fields) {
    groups[f.name] ??= [];
    groups[f.name].push(f);
  }
  const offsets = {};
  for (const el of form.elements) {
    if (!el.name || el.type === 'file' || el.hasAttribute('data-transient')) continue;
    const at = offsets[el.name] || 0;
    const saved = groups[el.name]?.[at];
    offsets[el.name] = at + 1;
    if (!saved) continue;
    if (['checkbox', 'radio'].includes(el.type)) el.checked = saved.checked;
    else el.value = saved.value;
  }
}
function trackForms() {
  for (const form of $$('form[data-track]', main)) {
    const key = draftKey(form);
    let stored;
    try {
      stored = JSON.parse(sessionStorage.getItem(key) || 'null');
    } catch {}
    if (
      stored &&
      form.hasAttribute('data-draft-clean-if-unchanged') &&
      String(stored.revision || '') === String(form.dataset.revision || '') &&
      JSON.stringify(snapshot(form)) === JSON.stringify(stored.fields)
    ) {
      sessionStorage.removeItem(key);
      stored = null;
    }
    if (stored) {
      if (String(stored.revision || '') === String(form.dataset.revision || '')) {
        restore(form, stored.fields);
        form.dispatchEvent(new Event('draftrestored'));
        form.insertAdjacentHTML(
          'afterbegin',
          '<div class="draft-notice">已恢复本机暂存的未保存内容。<button type="button" class="text-link" data-discard-draft>放弃草稿</button></div>',
        );
      } else {
        form.insertAdjacentHTML(
          'afterbegin',
          '<div class="draft-notice warning">记录版本已变化，本机仍保留上一版未保存内容。<button type="button" class="text-link" data-show-draft>查看暂存内容</button><button type="button" class="text-link" data-discard-draft>放弃旧草稿</button></div>',
        );
        $('[data-show-draft]', form)?.addEventListener('click', () =>
          modal(
            '未保存草稿',
            `<div class="draft-values">${stored.fields
              .filter((x) => x.value)
              .map((x) => `<p><strong>${h(x.name)}</strong><span>${h(x.value)}</span></p>`)
              .join('')}</div>`,
          ),
        );
      }
      $('[data-discard-draft]', form)?.addEventListener('click', () => {
        sessionStorage.removeItem(key);
        route();
      });
    }
    form.addEventListener('input', (event) => {
      if (event.target?.hasAttribute?.('data-transient')) return;
      sessionStorage.setItem(
        key,
        JSON.stringify({ revision: form.dataset.revision, fields: snapshot(form) }),
      );
      $('#save-status').textContent = '有未保存内容 · 已暂存本机';
    });
  }
}
function saved(form, message = '已保存') {
  if (form) sessionStorage.removeItem(draftKey(form));
  $('.draft-notice', form || main)?.remove();
  $('#save-status').textContent =
    `${message} · ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`;
  toast(message);
}
function chooseRoute(path) {
  let match;
  if (path === '/overview') return overviewPage;
  if (path === '/products') return productsPage;
  if ((match = path.match(/^\/products\/(\d+)$/))) return (ctx) => productPage(ctx, match[1]);
  if (path === '/imports') return importsPage;
  if ((match = path.match(/^\/imports\/(\d+)$/))) return (ctx) => importPage(ctx, match[1]);
  if (path === '/research' || path === '/hotspots')
    return (ctx) => researchPage(ctx, path === '/hotspots');
  if ((match = path.match(/^\/sources\/(\d+)$/))) return (ctx) => sourcePage(ctx, match[1]);
  if (path === '/designs') return designsPage;
  if (path === '/designs/new') return newDesignPage;
  if ((match = path.match(/^\/studio\/(\d+)$/))) return (ctx) => studioPage(ctx, match[1]);
  if (path === '/artworks') return artworksPage;
  if ((match = path.match(/^\/production\/(\d+)$/))) return (ctx) => productionPage(ctx, match[1]);
  if (path === '/jobs') return jobsPage;
  if (path === '/listings') return listingsPage;
  if (path === '/families') return familiesPage;
  if (path === '/families/new') return (ctx) => familyEditorPage(ctx);
  if ((match = path.match(/^\/families\/(\d+)$/))) return (ctx) => familyEditorPage(ctx, match[1]);
  if ((match = path.match(/^\/listings\/(\d+)$/))) return (ctx) => listingPage(ctx, match[1]);
  if (path === '/exports') return exportsPage;
  if ((match = path.match(/^\/exports\/(\d+)$/))) return (ctx) => exportPage(ctx, match[1]);
  if ((match = path.match(/^\/settings\/([a-z_]+)$/))) return (ctx) => settingsPage(ctx, match[1]);
  return null;
}
async function route() {
  const seq = ++serial;
  dispose?.();
  dispose = null;
  const raw = (location.hash.slice(1) || '/overview').split('?');
  const path = raw[0],
    query = new URLSearchParams(raw[1] || '');
  if (
    [
      '/products',
      '/research',
      '/hotspots',
      '/designs',
      '/artworks',
      '/listings',
      '/exports',
      '/imports',
    ].includes(path)
  )
    sessionStorage.setItem('last-route:' + path, location.hash || '#/overview');
  $('#sidebar').classList.remove('open');
  const render = chooseRoute(path);
  nav(path);
  main.setAttribute('aria-busy', 'true');
  main.innerHTML =
    '<div class="page-loading"><span class="spinner"></span><p>正在读取工作资料</p></div>';
  try {
    if (!render) {
      main.innerHTML = empty(
        '这个页面不存在',
        '请从导航打开工作区。',
        link('/overview', '返回工作台'),
      );
      return;
    }
    const config = configCache || (await api('/api/config'));
    configCache = config;
    try {
      const settings = await api('/api/settings');
      sessionStorage.setItem('woven-page-size', String(settings.display?.value?.page_size || 24));
    } catch {}
    const ctx = {
      path,
      query,
      config,
      api,
      saved,
      reload: () => route(),
      invalidateConfig: () => {
        configCache = null;
      },
      go: (route, params = {}) => {
        location.hash = href(route, params);
      },
      setContext: context,
      main,
      toast,
      bindRefresh: (work, ms = 3000) => {
        let stopped = false;
        const tick = async () => {
          if (stopped) return;
          try {
            await work();
          } catch {}
          if (!stopped) timer = setTimeout(tick, ms);
        };
        let timer = setTimeout(tick, ms);
        return () => {
          stopped = true;
          clearTimeout(timer);
        };
      },
    };
    const page = await render(ctx);
    if (seq !== serial) return;
    context(page.context || null);
    nav(path);
    document.title = `${page.title} · 织造`;
    $('#breadcrumb').innerHTML =
      `<span>工作空间</span><span class="crumb-sep">/</span><strong>${h(page.title)}</strong>`;
    main.className = page.className || '';
    main.innerHTML = page.html;
    main.setAttribute('aria-busy', 'false');
    const cleanup = await page.bind?.();
    dispose = typeof cleanup === 'function' ? cleanup : null;
    enhanceProductSelectors(main);
    trackForms();
    const focus = query.get('focus') || query.get('field');
    if (focus) {
      const el =
        $(`[name="${CSS.escape(focus)}"]`, main) ||
        $(`[name="attribute_${CSS.escape(focus)}"]`, main) ||
        $(`[data-field="${CSS.escape(focus)}"]`, main);
      let collapsed = el?.closest('details');
      while (collapsed) {
        collapsed.open = true;
        collapsed = collapsed.parentElement?.closest('details');
      }
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      (el?.matches('input,select,textarea')
        ? el
        : el?.querySelector('input,select,textarea')
      )?.focus();
      el?.classList.add('field-highlight');
    }
    if (!focus) {
      const scroll = sessionStorage.getItem(`scroll:${location.hash}`);
      if (scroll) window.scrollTo(0, Number(scroll));
      else window.scrollTo(0, 0);
    }
  } catch (error) {
    if (seq !== serial) return;
    main.className = '';
    main.innerHTML =
      errorBox(error) +
      empty(
        '工作资料暂时无法读取',
        '请检查本机服务状态，重新加载后可继续。',
        '<button type="button" class="button primary" id="retry-page">重新加载</button>',
      );
    $('#retry-page').onclick = route;
    main.setAttribute('aria-busy', 'false');
  }
}
window.addEventListener('hashchange', route);
window.addEventListener('beforeunload', (event) => {
  const dialog = $('#modal');
  if (dialog?.open && dialog.dataset.dirty === 'true') {
    event.preventDefault();
    event.returnValue = '';
  }
});
window.addEventListener(
  'scroll',
  () => sessionStorage.setItem(`scroll:${location.hash}`, String(window.scrollY)),
  { passive: true },
);
$('#menu-toggle').onclick = () => $('#sidebar').classList.toggle('open');
document.addEventListener('click', (event) => {
  const a = event.target.closest('a[href^="#/"]');
  if (a && a.hash === location.hash) {
    event.preventDefault();
    route();
  }
});
api('/api/health')
  .then((result) => {
    $('#connection-dot').classList.add('online');
    $('#connection-label').textContent = result.ok ? '本机已连接' : '服务待检查';
  })
  .catch(() => {
    $('#connection-dot').classList.add('offline');
    $('#connection-label').textContent = '连接中断';
  });
route();
