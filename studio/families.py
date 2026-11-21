"""Explicit listing families: a logical parent never becomes a physical SKU."""
from copy import deepcopy
import re

from fastapi import APIRouter
from . import db,domain,native_rules

router=APIRouter(prefix='/api')
FORBIDDEN_PARENT_FIELDS={'sku','sku_code','price','stock','product_id','product_id_type','barcode','source_asin','cost_price','cost_currency','supplier_code','supplier_id','image_url','parent_sku','parentage','fulfillment_channel','condition','quantity','item_length','item_width','item_weight','package_weight','weight_g','package_weight_g','model_number'}


def parent_omitted_columns(config):
    prefixes=('amzn1.volt.ca.product_id','fulfillment_availability','purchasable_offer','item_weight','item_package','model_number','condition_type','main_product_image','other_product_image','child_parent_sku_relationship')
    return {c['column'] for c in config.get('columns',[]) if c.get('identifier','').startswith(prefixes)}


def child_values(family,values):
    return {**values,'parentage':'Child','parent_sku':family['parent_sku'],'product_type':family['product_type'],
        'variation':family['variation'],'listing_action':family['listing_action']}


def parent_values(family,config=None):
    fields=family['fields'];attributes=fields.get('attributes') or {}
    values={**attributes,**{k:v for k,v in fields.items() if k not in ('attributes','bullets')},
        'sku':family['parent_sku'],'parentage':'Parent','product_type':family['product_type'],
        'variation':family['variation'],'listing_action':family['listing_action']}
    for index,bullet in enumerate(fields.get('bullets') or [],1):values['bullet_'+str(index)]=bullet
    for key in FORBIDDEN_PARENT_FIELDS:values.pop(key,None)
    if config:
        omitted=parent_omitted_columns(config)
        for key,column in config.get('mapping',{}).items():
            if column in omitted:values.pop(key,None)
    values['sku']=family['parent_sku']
    values['parentage']='Parent'
    return values


def _children(cur,family_id):
    return db.rows(cur,'SELECT l.*,s.code sku_code,s.label sku_label,p.name FROM family_members m JOIN listings l ON l.id=m.listing_id JOIN skus s ON s.id=l.sku_id JOIN products p ON p.id=l.product_id WHERE m.family_id=%s ORDER BY m.sort_order,m.listing_id',(family_id,))


def _structural_issues(cur,family,children,config):
    from .transfers import export_values
    issues=[];values=[]
    for child in children:
        if child['target_id']!=family['target_id']:issues.append({'field':'listing_ids','listing_id':child['id'],'message':'子刊登不属于同一目标店铺。'})
        sku=domain.get(cur,'skus',child['sku_id'])
        actual=export_values(child,sku)
        for field,expected in [('product_type',family['product_type']),('parentage','Child'),('parent_sku',family['parent_sku']),('variation',family['variation'])]:
            if actual.get(field) not in (None,'',expected):issues.append({'field':field,'listing_id':child['id'],'message':'子刊登已有不同的'+field+'，请先明确修正该刊登或选择正确分组。'})
        if sku['code']==family['parent_sku']:issues.append({'field':'parent_sku','message':'逻辑父SKU不能与实际销售SKU相同。'})
        values.append(child_values(family,actual))
    for problem in native_rules.batch_issues(config,[parent_values(family,config),*values]):
        index=problem['row_index']-1
        issues.append({**problem,**({'listing_id':children[index]['id']} if index>=0 else {})})
    return issues


def family_diagnostics(cur,family,children=None):
    from .transfers import export_values
    children=_children(cur,family['id']) if children is None else children
    template=domain.get(cur,'templates',family['template_id']);target=domain.get(cur,'targets',family['target_id'])
    result=[]
    if len(children)<2:result.append({'field':'listing_ids','message':'父子分组至少需要两个真实子刊登。'})
    result.extend(_structural_issues(cur,family,children,template['config']))
    for issue in native_rules.issues(template['config'],parent_values(family,template['config']),target):
        result.append({**issue,'row_type':'parent','family_id':family['id'],'href':f"#/families/{family['id']}?field={issue['field']}"})
    for child in children:
        sku=domain.get(cur,'skus',child['sku_id']);values=child_values(family,export_values(child,sku))
        for issue in native_rules.issues(template['config'],values,target):
            result.append({**issue,'row_type':'child','listing_id':child['id'],'href':f"#/listings/{child['id']}?field={issue['field']}"})
    return result


@router.get('/families')
def list_families(page:int=1,page_size:int=24,target_id:int|None=None,template_id:int|None=None):
    clauses=['1=1'];args=[]
    for key,value in [('target_id',target_id),('template_id',template_id)]:
        if value:clauses.append('f.'+key+'=%s');args.append(value)
    with db.transaction() as cur:
        result=domain.page(cur,'listing_families f',' AND '.join(clauses),args,page,page_size,
            select='f.*,(SELECT COUNT(*) FROM family_members m WHERE m.family_id=f.id) child_count',order='f.id DESC')
    for item in result['items']:item.update(title=item['fields'].get('title',''),brand=item['fields'].get('brand',''))
    return result


@router.get('/families/{family_id}')
def family_detail(family_id:int):
    with db.transaction() as cur:
        family=domain.get(cur,'listing_families',family_id)
        children=_children(cur,family_id)
        family.update(children=children,listing_ids=[c['id'] for c in children],child_count=len(children),
            title=family['fields'].get('title',''),brand=family['fields'].get('brand',''),
            template=domain.get(cur,'templates',family['template_id']),target=domain.get(cur,'targets',family['target_id']),
            diagnostics=family_diagnostics(cur,family,children),external_parent_status='not_verified')
    return family


def save_family(body,family_id=None):
    with db.transaction() as cur:
        current=domain.get(cur,'listing_families',family_id,True) if family_id else None
        if current:domain.revision(current,body)
        merged={**(current or {}),**body}
        target_id=int(domain.required(merged.get('target_id'),'目标店铺'))
        template_id=int(domain.required(merged.get('template_id'),'原生模板'))
        target=domain.get(cur,'targets',target_id,True);template=domain.get(cur,'templates',template_id)
        if not target['enabled'] or template['target_id']!=target_id:raise domain.Problem('模板、目标店铺必须一致且店铺已启用')
        if (template['config'].get('native_rules') or {}).get('adapter')!='amazon_pillow_us_v1':raise domain.Problem('当前逻辑父体行仅完成该Amazon US PILLOW原生模板适配')
        parent=str(domain.required(merged.get('parent_sku'),'父SKU'))
        if len(parent)>120 or any(ord(c)<32 for c in parent):raise domain.Problem('父SKU过长或含控制字符')
        if current and (current['parent_sku']!=parent or current['target_id']!=target_id):raise domain.Problem('父SKU与目标店铺是稳定身份，保存时不能更换','family_identity_frozen',409)
        if db.one(cur,'SELECT id FROM skus WHERE code=%s',(parent,)):raise domain.Problem('父SKU已被实际销售SKU使用，逻辑父体不能冒充实物SKU')
        other=db.one(cur,'SELECT id FROM listing_families WHERE target_id=%s AND parent_sku=%s',(target_id,parent))
        if other and other['id']!=family_id:raise domain.Problem('此目标店铺的父SKU已存在，请打开现有分组','family_exists',409,{'family_id':other['id']})
        fields=deepcopy(merged.get('fields') or {})
        if not isinstance(fields,dict) or not isinstance(fields.get('attributes',{}),dict):raise domain.Problem('父体共同字段格式无效')
        for key in ('title','brand'):
            if key in body:fields[key]=body[key]
            fields[key]=str(domain.required(fields.get(key),'父体'+key))
        if not isinstance(fields.get('bullets',[]),list):raise domain.Problem('父体卖点应为列表')
        for key,value in {**fields,**fields.get('attributes',{})}.items():
            if (key in FORBIDDEN_PARENT_FIELDS or template['config'].get('mapping',{}).get(key) in parent_omitted_columns(template['config'])) and value not in (None,'',[],{}):raise domain.Problem('逻辑父体不能填写销售报价、库存、条码、供货身份或实物重量：'+key)
        family={'target_id':target_id,'template_id':template_id,'parent_sku':parent,
            'product_type':str(domain.required(merged.get('product_type'),'产品类型')),
            'variation':str(domain.required(merged.get('variation'),'变体主题')),
            'listing_action':str(domain.required(merged.get('listing_action'),'刊登操作')),'fields':fields}
        rules=template['config']['native_rules']['rules']
        for key,column in [('product_type','B'),('variation','F'),('listing_action','C')]:
            rule=next((r for r in rules if r['column']==column),{})
            allowed=(rule.get('enums') or {}).get(family['product_type'],[])
            if not allowed or family[key] not in allowed:raise domain.Problem(key+'不属于模板允许值',fields={key:{'allowed':allowed}})
        if family['listing_action']=='Delete':raise domain.Problem('此工作流不生成删除父子刊登的文件')
        ids=list(dict.fromkeys(int(v) for v in body.get('listing_ids',[])))
        if len(ids)<2 or len(ids)>1000:raise domain.Problem('分组需要2至1000个明确的真实子刊登')
        children=[]
        for ident in ids:
            child=domain.get(cur,'listings',ident,True)
            existing=db.one(cur,'SELECT family_id FROM family_members WHERE listing_id=%s',(ident,))
            if existing and existing['family_id']!=family_id:raise domain.Problem('子刊登已属于另一父子分组','already_grouped',409,{'listing_id':ident,'family_id':existing['family_id']})
            children.append(child)
        problems=_structural_issues(cur,family,children,template['config'])
        if problems:raise domain.Problem('子刊登关系或变体属性需要修正','invalid_family',422,problems)
        if current:
            old_ids=[c['id'] for c in _children(cur,family_id)]
            if all(current[k]==v for k,v in family.items()) and old_ids==ids:return family_detail(family_id)
            db.update(cur,'listing_families',family_id,{**family,'revision':current['revision']+1})
            cur.execute('DELETE FROM family_members WHERE family_id=%s',(family_id,))
        else:family_id=db.insert(cur,'listing_families',family)
        for position,ident in enumerate(ids):db.insert(cur,'family_members',{'family_id':family_id,'listing_id':ident,'sort_order':position})
    return family_detail(family_id)


@router.post('/families')
def create_family(body:dict):return save_family(body)


@router.put('/families/{family_id}')
def update_family(family_id:int,body:dict):return save_family(body,family_id)


@router.post('/families/{family_id}/validate')
def validate_family(family_id:int):
    family=family_detail(family_id)
    return {'ready':not family['diagnostics'],'issues':family['diagnostics'],'family_id':family_id,'revision':family['revision'],'external_parent_status':'not_verified'}
