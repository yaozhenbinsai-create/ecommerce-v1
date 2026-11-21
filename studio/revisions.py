"""A single sales-image change creates a new manifest, retaining all old uses."""
from copy import deepcopy
from hashlib import sha256
import re

from fastapi import APIRouter

from . import db, domain as d, providers

router = APIRouter(prefix='/api')
SALES_ROLE = re.compile(r'^(main|sales-[1-9]\d*|detail-[1-9]\d*)$')


@router.post('/designs/{design_id}/revise-image')
def revise_image(design_id: int, body: dict):
    instruction = str(body.get('instruction') or '').strip()
    if not instruction or len(instruction) > 6000:
        raise d.Problem('请填写具体的单图修改要求（不超过6000字）', fields={'instruction': '缺少有效修改要求'})
    try:
        selected_id = int(body.get('design_asset_id'))
    except (ValueError, TypeError):
        raise d.Problem('请选择实际设计成果') from None
    token = str(body.get('intent_key') or '').strip()
    if not token or len(token) > 200:
        raise d.Problem('缺少有效请求标识')
    identity = sha256(db.dump([design_id, selected_id, instruction]).encode()).hexdigest()
    intent = 'image-revision:' + sha256(token.encode()).hexdigest()
    with db.transaction() as cur:
        source = d.get(cur, 'designs', design_id, True)
        old_job = db.one(cur, 'SELECT * FROM jobs WHERE intent_key=%s FOR UPDATE', (intent,))
        if old_job:
            if old_job['kind'] != 'image_revision' or old_job['payload'].get('request_identity') != identity:
                raise d.Problem('此请求标识已用于不同修改', 'intent_conflict', 409)
            return {'design_id': old_job['entity_id'], 'job': d.public_job(old_job)}
        d.revision(source, body)
        if source['status'] != 'completed':
            raise d.Problem('整套设计尚未生成完毕；请先接续原任务或复制完整新版本', 'incomplete_design', 409)
        if d.design_stale(cur, source):
            raise d.Problem('商品事实已变化；请建立采用最新事实的完整设计版本', 'stale_input', 409)
        assets = db.rows(cur, 'SELECT * FROM design_assets WHERE design_id=%s ORDER BY version,id', (design_id,))
        latest = {a['role']: a for a in assets}
        selected = next((a for a in latest.values() if a['id'] == selected_id), None)
        if not selected or not SALES_ROLE.fullmatch(selected['role']):
            raise d.Problem('单图修改仅用于当前销售图或完整详情图；更改母稿/组件图案请复制完整设计版本', 'unsupported_role')
        snapshot = deepcopy(source['input_snapshot'])
        if not all(k in snapshot for k in ('product', 'sku')):
            raise d.Problem('原任务缺少冻结事实，不能静默改用当前商品数据', 'missing_snapshot', 409)
        config = deepcopy(source['config'])
        config['image_revision'] = {'source_design_id': design_id, 'source_design_asset_id': selected_id,
                                    'role': selected['role'], 'instruction': instruction}
        values = {k: deepcopy(source[k]) for k in ('product_id', 'sku_id', 'mode', 'brief', 'source_ids', 'input_revision')}
        values.update(config=config, parent_id=design_id, input_snapshot={}, status='queued')
        new_id = db.insert(cur, 'designs', values)
        retained = []
        for artifact in latest.values():
            if artifact['id'] == selected_id:
                continue
            value = {k: deepcopy(artifact[k]) for k in ('asset_id', 'role', 'label', 'parent_asset_id', 'meta')}
            value.update(design_id=new_id, version=1)
            value['meta'] = {**value['meta'], 'reused_from_design_id': design_id,
                             'reused_from_design_asset_id': artifact['id']}
            db.insert(cur, 'design_assets', value)
            retained.append(artifact['asset_id'])
        new_design = d.get(cur, 'designs', new_id)
        payload = {'design': new_design, 'product': snapshot['product'], 'sku': snapshot['sku'],
                   'sources': snapshot.get('sources', []), 'selected': selected, 'instruction': instruction,
                   'request_identity': identity, 'source_design_id': design_id,
                   'expected_roles': list(latest), 'retained_asset_ids': retained}
        job_id = db.insert(cur, 'jobs', {'kind': 'image_revision', 'entity_id': new_id,
                                       'intent_key': intent, 'payload': payload, 'steps': []})
        db.update(cur, 'designs', new_id, {'job_id': job_id, 'input_snapshot': payload})
        return {'design_id': new_id, 'job': d.public_job(d.get(cur, 'jobs', job_id))}


def run_image_revision(job):
    # Imported lazily: engine owns durable steps and cancellation semantics.
    from . import engine
    payload = job['payload']
    design, selected, sku = payload['design'], payload['selected'], payload['sku']
    with db.transaction() as cur:
        db.update(cur, 'designs', design['id'], {'status': 'running'})
    selected_file = d.get_asset(selected['asset_id'])
    reference_ids = list(dict.fromkeys([selected['asset_id'],
        *selected.get('meta', {}).get('reference_asset_ids', []),
        *([selected['parent_asset_id']] if selected.get('parent_asset_id') else [])]))
    facts = {'name': payload['product']['name'], 'label': sku['label'],
             'facts': sku['facts'], 'components': sku['components']}
    prompt = ('这是对已有销售图片的单张修改。第一个附件是用户指定的修改对象；后续附件是其原母稿，仅用于保持商品/图案一致。'
              '只输出一张修改后的同角色图片。按照本次修改要求改变指定部分，其他构图、商品几何、数量、材质、颜色、图案保持。'
              '不得因修改背景或文案而改变商品事实；未知性能、认证和结构不得补造。不要显示内部SKU、系统字段或操作说明。'
              '不重做整套图组，不把后续附件拼成额外视窗。\n'
              + db.dump({'role': selected['role'], 'facts': facts, 'change': payload['instruction']}))
    if selected['role'] == 'main':
        prompt += '\n这是销售主图，保持无营销文字、无附加边框和无拼贴。'
    if selected['role'].startswith('detail-'):
        prompt += '\n这是完整详情长图，输出一张完整连续排版，保持未要求修改的区域。'
    size = f"{selected_file['width']}x{selected_file['height']}"
    artifact = engine._step(job['id'], 'revise-image', '只修改' + selected['label'],
        lambda checkpoint: engine._generated(job['id'], 'revise-image', design, selected['role'], selected['label'],
            prompt, [engine._asset_path(i) for i in reference_ids], selected['asset_id'], size, checkpoint,
            asset_meta={'edited_from_design_asset_id': selected['id'], 'edited_from_asset_id': selected['asset_id'],
                        'reference_asset_ids': reference_ids, 'instruction': payload['instruction']}), paid=True)
    with db.transaction() as cur:
        roles = {a['role'] for a in db.rows(cur, 'SELECT role FROM design_assets WHERE design_id=%s', (design['id'],))}
        if roles != set(payload['expected_roles']):
            raise providers.ProviderError('新版本图组角色不完整，已保留单图结果，不能标为整套完成。')
        db.update(cur, 'designs', design['id'], {'status': 'completed'})
    return {'design_id': design['id'], 'edited_asset_id': artifact['asset_id'],
            'retained_asset_ids': payload['retained_asset_ids'], 'model_image_requests': 1,
            'visual_verification': 'not_performed', 'listing_changed': False}
