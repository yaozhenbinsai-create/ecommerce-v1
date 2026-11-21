"""Explicit local image allowlist. This module never enables a public deployment."""
from hashlib import sha256
import ipaddress
from pathlib import Path
import re
from urllib.parse import urlsplit

from fastapi import APIRouter
from fastapi.responses import FileResponse
from PIL import Image,UnidentifiedImageError
from . import db,domain as d
from .transfers import resolve_listing_template,mapped_export_images

router=APIRouter()
PREFIX='/studio-public-media'

def _setting(cur):
    return db.one(cur,"SELECT * FROM settings WHERE name='public_media'") or {'name':'public_media','value':{'base_url':''},'revision':0}

def _base(value):
    if value in (None,''):return ''
    if not isinstance(value,str) or len(value)>1000:raise d.Problem('公开图片地址必须是有效 HTTPS 地址')
    try:
        parsed=urlsplit(value.strip());host=(parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme!='https' or not host or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip('/')!=PREFIX:raise ValueError()
        if host=='localhost' or host.endswith(('.localhost','.local')) or '.' not in host:raise ValueError()
        try:
            if not ipaddress.ip_address(host).is_global:raise ValueError()
        except ValueError:
            # An invalid numeric address is not a hostname fallback.
            if re.fullmatch(r'[0-9.:]+',host):raise
        if parsed.port and not 1<=parsed.port<=65535:raise ValueError()
    except ValueError:raise d.Problem('请输入独立 HTTPS 图片入口，例如 https://images.example.com/studio-public-media；不能使用本机、账号信息、查询串或旧 /public-media 路径') from None
    return value.strip().rstrip('/')

def _url(config,record):
    base=config['value'].get('base_url','')
    return base+'/'+record['sha256']+'.'+record['extension'] if base else ''

def _configuration(setting):
    return {'base_url':setting['value'].get('base_url',''),'revision':setting['revision'],
        'configured':bool(setting['value'].get('base_url')),'public_route_prefix':PREFIX,'deployment_status':'not_verified',
        'message':'这里只保存独立公网入口地址。没有部署公网代理，也没有执行可访问性校验。'}

@router.get('/api/public-images/config')
def public_configuration():
    with db.transaction() as cur:return _configuration(_setting(cur))

@router.put('/api/public-images/config')
def save_public_configuration(body:dict):
    base=_base(body.get('base_url'))
    with db.transaction() as cur:
        current=db.one(cur,"SELECT * FROM settings WHERE name='public_media' FOR UPDATE") or _setting(cur)
        d.revision(current,body)
        if current['value'].get('base_url','')==base:return _configuration(current)
        if current['revision']:
            db.record_configuration(cur,'settings:public_media',0,current['revision'],current)
            cur.execute("UPDATE settings SET value=%s,revision=revision+1 WHERE name='public_media'",(db.dump({'base_url':base}),))
        else:db.insert(cur,'settings',{'name':'public_media','value':{'base_url':base}})
        saved=_setting(cur);db.record_configuration(cur,'settings:public_media',0,saved['revision'],saved)
        return _configuration(saved)

def _fingerprint(value):return sha256(db.dump(value).encode()).hexdigest()

def _registry_lock(cur):
    # All publish/revoke operations use the same order before locking listings
    # and asset registrations. Connection close releases this advisory lock.
    lock='public-images:'+sha256(db.SCHEMA.encode()).hexdigest()[:48]
    if db.one(cur,'SELECT GET_LOCK(%s,10) acquired',(lock,))['acquired']!=1:raise d.Problem('公开图片登记正在更新，请稍后重试','publication_busy',409)

def _checked_file(asset):
    original=Path(asset['path']);path=original.resolve();root=(db.DATA/'assets').resolve()
    if path.parent!=root or original.is_symlink() or not path.is_file():raise d.Problem('图片资产文件不存在或路径无效','asset_missing',409)
    digest=sha256();size=0
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block);size+=len(block)
    if digest.hexdigest()!=asset['sha256'] or size!=asset['byte_size']:raise d.Problem('图片文件与保存的资产哈希不一致，不能公开','asset_corrupt',409)
    try:
        with Image.open(path) as image:
            image.verify();format=image.format
    except (OSError,ValueError,UnidentifiedImageError):raise d.Problem('实际文件不是完整可读图片，不能公开') from None
    if format not in {'JPEG','PNG'}:raise d.Problem('公开图片准备仅支持实际 JPEG 或 PNG；请先选择相应格式的正式设计资产')
    return path,('jpg','image/jpeg') if format=='JPEG' else ('png','image/png')

def _selection(cur,listing_id,requested=None):
    listing=d.get(cur,'listings',listing_id)
    template=resolve_listing_template(cur,listing,requested)
    if not template:raise d.Problem('请先选择目标店铺原生模板，再准备该模板实际导出的图片','template_missing',409)
    images=mapped_export_images(template['config'],listing)
    if not images:raise d.Problem('所选模板没有映射可公开的图片槽')
    return listing,template,images

def _record_view(record,image,setting):
    url=_url(setting,record)
    return {'publication_id':record['id'],'asset_id':record['asset_id'],'role':image.get('role'),'design_asset_id':image.get('design_asset_id'),
        'sha256':record['sha256'],'extension':record['extension'],'mime':record['mime'],'bytes':record['byte_size'],
        'url':url,'local_route':PREFIX+'/'+record['sha256']+'.'+record['extension'],'status':record['status'],'revision':record['revision'],
        'state':'revoked' if record['status']=='revoked' else 'prepared' if url else 'pending_config',
        'verified':bool(record['status']=='registered' and url and image.get('url')==url and image.get('url_status')=='verified')}

@router.get('/api/listings/{listing_id}/public-images')
def listing_public_images(listing_id:int,template_id:int|None=None):
    with db.transaction() as cur:
        listing,template,images=_selection(cur,listing_id,template_id);setting=_setting(cur);items=[]
        for image in images:
            record=db.one(cur,'SELECT * FROM public_image_assets WHERE asset_id=%s',(image['asset_id'],))
            if record:items.append(_record_view(record,image,setting))
            else:items.append({'publication_id':None,'asset_id':image['asset_id'],'role':image.get('role'),'state':'not_prepared','status':'not_registered','url':'','verified':False})
        return {'listing_id':listing_id,'listing_revision':listing['revision'],'template_id':template['id'],'template_revision':template['revision'],
            'configuration':_configuration(setting),'items':items,'external_deployment_performed':False,
            'message':'仅准备所选模板实际导出的图片；需要独立部署并通过真实 HTTPS 图片校验，才可用于导出。'}

@router.post('/api/listings/{listing_id}/public-images/prepare')
def prepare_public_images(listing_id:int,body:dict):
    requested=body.get('template_id')
    with db.transaction() as cur:
        listing,template,images=_selection(cur,listing_id,requested);d.revision(listing,body)
    checked=[]
    for image in images:
        asset=d.get_asset(image['asset_id']);path,(extension,mime)=_checked_file(asset)
        checked.append((image,asset,extension,mime))
    with db.transaction() as cur:
        _registry_lock(cur)
        current=d.get(cur,'listings',listing_id,True);d.revision(current,body)
        latest=resolve_listing_template(cur,current,requested)
        if not latest or (latest['id'],latest['revision'])!=(template['id'],template['revision']):raise d.Problem('准备期间模板已变化，请按当前模板重新准备','template_changed',409)
        setting=_setting(cur)
        for image,asset,extension,mime in checked:
            cur.execute("INSERT INTO public_image_assets(asset_id,sha256,extension,mime,byte_size) VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",(asset['id'],asset['sha256'],extension,mime,asset['byte_size']))
            record=d.get(cur,'public_image_assets',cur.lastrowid,True)
            if record['status']=='revoked':
                db.update(cur,'public_image_assets',record['id'],{'status':'registered','revision':record['revision']+1});record=d.get(cur,'public_image_assets',record['id'])
            snapshot={'listing_id':listing_id,'listing_revision':listing['revision'],'template_id':template['id'],'template_revision':template['revision'],
                'asset_id':asset['id'],'sha256':asset['sha256'],'role':image.get('role'),'design_asset_id':image.get('design_asset_id'),
                'publication_revision':record['revision'],'configuration_revision':setting['revision'],'url':_url(setting,record)}
            cur.execute('INSERT INTO public_image_events(publication_id,action,listing_id,template_id,fingerprint,snapshot) VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)',
                (record['id'],'prepared',listing_id,template['id'],_fingerprint(snapshot),db.dump(snapshot)))
    return listing_public_images(listing_id,requested)

@router.get('/api/public-images/{publication_id}')
def public_image_detail(publication_id:int):
    with db.transaction() as cur:
        record=d.get(cur,'public_image_assets',publication_id)
        record['events']=db.rows(cur,'SELECT * FROM public_image_events WHERE publication_id=%s ORDER BY id DESC LIMIT 100',(publication_id,))
        record['url']=_url(_setting(cur),record);record['verified']=False
        record['referencing_listing_count']=db.one(cur,'SELECT COUNT(*) n FROM listings WHERE JSON_CONTAINS(images,JSON_OBJECT(%s,%s))',('asset_id',record['asset_id']))['n']
        return record

@router.post('/api/public-images/{publication_id}/revoke')
def revoke_public_image(publication_id:int,body:dict):
    affected=0
    with db.transaction() as cur:
        _registry_lock(cur)
        record=d.get(cur,'public_image_assets',publication_id,True);d.revision(record,body)
        if record['status']!='revoked':
            db.update(cur,'public_image_assets',publication_id,{'status':'revoked','revision':record['revision']+1})
            urls={item['url'] for item in db.rows(cur,"SELECT DISTINCT JSON_UNQUOTE(JSON_EXTRACT(snapshot,'$.url')) url FROM public_image_events WHERE publication_id=%s AND action='prepared'",(publication_id,)) if item['url']}
            urls.add(_url(_setting(cur),record));after=0
            while True:
                listings=db.rows(cur,'SELECT * FROM listings WHERE id>%s AND JSON_CONTAINS(images,JSON_OBJECT(%s,%s)) ORDER BY id LIMIT 100 FOR UPDATE',(after,'asset_id',record['asset_id']))
                if not listings:break
                for listing in listings:
                    after=listing['id'];changed=False
                    for image in listing['images']:
                        if image.get('asset_id')==record['asset_id'] and image.get('url') and image['url'] in urls:
                            image.update(url_status='revoked',verification_error='此资产公开访问已撤销，重新准备后仍须重新验证 HTTPS 地址');changed=True
                    if changed:db.update(cur,'listings',listing['id'],{'images':listing['images'],'revision':listing['revision']+1});affected+=1
            snapshot={'publication_id':publication_id,'publication_revision':record['revision']+1,'sha256':record['sha256'],'asset_id':record['asset_id'],'scope':'all prepared URLs for this asset','affected_listings':affected}
            db.insert(cur,'public_image_events',{'publication_id':publication_id,'action':'revoked','fingerprint':_fingerprint(snapshot),'snapshot':snapshot})
    result=public_image_detail(publication_id);result['affected_listings']=affected;result['message']='此资产的公开路由已撤销；所有使用该公开路由的刊登均受影响。已被外部下载的副本无法收回。'
    return result

def publication_problem(cur,image):
    """Only URLs actually recorded by this publisher are governed by its revocation."""
    record=db.one(cur,'SELECT * FROM public_image_assets WHERE asset_id=%s',(image.get('asset_id'),))
    if not record or record['status']!='revoked' or not image.get('url'):return None
    reference=db.one(cur,"SELECT id FROM public_image_events WHERE publication_id=%s AND action='prepared' AND JSON_UNQUOTE(JSON_EXTRACT(snapshot,'$.url'))=%s LIMIT 1",(record['id'],image['url']))
    return '该图片的公开访问已撤销，请明确重新准备或更换图片地址' if reference or image['url']==_url(_setting(cur),record) else None

@router.api_route(PREFIX+'/{filename}',methods=['GET','HEAD'])
def read_public_image(filename:str):
    match=re.fullmatch(r'([a-f0-9]{64})\.(jpg|png)',filename)
    if not match:raise d.Problem('公开图片不存在','not_found',404)
    with db.transaction() as cur:
        record=db.one(cur,"SELECT * FROM public_image_assets WHERE sha256=%s AND extension=%s AND status='registered'",match.groups())
    if not record:raise d.Problem('公开图片不存在','not_found',404)
    asset=d.get_asset(record['asset_id']);path,(extension,mime)=_checked_file(asset)
    if extension!=record['extension'] or mime!=record['mime']:raise d.Problem('公开图片与登记内容不一致','asset_corrupt',409)
    return FileResponse(path,media_type=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'",'Content-Disposition':'inline; filename="'+filename+'"'})
