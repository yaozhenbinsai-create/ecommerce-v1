import {
  $,
  h,
  arr,
  api,
  modal,
  notice,
  field,
  submit,
  button,
  table,
  badge,
  bindForm,
  bindAction,
  closeModal,
  thumb,
  imageRoleLabel,
  compareImageRoles,
} from './ui.js';

const stateNames = {
  not_prepared: '尚未准备',
  pending_config: '已登记，待配置入口',
  prepared: '地址已准备',
  revoked: '访问已撤销',
};

export async function openPublicImages(ctx, listing, templateId, applyUrls) {
  let [configuration, publication] = await Promise.all([
    api('/api/public-images/config'),
    api(
      `/api/listings/${listing.id}/public-images?` +
        new URLSearchParams({ template_id: templateId }),
    ),
  ]);
  const rows = () =>
    table(
      ['实际设计图片', '准备状态', '生成的地址'],
      [...arr(publication.items)]
        .sort(compareImageRoles)
        .map((item) => [
          `<div class="table-product">${thumb(item.asset_id, imageRoleLabel(item.role))}<div><strong>${h(imageRoleLabel(item.role))}</strong><p>资产 #${item.asset_id}</p></div></div>`,
          `${badge(item.state === 'prepared' ? 'registered' : item.state === 'revoked' ? 'failed' : 'pending', stateNames[item.state] || item.state)}<p>${item.verified ? '此前已核对当前地址与资产' : '尚未通过公网读取校验'}</p>`,
          item.url
            ? `<span class="public-image-url">${h(item.url)}</span>`
            : '<span class="muted small">尚未配置独立 HTTPS 入口</span>',
        ]),
      '当前模板没有可准备的图片',
    );
  const dialog = modal(
    '准备当前模板的公开图片地址',
    `<div class="stack">${notice('此操作只在本机登记允许读取的实际图片，并组合配置的地址。不会部署公网入口，也不会把准备成功标为图片已可访问。')}<form id="public-image-config"><div class="row-between"><h3>独立 HTTPS 图片入口</h3><span class="muted small" id="public-image-config-revision">配置 v${configuration.revision}</span></div>${field('已经部署或计划使用的图片入口', 'base_url', configuration.base_url || '', { type: 'url', placeholder: 'https://images.example.com/studio-public-media', help: '路径必须为 /studio-public-media。这里只保存地址；尚未部署时，后续真实校验仍会失败。' })}<div class="form-actions">${submit('保存入口配置')}</div></form><section><div class="row-between"><h3>按已保存刊登 v${listing.revision} 与模板 #${templateId} 准备</h3>${button('登记本次实际图片', 'prepare-public-images-now', 'secondary')}</div><p class="muted small" style="margin:12px 0">只处理当前模板映射的图片槽位。已撤销资产再次准备会重新登记其公开读取许可。</p><div id="public-image-records">${rows()}</div></section><div class="form-actions">${button('应用生成地址到当前表单', 'apply-public-image-urls', 'primary', arr(publication.items).some((item) => item.state === 'prepared' && item.url) ? '' : 'disabled')}</div><p class="muted small">应用后仍是未保存草稿。请核对并保存，再执行现有的“保存并验证图片地址”；服务端实际读取 HTTPS 图片并核对哈希。</p><div id="public-image-message"></div></div>`,
  );
  const render = () => {
    $('#public-image-records', dialog).innerHTML = rows();
    $('[data-action=apply-public-image-urls]', dialog).disabled = !arr(publication.items).some(
      (item) => item.state === 'prepared' && item.url,
    );
    $('#public-image-config-revision', dialog).textContent = '配置 v' + configuration.revision;
  };
  bindForm(
    '#public-image-config',
    async (form, values) => {
      configuration = await api('/api/public-images/config', {
        method: 'PUT',
        body: { revision: configuration.revision, base_url: values.base_url },
      });
      publication = await api(
        `/api/listings/${listing.id}/public-images?` +
          new URLSearchParams({ template_id: templateId }),
      );
      render();
      dialog.dataset.dirty = 'false';
      $('#public-image-message', dialog).innerHTML =
        notice('入口配置已保存；这不代表公网已经部署或可访问。');
    },
    dialog,
  );
  bindAction(
    'prepare-public-images-now',
    async () => {
      publication = await api(`/api/listings/${listing.id}/public-images/prepare`, {
        method: 'POST',
        body: { revision: listing.revision, template_id: templateId },
      });
      configuration = publication.configuration || configuration;
      render();
      $('#public-image-message', dialog).innerHTML = notice(
        h(publication.message || '实际图片已登记，尚未修改刊登草稿地址。'),
      );
    },
    dialog,
  );
  bindAction(
    'apply-public-image-urls',
    () => {
      const ready = arr(publication.items).filter(
        (item) => item.state === 'prepared' && item.url?.startsWith('https://'),
      );
      if (!ready.length) throw new Error('还没有已准备的 HTTPS 地址。');
      const applied = applyUrls(ready);
      if (!applied) throw new Error('这些图片与当前表单没有对应关系，请重新打开当前刊登核对版本。');
      closeModal();
      ctx.toast(`已将 ${applied} 个地址应用到当前表单；尚未保存和验证`);
    },
    dialog,
  );
}
