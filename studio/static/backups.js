import {
  $,
  h,
  arr,
  api,
  panel,
  notice,
  field,
  button,
  table,
  link,
  badge,
  date,
  metadata,
  bindAction,
  intent,
  progress,
} from './ui.js';

const size = (value) => `${(Number(value || 0) / 1024 / 1024).toFixed(2)} MB`;
const names = {
  queued: '排队创建',
  running: '备份中',
  creating: '备份中',
  completed: '备份已创建',
  complete: '备份已创建',
  ready: '备份已创建',
  verified: '校验通过',
  failed: '备份失败',
};
function verificationHtml(result) {
  if (!result)
    return '<p class="muted small">还没有本次校验结果。读取目录文件并核对清单后，才能确认备份内容完整。</p>';
  return (
    notice(
      result.ok
        ? `校验通过：已核对 ${result.checked_files || 0} 个文件、${size(result.checked_bytes)}。`
        : `校验未通过：${arr(result.errors).length} 项问题需要处理。`,
      result.ok ? 'info' : 'warning',
    ) +
    (arr(result.errors).length
      ? `<ul class="backup-errors">${result.errors.map((error) => `<li>${h(typeof error === 'string' ? error : error.message || error.error || JSON.stringify(error))}</li>`).join('')}</ul>`
      : '') +
    (result.manifest_sha256
      ? `<p class="muted small">清单 SHA-256：<span class="mono">${h(result.manifest_sha256)}</span></p>`
      : '')
  );
}

export async function backupSettings(ctx) {
  const data = await api('/api/backups');
  const selectedId = ctx.query.get('backup_id');
  const selected = selectedId ? await api('/api/backups/' + encodeURIComponent(selectedId)) : null;
  const job = selected?.job_id ? await api('/api/jobs/' + selected.job_id) : null;
  const cachedCheck = selectedId
    ? JSON.parse(sessionStorage.getItem('backup-verification:' + selectedId) || 'null')
    : null;
  const active = job && ['queued', 'running'].includes(job.status);
  const hasManifest = !!selected?.manifest && !active;
  return {
    body:
      panel(
        '创建完整工作资料备份',
        `<div class="panel-body"><div class="row-between"><div><h3>结构化数据与正式资产一起保留</h3><p class="muted small">创建后台任务，将数据和资产保存到独立本机目录。备份完成与校验通过分别记录。</p></div>${button('创建备份', 'create-backup', 'primary')}</div></div>`,
      ) +
      panel(
        '已有备份',
        table(
          ['创建时间 / 标识', '原数据库', '内容规模', '状态', '最近校验', '操作'],
          arr(data.items).map((item) => [
            `<strong>${date(item.created_at)}</strong><p class="mono">${h(item.id)}</p>`,
            h(item.source_schema || '—'),
            `${item.table_count || 0} 张表 · ${item.rows || 0} 行<p>${item.assets || 0} 份资产 · ${size(item.total_bytes)}</p>`,
            badge(item.status, names[item.status] || item.status),
            `<span data-backup-check="${h(item.id)}">${item.verified_at ? date(item.verified_at) : '尚未校验'}</span>`,
            link('/settings/backups', '查看文件与校验 →', 'text-link', { backup_id: item.id }),
          ]),
          '尚未创建备份',
        ),
      ) +
      (selected
        ? `<div class="section-heading"><h2>备份 ${h(selected.id)}</h2><span>${badge(selected.status, names[selected.status] || selected.status)}</span></div>${job ? panel('创建任务', `<div class="panel-body">${progress(job)}${job.error ? notice(h(job.error), 'error') : ''}${link('/jobs', '查看任务记录 →', 'text-link', { job_id: job.id })}</div>`) : ''}<div class="two-column">${panel(
            '本机目录与完整性',
            `<div class="panel-body">${metadata([
              ['来源数据库', h(selected.source_schema || '—')],
              [
                '备份规模',
                `${selected.table_count || 0} 张表 / ${selected.rows || 0} 行 / ${selected.assets || 0} 份资产`,
              ],
              ['内容大小', size(selected.total_bytes)],
              ['创建时间', date(selected.created_at)],
            ])}${selected.directory ? `<div style="margin-top:18px">${field('完整备份所在目录', 'backup_directory', selected.directory, { readonly: true, help: '整份备份由此目录中的数据文件、资产和清单组成。单独清单不能用于恢复。' })}<div class="row" style="margin-top:10px">${button('复制目录路径', 'copy-backup-directory', 'secondary small')}${hasManifest ? `<a class="button secondary small" href="/api/backups/${encodeURIComponent(selected.id)}/download" download>下载校验清单</a>` : ''}</div></div>` : ''}${selected.error ? notice(h(selected.error), 'error') : ''}<section class="form-section"><div class="row-between"><h3>逐文件校验</h3>${button('核对备份完整性', 'verify-backup', 'secondary small', hasManifest ? '' : 'disabled')}</div><div id="backup-verification">${verificationHtml(selected.verification || cachedCheck)}</div></section></div>`,
          )}${panel('恢复到新的数据库', `<div class="panel-body">${notice('恢复使用独立的新数据库和新目录，已有工作资料保持原样。这里提供命令和依据，不在网页中执行覆盖恢复。')}${hasManifest && selected.restore_command ? `${field('恢复命令', 'restore_command', selected.restore_command, { readonly: true, textarea: true, rows: 5, help: '先完成备份校验，再按命令指定新的数据库与目录。' })}<div class="form-actions">${button('复制恢复命令', 'copy-restore-command', 'secondary')}</div>` : '<p class="muted small">备份完成后显示服务端提供的恢复命令。</p>'}</div>`)}</div>`
        : notice('打开一份备份，查看实际目录、任务进度和校验结果。')),
    bind() {
      bindAction('create-backup', async () => {
        const key = sessionStorage.getItem('backup-create-intent') || intent('backup');
        sessionStorage.setItem('backup-create-intent', key);
        const result = await api('/api/backups', { method: 'POST', body: { intent_key: key } });
        sessionStorage.removeItem('backup-create-intent');
        ctx.saved(null, '备份任务已建立');
        ctx.go('/settings/backups', { backup_id: result.backup_id });
      });
      bindAction('verify-backup', async () => {
        const result = await api(`/api/backups/${encodeURIComponent(selected.id)}/verify`, {
          method: 'POST',
          body: {},
        });
        sessionStorage.setItem('backup-verification:' + selected.id, JSON.stringify(result));
        $('#backup-verification').innerHTML = verificationHtml(result);
        const listCheck = $(`[data-backup-check="${CSS.escape(selected.id)}"]`);
        if (listCheck)
          listCheck.textContent = `${date(result.verified_at)} · ${result.ok ? '通过' : '未通过'}`;
        ctx.toast(
          result.ok ? '备份完整性校验通过' : '备份校验未通过，请查看具体问题',
          result.ok ? undefined : 'error',
        );
      });
      const copy = async (name) => {
        const input = $(`[name=${name}]`);
        if (!input) return;
        try {
          await navigator.clipboard.writeText(input.value);
          ctx.toast('已复制');
        } catch {
          input.focus();
          input.select();
          ctx.toast('已选中内容，请复制后使用');
        }
      };
      bindAction('copy-backup-directory', () => copy('backup_directory'));
      bindAction('copy-restore-command', () => copy('restore_command'));
      if (active) return ctx.bindRefresh(() => ctx.reload(), 5000);
    },
  };
}
