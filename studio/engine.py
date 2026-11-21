"""Persistent, sequential business jobs with immutable inputs and resumable steps."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import threading
import time

from PIL import Image

from . import db, providers
from .patterns import normalize_design_patterns, requested_canvas, raster_measurements, component_role, PatternError

STOP = threading.Event()
_THREAD = None


class Cancelled(RuntimeError):
    pass


def _json(value):
    return db.dump(value)


def supplier_master_prompt(facts):
    """A single-stage brief; whole-set/page requests never enter the master."""
    return (
        '当前只执行供应商展示流程的第1阶段：生成一张单商品中性实物母稿。'
        '这不是销售图、详情图或整套图片方案。本次最终画面只允许一个连续场景、一个视角、一个完整商品主体。'
        '使用自然中性的3/4视角，让真实外形、厚度、颜色分区和可见表面纹理清楚可辨；'
        '商品完整置于纯净白色或极浅中性背景，柔和均匀摄影光和轻微接触阴影，四周留合理空白。'
        '附件是当前SKU唯一真实原图依据，保持其可见几何、相对比例、材质表面、图案、颜色、缝线和销售组成。'
        '不能把来源宣传画面的布局、文字、其他角度、旁边道具或其他规格复制到母稿。'
        '严禁人物、手、使用场景、床或其他道具；严禁拼贴、多格、多角度、分镜、放大窗、局部特写窗、'
        '对比图、结构拆解、爆炸图、剖面、透明透视、悬浮填充物、尺寸箭头、图标和营销文字。'
        '看不到的内部结构不能凭材料名称臆造，不能拆开或改变商品来展示其填充。'
        '真实信息不确定时忠实保留原图，不增加形状、性能或部件。'
        '用户关于销售图、详情图的数量、内容及其他整套要求只在后续策划阶段执行，本次不得提前画出或合并。'
        '\n以下JSON只用于约束商品身份与真实事实，不是画面排版或需要印上的文字：\n'
        +_json(facts))


def validate_page_copy(pages,internal_sku):
    for page in pages:
        copy=page.get('copy')
        if not isinstance(copy,str): raise providers.ProviderError('页面文案必须是字符串，不能混入内部对象或执行参数。')
        if internal_sku and str(internal_sku).casefold() in copy.casefold():
            raise providers.ProviderError('页面顾客文案包含系统内部SKU，方案已保留但不会用于出图。请在新版本中修正策划。')
        if page.get('role')=='main' and copy.strip():
            raise providers.ProviderError('销售主图方案包含营销文案，主图copy必须为空；方案已保留，没有默许进入成图。')


def _checkpoint_path(job_id, key, suffix='json'):
    directory = db.DATA / 'checkpoints' / str(int(job_id))
    directory.mkdir(parents=True, exist_ok=True)
    safe = hashlib.sha256(key.encode()).hexdigest()[:24]
    return directory / (safe + '.' + suffix)


def _atomic(path, content):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('wb') as file:
        file.write(content)
        file.flush()
        import os
        os.fsync(file.fileno())
    temporary.replace(path)


def _save_steps(job_id, steps, **changes):
    completed = sum(row['status'] == 'succeeded' for row in steps)
    return db.update_job(job_id, steps=steps, progress=min(99, round(completed / max(1, len(steps)) * 95)), **changes)


def _step(job_id, key, label, function, *, paid=False):
    job = db.get_job(job_id)
    if job['cancel_requested'] or STOP.is_set():
        raise Cancelled('已停止领取后续步骤；已发出的结果仍保留。')
    steps = job['steps']
    step = next((row for row in steps if row['key'] == key), None)
    if step and step['status'] == 'succeeded':
        return step.get('result')
    result_path = _checkpoint_path(job_id, key)
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding='utf-8'))
        if step is None:
            step = {'key': key, 'label': label, 'paid': paid}
            steps.append(step)
        step.update(status='succeeded', result=result, error=None)
        _save_steps(job_id, steps, stage=label)
        return result
    if step and step['status'] in ('unknown', 'running'):
        raise providers.ProviderError('原步骤结果未知，禁止重新发送：' + label, unknown=True)
    if step is None:
        step = {'key': key, 'label': label, 'paid': paid}
        steps.append(step)
    elif step.get('recovery_status')=='known_no_output':
        # The original identity remains in attempt_history; an explicit resume
        # starts a separate attempt and must not inherit its codex thread ID.
        previous=step.get('request_ref') or {}
        if previous.get('prompt'):step['frozen_prompt']=previous['prompt']
        if previous.get('size'):step['frozen_size']=previous['size']
        step['request_ref']={}
        step.pop('recovery_status',None)
    step.update(status='running', error=None)
    _save_steps(job_id, steps, stage=label)

    def checkpoint(ref):
        step['request_ref'] = {**step.get('request_ref', {}), **ref}
        # Persist the request identity before the adapter continues.
        _save_steps(job_id, steps, stage=label)

    try:
        result = function(checkpoint)
        _atomic(result_path, _json(result).encode('utf-8'))
        step.update(status='succeeded', result=result, error=None)
        if isinstance(result, dict) and result.get('asset_id'):
            step['asset_id'] = result['asset_id']
        _save_steps(job_id, steps, stage=label)
        return result
    except providers.ProviderError as exc:
        step.update(status='unknown' if exc.unknown else 'failed', error=str(exc))
        if exc.request_ref:
            step['request_ref'] = {**step.get('request_ref', {}), **exc.request_ref}
        _save_steps(job_id, steps)
        raise
    except Exception as exc:
        # Paid requests which returned bytes have a durable local image/text
        # checkpoint; database failures can safely finish from those bytes.
        cached = _checkpoint_path(job_id, key, 'image').exists() or _checkpoint_path(job_id, key, 'txt').exists()
        unknown = paid and bool(step.get('request_ref')) and not cached
        step.update(status='unknown' if unknown else 'failed', error='步骤保存或处理失败：' + type(exc).__name__)
        _save_steps(job_id, steps)
        if unknown:
            raise providers.ProviderError('请求已进入适配器但未保存结果；禁止重新发送。', unknown=True) from None
        raise


def _model_json(job_id, key, prompt, paths=(), *, model_id=None, checkpoint=lambda _: None):
    path = _checkpoint_path(job_id, key, 'txt')
    previous=next((s for s in db.get_job(job_id)['steps'] if s['key']==key),{})
    prompt=previous.get('frozen_prompt') or (previous.get('request_ref') or {}).get('prompt') or prompt
    if path.exists():
        text = path.read_text(encoding='utf-8')
    else:
        checkpoint({'prompt': prompt})
        text = providers.text(prompt, paths, model_id=model_id, checkpoint=checkpoint)
        _atomic(path, text.encode('utf-8'))
    clean = text.strip()
    if clean.startswith('```'):
        clean = clean.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        result = json.loads(clean)
    except ValueError:
        raise providers.ProviderError('模型返回的结构化内容无效；原文已保存，需修正任务书后创建新版本。') from None
    if not isinstance(result, dict):
        raise providers.ProviderError('模型必须返回 JSON 对象；原文已保存。')
    return result


def _asset_path(asset_id):
    from . import domain
    asset = domain.get_asset(int(asset_id))
    return Path(asset['path'])


def _generated(job_id, key, design, role, label, prompt, paths, parent_asset_id, size, checkpoint, *, physical=None, asset_meta=None):
    from . import domain
    # The local Codex image bridge has no size argument. Include the intended
    # canvas in the exact prompt used by every transport and its saved hash.
    prompt += f'\n本次输出画布规格：{size} 像素。按此宽高关系构图；不要把规格文字画进图片。'
    with db.transaction() as cur:
        existing = db.one(cur, 'SELECT * FROM design_assets WHERE design_id=%s AND role=%s AND version=1', (design['id'], role))
    if existing:
        return existing
    path = _checkpoint_path(job_id, key, 'image')
    previous=next((s for s in db.get_job(job_id)['steps'] if s['key']==key),{})
    ref=previous.get('request_ref') or {}
    prompt=previous.get('frozen_prompt') or ref.get('prompt') or prompt
    size=previous.get('frozen_size') or ref.get('size') or size
    if not path.exists():
        checkpoint({'prompt': prompt, 'size': size})
        raw = providers.image(prompt, paths, size=size, model_id=design['config'].get('image_model_id'), checkpoint=checkpoint)
        _atomic(path, raw)
    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as image:
        actual_size=image.size
        image.verify()
    measured={}
    if physical:
        try: measured=raster_measurements(*actual_size,*physical)
        except PatternError as exc: raise providers.ProviderError(str(exc)) from None
    asset = domain.store_asset(raw, f"design-{design['id']}-{role}.png", 'image/png')
    with db.transaction() as cur:
        existing = db.one(cur, 'SELECT * FROM design_assets WHERE design_id=%s AND role=%s AND version=1', (design['id'], role))
        if existing:
            return existing
        ident = db.insert(cur, 'design_assets', {'design_id': design['id'], 'asset_id': asset['id'], 'role': role,
            'label': label, 'version': 1, 'parent_asset_id': parent_asset_id,
            'meta': {'width': asset.get('width'), 'height': asset.get('height'), 'sku_id': design['sku_id'],
                     'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(), 'input_revision': design.get('input_revision'),
                     'source_ids': design.get('source_ids', []), 'quality_status': 'not_visually_verified',
                     'requested_size':size,'measurements':measured,**(asset_meta or {})}})
        return db.one(cur, 'SELECT * FROM design_assets WHERE id=%s', (ident,))


def _research(job):
    payload = job['payload']
    kind = payload.get('kind', 'market')
    platforms = payload.get('platforms') or (['开放热点'] if kind == 'hotspot' else [])
    if kind != 'hotspot' and not payload.get('product_id'):
        raise providers.ProviderError('市场与社交研究必须先选择目标商品。')
    if not platforms or not str(payload.get('query', '')).strip():
        raise providers.ProviderError('请选择来源平台并填写研究主题。')
    all_ids = [];failure_reasons=[]
    for index, platform in enumerate(platforms):
        def discover(checkpoint, platform=platform, index=index):
            import base64
            from . import domain
            cached=_checkpoint_path(job['id'],f'search-{index}','research')
            if cached.exists():result=json.loads(cached.read_text(encoding='utf-8'))
            else:
                result=providers.research(payload['query'],platform,hotspot=kind=='hotspot',limit=payload.get('limit',6),urls=payload.get('direct_urls'),checkpoint=checkpoint)
                _atomic(cached,_json(result).encode('utf-8'))
            # Large image bytes remain in the disk checkpoint, never job JSON
            # or UI polling. A database failure resumes from the saved response.
            for row in result.get('items',[]):
                thumbnail=row.pop('thumbnail',None)
                if thumbnail:
                    raw=base64.b64decode(thumbnail['data'],validate=True)
                    asset=domain.store_asset(raw,'source-image.png',thumbnail.get('mime','image/png'),kind='research')
                    row['image_asset_id']=asset['id'];row['image_url']=thumbnail['url']
            return result
        found = _step(job['id'], f'search-{index}', '检索并读取 ' + platform, discover)
        if not found.get('items'):
            reasons=[str(a.get('provider'))+'：'+str(a.get('error') or '没有符合当前范围的详情') for a in found.get('attempts',[]) if a.get('outcome') in {'blocked','failure'}]
            failure_reasons.append(platform+'：'+'；'.join(list(dict.fromkeys(reasons))[:3]) if reasons else platform+'：公开搜索没有返回符合范围的详情')
        def persist(checkpoint, found=found, platform=platform):
            ids = []
            with db.transaction() as cur:
                for row in found['items']:
                    url = row.get('resolved_url') or row['url']
                    fingerprint = hashlib.sha256(_json([kind, platform, url, payload.get('product_id')]).encode()).hexdigest()
                    old = db.one(cur, 'SELECT * FROM sources WHERE fingerprint=%s', (fingerprint,))
                    metadata={'provider':found.get('provider'),'access_status':row.get('access_status'),'access_error':row.get('access_error'),
                        'published_at':row.get('published_at'),'query':found.get('query'),'evidence_level':'page_text' if row.get('body') else 'search_snippet' if row.get('snippet') else 'url_only',
                        'cross_category':bool(payload.get('cross_category')),'acquisition':row.get('acquisition',payload.get('acquisition','automatic_search')),
                        'image_asset_id':row.get('image_asset_id'),'image_url':row.get('image_url'),'image_status':row.get('image_status'),
                        'image_error':row.get('image_error'),'attempts':found.get('attempts',[]),'page_reader':row.get('page_reader'),'discovered_from':row.get('discovered_from')}
                    title=row.get('title',url)[:500];excerpt=(row.get('body') or row.get('snippet') or '')[:12000]
                    if old:
                        # Re-observation retains the source identity and adopted
                        # snapshots. Failed reads never erase earlier evidence.
                        if not excerpt:excerpt=old['excerpt']
                        if title==url:title=old['title']
                        if not metadata['image_asset_id']:
                            for key in ('image_asset_id','image_url'):metadata[key]=(old.get('meta') or {}).get(key)
                        substantive=title!=old['title'] or excerpt!=old['excerpt'] or metadata.get('image_asset_id')!=(old.get('meta') or {}).get('image_asset_id')
                        db.update(cur,'sources',old['id'],{'title':title,'excerpt':excerpt,'meta':metadata,'revision':old['revision']+int(substantive)})
                        cur.execute('UPDATE sources SET observed_at=CURRENT_TIMESTAMP WHERE id=%s',(old['id'],))
                        ids.append(old['id'])
                        continue
                    ident = db.insert(cur, 'sources', {'kind': kind, 'platform': platform, 'url': url[:2000],
                        'title':title,'excerpt':excerpt,
                        'source_category': payload.get('source_category', ''), 'product_id': payload.get('product_id') if kind != 'hotspot' else None,
                        'elements': [], 'job_id': job['id'], 'fingerprint': fingerprint,
                        'meta':metadata})
                    ids.append(ident)
            return {'source_ids': ids, 'count': len(ids)}
        persisted = _step(job['id'], f'persist-{index}', '保存 ' + platform + ' 的来源与证据', persist)
        all_ids.extend(persisted['source_ids'])
    if not all_ids:
        raise providers.ProviderError('本次没有取得可用来源。'+' / '.join(failure_reasons)+ '。原查询和访问记录已保留；人工验证或限流不会自动绕过。')
    return {'source_ids': list(dict.fromkeys(all_ids)), 'count': len(set(all_ids))}


def repeat_canvas(tile, width, height, mode='basic'):
    """Deterministic preview/production tiling, with no image synthesis."""
    if mode not in ('basic', 'half-drop'):
        raise providers.ProviderError('当前实际支持基础循环 basic 和半落位 half-drop。')
    tile = tile.convert('RGBA')
    tw, th = tile.size
    canvas = Image.new('RGBA', (width, height), 'white')
    for column, x in enumerate(range(-tw, width + tw, tw)):
        shift = th // 2 if mode == 'half-drop' and column % 2 else 0
        for y in range(-th, height + th, th):
            canvas.alpha_composite(tile, (x, y + shift))
    return canvas


def _repeat_preview(job, design, unit, checkpoint, *, settings=None, role='repeat-preview', component_key=None):
    from . import domain
    with Image.open(_asset_path(unit['asset_id'])) as original:
        tile = original.convert('RGB')
        tile.thumbnail((600, 600))
        width, height = tile.size
        repeat = settings or design['config'].get('repeat', {})
        mode = repeat.get('mode', 'basic')
        preview = repeat_canvas(tile, width * 3, height * 3, mode)
        buf = io.BytesIO(); preview.save(buf, 'PNG')
    asset = domain.store_asset(buf.getvalue(), f"design-{design['id']}-{role.replace(':','-')}.png", 'image/png')
    with db.transaction() as cur:
        existing = db.one(cur, 'SELECT * FROM design_assets WHERE design_id=%s AND role=%s AND version=1', (design['id'], role))
        if existing:
            return existing
        ident = db.insert(cur, 'design_assets', {'design_id': design['id'], 'asset_id': asset['id'], 'role': role,
            'label': (unit.get('label','组件')+' · 3×3 拼接预览') if component_key else '3×3 拼接预览', 'version': 1, 'parent_asset_id': unit['asset_id'],
            'meta': {'repeat': repeat,'component_key':component_key, 'preview_only': True, 'production_ready': False, 'quality_status': 'not_visually_verified'}})
        return db.one(cur, 'SELECT * FROM design_assets WHERE id=%s', (ident,))


def _design(job):
    payload = job['payload']
    design, product, sku = payload['design'], payload['product'], payload['sku']
    try: config=normalize_design_patterns(design.get('config') or {},sku,design['mode'])
    except PatternError as exc: raise providers.ProviderError(str(exc)) from None
    design['config'] = config
    visual_preferences={key:config[key] for key in ('style','palette','scene') if config.get(key) not in (None,'',[],{})}
    preference_scope=('冻结视觉配置是实际执行要求：style用于视觉风格，palette用于允许的配色，scene用于非主图销售场景。'
        '这些偏好不能改变真实商品的材料、颜色、数量、形状或尺寸；供应商原图和结构化事实优先。'
        '平面母稿保持平面图案，供应商母稿保持单商品中性背景，不把scene变成母稿摄影背景。'
        '主图main始终纯白背景；scene只在其他销售图中执行，不覆盖主图白底。')
    facts = {'name': product['name'], 'label': sku['label'], 'facts': sku['facts'], 'components': sku['components']}
    sources = [{'id': s['id'], 'title': s['title'], 'url': s['url'], 'excerpt': s.get('excerpt', '')[:4500],
                'elements': s.get('adopted_elements',s.get('elements', [])), 'meta': s.get('meta')} for s in payload.get('sources', [])]
    with db.transaction() as cur:
        db.update(cur, 'designs', design['id'], {'status': 'running'})
    supplier = design['mode'] == 'supplier'
    repeat = design['mode'] == 'repeat'
    if supplier and not sku.get('original_asset_id'):
        raise providers.ProviderError('供应商设计缺少当前 SKU 的有效原图，不能借用其他规格图片。')
    route = None
    if not supplier:
        source_asset_ids=list(dict.fromkeys((s.get('meta') or {}).get('image_asset_id') for s in payload.get('sources',[]) if (s.get('meta') or {}).get('image_asset_id')))
        source_paths=[_asset_path(ident) for ident in source_asset_ids]
        sources=[{**source,**({'image_reference_index':source_asset_ids.index(source['meta']['image_asset_id'])+1} if (source.get('meta') or {}).get('image_asset_id') in source_asset_ids else {})} for source in sources]
        count = max(1, min(6, int(config.get('route_count', 3))))
        prompt = ('你是电商产品原创设计师。只输出 JSON，不调用工具。基于冻结资料形成实质不同的创意路线，'
                  '允许跨品类借鉴元素但不复制原图、品牌、角色、构图。热点事实和创意推演分开。'
                  '不得给真实商品增加未提供的材料、尺寸、功能。没有额外 AI 审核环节。\n'
                  f'输出恰好 {count} 条 routes，每条含 name,story,palette,composition,master_prompt；'
                  '另含 recommended_index（从0开始），选择理由只为设计方案建议。\n'
                  + preference_scope+'\n'
                  + _json({'product': facts, 'sources': sources, 'brief': design['brief'], 'mode': design['mode'], 'visual_preferences':visual_preferences, **({'repeat':config['repeat']} if repeat and config.get('repeat') else {}),'component_plans':config.get('component_plans',[])}))
        prompt += '\n如有来源图片附件，只用于观察用户已采用的元素、风格和结构关系；不可直接复制来源商品、商标或把来源图片当作目标商品事实。'
        routes = _step(job['id'], 'routes', '形成原创创意路线', lambda checkpoint: _model_json(job['id'], 'routes', prompt,source_paths,
                       model_id=config.get('text_model_id'), checkpoint=checkpoint), paid=True)
        if len(routes.get('routes', [])) != count:
            raise providers.ProviderError('创意路线数量与任务书不符；原响应已保存，请修正任务书创建新版本。')
        selected = int(config.get('selected_route', routes.get('recommended_index', 0)))
        if not 0 <= selected < count:
            raise providers.ProviderError('选择的创意路线编号超出范围。')
        route = routes['routes'][selected]
        if not isinstance(route, dict) or not route.get('master_prompt'):
            raise providers.ProviderError('所选创意路线缺少可执行母稿说明。')
    if supplier:
        prompt = supplier_master_prompt(facts)
        paths = [_asset_path(sku['original_asset_id'])]
    else:
        prompt = ('依据原创路线生成' + ('可无缝四方连续拼接的独立印花循环单元，正交边缘连续，平面图案，不做商品效果图，不加文字。' if repeat else
                  '完整大印花设计母稿，完整构图与视觉主次，不做页面拼贴，不加无依据营销文字。')
                  + '\n遵守真实产品组件适配：' + _json(facts) + '\n原创路线：' + _json(route)
                  + '\n任务要求：' + design['brief'] + ('\n循环单元参数：' if repeat else '\n组件参数：') + _json(config.get('repeat') or config.get('components') or []))
        paths = []
    prompt+='\n'+preference_scope+'\n冻结视觉配置：'+_json(visual_preferences)
    if config.get('reference_asset_id'):
        # A fork carries the explicitly selected artifact as an edit target.
        # It never turns that generated image into supplier facts.
        paths = [*paths, _asset_path(config['reference_asset_id'])]
        prompt += '\n最后一个附件是用户明确选中的现有设计修改目标；按本次任务要求修改，保留未要求改变的设计部分。商品事实仍只来自冻结资料。'
    masters=[]; master_contracts=[]
    component_plans=config.get('component_plans') or []
    has_repeat=repeat or any(plan['mode']=='repeat' for plan in component_plans)
    if component_plans:
        for plan in component_plans:
            key=plan['component_key']; is_repeat=plan['mode']=='repeat'; component=plan['component_snapshot']
            physical=(plan['repeat']['unit_width_mm'],plan['repeat']['unit_height_mm']) if is_repeat else (plan['width_cm']*10,plan['height_cm']*10)
            canvas=requested_canvas(*physical)
            role=component_role(key,'tile' if is_repeat else 'master')
            label=component.get('name',key)+(' · 循环单元' if is_repeat else ' · 完整构图母稿')
            component_prompt=('只为指定真实组件生成一张独立平面图案，不生成产品摄影或多组件拼贴。'
                +('四边可连续拼接的循环单元，保持声明的单元物理宽高比例。' if is_repeat else '完整大印花构图，主体完整，按组件物理宽高比例布置，不裁切主体。')
                +'此组件与其他组件共用原创主题，但独立保存尺寸、构图和产物；禁止把其他组件混进本画布。\n'
                +preference_scope+'\n'
                +_json({'product_facts':facts,'route':route,'overall_brief':design['brief'],'component_plan':plan,'canvas':canvas,'visual_preferences':visual_preferences}))
            art=_step(job['id'],role,'生成'+label,lambda checkpoint,role=role,label=label,component_prompt=component_prompt,canvas=canvas,physical=physical,plan=plan:
                _generated(job['id'],role,design,role,label,component_prompt,paths,None,canvas['size'],checkpoint,
                    physical=physical,asset_meta={'component_key':plan['component_key'],'pattern_mode':plan['mode'],'component_plan':plan,'repeat':plan.get('repeat')}),paid=True)
            masters.append(art)
            master_contracts.append({'reference_index':len(masters),'asset_id':art['asset_id'],'component_key':key,'name':component.get('name',key),'mode':plan['mode'],**({'repeat':plan['repeat']} if is_repeat else {}),'component':component})
            if is_repeat:
                preview_role=component_role(key,'repeat-preview')
                _step(job['id'],preview_role,'生成'+label+'的 3×3 拼接预览',lambda checkpoint,art=art,plan=plan,preview_role=preview_role:
                    _repeat_preview(job,design,art,checkpoint,settings=plan['repeat'],role=preview_role,component_key=plan['component_key']))
    else:
        physical=(config['repeat']['unit_width_mm'],config['repeat']['unit_height_mm']) if repeat else None
        canvas=requested_canvas(*physical) if physical else {'size':'1536x1024'}
        master = _step(job['id'], 'master', '生成本 SKU 母稿' if supplier else '生成循环花型单元' if repeat else '生成原创母稿',
                       lambda checkpoint: _generated(job['id'], 'master', design, 'tile' if repeat else 'master',
                       '循环花型单元' if repeat else '设计母稿', prompt, paths, None, canvas['size'], checkpoint,
                           physical=physical,asset_meta={'pattern_mode':'repeat' if repeat else 'supplier' if supplier else 'large','repeat':config.get('repeat')}), paid=True)
        masters.append(master)
        master_contracts.append({'reference_index':1,'mode':design['mode'],**({'repeat':config['repeat']} if repeat else {})})
        if repeat:
            _step(job['id'], 'repeat-preview', '生成可检查的 3×3 拼接预览', lambda checkpoint: _repeat_preview(job, design, master, checkpoint))
    reference_paths=[_asset_path(art['asset_id']) for art in masters]
    sales = max(1, min(12, int(config.get('sales_count', 7 if supplier else 4))))
    details = max(0, min(3, int(config.get('detail_count', 1))))
    roles = [('main', '销售主图', '1024x1024')] + [(f'sales-{i+1}', f'销售图 {i+1}', '1536x1024') for i in range(1, sales)]
    roles += [(f'detail-{i+1}', '完整详情图' + (f' {i+1}' if details > 1 else ''), '1024x1536') for i in range(details)]
    planning = ('你是商品视觉策划。仅输出 JSON 对象 {"pages":[...]}。必须为每个角色一次规划一个完整方案，'
                '每页含 role,copy,visual。copy 是唯一允许印刷的顾客文案，visual 是镜头、场景、构图、光照说明。'
                '附件依次对应reference_contracts内的本SKU母稿或独立组件图案，所有页同源，不得互相派生或改造产品事实。'
                '主图突出销售主体，完整详情图一次生成完整连续排版，不拆成零散图片。'
                '原创大印花完整保留构图。'
                +('循环组件以其对应单元按声明的真实尺度重复，不能替换花型；非循环组件不得使用循环或offset指令。' if has_repeat else '当前没有循环花型组件，不得出现平铺、循环单元、offset或花型密度的画面指令。')
                +'主图角色main的copy必须为空字符串，visual必须为纯白RGB(255,255,255)背景上的单个实际销售主体；无床、房间、其他道具、分格或文字。卧室或生活方式场景仅可用于其他sales角色。'
                '其他copy只允许顾客可见的商品文案，严禁内部SKU编码、数据库ID、任务号、流程说明或执行参数。'
                '无来源的性能、认证、重量、材料和尺寸不得编造，真实近似值、单位、范围和销售组成必须保留。\n'
                +preference_scope+'\n'
                + _json({'roles': [r[0] for r in roles], 'product': facts, 'brief': design['brief'], 'route': route,'visual_preferences':visual_preferences,
                         'language': config.get('language', 'en'),**({'repeat':config['repeat']} if repeat and config.get('repeat') else {}),'reference_contracts':master_contracts}))
    plans = _step(job['id'], 'planning', '规划整套销售图与完整详情图', lambda checkpoint:
                  _model_json(job['id'], 'planning', planning, reference_paths,
                              model_id=config.get('text_model_id'), checkpoint=checkpoint), paid=True)
    pages = plans.get('pages', [])
    if not isinstance(pages, list) or len(pages) != len(roles) or {p.get('role') for p in pages if isinstance(p, dict)} != {r[0] for r in roles}:
        raise providers.ProviderError('整套方案缺页、重复或角色不符；没有跳过缺失图片。')
    validate_page_copy(pages,sku['code'])
    assets = list(masters)
    for role, label, size in roles:
        page = dict(next(p for p in pages if p['role'] == role))
        if not page.get('visual'):
            raise providers.ProviderError('方案缺少画面说明：' + label)
        if role=='main':
            page['visual']='纯白RGB(255,255,255)无缝背景。画面仅包含当前SKU实际销售主体，完整且居中，忠实保持母稿身份和真实销售数量；不出现床、床头、卧室、人物、手、附加道具、分格、局部窗、文字、图标或边框。可保留极轻微真实接触阴影，白色背景不能偏灰。'
        prompt = ('只生成一张完整图片。附件均为同一SKU的母稿/独立组件图案，按reference_contracts的附件序号分别用于指定组件，忠实保持商品或图案，不依赖其他效果图。'
                  '严格区分可印刷 copy 与不可印刷的 visual 执行说明。不得把内部字段名、SKU、任务指令印到成品。'
                  '保持真实商品数量、材料、构件与图案；不得新增事实。\n'+preference_scope+'\n' + _json({'facts': facts, 'page': page,
                      'visual_preferences':{k:v for k,v in visual_preferences.items() if role!='main' or k!='scene'},
                      'output_size': size, **({'repeat':config['repeat']} if repeat and config.get('repeat') else {}), 'components': sku['components'],'reference_contracts':master_contracts}))
        if role == 'main':
            prompt += '\n主图白底规则为本次构图的强制条件：背景必须纯白RGB(255,255,255)，只展示实际销售主体；不得沿用策划中任何床景、道具或营销文字。'
        if role.startswith('detail'):
            prompt += '\n所有详情区域自然组织在这一张完整纵向图片中。'
        asset = _step(job['id'], role, '生成' + label, lambda checkpoint, role=role, label=label, size=size, prompt=prompt:
                      _generated(job['id'], role, design, role, label, prompt, reference_paths,
                                 masters[0]['asset_id'] if len(masters)==1 else None, size, checkpoint,
                                 asset_meta={'reference_asset_ids':[a['asset_id'] for a in masters],'reference_contracts':master_contracts}), paid=True)
        assets.append(asset)
    with db.transaction() as cur:
        saved = db.rows(cur, 'SELECT * FROM design_assets WHERE design_id=%s', (design['id'],))
        if not {r[0] for r in roles}.issubset({a['role'] for a in saved}):
            raise providers.ProviderError('设计成果缺失，不能标为成套完成。')
        db.update(cur, 'designs', design['id'], {'status': 'completed'})
    return {'design_id': design['id'], 'asset_ids': [a['asset_id'] for a in assets], 'sales_count': sales, 'detail_count': details,
            'visual_verification': 'not_performed', 'production_ready': False}


def _copy(job):
    payload = job['payload']
    listing, product, sku, target = (payload[k] for k in ('listing', 'product', 'sku', 'target'))
    prompt = ('为指定目标店铺撰写顾客文案，仅输出 JSON {"title":"","bullets":[],"description":"","search_terms":""}。'
              '根据真实商品用途、购买理由与销售组成，用目的地语言自然表达。不能编造性能、认证、材质、尺寸、价格或物流。'
              '不把内部 SKU、核对说明、缺项或系统流程印到顾客文案。保留真实单位、范围、近似值及规格区别。'
              '仅给文案建议，不改变商品事实或报价。\n' + _json({'product': product['name'], 'sku': sku['label'],
                'facts': sku['facts'], 'components': sku['components'], 'target': target, 'existing': listing.get('fields', {})}))
    suggestion = _step(job['id'], 'copy', '生成目的地文案建议', lambda checkpoint:
                       _model_json(job['id'], 'copy', prompt, model_id=(payload.get('routing') or {}).get('text_model_id'), checkpoint=checkpoint), paid=True)
    if not all(k in suggestion for k in ('title', 'bullets', 'description', 'search_terms')) or not isinstance(suggestion['bullets'], list):
        raise providers.ProviderError('文案响应缺少标题、卖点、描述或搜索词；原稿已保存。')
    return {'listing_id': listing['id'], 'listing_revision': listing['revision'], 'suggestion': suggestion,
            'applied': False, 'message': '建议已保存；在刊登编辑器中应用后保存，不覆盖现有人工内容。'}


def run_job(job_id):
    job = db.get_job(job_id)
    try:
        from .revisions import run_image_revision
        from .backups import run_backup
        handler = {'research': _research, 'design': _design, 'copy': _copy,'image_revision':run_image_revision,'backup':run_backup}.get(job['kind'])
        if handler is None:
            raise providers.ProviderError('未实现的任务类型：' + job['kind'])
        result = handler(job)
        db.update_job(job_id, status='succeeded', stage='已完成', progress=100, result=result, error=None)
    except Cancelled as exc:
        db.update_job(job_id, status='cancelled', stage='已停止', error=str(exc))
        if job['kind'] in {'design','image_revision'}:
            with db.transaction() as cur:
                db.update(cur, 'designs', job['entity_id'], {'status': 'cancelled'})
    except Exception as exc:
        unknown = isinstance(exc, providers.ProviderError) and exc.unknown
        error = str(exc) if isinstance(exc, providers.ProviderError) else '任务处理失败：' + type(exc).__name__
        db.update_job(job_id, status='unknown' if unknown else 'failed', error=error)
        if job['kind'] in {'design','image_revision'}:
            with db.transaction() as cur:
                db.update(cur, 'designs', job['entity_id'], {'status': 'unknown' if unknown else 'failed'})


def _worker():
    while not STOP.is_set():
        try:
            with db.transaction() as cur:
                job = db.one(cur, "SELECT * FROM jobs WHERE status='queued' AND cancel_requested=FALSE ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED")
                if job:
                    db.update(cur, 'jobs', job['id'], {'status': 'running', 'stage': '开始处理', 'error': None})
            if job:
                run_job(job['id'])
            else:
                STOP.wait(1)
        except Exception:
            # Database outages retain the durable step checkpoints and do not
            # become a retry loop issuing external requests.
            STOP.wait(3)


def start_worker():
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    with db.transaction() as cur:
        interrupted = db.rows(cur, "SELECT * FROM jobs WHERE status='running'")
        for job in interrupted:
            unknown = False
            for step in job['steps']:
                if step['status'] == 'running':
                    result = _checkpoint_path(job['id'], step['key'])
                    media = _checkpoint_path(job['id'], step['key'], 'image')
                    text = _checkpoint_path(job['id'], step['key'], 'txt')
                    safe = result.exists() or media.exists() or text.exists() or not step.get('paid')
                    step['status'] = 'failed' if safe else 'unknown'
                    unknown |= not safe
            db.update(cur, 'jobs', job['id'], {'status': 'unknown' if unknown else 'queued', 'steps': job['steps'],
                'error': '服务中断，已发请求结果未知，禁止重发。' if unknown else None})
            if job['kind'] in {'design','image_revision'}:db.update(cur,'designs',job['entity_id'],{'status':'unknown' if unknown else 'queued'})
    STOP.clear()
    _THREAD = threading.Thread(target=_worker, name='studio-business-worker', daemon=True)
    _THREAD.start()


def stop_worker():
    STOP.set()
