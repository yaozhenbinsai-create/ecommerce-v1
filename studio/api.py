"""Local business API. Every write uses explicit identities and revision checks."""
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
import ipaddress
import socket
import logging
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, Query, Request, UploadFile, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
import pymysql

from studio import db, domain as d
from studio.admin_auth import require_admin


@asynccontextmanager
async def lifespan(app):
    db.initialize()
    from studio import engine
    engine.start_worker()
    yield
    engine.stop_worker()


app=FastAPI(title='商品创作与刊登工作台',version='1.0-rebuild',lifespan=lifespan)
app.middleware('http')(require_admin)


@app.middleware('http')
async def local_origin(request:Request,call_next):
    host=request.url.hostname
    if host not in {'localhost','127.0.0.1','::1','testserver'}: return JSONResponse({'error':{'code':'local_only','message':'仅允许本机访问','fields':{}}},403)
    origin=request.headers.get('origin')
    if request.method not in {'GET','HEAD','OPTIONS'} and origin and urlsplit(origin).netloc!=request.url.netloc:
        return JSONResponse({'error':{'code':'origin_mismatch','message':'请求来源不匹配','fields':{}}},403)
    return await call_next(request)


@app.exception_handler(d.Problem)
async def domain_problem(request,exc):
    return JSONResponse({'error':{'code':exc.code,'message':exc.message,'fields':exc.fields}},exc.status)


@app.exception_handler(RequestValidationError)
async def validation_problem(request,exc):
    return JSONResponse({'error':{'code':'invalid_input','message':'请求字段格式不正确','fields':{'.'.join(map(str,e['loc'])):e['msg'] for e in exc.errors()}}},422)


@app.exception_handler(HTTPException)
async def http_problem(request,exc):
    detail=exc.detail if isinstance(exc.detail,dict) else {'code':'request_error','message':str(exc.detail),'fields':{}}
    return JSONResponse({'error':detail},exc.status_code)


@app.exception_handler(ValueError)
async def value_problem(request,exc):
    return JSONResponse({'error':{'code':'invalid_value','message':'输入值格式不正确，请核对编号、数字及日期。','fields':{}}},422)


@app.exception_handler(pymysql.IntegrityError)
async def constraint_problem(request,exc):
    return JSONResponse({'error':{'code':'identity_conflict','message':'该编号或关联已存在，请刷新查看已有记录。','fields':{}}},409)


@app.exception_handler(pymysql.OperationalError)
async def database_problem(request,exc):
    code=exc.args[0] if exc.args and isinstance(exc.args[0],int) else None
    logging.getLogger('studio.api').warning('mysql_error code=%s path=%s',code,request.url.path[:200])
    if code in {3140,3141,3143,3156}:
        return JSONResponse({'error':{'code':'invalid_json_value','message':'字段内容不能保存为有效JSON，请核对输入格式；原有记录未被替换。','fields':{}}},422)
    if code in {1040,2002,2003,2006,2013}:
        return JSONResponse({'error':{'code':'database_unavailable','message':'数据库暂时不可用，编辑内容尚未保存，请稍后重试。','fields':{}}},503)
    return JSONResponse({'error':{'code':'database_operation_failed','message':'数据库操作未完成，错误编号已记录以便排查；编辑内容尚未保存。','fields':{}}},500)


@app.get('/api/health')
def health():
    with db.transaction() as cur: db.one(cur,'SELECT 1 AS ok')
    return {'ok':True,'version':'1.0-rebuild','database':db.SCHEMA}


@app.get('/api/dashboard')
def dashboard():
    with db.transaction() as cur:
        counts={table:db.one(cur,f'SELECT COUNT(*) n FROM {table}')['n'] for table in ('products','skus','sources','designs','listings','exports')}
        recent_jobs=db.rows(cur,'SELECT id,kind,entity_id,status,stage,progress,error,created_at,updated_at FROM jobs ORDER BY id DESC LIMIT 12')
        recent_products=db.rows(cur,'SELECT p.*,d.name category,(SELECT COUNT(*) FROM skus s WHERE s.product_id=p.id) sku_count FROM products p JOIN directories d ON d.id=p.category_id ORDER BY p.id DESC LIMIT 8')
        attention=[{'title':f"任务 #{j['id']} {j['stage']}",'reason':j['error'] or '需要处理','href':f"#/studio/{j['entity_id']}" if j['kind']=='design' else '#/overview'} for j in recent_jobs if j['status'] in {'failed','unknown'}]
        missing=db.rows(cur,'SELECT p.id,p.name FROM products p WHERE NOT EXISTS (SELECT 1 FROM skus s WHERE s.product_id=p.id) ORDER BY p.id DESC LIMIT 8')
        attention += [{'title':p['name'],'reason':'尚无真实 SKU，不能开始设计','href':f"#/products/{p['id']}"} for p in missing]
        return {'counts':counts,'attention':attention,'recent_jobs':recent_jobs,'recent_products':recent_products}


@app.get('/api/config')
def config():
    with db.transaction() as cur:
        result={kind:[] for kind in ('categories','suppliers','sources','design_presets','production_presets')}
        for row in db.rows(cur,'SELECT * FROM directories ORDER BY kind,id LIMIT 500'):
            row['spec']=row['config']
            if row['kind']=='sources': row['capability']=d.source_capability(row['config'])
            result.setdefault(row['kind'],[]).append(row)
        result['targets']=db.rows(cur,'SELECT * FROM targets ORDER BY id LIMIT 100')
        result['storage']=db.one(cur,"SELECT * FROM settings WHERE name='storage'")
    try:
        from studio import providers
        result['models']=providers.models()
    except (ImportError,AttributeError): result['models']=[]
    return result


@app.post('/api/config/{kind}')
def add_directory(kind:str,body:dict): return d.catalogue(kind,body)


@app.put('/api/config/{kind}/{entity_id}')
def edit_directory(kind:str,entity_id:int,body:dict): return d.catalogue(kind,body,entity_id)


@app.get('/api/config/{kind}/{entity_id}/history')
def configuration_history(kind:str,entity_id:int,page:int=1,page_size:int=24):
    with db.transaction() as cur: return d.page(cur,'configuration_revisions','resource_kind=%s AND resource_id=%s',(kind,entity_id),page,page_size)


@app.get('/api/preferences/{scope}/{scope_id}')
def preferences(scope:str,scope_id:int):
    if scope not in {'product','sku'}: raise d.Problem('偏好范围无效')
    with db.transaction() as cur:
        d.get(cur,'products' if scope=='product' else 'skus',scope_id)
        return db.one(cur,'SELECT * FROM preferences WHERE scope=%s AND scope_id=%s',(scope,scope_id)) or {'scope':scope,'scope_id':scope_id,'revision':0,'value':{}}


@app.put('/api/preferences/{scope}/{scope_id}')
def edit_preferences(scope:str,scope_id:int,body:dict): return d.save_preferences(scope,scope_id,body)


@app.get('/api/skus/{sku_id}/design-configuration')
def resolved_design_configuration(sku_id:int,mode:str='large',preset_id:int|None=None):
    with db.transaction() as cur:
        sku=d.get(cur,'skus',sku_id); product=d.get(cur,'products',sku['product_id'])
        values,refs=d.resolve_preferences(cur,product,sku,{'preset_id':preset_id} if preset_id else {},mode)
        category=d.get(cur,'directories',product['category_id'])
        return {'effective':{k:v for k,v in values.items() if not k.startswith('_')},'sources':values['_sources'],'configuration_refs':refs,
            'fact_schema':category['config'].get('attributes',[]),'component_schema':category['config'].get('components',[]),'facts_are_not_defaulted':True}


@app.get('/api/settings')
def settings():
    with db.transaction() as cur: return {r['name']:{'revision':r['revision'],'value':r['value']} for r in db.rows(cur,'SELECT * FROM settings')}


@app.put('/api/settings')
def save_settings(body:dict):
    name=body.get('name')
    if name not in {'storage','routing','display','design_defaults'}: raise d.Problem('设置名称无效')
    value=body.get('value',{})
    if not isinstance(value,dict): raise d.Problem('设置值必须为对象')
    if name=='design_defaults': d.preference_values(value)
    if name=='storage':
        for key in ('temporary_days','source_cache_days'):
            try: number=int(value.get(key,14 if key=='temporary_days' else 30))
            except (ValueError,TypeError): raise d.Problem('保留天数必须为整数')
            if not 1<=number<=365: raise d.Problem('保留天数须在 1–365 范围内')
            value[key]=number
    with db.transaction() as cur:
        current=db.one(cur,'SELECT * FROM settings WHERE name=%s FOR UPDATE',(name,)); d.revision(current,body)
        if current['value']==value: return {name:current}
        db.record_configuration(cur,'settings:'+name,0,current['revision'],current)
        cur.execute('UPDATE settings SET value=%s,revision=revision+1 WHERE name=%s',(db.dump(value),name))
        updated=db.one(cur,'SELECT * FROM settings WHERE name=%s',(name,)); db.record_configuration(cur,'settings:'+name,0,updated['revision'],updated)
    return settings()


@app.get('/api/products')
def products(q:str='',category_id:int|None=None,page:int=1,page_size:int=24,before:int|None=None):
    where=['1=1']; args=[]
    if q: where.append('(p.name LIKE %s OR EXISTS(SELECT 1 FROM skus s WHERE s.product_id=p.id AND s.code LIKE %s))'); args += ['%'+q+'%']*2
    if category_id: where.append('p.category_id=%s'); args.append(category_id)
    with db.transaction() as cur:
        return d.page(cur,'products p JOIN directories c ON c.id=p.category_id',' AND '.join(where),args,page,page_size,
            'p.*,c.name category,(SELECT COUNT(*) FROM skus s WHERE s.product_id=p.id) sku_count,(SELECT COUNT(*) FROM designs ds WHERE ds.product_id=p.id) design_count,(SELECT COUNT(*) FROM listings l WHERE l.product_id=p.id) listing_count','p.id DESC',before)


@app.post('/api/products')
def create_product(body:dict): return d.save_product(body)


@app.get('/api/products/{entity_id}')
def product_detail(entity_id:int): return d.product_detail(entity_id)


@app.put('/api/products/{entity_id}')
def edit_product(entity_id:int,body:dict): return d.save_product(body,entity_id)


@app.get('/api/products/{entity_id}/skus')
def product_skus(entity_id:int,page:int=1,page_size:int=24,before:int|None=None,q:str=''):
    where=['product_id=%s'];args=[entity_id]
    if q:where.append('(code LIKE %s OR label LIKE %s)');args+=['%'+q+'%']*2
    with db.transaction() as cur:
        d.get(cur,'products',entity_id)
        return d.page(cur,'skus',' AND '.join(where),args,page,page_size,before=before)


@app.post('/api/products/{entity_id}/skus')
def create_sku(entity_id:int,body:dict): return d.save_sku(body,product_id=entity_id)


@app.put('/api/skus/{entity_id}')
def edit_sku(entity_id:int,body:dict): return d.save_sku(body,entity_id=entity_id)


@app.get('/api/skus/{entity_id}')
def sku_detail(entity_id:int):
    with db.transaction() as cur: return d.get(cur,'skus',entity_id)


@app.post('/api/assets')
async def upload_asset(file:UploadFile=File(...)):
    content=await file.read(80*1024*1024+1)
    row=d.store_asset(content,file.filename or 'asset.bin',file.content_type or '')
    return {key:value for key,value in row.items() if key!='path'}


@app.get('/api/assets/{entity_id}')
def read_asset(entity_id:int,download:bool=False):
    asset=d.get_asset(entity_id)
    if not Path(asset['path']).is_file(): raise d.Problem('资产文件丢失，请恢复对应文件','asset_missing',404)
    inline=asset['mime'] in {'image/png','image/jpeg','image/webp','image/gif'}
    return FileResponse(asset['path'],media_type=asset['mime'],filename=asset['name'] if download or not inline else None,
        headers={'X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})


@app.get('/api/sources')
def sources(kind:str|None=None,product_id:int|None=None,q:str='',page:int=1,page_size:int=24,before:int|None=None):
    where=['1=1']; args=[]
    if kind: where.append('s.kind=%s'); args.append(kind)
    if product_id:
        where.append('(s.product_id=%s OR EXISTS(SELECT 1 FROM product_sources x WHERE x.source_id=s.id AND x.product_id=%s) OR EXISTS(SELECT 1 FROM source_adoptions a WHERE a.source_id=s.id AND a.product_id=%s))'); args += [product_id]*3
    if q: where.append('(s.title LIKE %s OR s.excerpt LIKE %s)'); args += ['%'+q+'%']*2
    with db.transaction() as cur:
        result=d.page(cur,'sources s',' AND '.join(where),args,page,page_size,'s.*','s.id DESC',before)
        result['items']=[d.source_record(row) for row in result['items']]
        return result


@app.post('/api/research')
def research(body:dict):
    intent=str(d.required(body.get('intent_key'),'请求标识'))
    source=None; config={}
    with db.transaction() as cur:
        existing=db.one(cur,'SELECT * FROM jobs WHERE intent_key=%s',(intent,))
        if existing:
            if existing['kind']!='research' or str(existing['payload'].get('source_id') or '')!=str(body.get('source_id') or ''):
                raise d.Problem('重复请求标识属于另一操作','intent_conflict',409)
            old_product=existing['payload'].get('product_id')
            requested_product=None if existing['payload'].get('kind')=='hotspot' else body.get('product_id')
            if str(old_product or '')!=str(requested_product or ''): raise d.Problem('重复请求标识属于另一目标商品','intent_conflict',409)
            return d.public_job(existing)
        if body.get('source_id'):
            source=d.get(cur,'directories',int(body['source_id']))
            if source['kind']!='sources': raise d.Problem('所选记录不是信息来源方案')
            if not source['enabled']: raise d.Problem('来源方案已停用，请选择启用方案','source_disabled',409)
            config=source['config']
            if not d.source_capability(config)['supported']: raise d.Problem('所选来源尚未适配，登记名称不能代替执行能力','source_unsupported',422)
        kind=config.get('kind','market') if source else body.get('kind','market')
        if kind not in d.RESEARCH_PLATFORMS: raise d.Problem('研究来源类型无效')
        if source and body.get('kind') and body['kind']!=kind: raise d.Problem('来源方案的使用范围与当前页面不一致','source_scope_mismatch',422)
        raw_query=d.required(body.get('query',config.get('query')),'查询主题')
        if not isinstance(raw_query,str): raise d.Problem('查询主题必须为文本')
        pid=None if kind=='hotspot' else int(d.required(body.get('product_id'),'目标商品'))
        product=d.get(cur,'products',pid) if pid else None
        if product:
            product['category']=d.get(cur,'directories',product['category_id'])['name']
    platforms=[config['platform']] if source else body.get('platforms',[])
    if not isinstance(platforms,list) or not platforms or len(platforms)>10: raise d.Problem('请选择 1–10 个来源平台')
    if any(not isinstance(platform,str) or platform not in d.RESEARCH_PLATFORMS[kind] for platform in platforms): raise d.Problem('来源平台与研究范围不匹配，或尚未适配','source_unsupported',422)
    locale=str(body.get('locale',config.get('locale','')) or '').strip()
    cross_category=bool(body.get('cross_category',config.get('cross_category',False))) if kind!='hotspot' else False
    source_category=str(body.get('source_category',config.get('source_category','')) or '').strip() if cross_category else ''
    query=' '.join(part for part in (raw_query,source_category,locale) if part)
    payload={**body,'kind':kind,'query':query,'raw_query':raw_query,'platforms':list(dict.fromkeys(platforms)),'locale':locale,
        'locale_support':'search_hint','cross_category':cross_category,'source_category':source_category,
        'product_id':pid,'product':product,'source_id':source['id'] if source else None,'source_configuration':source,
        'limit':min(30,max(1,int(body.get('limit',config.get('limit',12)))))}
    return d.public_job(db.create_job('research',pid,payload,intent))


@app.post('/api/sources/from-url')
def source_from_url(body:dict):
    url=d.required(body.get('url'),'公开来源链接')
    if not isinstance(url,str) or len(url)>2000:raise d.Problem('来源链接无效或超过2000字，请使用原始公开页面链接')
    parsed=urlsplit(url);host=(parsed.hostname or '').rstrip('.').lower()
    if parsed.scheme not in {'http','https'} or not host or parsed.username or parsed.password or host=='localhost' or host.endswith(('.localhost','.local')):raise d.Problem('只允许无账号信息的公开HTTP或HTTPS网页')
    try:
        if not ipaddress.ip_address(host).is_global:raise d.Problem('不能读取本机或内网来源')
    except ValueError:pass
    kind=body.get('kind','market')
    if kind not in d.RESEARCH_PLATFORMS:raise d.Problem('来源类型无效')
    pid=None if kind=='hotspot' else int(d.required(body.get('product_id'),'目标商品'))
    with db.transaction() as cur:product=d.get(cur,'products',pid) if pid else None
    from studio.providers import PLATFORMS
    platform=next((name for name,domains in PLATFORMS.items() if any(host==domain or host.endswith('.'+domain) for domain in domains)),'公开网页')
    payload={'kind':kind,'product_id':pid,'product':product,'platforms':[platform],'query':url,'raw_query':url,'direct_urls':[url],
        'acquisition':'direct_url','source_category':str(body.get('source_category') or ''),'limit':1,'cross_category':bool(body.get('cross_category'))}
    return d.public_job(db.create_job('research',pid,payload,str(d.required(body.get('intent_key'),'请求标识'))))


@app.get('/api/research/{job_id}')
def research_detail(job_id:int):
    with db.transaction() as cur:
        job=d.get(cur,'jobs',job_id)
        if job['kind']!='research': raise d.Problem('该任务不是研究任务')
        ids=list((job.get('result') or {}).get('source_ids') or [])
        for step in job.get('steps') or []:
            ids.extend((step.get('result') or {}).get('source_ids') or [])
        ids=list(dict.fromkeys(int(v) for v in ids))[:100]
        items=db.rows(cur,'SELECT * FROM sources WHERE id IN ('+','.join(['%s']*len(ids))+') ORDER BY id',tuple(ids)) if ids else db.rows(cur,'SELECT * FROM sources WHERE job_id=%s ORDER BY id LIMIT 100',(job_id,))
        return {'job':d.public_job(job),'items':[d.source_record(row) for row in items]}


@app.get('/api/sources/{entity_id}')
def source_detail(entity_id:int):
    with db.transaction() as cur:
        row=d.source_record(d.get(cur,'sources',entity_id))
        row['adoptions']=db.rows(cur,'SELECT a.*,p.name product_name FROM source_adoptions a JOIN products p ON p.id=a.product_id WHERE a.source_id=%s ORDER BY a.id DESC LIMIT 100',(entity_id,))
        return row


@app.post('/api/sources/{entity_id}/adopt')
def adopt_source(entity_id:int,body:dict):
    pid=int(d.required(body.get('product_id'),'目标商品')); elements=body.get('elements',{})
    if isinstance(elements,str): elements={'notes':elements}
    if not isinstance(elements,dict): raise d.Problem('采用元素必须为对象')
    with db.transaction() as cur:
        row=d.get(cur,'sources',entity_id,True); d.get(cur,'products',pid)
        cur.execute('INSERT INTO source_adoptions(source_id,product_id,elements,snapshot) VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE elements=VALUES(elements)',(entity_id,pid,db.dump(elements),db.dump(row)))
        cur.execute('INSERT INTO product_sources(product_id,source_id,elements) VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE elements=VALUES(elements)',(pid,entity_id,db.dump(elements)))
        return db.one(cur,'SELECT * FROM source_adoptions WHERE source_id=%s AND product_id=%s',(entity_id,pid))


@app.get('/api/designs')
def designs(product_id:int|None=None,status:str|None=None,page:int=1,page_size:int=24,before:int|None=None,sku_id:int|None=None,q:str=''):
    where=['1=1']; args=[]
    if product_id: where.append('ds.product_id=%s'); args.append(product_id)
    if status: where.append('ds.status=%s'); args.append(status)
    if sku_id:where.append('ds.sku_id=%s');args.append(sku_id)
    if q:where.append('(ds.brief LIKE %s OR CAST(ds.id AS CHAR)=%s)');args+=['%'+q+'%',q]
    with db.transaction() as cur:
        if sku_id:
            sku=d.get(cur,'skus',sku_id)
            if product_id and sku['product_id']!=product_id:raise d.Problem('SKU 不属于当前商品')
        result=d.page(cur,'designs ds JOIN products p ON p.id=ds.product_id JOIN skus s ON s.id=ds.sku_id',' AND '.join(where),args,page,page_size,'ds.*,p.name product_name,s.code sku_code','ds.id DESC',before)
        result['items']=[d.design_record(cur,row) for row in result['items']]; return result


@app.post('/api/designs')
def create_design(body:dict): return d.save_design(body)


@app.get('/api/designs/{entity_id}')
def design_detail(entity_id:int): return d.design_detail(entity_id)


@app.put('/api/designs/{entity_id}')
def edit_design(entity_id:int,body:dict): return d.save_design(body,entity_id)


@app.post('/api/designs/{entity_id}/start')
def start_design(entity_id:int,body:dict): return d.public_job(d.start_design(entity_id,body))


@app.post('/api/designs/{entity_id}/fork')
def fork_design(entity_id:int,body:dict): return d.fork_design(entity_id,body)


@app.post('/api/designs/{entity_id}/listing')
def design_listing(entity_id:int,body:dict):
    design=d.design_detail(entity_id)
    return d.create_listing({'product_id':design['product_id'],'sku_id':design['sku_id'],'design_id':entity_id,'target_id':body.get('target_id')})


@app.get('/api/jobs')
def jobs(status:str|None=None,kind:str|None=None,page:int=1,page_size:int=24,before:int|None=None):
    where=['1=1']; args=[]
    if status: where.append('status=%s'); args.append(status)
    if kind: where.append('kind=%s'); args.append(kind)
    with db.transaction() as cur:
        result=d.page(cur,'jobs',' AND '.join(where),args,page,page_size,'id,kind,entity_id,status,stage,progress,error,steps,created_at,updated_at',before=before)
        result['items']=[d.public_job(j) for j in result['items']]; return result


@app.get('/api/jobs/{entity_id}')
def job_detail(entity_id:int):
    with db.transaction() as cur: return d.public_job(d.get(cur,'jobs',entity_id))


@app.post('/api/jobs/{entity_id}/resume')
def resume_job(entity_id:int): return d.public_job(d.resume_job(entity_id))


@app.post('/api/jobs/{entity_id}/cancel')
def cancel_job(entity_id:int): return d.public_job(d.cancel_job(entity_id))


@app.get('/api/artworks')
def artworks(product_id:int|None=None,role:str|None=None,page:int=1,page_size:int=24,before:int|None=None):
    where=['1=1']; args=[]
    if product_id: where.append('ds.product_id=%s'); args.append(product_id)
    if role: where.append('a.role LIKE %s'); args.append(role+'%')
    with db.transaction() as cur:
        result=d.page(cur,'design_assets a JOIN designs ds ON ds.id=a.design_id JOIN products p ON p.id=ds.product_id JOIN assets f ON f.id=a.asset_id',' AND '.join(where),args,page,page_size,'a.*,ds.product_id,ds.sku_id,p.name product_name,f.width,f.height,f.sha256','a.id DESC',before)
        for row in result['items']: row['url']=f"/api/assets/{row['asset_id']}"
        return result


@app.get('/api/artworks/{entity_id}')
def artwork_detail(entity_id:int):
    with db.transaction() as cur:
        row=d.get(cur,'design_assets',entity_id); row['asset']=d.asset_record(d.get(cur,'assets',row['asset_id'])); row['design']=d.design_record(cur,d.get(cur,'designs',row['design_id'])); return row


@app.get('/api/targets')
def targets():
    with db.transaction() as cur: return db.rows(cur,'SELECT * FROM targets ORDER BY id DESC LIMIT 100')


@app.post('/api/targets')
def create_target(body:dict): return d.save_target(body)


@app.put('/api/targets/{entity_id}')
def edit_target(entity_id:int,body:dict): return d.save_target(body,entity_id)


@app.get('/api/listings')
def listings(target_id:int|None=None,product_id:int|None=None,page:int=1,page_size:int=24,before:int|None=None):
    where=['1=1']; args=[]
    if target_id: where.append('target_id=%s'); args.append(target_id)
    if product_id: where.append('product_id=%s'); args.append(product_id)
    with db.transaction() as cur:
        result=d.page(cur,'listings',' AND '.join(where),args,page,page_size,before=before)
        result['items']=[d.listing_record(cur,row) for row in result['items']]; return result


@app.post('/api/listings')
def create_listing(body:dict): return d.create_listing(body)


@app.get('/api/listings/{entity_id}')
def listing_detail(entity_id:int): return d.listing_detail(entity_id)


@app.put('/api/listings/{entity_id}')
def edit_listing(entity_id:int,body:dict): return d.save_listing(entity_id,body)


@app.post('/api/listings/{entity_id}/copy')
def copy_listing(entity_id:int,body:dict): return d.public_job(d.listing_copy(entity_id,body))


@app.post('/api/listings/{entity_id}/validate')
def validate_listing(entity_id:int):
    row=d.listing_detail(entity_id); return {'issues':row['diagnostics'],'ready':not row['diagnostics']}


@app.post('/api/listings/{entity_id}/images/verify')
def verify_listing_images(entity_id:int,body:dict):
    row=d.listing_detail(entity_id); d.revision(row,body)
    from studio.transfers import resolve_listing_template,mapped_export_images
    requested=body.get('template_id')
    if requested not in (None,''):
        try:
            if isinstance(requested,bool): raise ValueError()
            requested=int(requested)
            if requested<1: raise ValueError()
        except (TypeError,ValueError): raise d.Problem('请选择有效的刊登模板') from None
    with db.transaction() as cur: template=resolve_listing_template(cur,row,requested)
    if not template: raise d.Problem('请先绑定目标店铺原生模板，再校验实际导出的图片','template_missing',409)
    images=row['images']
    for item in mapped_export_images(template['config'],row):
        url=item.get('url',''); current=url
        try:
            for _ in range(4):
                parsed=urlsplit(current)
                if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password: raise ValueError('需要 HTTPS 地址')
                addresses=socket.getaddrinfo(parsed.hostname,parsed.port or 443,type=socket.SOCK_STREAM)
                if any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses): raise ValueError('仅支持公开图片地址')
                with httpx.stream('GET',current,timeout=15,follow_redirects=False,trust_env=False) as response:
                    if response.is_redirect:
                        from urllib.parse import urljoin
                        current=urljoin(current,response.headers['location']); continue
                    response.raise_for_status(); hasher=sha256(); size=0
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>80*1024*1024: raise ValueError('远程图片过大')
                        hasher.update(chunk)
                    asset=d.get_asset(item['asset_id'])
                    if hasher.hexdigest()!=asset['sha256']: raise ValueError('远程图片与绑定资产不一致')
                    item['url_status']='verified'; item['verification_error']=''; break
            else: raise ValueError('图片跳转过多')
        except Exception as exc: item['url_status']='failed'; item['verification_error']=str(exc)[:250]
    with db.transaction() as cur:
        current=d.get(cur,'listings',entity_id,True); d.revision(current,body)
        active=resolve_listing_template(cur,current,requested)
        if not active or (active['id'],active['revision'])!=(template['id'],template['revision']): raise d.Problem('图片校验期间模板已变更，请按当前模板重新校验','template_changed',409)
        db.update(cur,'listings',entity_id,{'images':images,'revision':current['revision']+1})
    return d.listing_detail(entity_id)


@app.get('/api/storage')
def storage(): return d.storage_summary()


@app.post('/api/storage/cleanup')
def cleanup_storage():
    result=d.cleanup_storage(); result['storage']=d.storage_summary(); return result


try:
    from studio.transfers import router as transfers_router
    app.include_router(transfers_router)
except ModuleNotFoundError as exc:
    if exc.name!='studio.transfers': raise

from studio.revisions import router as revisions_router
app.include_router(revisions_router)
from studio.families import router as families_router
app.include_router(families_router)
from studio.backups import router as backups_router
app.include_router(backups_router)
from studio.thumbnails import router as thumbnails_router
app.include_router(thumbnails_router)
from studio.public_media import router as public_media_router
app.include_router(public_media_router)
from studio.recovery import router as recovery_router
app.include_router(recovery_router)

STATIC=Path(__file__).with_name('static')
if STATIC.is_dir(): app.mount('/static',StaticFiles(directory=STATIC),name='static')


@app.get('/')
def home():
    path=STATIC/'index.html'
    if not path.exists(): raise d.Problem('界面文件尚未准备好','ui_unavailable',503)
    return FileResponse(path)
