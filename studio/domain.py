"""Business identities, revisions and frozen hand-offs."""
from hashlib import sha256
from io import BytesIO
import mimetypes
import math
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4
from copy import deepcopy

from PIL import Image
from studio import db


class Problem(Exception):
    def __init__(self,message,code='invalid_input',status=422,fields=None):
        super().__init__(message); self.message=message; self.code=code; self.status=status; self.fields=fields or {}


def required(value,label):
    if value is None or (isinstance(value,str) and not value.strip()): raise Problem(label+'不能为空',fields={label:'必填'})
    return value.strip() if isinstance(value,str) else value


def get(cur,table,entity_id,lock=False):
    value=db.one(cur,f'SELECT * FROM `{table}` WHERE id=%s'+(' FOR UPDATE' if lock else ''),(entity_id,))
    if not value: raise Problem('记录不存在或已移除','not_found',404)
    return value


def revision(current,body):
    if str(body.get('revision'))!=str(current['revision']):
        raise Problem('内容已有新版本。请保留当前编辑，刷新比较后重新保存。','revision_conflict',409,{'revision':current['revision']})


def page(cur,table,where='1=1',args=(),number=1,size=24,select='*',order='id DESC',before=None):
    number=max(1,int(number)); size=min(100,max(1,int(size)))
    total=db.one(cur,f'SELECT COUNT(*) AS n FROM {table} WHERE {where}',args)['n']
    if before:
        id_column=order.split()[0]
        if not re.fullmatch(r'(?:[a-z_]+\.)?id',id_column): raise Problem('该列表不支持游标')
        items=db.rows(cur,f'SELECT {select} FROM {table} WHERE ({where}) AND {id_column}<%s ORDER BY {order} LIMIT %s',tuple(args)+(int(before),size))
    else:
        items=db.rows(cur,f'SELECT {select} FROM {table} WHERE {where} ORDER BY {order} LIMIT %s OFFSET %s',tuple(args)+(size,(number-1)*size))
    return {'items':items,'total':total,'page':number,'page_size':size,'next_cursor':items[-1]['id'] if len(items)==size else None}


def public_job(job):
    if not job: return None
    result=deepcopy(job)
    for step in result.get('steps') or []:
        attempts=[step,*[attempt for attempt in step.get('attempt_history',[]) if isinstance(attempt,dict)]]
        for attempt in attempts:
            receipt=attempt.get('request_ref')
            if isinstance(receipt,dict):
                for key in ('download_url','api_key','authorization','token','credentials'): receipt.pop(key,None)
    return result


def asset_record(row):
    row=dict(row); row['url']=f"/api/assets/{row['id']}"; row['path']=str(db.DATA/'assets'/f"{row['sha256']}.{row['extension']}")
    return row


def source_record(row):
    row=dict(row);meta=row.get('meta') or {}
    row['image_asset_id']=meta.get('image_asset_id')
    row['image_url']=f"/api/assets/{row['image_asset_id']}" if row['image_asset_id'] else None
    row['acquisition']=meta.get('acquisition','automatic_search')
    return row


def get_asset(asset_id):
    with db.transaction() as cur: return asset_record(get(cur,'assets',asset_id))


def store_asset(content,name,mime='',kind='original'):
    if not content: raise Problem('文件为空')
    if len(content)>80*1024*1024: raise Problem('单文件不能超过 80 MB')
    name=Path(str(name).replace('\\','/')).name[:240] or 'asset.bin'
    ext=Path(name).suffix.lower().lstrip('.')
    if not re.fullmatch('[a-z0-9]{1,10}',ext): ext='bin'
    mime=mime or mimetypes.guess_type(name)[0] or 'application/octet-stream'
    width=height=None
    if mime.startswith('image/') or ext in {'png','jpg','jpeg','webp','tif','tiff'}:
        try:
            with Image.open(BytesIO(content)) as im:
                width,height=im.size
                detected=im.format
                mime=Image.MIME.get(detected,mime)
                ext={'JPEG':'jpg','PNG':'png','WEBP':'webp','TIFF':'tiff','GIF':'gif'}.get(detected,ext)
                im.verify()
        except Exception as exc: raise Problem('图片无法读取，请使用有效原图') from exc
    digest=sha256(content).hexdigest(); destination=db.DATA/'assets'/f'{digest}.{ext}'
    destination.parent.mkdir(parents=True,exist_ok=True)
    with db.transaction() as cur:
        # A connection-scoped lock serializes both the filesystem publication
        # and SQL identity across threads/processes. transaction() commits and
        # closes this connection, releasing the named lock after publication.
        lock_name='asset:'+sha256((db.SCHEMA+':'+digest).encode()).hexdigest()[:56]
        locked=db.one(cur,'SELECT GET_LOCK(%s,10) acquired',(lock_name,))
        if not locked or locked['acquired']!=1:raise Problem('相同文件仍在保存，请稍后重试','asset_busy',503)
        old=db.one(cur,'SELECT * FROM assets WHERE sha256=%s',(digest,))
        if old:
            path=Path(asset_record(old)['path'])
            if not path.exists(): path.write_bytes(content)
            return asset_record(old)
        if not destination.is_file() or sha256(destination.read_bytes()).hexdigest()!=digest:
            temp=destination.with_name(uuid4().hex+'.tmp'); temp.write_bytes(content); temp.replace(destination)
        cur.execute('INSERT INTO assets(sha256,name,mime,extension,byte_size,width,height,kind) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)',(digest,name,mime,ext,len(content),width,height,kind))
        aid=cur.lastrowid
        # An INSERT resolving a duplicate may observe a row committed after the
        # initial repeatable-read snapshot. Read its current locked version.
        return asset_record(get(cur,'assets',aid,True))


def catalogue(kind,body,entity_id=None):
    if kind not in {'categories','suppliers','sources','design_presets','production_presets'}: raise Problem('不支持的配置目录')
    name=required(body.get('name'),'名称')
    config=body.get('spec',body.get('config',{}))
    if not isinstance(config,dict): raise Problem('配置必须为对象')
    if kind=='categories':
        attributes=config.get('attributes',[])
        if not isinstance(attributes,list): raise Problem('品类属性必须为列表')
        keys=set()
        for attribute in attributes:
            if not isinstance(attribute,dict) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',str(attribute.get('key',''))): raise Problem('属性 key 应为稳定的英文标识')
            if attribute['key'] in keys: raise Problem('属性 key 重复')
            keys.add(attribute['key'])
            if attribute.get('type')=='choice' and 'options' not in attribute and isinstance(attribute.get('choices'),list): attribute['options']=attribute['choices']
            if attribute.get('type','text') not in {'text','number','integer','choice','boolean'}: raise Problem('属性类型不支持')
            if attribute.get('type')=='choice' and not isinstance(attribute.get('options'),list): raise Problem('枚举属性需要选项列表')
    with db.transaction() as cur:
        values={'name':name,'config':config,'enabled':bool(body.get('enabled',True))}
        if entity_id:
            current=get(cur,'directories',entity_id,True)
            if current['kind']!=kind: raise Problem('配置目录不匹配')
            revision(current,body)
            if all(current[k]==v for k,v in values.items()): current['spec']=current['config']; return current
            db.record_configuration(cur,kind,entity_id,current['revision'],current)
            values['revision']=current['revision']+1; db.update(cur,'directories',entity_id,values)
        else: entity_id=db.insert(cur,'directories',{'kind':kind,**values})
        row=get(cur,'directories',entity_id); db.record_configuration(cur,kind,entity_id,row['revision'],row); row['spec']=row['config']; return row


def category(cur,category_id):
    row=get(cur,'directories',category_id)
    if row['kind']!='categories' or not row['enabled']: raise Problem('请选择启用的产品品类')
    return row


RESEARCH_PLATFORMS={
    'market':{'Amazon','Temu','Alibaba.com','AliExpress'},
    'social':{'Pinterest','Instagram','TikTok','YouTube','Reddit'},
    'hotspot':{'开放热点'},
}


def source_capability(config):
    """A registered name does not automatically provide an executable adapter."""
    from studio.providers import PLATFORMS
    kind=config.get('kind','market'); platform=config.get('platform')
    supported=kind in RESEARCH_PLATFORMS and platform in RESEARCH_PLATFORMS[kind] and platform in PLATFORMS
    return {'supported':supported,'reason':'使用已实现的搜索与页面读取适配；实际可用性以本次任务结果为准' if supported else '该来源平台或使用范围尚未适配，不能创建执行任务',
        'locale_support':'作为查询关键词提示，不保证来源站点地域或语言过滤'}


def validate_facts(facts,definition):
    """Validate supplied values without inventing missing product facts."""
    for attribute in definition.get('attributes',[]):
        key=attribute['key']; value=facts.get(key)
        if value in (None,''): continue
        kind=attribute.get('type','text'); label=attribute.get('label',key)
        valid=True
        if kind in {'number','integer'}:
            valid=isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
            if valid and kind=='integer': valid=float(value).is_integer()
        elif kind=='boolean': valid=isinstance(value,bool)
        elif kind=='text': valid=isinstance(value,str)
        elif kind=='choice':
            options=[x.get('value',x.get('key',x.get('label'))) if isinstance(x,dict) else x for x in attribute.get('options',[])]
            valid=value in options
        if not valid: raise Problem(f'{label} 与品类属性类型或选项不符',fields={f'facts.{key}':'请按品类定义填写真实值，未知值留空'})


def save_product(body,entity_id=None):
    description=body.get('description')
    if description is None:description={}
    elif isinstance(description,str):description={'notes':description}
    if not isinstance(description,dict):raise Problem('商品说明必须为文本或对象',fields={'description':'请填写商品说明文本'})
    with db.transaction() as cur:
        name=required(body.get('name'),'商品名称'); cid=int(required(body.get('category_id'),'品类')); category(cur,cid)
        values={'name':name[:240],'category_id':cid,'description':description}
        if entity_id:
            current=get(cur,'products',entity_id,True); revision(current,body)
            if not all(current[k]==v for k,v in values.items()):
                values['revision']=current['revision']+1; db.update(cur,'products',entity_id,values)
        else:
            origin=body.get('origin','original')
            if origin not in {'original','supplier'}: raise Problem('商品入口无效')
            entity_id=db.insert(cur,'products',{'origin':origin,**values})
    return product_detail(entity_id)


def save_sku(body,product_id=None,entity_id=None):
    facts=body.get('facts',{}); components=body.get('components',[])
    if not isinstance(facts,dict) or not isinstance(components,list): raise Problem('SKU 事实或组件格式不正确')
    if len(components)>30: raise Problem('组件数量超过 30')
    seen=set()
    for comp in components:
        if not isinstance(comp,dict) or not comp.get('key') or comp['key'] in seen: raise Problem('组件需要不重复的 key')
        seen.add(comp['key'])
        for measure in ('quantity','width_cm','height_cm'):
            value=comp.get(measure)
            if value not in (None,''):
                try:
                    if isinstance(value,bool) or not math.isfinite(float(value)) or float(value)<=0: raise ValueError()
                except (ValueError,TypeError): raise Problem(f'组件 {measure} 必须为正数')
    with db.transaction() as cur:
        current=get(cur,'skus',entity_id,True) if entity_id else None
        if current: revision(current,body); product_id=current['product_id']
        product=get(cur,'products',product_id)
        validate_facts(facts,category(cur,product['category_id'])['config'])
        original=body.get('original_asset_id',current['original_asset_id'] if current else None)
        if original:
            asset=get(cur,'assets',original)
            if not asset['width']: raise Problem('SKU 原图必须是有效图片')
        values={'label':str(body.get('label') or ''),'facts':facts,'components':components,'original_asset_id':original}
        if current:
            if all(current[k]==v for k,v in values.items()): return current
            values['revision']=current['revision']+1
            values['provenance']={**current['provenance'],'manual_fields':list(facts),'revision':values['revision']}
            db.update(cur,'skus',entity_id,values)
        else:
            code=str(body.get('code') or f'SKU-{uuid4().hex[:12].upper()}')
            if db.one(cur,'SELECT id FROM listing_families WHERE parent_sku=%s LIMIT 1',(code,)):raise Problem('此编号已用于逻辑父体，不是实际销售 SKU')
            entity_id=db.insert(cur,'skus',{'product_id':product_id,'code':code,'provenance':{'manual_fields':list(facts)},**values})
        return get(cur,'skus',entity_id)


def design_stale(cur,design):
    frozen=design.get('input_revision') or {}
    if not frozen: return False
    p=get(cur,'products',design['product_id']); s=get(cur,'skus',design['sku_id'])
    if frozen.get('product')!=p['revision'] or frozen.get('sku')!=s['revision']: return True
    for sid,rev in frozen.get('sources',{}).items():
        source=db.one(cur,'SELECT revision FROM sources WHERE id=%s',(sid,))
        if not source or source['revision']!=rev: return True
    for sid,adopted_hash in frozen.get('adoptions',{}).items():
        adoption=db.one(cur,'SELECT elements FROM source_adoptions WHERE source_id=%s AND product_id=%s',(sid,design['product_id'])) or db.one(cur,'SELECT elements FROM product_sources WHERE source_id=%s AND product_id=%s',(sid,design['product_id']))
        if sha256(db.dump(adoption['elements'] if adoption else None).encode()).hexdigest()!=adopted_hash: return True
    return design.get('status')=='draft' and configuration_changed(cur,design)


def configuration_changed(cur,design):
    for reference in (design.get('input_revision') or {}).get('configuration',[]):
        if reference['kind']=='setting': current=db.one(cur,'SELECT revision FROM settings WHERE name=%s',(reference['id'],))
        elif reference['kind']=='preference_scope':
            scope,scope_id=str(reference['id']).split(':',1)
            current=db.one(cur,'SELECT revision FROM preferences WHERE scope=%s AND scope_id=%s',(scope,int(scope_id))) or {'revision':0}
        else: current=db.one(cur,'SELECT revision FROM '+('preferences' if reference['kind']=='preference' else 'directories')+' WHERE id=%s',(reference['id'],))
        if not current or current['revision']!=reference['revision']: return True
    return False


def design_record(cur,row):
    row=dict(row); row['stale']=design_stale(cur,row)
    row['configuration_changed']=configuration_changed(cur,row)
    row['assets']=db.rows(cur,'SELECT d.*,a.width,a.height,a.sha256,a.name FROM design_assets d JOIN assets a ON a.id=d.asset_id WHERE d.design_id=%s ORDER BY d.id',(row['id'],))
    for asset in row['assets']: asset['url']=f"/api/assets/{asset['asset_id']}"
    return row


def product_detail(entity_id):
    with db.transaction() as cur:
        p=get(cur,'products',entity_id); p['category']=get(cur,'directories',p['category_id'])['name']
        p['skus']=db.rows(cur,'SELECT * FROM skus WHERE product_id=%s ORDER BY id LIMIT 100',(entity_id,))
        p['sources']=db.rows(cur,'SELECT s.* FROM sources s WHERE s.product_id=%s OR EXISTS(SELECT 1 FROM product_sources x WHERE x.source_id=s.id AND x.product_id=%s) OR EXISTS(SELECT 1 FROM source_adoptions a WHERE a.source_id=s.id AND a.product_id=%s) ORDER BY s.id DESC LIMIT 24',(entity_id,entity_id,entity_id))
        p['designs']=[design_record(cur,d) for d in db.rows(cur,'SELECT * FROM designs WHERE product_id=%s ORDER BY id DESC LIMIT 24',(entity_id,))]
        p['listings']=[listing_record(cur,l) for l in db.rows(cur,'SELECT * FROM listings WHERE product_id=%s ORDER BY id DESC LIMIT 24',(entity_id,))]
        for key,table in [('sku_count','skus'),('design_count','designs'),('listing_count','listings')]: p[key]=db.one(cur,f'SELECT COUNT(*) n FROM {table} WHERE product_id=%s',(entity_id,))['n']
        p['next_actions']=([{'title':'补充真实 SKU','href':f'#/products/{entity_id}','reason':'设计需要明确规格与事实'}] if not p['sku_count'] else [{'title':'建立设计任务书','href':f'#/designs/new?product_id={entity_id}','reason':'沿用当前商品和 SKU 资料'}])
        return p


PREFERENCE_KEYS={'route_count','sales_count','detail_count','selected_route','repeat','components','component_plans','text_model_id','image_model_id','style','palette','scene','output_size','reference_asset_id'}


def preference_values(value):
    if not isinstance(value,dict) or set(value)-PREFERENCE_KEYS: raise Problem('设计偏好只能包含已支持的设计参数，不能覆盖商品事实')
    for key,item in value.items():
        if isinstance(item,dict) and 'state' in item:
            if item['state'] not in {'inherit','value','clear','not_applicable'}: raise Problem(f'{key} 的继承状态无效')
            if item['state']=='value' and 'value' not in item: raise Problem(f'{key} 缺少显式值')
    return value


def save_preferences(scope,scope_id,body):
    if scope not in {'product','sku'}: raise Problem('偏好范围只支持商品或 SKU')
    value=preference_values(body.get('value',{}))
    with db.transaction() as cur:
        get(cur,'products' if scope=='product' else 'skus',scope_id)
        current=db.one(cur,'SELECT * FROM preferences WHERE scope=%s AND scope_id=%s FOR UPDATE',(scope,scope_id))
        if current:
            revision(current,body)
            if current['value']==value: return current
            db.record_configuration(cur,'preferences',current['id'],current['revision'],current)
            db.update(cur,'preferences',current['id'],{'value':value,'revision':current['revision']+1}); ident=current['id']
        else:
            if body.get('revision') not in (None,0): raise Problem('偏好版本已变化','revision_conflict',409)
            ident=db.insert(cur,'preferences',{'scope':scope,'scope_id':scope_id,'value':value})
        result=get(cur,'preferences',ident); db.record_configuration(cur,'preferences',ident,result['revision'],result); return result


def resolve_preferences(cur,p,s,overrides,mode):
    raw=overrides.get('_overrides',overrides) if isinstance(overrides,dict) else {}
    raw={k:v for k,v in raw.items() if not k.startswith('_')}
    preset_id=raw.pop('preset_id',None)
    layers=[('系统初始值',{'route_count':3,'sales_count':7 if mode=='supplier' else 4,'detail_count':1})]
    refs=[]
    routing=db.one(cur,"SELECT * FROM settings WHERE name='routing'")
    if routing: layers.append(('模型通道',routing['value'])); refs.append({'kind':'setting','id':'routing','revision':routing['revision']})
    general=db.one(cur,"SELECT * FROM settings WHERE name='design_defaults'")
    if general: layers.append(('通用设计偏好',general['value'])); refs.append({'kind':'setting','id':'design_defaults','revision':general['revision']})
    if preset_id:
        preset=get(cur,'directories',preset_id)
        if preset['kind']!='design_presets' or not preset['enabled']: raise Problem('设计方案不存在或已停用')
        layers.append((f"方案 {preset['name']} v{preset['revision']}",preset['config'])); refs.append({'kind':'directory','id':preset['id'],'revision':preset['revision']})
    cat=category(cur,p['category_id'])
    layers.append((f"品类 {cat['name']} v{cat['revision']}",cat['config'].get('design_defaults',{}))); refs.append({'kind':'directory','id':cat['id'],'revision':cat['revision']})
    for scope,scope_id,title in [('product',p['id'],'商品偏好'),('sku',s['id'],'SKU 偏好')]:
        pref=db.one(cur,'SELECT * FROM preferences WHERE scope=%s AND scope_id=%s',(scope,scope_id))
        if pref: layers.append((f"{title} v{pref['revision']}",pref['value']))
        refs.append({'kind':'preference_scope','id':f'{scope}:{scope_id}','revision':pref['revision'] if pref else 0})
    layers.append(('当前任务',raw)); result={}; sources={}
    for origin,values in layers:
        for key,value in values.items():
            if key not in PREFERENCE_KEYS: continue
            state=value.get('state') if isinstance(value,dict) else 'value'
            if state=='inherit': continue
            actual=None if state in {'clear','not_applicable'} else value.get('value') if state=='value' and isinstance(value,dict) and 'state' in value else value
            result[key]=actual; sources[key]={'source':origin,'state':state or 'value'}
    result['_overrides']={**raw,**({'preset_id':preset_id} if preset_id else {})}
    result['_sources']=sources; result['_configuration_refs']=refs
    return result,refs


def _design_inputs(cur,body):
    p=get(cur,'products',int(required(body.get('product_id'),'商品')))
    s=get(cur,'skus',int(required(body.get('sku_id'),'SKU')))
    if s['product_id']!=p['id']: raise Problem('SKU 不属于当前商品')
    mode=body.get('mode','large')
    if mode not in {'supplier','large','repeat'}: raise Problem('设计模式无效')
    ids=list(dict.fromkeys(int(x) for x in body.get('source_ids',[])))
    if len(ids)>30: raise Problem('一次最多采用 30 条来源')
    sources=[]; adoptions={}
    for sid in ids:
        source=get(cur,'sources',sid,True)
        adopted=db.one(cur,'SELECT elements,snapshot FROM source_adoptions WHERE source_id=%s AND product_id=%s',(sid,p['id'])) or db.one(cur,'SELECT elements FROM product_sources WHERE source_id=%s AND product_id=%s',(sid,p['id']))
        if source['kind']!='hotspot' and source['product_id']!=p['id'] and not adopted: raise Problem('请先将此来源采用到当前商品')
        if adopted: source={**source,'adopted_elements':adopted['elements']}
        adoptions[str(sid)]=sha256(db.dump(adopted['elements'] if adopted else None).encode()).hexdigest()
        sources.append(source)
    config=body.get('config') or {}
    if not isinstance(config,dict): raise Problem('设计配置必须为对象')
    config,configuration_refs=resolve_preferences(cur,p,s,config,mode)
    for key,maximum,default in [('route_count',5,3),('sales_count',12,7 if mode=='supplier' else 4),('detail_count',3,1)]:
        try:
            original=config.get(key,default)
            number=int(original)
            if isinstance(original,bool) or float(original)!=number: raise ValueError()
        except (ValueError,TypeError,OverflowError): raise Problem('设计数量必须是整数')
        if not 0<=number<=maximum or (key!='detail_count' and number==0): raise Problem(f'{key} 超出允许范围')
        config[key]=number
    from studio.patterns import normalize_design_patterns, PatternError
    try: config=normalize_design_patterns(config,s,mode)
    except PatternError as exc: raise Problem(str(exc)) from None
    rev={'product':p['revision'],'sku':s['revision'],'sources':{str(x['id']):x['revision'] for x in sources},'adoptions':adoptions,'configuration':configuration_refs}
    return p,s,sources,config,rev


def save_design(body,entity_id=None):
    with db.transaction() as cur:
        current=get(cur,'designs',entity_id,True) if entity_id else None
        if current:
            revision(current,body)
            if current['status']!='draft' or current['job_id']: raise Problem('已开始的设计已冻结，请复制新版本','frozen',409)
            body={**current,**body}
        p,s,sources,config,rev=_design_inputs(cur,body)
        values={'product_id':p['id'],'sku_id':s['id'],'mode':body.get('mode','large'),'brief':str(body.get('brief') or ''),
            'source_ids':[x['id'] for x in sources],'config':config,'input_revision':rev,
            'input_snapshot':{'product':p,'sku':s,'sources':sources}}
        if current:
            if not all(current[k]==v for k,v in values.items()):
                values['revision']=current['revision']+1; db.update(cur,'designs',entity_id,values)
        else: values['parent_id']=body.get('parent_id'); entity_id=db.insert(cur,'designs',values)
    return design_detail(entity_id)


def design_detail(entity_id):
    with db.transaction() as cur:
        row=design_record(cur,get(cur,'designs',entity_id))
        row['current_product']=get(cur,'products',row['product_id']);row['current_sku']=get(cur,'skus',row['sku_id'])
        row['current_sources']=[get(cur,'sources',sid) for sid in row['source_ids']]
        frozen=row.get('input_snapshot') or {};started=row['status']!='draft' or bool(row.get('job_id'))
        row['product']=deepcopy(frozen.get('product',row['current_product'])) if started else row['current_product']
        row['sku']=deepcopy(frozen.get('sku',row['current_sku'])) if started else row['current_sku']
        row['sources']=deepcopy(frozen.get('sources',row['current_sources'])) if started else row['current_sources']
        row['job']=public_job(get(cur,'jobs',row['job_id'])) if row['job_id'] else None
        return row


def start_design(entity_id,body):
    intent=str(body.get('intent_key') or f'design:{entity_id}')
    with db.transaction() as cur:
        d=get(cur,'designs',entity_id,True)
        if d['job_id']: return get(cur,'jobs',d['job_id'])
        if d['status']!='draft': raise Problem('设计不能再次启动','frozen',409)
        p,s,sources,config,rev=_design_inputs(cur,d)
        if d['mode']=='supplier' and not s['original_asset_id']: raise Problem('供应商设计需要当前 SKU 的原图',fields={'original_asset_id':'缺少原图'})
        if design_stale(cur,d): raise Problem('任务书建立后事实有变化，请先保存最新任务书','stale_input',409)
        routing=db.one(cur,"SELECT value FROM settings WHERE name='routing'")
        frozen_config={**((routing or {}).get('value') or {}),**config}
        d={**d,'config':frozen_config}
        payload={'design':d,'product':p,'sku':s,'sources':sources}
        old=db.one(cur,'SELECT * FROM jobs WHERE intent_key=%s',(intent,))
        if old:
            if old['kind']!='design' or old['entity_id']!=entity_id: raise Problem('重复请求标识属于另一操作','intent_conflict',409)
            return old
        jid=db.insert(cur,'jobs',{'kind':'design','entity_id':entity_id,'intent_key':intent,'payload':payload,'steps':[]})
        db.update(cur,'designs',entity_id,{'status':'queued','job_id':jid,'input_snapshot':payload,'input_revision':rev,'config':frozen_config})
        return get(cur,'jobs',jid)


def fork_design(entity_id,body):
    d=design_detail(entity_id)
    config={**d['config'].get('_overrides',d['config']),**body.get('config',{})}
    if body.get('asset_id'):
        if not any(a['asset_id']==int(body['asset_id']) for a in d['assets']): raise Problem('修改参考图不属于该设计')
        config['reference_asset_id']=int(body['asset_id'])
    return save_design({'product_id':d['product_id'],'sku_id':d['sku_id'],'mode':d['mode'],'brief':body.get('brief',d['brief']),
        'source_ids':d['source_ids'],'config':config,'parent_id':entity_id})


def save_target(body,entity_id=None):
    allowed={'Amazon','Temu','Alibaba.com','AliExpress'}
    if body.get('platform') not in allowed: raise Problem('平台不在本期范围')
    values={key:str(required(body.get(key),key)) for key in ('name','platform','site','mode','category','language','currency')}
    values['currency']=values['currency'].upper()
    if not re.fullmatch('[A-Z]{3}',values['currency']): raise Problem('币种应为三字母代码')
    values['enabled']=bool(body.get('enabled',True))
    with db.transaction() as cur:
        if entity_id:
            current=get(cur,'targets',entity_id,True); revision(current,body)
            if not all(current[k]==v for k,v in values.items()):
                values['revision']=current['revision']+1; db.update(cur,'targets',entity_id,values)
        else: entity_id=db.insert(cur,'targets',values)
        return get(cur,'targets',entity_id)


def listing_record(cur,row):
    row=dict(row); s=get(cur,'skus',row['sku_id']); p=get(cur,'products',row['product_id']); t=get(cur,'targets',row['target_id'])
    rev=row['input_revision']
    row['stale']=rev.get('sku')!=s['revision'] or rev.get('product')!=p['revision'] or rev.get('target')!=t['revision']
    if row['design_id']:
        d=get(cur,'designs',row['design_id']); row['stale']=row['stale'] or design_stale(cur,d) or rev.get('design')!=d['revision']
    row['name']=p['name']; row['sku_code']=s['code']; row['target_name']=t['name']; row['diagnostics']=listing_issues(cur,row)
    return row


def listing_issues(cur,row):
    issues=[]; fields=row['fields']; sid=row['sku_id']; href=f"#/listings/{row['id']}"
    def add(field,message): issues.append({'field':field,'sku_id':sid,'message':message,'href':href})
    for field in ('title','price','stock'):
        if fields.get(field) in (None,''): add(field,'请填写'+{'title':'标题','price':'售价','stock':'库存'}[field])
    for field in ('price','stock'):
        if fields.get(field) not in (None,''):
            try:
                if isinstance(fields[field],bool) or not math.isfinite(float(fields[field])) or float(fields[field])<0 or (field=='stock' and float(fields[field])%1): raise ValueError()
            except (TypeError,ValueError): add(field,'请输入有效的非负'+('整数' if field=='stock' else '金额'))
    if row.get('stale'): add('dependencies','商品事实、设计或店铺配置已变更，请比较并明确刷新关联')
    images=row['images'] or []
    if not images: add('images','尚未绑定设计图片')
    elif not any(x.get('role') in {'main','hero','sales-1','sales_1'} for x in images): add('images.main','尚未指定商品主图')
    from studio.transfers import listing_template_issues,resolve_listing_template,mapped_export_images
    template=resolve_listing_template(cur,row)
    exported=mapped_export_images(template['config'],row) if template else images
    for i,image in enumerate(images):
        if image not in exported: continue
        if not image.get('asset_id'): add(f'images.{i}.asset_id','图片没有绑定资产')
        parsed=urlsplit(str(image.get('url') or ''))
        if parsed.scheme!='https' or not parsed.hostname or image.get('url_status')!='verified': add(f'images.{i}.url','图片需要与当前资产对应、验证可访问的 HTTPS 地址')
        from studio.public_media import publication_problem
        publication_error=publication_problem(cur,image)
        if publication_error:add(f'images.{i}.url',publication_error)
    if not template: add('template','尚未绑定当前店铺原生模板')
    else:
        issues.extend(listing_template_issues(cur,row))
    return issues


def create_listing(body):
    with db.transaction() as cur:
        pid=int(required(body.get('product_id'),'商品')); sid=int(required(body.get('sku_id'),'SKU')); tid=int(required(body.get('target_id'),'店铺'))
        p=get(cur,'products',pid); s=get(cur,'skus',sid); t=get(cur,'targets',tid)
        if s['product_id']!=pid: raise Problem('SKU 不属于当前商品')
        if not t['enabled']: raise Problem('该店铺已停用')
        existing=db.one(cur,'SELECT * FROM listings WHERE sku_id=%s AND target_id=%s',(sid,tid))
        did=body.get('design_id'); images=[]; d=None
        if did:
            d=get(cur,'designs',did)
            if d['sku_id']!=sid or d['product_id']!=pid: raise Problem('设计不属于当前 SKU')
            assets=db.rows(cur,'SELECT * FROM design_assets WHERE design_id=%s ORDER BY id',(did,))
            images=[{'role':a['role'],'asset_id':a['asset_id'],'design_asset_id':a['id'],'url':'','url_status':'missing'} for a in assets if any(x in a['role'] for x in ('sales','hero','detail','main','scene','gallery'))]
            if not images: raise Problem('当前设计尚无可用于刊登的销售图')
        if existing:
            if did and existing['design_id']!=did: raise Problem('该 SKU 已有此店铺刊登。请在刊登详情比较并更换设计版本。','listing_exists',409,{'listing_id':existing['id']})
            return listing_record(cur,existing)
        rev={'product':p['revision'],'sku':s['revision'],'target':t['revision'],'design':d['revision'] if d else None}
        lid=db.insert(cur,'listings',{'product_id':pid,'sku_id':sid,'target_id':tid,'design_id':did,'fields':{'title':p['name'],'description':'','bullets':[],'attributes':{}},'images':images,'input_revision':rev})
        return listing_record(cur,get(cur,'listings',lid))


def listing_detail(entity_id):
    with db.transaction() as cur:
        row=listing_record(cur,get(cur,'listings',entity_id)); row['product']=get(cur,'products',row['product_id']); row['sku']=get(cur,'skus',row['sku_id']); row['target']=get(cur,'targets',row['target_id'])
        row['family']=db.one(cur,'SELECT f.id,f.parent_sku,f.variation,f.revision,f.template_id FROM listing_families f JOIN family_members m ON m.family_id=f.id WHERE m.listing_id=%s',(entity_id,))
        from studio.transfers import resolve_listing_template
        template=resolve_listing_template(cur,row); row['active_template_id']=template['id'] if template else None
        row['design']=design_record(cur,get(cur,'designs',row['design_id'])) if row['design_id'] else None
        return row


def save_listing(entity_id,body):
    with db.transaction() as cur:
        current=get(cur,'listings',entity_id,True); revision(current,body)
        fields=body.get('fields',current['fields']); images=body.get('images',current['images'])
        design_id=body.get('design_id',current['design_id'])
        replacement=None
        if design_id!=current['design_id']:
            replacement=get(cur,'designs',design_id)
            if replacement['sku_id']!=current['sku_id'] or replacement['product_id']!=current['product_id']: raise Problem('替换设计不属于当前 SKU')
            assets=db.rows(cur,'SELECT * FROM design_assets WHERE design_id=%s ORDER BY id',(design_id,))
            images=[{'role':a['role'],'asset_id':a['asset_id'],'design_asset_id':a['id'],'url':'','url_status':'missing'} for a in assets if any(x in a['role'] for x in ('sales','hero','detail','main','scene','gallery'))]
            if not images: raise Problem('替换设计没有可刊登图片')
        if not isinstance(fields,dict) or not isinstance(images,list): raise Problem('刊登字段与图片格式不正确')
        if fields.get('_template_id') not in (None,''):
            try:
                if isinstance(fields['_template_id'],bool): raise ValueError()
                template_id=int(fields['_template_id'])
                if str(template_id)!=str(fields['_template_id']) or template_id<1: raise ValueError()
            except (TypeError,ValueError): raise Problem('请选择有效的刊登模板') from None
            from studio.transfers import resolve_listing_template
            resolve_listing_template(cur,{**current,'fields':fields},template_id)
        cleaned=[]
        for image in images:
            aid=int(required(image.get('asset_id'),'图片资产')); asset=get(cur,'assets',aid)
            design_asset=get(cur,'design_assets',int(required(image.get('design_asset_id'),'设计图片')))
            d=get(cur,'designs',design_asset['design_id'])
            if d['sku_id']!=current['sku_id'] or design_asset['asset_id']!=aid or d['id']!=design_id: raise Problem('刊登图片不属于当前 SKU 的所选设计版本')
            prior=next((x for x in current['images'] if x.get('asset_id')==aid and x.get('url')==image.get('url')),None)
            cleaned.append({'role':str(image.get('role') or design_asset['role']),'asset_id':aid,'design_asset_id':design_asset['id'],'url':str(image.get('url') or ''),'url_status':prior.get('url_status','unverified') if prior else 'unverified'})
        values={'fields':fields,'images':cleaned,'design_id':design_id,'revision':current['revision']+1}
        if body.get('refresh_dependencies') or replacement:
            p=get(cur,'products',current['product_id']); s=get(cur,'skus',current['sku_id']); t=get(cur,'targets',current['target_id']); d=get(cur,'designs',design_id) if design_id else None
            if d and design_stale(cur,d): raise Problem('设计仍引用旧事实，请选择按最新事实生成的设计')
            values['input_revision']={'product':p['revision'],'sku':s['revision'],'target':t['revision'],'design':d['revision'] if d else None}
        meaningful={k:v for k,v in values.items() if k!='revision'}
        if not all(current[k]==v for k,v in meaningful.items()): db.update(cur,'listings',entity_id,values)
    return listing_detail(entity_id)


def listing_copy(entity_id,body):
    row=listing_detail(entity_id)
    payload={key:row[key] for key in ('product','sku','target','design')}; payload['listing']={k:v for k,v in row.items() if k not in payload}
    with db.transaction() as cur: payload['routing']=(db.one(cur,"SELECT value FROM settings WHERE name='routing'") or {}).get('value',{})
    return db.create_job('copy',entity_id,payload,str(body.get('intent_key') or f'copy:{entity_id}:{row["revision"]}'))


def resume_job(entity_id):
    with db.transaction() as cur:
        job=get(cur,'jobs',entity_id,True)
        if job['status']=='unknown' or any(s.get('status') in {'unknown','running'} for s in job['steps']): raise Problem('存在结果未知的请求，必须先核对原请求，禁止重新发送','unknown_result',409)
        if job['status']!='failed': raise Problem('仅失败任务可接续','not_resumable',409)
        db.update(cur,'jobs',entity_id,{'status':'queued','stage':'等待接续未完成步骤','error':None,'cancel_requested':False})
        if job['kind'] in {'design','image_revision'}: db.update(cur,'designs',job['entity_id'],{'status':'queued'})
        return get(cur,'jobs',entity_id)


def cancel_job(entity_id):
    with db.transaction() as cur:
        job=get(cur,'jobs',entity_id,True)
        if job['status'] in {'succeeded','cancelled'}: return job
        values={'cancel_requested':True}
        if job['status']=='queued':
            values.update(status='cancelled',stage='已取消')
            if job['kind'] in {'design','image_revision'}:db.update(cur,'designs',job['entity_id'],{'status':'cancelled'})
        db.update(cur,'jobs',entity_id,values); return get(cur,'jobs',entity_id)


def _directory_bytes(path):
    import os
    size=count=0
    if not path.exists(): return {'bytes':0,'files':0}
    for base,dirs,files in os.walk(path,followlinks=False):
        dirs[:]=[name for name in dirs if not (Path(base)/name).is_symlink()]
        for name in files:
            item=Path(base)/name
            if not item.is_symlink():
                try: size+=item.stat().st_size; count+=1
                except FileNotFoundError: pass
    return {'bytes':size,'files':count}


def storage_summary():
    import time
    with db.transaction() as cur:
        assets=db.one(cur,'SELECT COUNT(*) count,COALESCE(SUM(byte_size),0) bytes FROM assets')
        groups=db.rows(cur,'SELECT kind,COUNT(*) count,COALESCE(SUM(byte_size),0) bytes FROM assets GROUP BY kind')
        database=db.one(cur,'SELECT COALESCE(SUM(data_length+index_length),0) bytes FROM information_schema.tables WHERE table_schema=%s',(db.SCHEMA,))
        policy=(db.one(cur,"SELECT value FROM settings WHERE name='storage'") or {}).get('value',{})
        candidates=db.rows(cur,"SELECT id FROM jobs WHERE status='succeeded' AND result IS NOT NULL AND updated_at<CURRENT_TIMESTAMP-INTERVAL %s DAY ORDER BY id LIMIT 200",(int(policy.get('temporary_days',14)),))
        protected=db.one(cur,"SELECT COUNT(*) count FROM jobs WHERE status IN ('queued','running','failed','unknown','cancelled')")['count']
        source_candidates=db.one(cur,"SELECT COUNT(*) count FROM sources s WHERE s.observed_at<CURRENT_TIMESTAMP-INTERVAL %s DAY AND NOT EXISTS(SELECT 1 FROM source_adoptions a WHERE a.source_id=s.id) AND NOT EXISTS(SELECT 1 FROM product_sources p WHERE p.source_id=s.id) AND NOT EXISTS(SELECT 1 FROM designs d WHERE JSON_CONTAINS(d.source_ids,JSON_ARRAY(s.id))) AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.id=s.job_id AND j.status IN ('queued','running','failed','unknown','cancelled'))",(int(policy.get('source_cache_days',30)),))['count']
        recent=db.rows(cur,'SELECT * FROM cleanup_runs ORDER BY id DESC LIMIT 10')
    checkpoint=_directory_bytes(db.DATA/'checkpoints'); actual=_directory_bytes(db.DATA/'assets')
    logs={'bytes':0,'files':0}
    for path in db.DATA.glob('*.log'):
        if path.is_file(): logs['bytes']+=path.stat().st_size; logs['files']+=1
    cutoff=time.time()-int(policy.get('temporary_days',14))*86400
    temporary=[p for p in (db.DATA/'assets').glob('*.tmp') if p.is_file() and p.stat().st_mtime<cutoff]
    reclaim=sum(_directory_bytes(db.DATA/'checkpoints'/str(j['id']))['bytes'] for j in candidates)
    return {'database':database,'assets':assets,'asset_groups':groups,'asset_files_bytes':actual['bytes'],
        'cache':checkpoint,'logs':logs,'backups':_directory_bytes(db.DATA/'backups'),
        'temporary_bytes':sum(p.stat().st_size for p in temporary),'cleanup_candidates':{'temporary_files':len(temporary),'completed_job_caches':len(candidates),'completed_job_cache_bytes':reclaim,'unadopted_sources':source_candidates},
        'protected':['商品原图','设计成果与历史版本','导出批次及回执','已采用或被设计引用的来源',f'{protected} 个未完成/未知任务的恢复资料'],
        'cleanup_scope':'过期临时文件、已成功任务的冗余磁盘检查点、未采用且无设计引用的过期来源；正式资产与未完成任务不删除','recent_cleanup':recent}


def cleanup_storage():
    import time
    with db.transaction() as cur:
        policy=(db.one(cur,"SELECT value FROM settings WHERE name='storage'") or {}).get('value',{})
        candidates=db.rows(cur,"SELECT id FROM jobs WHERE status='succeeded' AND result IS NOT NULL AND updated_at<CURRENT_TIMESTAMP-INTERVAL %s DAY ORDER BY id LIMIT 200",(int(policy.get('temporary_days',14)),))
    cutoff=time.time()-int(policy.get('temporary_days',14))*86400; removed=freed=cache_jobs=0
    asset_root=(db.DATA/'assets').resolve(); checkpoint_root=(db.DATA/'checkpoints').resolve()
    for path in asset_root.glob('*.tmp'):
        if path.resolve().parent==asset_root and not path.is_symlink() and path.is_file() and path.stat().st_mtime<cutoff:
            size=path.stat().st_size; path.unlink(); removed+=1; freed+=size
    for candidate in candidates:
        target=(checkpoint_root/str(candidate['id'])).resolve()
        if target.parent!=checkpoint_root or not target.is_dir() or target.is_symlink(): continue
        with db.transaction() as cur:
            current=get(cur,'jobs',candidate['id'],True)
            if current['status']!='succeeded' or current['result'] is None: continue
            for path in target.iterdir():
                if path.is_file() and not path.is_symlink() and path.resolve().parent==target:
                    size=path.stat().st_size; path.unlink(); removed+=1; freed+=size
            if not any(target.iterdir()): target.rmdir(); cache_jobs+=1
    with db.transaction() as cur:
        candidates=db.rows(cur,"SELECT s.id FROM sources s WHERE s.observed_at<CURRENT_TIMESTAMP-INTERVAL %s DAY AND NOT EXISTS(SELECT 1 FROM source_adoptions a WHERE a.source_id=s.id) AND NOT EXISTS(SELECT 1 FROM product_sources p WHERE p.source_id=s.id) AND NOT EXISTS(SELECT 1 FROM designs d WHERE JSON_CONTAINS(d.source_ids,JSON_ARRAY(s.id))) AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.id=s.job_id AND j.status IN ('queued','running','failed','unknown','cancelled')) ORDER BY s.id LIMIT 200 FOR UPDATE",(int(policy.get('source_cache_days',30)),))
        for item in candidates: cur.execute('DELETE FROM sources WHERE id=%s',(item['id'],))
        result={'removed_files':removed,'freed_bytes':freed,'completed_job_caches':cache_jobs,'expired_unadopted_sources':len(candidates),'protected_formal_assets':True}
        db.insert(cur,'cleanup_runs',{'summary':result})
    return result
