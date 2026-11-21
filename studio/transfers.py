"""Source-preserving imports, native workbook exports and deterministic production files."""
from copy import deepcopy
import csv
from hashlib import sha256
from io import BytesIO, StringIO
import ipaddress
import json
import math
from pathlib import Path
import posixpath
import re
import socket
from urllib.parse import urlsplit, urljoin
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
import httpx
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, column_index_from_string
from openpyxl.formula.translate import Translator
from PIL import Image
from . import db, domain
from .patterns import normalize_repeat, PatternError
from . import native_rules

router = APIRouter(prefix='/api')
MAX_FILE = 32 * 1024 * 1024
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'

def fail(message, fields=None, status=422):
    raise HTTPException(status_code=status, detail={'code':'transfer_error','message':message,'fields':fields or {}})

def required(cur, table, entity_id):
    item = db.one(cur, f'SELECT * FROM `{table}` WHERE id=%s', (entity_id,))
    if not item: fail('记录不存在。', status=404)
    return item

def asset_bytes(asset_id):
    return Path(domain.get_asset(asset_id)['path']).read_bytes()

def page_rows(table, where='', args=(), page=1, page_size=24):
    page=max(1,int(page)); page_size=min(100,max(1,int(page_size)))
    with db.transaction() as cur:
        total=db.one(cur,f'SELECT COUNT(*) n FROM `{table}` {where}',args)['n']
        items=db.rows(cur,f'SELECT * FROM `{table}` {where} ORDER BY id DESC LIMIT %s OFFSET %s',(*args,page_size,(page-1)*page_size))
    return dict(items=items,total=total,page=page,page_size=page_size)

async def read_upload(file):
    raw=await file.read(MAX_FILE+1)
    if len(raw)>MAX_FILE: fail('单个文件不能超过 32 MB，请按批次拆分。')
    if not raw: fail('文件为空。')
    return raw

def validate_archive(raw):
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries=archive.infolist()
            if len(entries)>10000 or sum(i.file_size for i in entries)>256*1024*1024:
                fail('工作簿展开后超过处理上限。')
    except BadZipFile: fail('文件不是有效的 Excel 工作簿。')

def cell_value(cell):
    if cell.value is None: return ''
    if isinstance(cell.value,(int,float)) and re.fullmatch(r'0+',str(cell.number_format or '')):
        return str(int(cell.value)).zfill(len(cell.number_format))
    if cell.data_type=='f': return ''  # Formula output is not source evidence.
    return str(cell.value).strip()

def parse_rows(raw, name, config=None):
    config=config or {}
    suffix=Path(name).suffix.lower()
    if suffix in ('.csv','.tsv'):
        try: decoded=raw.decode('utf-8-sig')
        except UnicodeDecodeError: decoded=raw.decode('gb18030')
        reader=csv.reader(StringIO(decoded),delimiter='\t' if suffix=='.tsv' else ',')
        columns=next(reader,[])
        if not columns or any(not str(c).strip() for c in columns) or len(set(columns))!=len(columns): fail('表头不能为空或重复。')
        rows=[]
        for i,values in enumerate(reader,2):
            if i>10002: fail('每批最多 10000 行。')
            if any(values): rows.append(dict(row_no=i,sheet_name='CSV',raw={**dict(zip(columns,values)),'_header_row':1,'_source_cells':{name:{'row':i,'column':get_column_letter(index),'formula':False} for index,name in enumerate(columns,1)}},images=[]))
        return columns,rows,['CSV']
    if suffix not in ('.xlsx','.xlsm'): fail('请使用 XLSX、XLSM、CSV 或 TSV 文件。')
    validate_archive(raw)
    book=load_workbook(BytesIO(raw),data_only=False,keep_links=False)
    all_columns=[]; rows=[]; sheets=[]
    try:
        selected_sheets=config.get('sheets') or ([config['sheet']] if config.get('sheet') else [])
        missing_sheets=[name for name in selected_sheets if name not in book.sheetnames]
        if missing_sheets:fail('保存的供应商方案所选工作表在当前文件中不存在，请明确选择新文件的工作表。',{'sheets':missing_sheets})
        for sheet in book.worksheets:
            if sheet.max_row>10002 or sheet.max_column>400: fail('工作表超过 10000 行或 400 列，请拆分。')
            chosen=config.get('sheet')
            if chosen and sheet.title!=chosen: continue
            if selected_sheets and sheet.title not in selected_sheets:continue
            if not chosen and (sheet.sheet_state!='visible' or re.search(r'说明|指南|帮助|instruction|definition|readme',sheet.title,re.I)): continue
            # Preserve exact sheet / row / column identities and embedded image ownership.
            candidates=list(sheet.iter_rows(min_row=1,max_row=min(30,sheet.max_row)))
            requested=int((config.get('sheet_headers') or {}).get(sheet.title) or config.get('header_row') or 0)
            aliases={a.casefold() for names in ALIASES.values() for a in names}
            def header_score(row):
                values=[cell_value(c) for c in row]
                matched=sum(v.casefold() in aliases for v in values if v)
                compact=sum(bool(v) and len(v)<=40 and '\n' not in v for v in values)
                return matched*100+compact
            if requested:
                if not 1<=requested<=sheet.max_row: fail('指定表头行超出工作表范围。')
                header=next(sheet.iter_rows(min_row=requested,max_row=requested))
            else:
                header=max(candidates,key=header_score,default=None)
                if header and header_score(header)<102: continue
            if not header: continue
            header_no=header[0].row
            names=[cell_value(c) or get_column_letter(c.column) for c in header]
            if len(set(names))!=len(names): names=[f'{v} [{get_column_letter(i)}]' for i,v in enumerate(names,1)]
            merged={}
            for area in sheet.merged_cells.ranges:
                for row_index in range(max(header_no+1,area.min_row),area.max_row+1):
                    for column_index in range(area.min_col,area.max_col+1):
                        merged[(row_index,column_index)]=(area.min_row,area.min_col)
            images={}
            for image in sheet._images:
                anchor=getattr(image.anchor,'_from',None)
                if anchor is None: continue
                content=image._data()
                image_asset=domain.store_asset(content,f'{Path(name).stem}-{sheet.title}-{anchor.row+1}.{image.format}',kind='original')
                images.setdefault(anchor.row+1,[]).append({'asset_id':image_asset['id'],'column':get_column_letter(anchor.col+1)})
            sheets.append(sheet.title)
            all_columns.extend(c for c in names if c not in all_columns)
            data_row=int(config.get('data_row') or header_no+1)
            if data_row<=header_no: fail('数据起始行必须位于表头之后。')
            for cells in sheet.iter_rows(min_row=data_row):
                values=[]; source_cells={}
                for i,c in enumerate(cells[:len(names)],1):
                    anchor=merged.get((c.row,i),(c.row,i))
                    source_cell=sheet.cell(*anchor)
                    values.append(cell_value(source_cell))
                    source_cells[names[i-1]]={'row':anchor[0],'column':get_column_letter(anchor[1]),'formula':source_cell.data_type=='f'}
                if not any(values) and cells[0].row not in images: continue
                record_index=next((i for i,v in enumerate(names) if v in ('记录类型','记录类别')),None)
                if record_index is not None and values[record_index] not in ('正式商品','商品','产品','SKU'): continue
                rows.append(dict(row_no=cells[0].row,sheet_name=sheet.title,raw={**dict(zip(names,values)),'_source_cells':source_cells,'_header_row':header_no},images=images.get(cells[0].row,[])))
                if len(rows)>10000: fail('每批最多 10000 行。')
    finally: book.close()
    if not rows: fail('未找到可导入的数据行。')
    return all_columns,rows,sheets

ALIASES={'product_name':['商品名称','产品名称','品名','产品','name','title'], 'sku_code':['供应商sku','商品编码','SKU','sku','货号'],
         'label':['规格','规格名称','变体','variant'], 'material':['材质','面料','material','材料组成'],'color':['颜色','color'],
         'size':['尺寸','尺码','size','尺寸说明'],'weight_g':['重量(g)','重量（g）','重量(克)','重量（克）','商品总重(g)'],
         'weight':['重量','weight'],'cost_price':['供货价','采购价','采购成本','cost price'],
         'cost_currency':['供货币种','采购币种','cost currency'],
         'price':['已确认销售价','已确认售价','confirmed sale price'],'suggested_price':[], 'stock':['库存','stock'],
         'brand':['品牌','brand'],'supplier_category':['类别','供应商品类'],'material_composition':[],'fabric':['面料成分'],'filling':['填充物成分'],
         'filling_weight_g':['填充重量(g)'],'length_cm':['商品长度(cm)'],'width_cm':['商品宽度(cm)'],'height_cm':['商品高度(cm)'],
         'natural_height_cm':['自然高度(cm)'],'compressed_height_cm':['受压高度(cm)'],'quantity':['销售件数'],
         'contents':['套装组成'],'currency':['售价币种','销售币种','sale currency'],'unit':['计价单位'],'origin_country':['原产国'],
         'origin_place':['详细产地'],'package_size':['包装尺寸'],'package_weight_g':['包装重量(g)'],
         'barcode':['GTIN/UPC/EAN'],'source_asin':['ASIN'],'manufacturer':['制造商'],'features':['产品特点与用途'],
         'source_notes':['备注补充说明','备注','补充说明']}

def import_structure(rows):
    sheets={}
    for row in rows:
        name=row['sheet_name']
        if name in sheets:continue
        cells=row['raw'].get('_source_cells',{})
        sheets[name]={'sheet':name,'header_row':row['raw'].get('_header_row'),
            'columns':{key:cells.get(key,{}).get('column') for key in row['raw'] if not key.startswith('_')}}
    return list(sheets.values())

def validate_import_mapping(mapping,columns):
    if not isinstance(mapping,dict) or any(key not in ALIASES or not isinstance(value,str) for key,value in mapping.items()):fail('供应商字段映射格式无效。')
    missing={key:value for key,value in mapping.items() if value and value not in columns}
    if missing:fail('映射列在当前文件中不存在，请按当前表头重新选择。',missing)
    return {key:mapping.get(key,'') for key in ALIASES}

def import_category(cur,value):
    if value in (None,''):return None
    try:
        if isinstance(value,bool) or int(value)<1 or str(int(value))!=str(value):raise ValueError()
        category_id=int(value)
    except (ValueError,TypeError):fail('请选择有效产品品类。')
    category=required(cur,'directories',category_id)
    if category['kind']!='categories' or not category['enabled']:fail('请选择已启用的产品品类。')
    return category_id

def import_category_selection(cur,batch):
    if 'category_id' in batch['summary']:
        return batch['summary']['category_id'],batch['summary'].get('category_source','batch')
    categories=db.rows(cur,'SELECT DISTINCT p.category_id FROM import_rows r JOIN products p ON p.id=r.product_id WHERE r.import_id=%s LIMIT 2',(batch['id'],))
    return (categories[0]['category_id'],'linked_products') if len(categories)==1 else (None,'unselected')

def recover_import_structure(cur,batch):
    """Recover only metadata from the preserved bytes; never reparses/writes rows or images."""
    asset=domain.get_asset(batch['source_asset_id']);raw=Path(asset['path']).read_bytes()
    if sha256(raw).hexdigest()!=asset['sha256']:fail('保留的供应商原文件哈希不一致，不能推断表头身份。')
    samples=db.rows(cur,'SELECT r.* FROM import_rows r JOIN (SELECT MIN(id) id FROM import_rows WHERE import_id=%s GROUP BY sheet_name) first_rows ON first_rows.id=r.id',(batch['id'],))
    if not samples:fail('批次没有可核对的来源行。')
    suffix=Path(batch['name']).suffix.lower();structure=[]
    if suffix in {'.csv','.tsv'}:
        try:decoded=raw.decode('utf-8-sig')
        except UnicodeDecodeError:decoded=raw.decode('gb18030')
        names=next(csv.reader(StringIO(decoded),delimiter='\t' if suffix=='.tsv' else ','),[])
        expected={key for key in samples[0]['raw'] if not key.startswith('_')}
        if set(names)!=expected or len(set(names))!=len(names):fail('原文件表头与批次保留的列身份不一致。')
        structure=[{'sheet':'CSV','header_row':1,'columns':{name:get_column_letter(index) for index,name in enumerate(names,1)}}]
    else:
        validate_archive(raw);book=load_workbook(BytesIO(raw),read_only=True,data_only=False,keep_links=False)
        try:
            for sample in samples:
                name=sample['sheet_name']
                if name not in book.sheetnames:fail('批次来源工作表在保留原文件中不存在。',{'sheet':name})
                sheet=book[name];expected={key for key in sample['raw'] if not key.startswith('_')}
                requested=(batch['config'].get('sheet_headers') or {}).get(name) or batch['config'].get('header_row') or sample['raw'].get('_header_row')
                matches=[]
                first=int(requested) if requested else 1;last=first if requested else min(30,sample['row_no']-1)
                for number in range(first,last+1):
                    cells=next(sheet.iter_rows(min_row=number,max_row=number));names=[cell_value(cell) or get_column_letter(index) for index,cell in enumerate(cells,1)]
                    if len(set(names))!=len(names):names=[f'{value} [{get_column_letter(index)}]' for index,value in enumerate(names,1)]
                    if set(names)==expected:matches.append({'sheet':name,'header_row':number,'columns':{header:get_column_letter(index) for index,header in enumerate(names,1)}})
                if len(matches)!=1:fail('无法从保留原文件唯一核对历史批次表头，请核对实际工作表与表头行。',{'sheet':name,'matching_header_rows':[item['header_row'] for item in matches]})
                structure.append(matches[0])
        finally:book.close()
    return structure,{'method':'read_only_exact_header_match','source_asset_id':asset['id'],'sha256':asset['sha256']}

def prepared_import_mapping(supplier,columns,rows):
    plan=supplier['config'].get('import_mapping')
    structure=import_structure(rows)
    if not isinstance(plan,dict) or plan.get('version')!=1:
        return {field:next((c for c in columns if c.casefold() in [a.casefold() for a in aliases]),'') for field,aliases in ALIASES.items()}, {'mapping_source':'automatic','mapping_issues':[],'column_identity':structure}
    mapping={field:str(plan.get('mapping',{}).get(field) or '') for field in ALIASES};issues=[]
    actual={sheet['sheet']:sheet for sheet in structure}
    for field,column in mapping.items():
        if not column:continue
        bindings=plan.get('bindings',{}).get(field,[])
        mismatch=[]
        for expected in bindings:
            sheet=actual.get(expected['sheet'])
            if not sheet or column not in sheet['columns'] or expected.get('header_row')!=sheet.get('header_row'):
                mismatch.append(expected)
        if column not in columns or mismatch:
            mapping[field]=''
            issues.append({'field':field,'message':f'供应商默认映射的列“{column}”或工作表/表头行在本文件中缺失，未自动替换为其他列。','expected':mismatch or bindings})
    return mapping,{'mapping_source':'supplier_default','mapping_default_revision':supplier['revision'],
        'mapping_default_snapshot':deepcopy(plan),'mapping_issues':issues,'column_identity':structure,
        'category_id':plan.get('category_id'),'category_source':'supplier_default'}

@router.get('/imports')
def list_imports(page:int=1,page_size:int=24): return page_rows('imports',page=page,page_size=page_size)

@router.post('/imports')
async def upload_import(file:UploadFile=File(...),supplier_id:int=Form(...),parse_config:str=Form('{}')):
    raw=await read_upload(file)
    with db.transaction() as cur:
        supplier=required(cur,'directories',supplier_id)
        if supplier['kind']!='suppliers' or not supplier['enabled']: fail('请选择已启用的供应商。')
    source=domain.store_asset(raw,file.filename or 'source.xlsx',kind='source')
    with db.transaction() as cur:
        previous=db.one(cur,'SELECT * FROM imports WHERE supplier_id=%s AND source_asset_id=%s ORDER BY id DESC LIMIT 1',(supplier_id,source['id']))
        if previous: return previous
    try: parse_options=json.loads(parse_config)
    except ValueError: fail('解析选区配置不是有效 JSON。')
    if not isinstance(parse_options,dict): fail('解析选区配置必须为对象。')
    saved=supplier['config'].get('import_mapping')
    if not parse_options and isinstance(saved,dict) and saved.get('version')==1:parse_options=deepcopy(saved.get('parse_config',{}))
    columns,rows,sheets=parse_rows(raw,file.filename or 'source.xlsx',parse_options)
    mapping,selection=prepared_import_mapping(supplier,columns,rows)
    with db.transaction() as cur:
        batch_id=db.insert(cur,'imports',dict(name=file.filename or '供应商文件',supplier_id=supplier_id,source_asset_id=source['id'],status='parsed',config=parse_options,mapping=mapping,summary={'row_count':len(rows),'columns':columns,'sheets':sheets,**selection}))
        for row in rows: db.insert(cur,'import_rows',dict(import_id=batch_id,**row,issues=[],status='pending'))
    return {'id':batch_id,'status':'parsed'}

@router.get('/imports/{batch_id}')
def import_detail(batch_id:int,page:int=1,page_size:int=24):
    with db.transaction() as cur:
        item=required(cur,'imports',batch_id);supplier=required(cur,'directories',item['supplier_id'])
        item['category_id'],item['category_source']=import_category_selection(cur,item)
        item['product_name']=item['summary'].get('product_name','')
    item['supplier']={key:supplier[key] for key in ('id','name','revision')}
    item.update(mapping_source=item['summary'].get('mapping_source','automatic'),mapping_default_revision=item['summary'].get('mapping_default_revision'),mapping_issues=item['summary'].get('mapping_issues',[]))
    paged=page_rows('import_rows','WHERE import_id=%s',(batch_id,),page,page_size)
    item.update(columns=item['summary'].get('columns',[]),sheets=item['summary'].get('sheets',[]),rows=paged['items'],pagination={k:v for k,v in paged.items() if k!='items'})
    return item

@router.post('/imports/{batch_id}/save-mapping-default')
def save_import_mapping_default(batch_id:int,body:dict):
    with db.transaction() as cur:
        batch=domain.get(cur,'imports',batch_id,True);domain.revision(batch,body)
        supplier=domain.get(cur,'directories',batch['supplier_id'],True)
        if supplier['revision']!=body.get('supplier_revision'):fail('供应商方案已变化，请刷新后比较映射。',status=409)
        if supplier['kind']!='suppliers' or not supplier['enabled']:fail('供应商已停用，不能保存新默认映射。')
        mapping=validate_import_mapping(body.get('mapping',batch['mapping']),batch['summary'].get('columns',[]))
        structure=batch['summary'].get('column_identity')
        recovered=None
        if not structure:structure,recovered=recover_import_structure(cur,batch)
        selected,_=import_category_selection(cur,batch)
        category_id=import_category(cur,body.get('category_id',selected))
        bindings={field:[{'sheet':sheet['sheet'],'header_row':sheet['header_row'],'header':column,'column':sheet['columns'][column]} for sheet in structure if column in sheet['columns']] for field,column in mapping.items() if column}
        if any(identity['header_row'] is None for entries in bindings.values() for identity in entries):fail('表头行身份缺失，不能保存默认方案。')
        parse_config={**batch['config'],'sheets':[sheet['sheet'] for sheet in structure],'sheet_headers':{sheet['sheet']:sheet['header_row'] for sheet in structure}}
        plan={'version':1,'mapping':mapping,'bindings':bindings,'parse_config':parse_config,'source_import_id':batch_id,'source_asset_id':batch['source_asset_id'],'category_id':category_id}
        config={**supplier['config'],'import_mapping':plan}
        if supplier['config']!=config:
            db.record_configuration(cur,'suppliers',supplier['id'],supplier['revision'],supplier)
            db.update(cur,'directories',supplier['id'],{'config':config,'revision':supplier['revision']+1})
            supplier=required(cur,'directories',supplier['id']);db.record_configuration(cur,'suppliers',supplier['id'],supplier['revision'],supplier)
        summary={**batch['summary'],'mapping_source':'supplier_default','mapping_default_revision':supplier['revision'],'mapping_default_snapshot':deepcopy(plan),'mapping_issues':[],
            'column_identity':structure,'category_id':category_id,'category_source':'batch'}
        if recovered:summary['column_identity_recovery']=recovered
        if batch['mapping']!=mapping or batch['summary']!=summary:db.update(cur,'imports',batch_id,{'mapping':mapping,'summary':summary,'revision':batch['revision']+1})
    return import_detail(batch_id)

@router.post('/imports/{batch_id}/reparse')
def reparse_import(batch_id:int,body:dict):
    with db.transaction() as cur:
        batch=required(cur,'imports',batch_id)
        if db.one(cur,"SELECT id FROM import_rows WHERE import_id=%s AND (sku_id IS NOT NULL OR status='applied') LIMIT 1",(batch_id,)):
            fail('该批次已有商品关联，不能覆盖来源行；请保留本批次并上传新批次。')
    config={k:body[k] for k in ('sheet','header_row','data_row') if k in body}
    columns,rows,sheets=parse_rows(asset_bytes(batch['source_asset_id']),batch['name'],config)
    with db.transaction() as cur:
        current=required(cur,'imports',batch_id)
        if current['revision']!=body.get('revision'): fail('批次已变化，请重新读取。',status=409)
        if db.one(cur,'SELECT id FROM import_rows WHERE import_id=%s AND sku_id IS NOT NULL LIMIT 1',(batch_id,)):
            fail('解析期间批次已关联商品，请重新读取。',status=409)
        mapping,selection=prepared_import_mapping(required(cur,'directories',current['supplier_id']),columns,rows)
        cur.execute('DELETE FROM import_rows WHERE import_id=%s',(batch_id,))
        for row in rows: db.insert(cur,'import_rows',dict(import_id=batch_id,**row,issues=[],status='pending'))
        db.update(cur,'imports',batch_id,{'config':config,'mapping':mapping,'summary':{'row_count':len(rows),'columns':columns,'sheets':sheets,**selection},'status':'parsed','revision':current['revision']+1})
    return import_detail(batch_id)

@router.post('/imports/{batch_id}/apply')
def apply_import(batch_id:int,body:dict):
    mapping=body.get('mapping') or {}; decisions=body.get('decisions') or {}; category_id=body.get('category_id')
    if not category_id: fail('请选择产品品类。')
    with db.transaction() as cur:
        batch=required(cur,'imports',batch_id)
        if 'revision' in body:domain.revision(batch,body)
        category_id=import_category(cur,category_id)
        mapping=validate_import_mapping(mapping,batch['summary'].get('columns',[]))
        ignored=body.get('ignore_missing_fields',[])
        if not isinstance(ignored,list) or any(key not in ALIASES for key in ignored):fail('忽略缺失字段格式无效。')
        unresolved=[item for item in batch['summary'].get('mapping_issues',[]) if not mapping.get(item['field']) and item['field'] not in ignored]
        if unresolved:fail('默认映射在本文件有缺列，请重新映射或明确勾选本批忽略后再导入。',{'mapping_issues':unresolved})
    if re.search(r'供货|采购|成本|cost',str(mapping.get('price','')),re.I):
        fail('供货价或采购成本必须映射为供货成本，不能作为已确认销售价。',{'price':'请改用 cost_price 映射'})
    if re.search(r'供货|采购|成本|cost',str(mapping.get('currency','')),re.I):
        fail('供货币种必须映射为供货币种，不能作为销售币种。',{'currency':'请改用 cost_currency 映射'})
    counts={'created':0,'updated':0,'unchanged':0,'conflicts':0,'failed':0}
    after=0
    while True:
        with db.transaction() as cur:
            batch=required(cur,'imports',batch_id)
            category=required(cur,'directories',int(category_id))
            if category['kind']!='categories': fail('品类无效。')
            pending=db.rows(cur,'SELECT * FROM import_rows WHERE import_id=%s AND id>%s ORDER BY id LIMIT 100',(batch_id,after))
        if not pending: break
        for row in pending:
            after=row['id']
            value=lambda key: str(row['raw'].get(mapping.get(key,''),'')).strip()
            code=value('sku_code'); name=value('product_name') or str(body.get('product_name') or '').strip()
            issues=[]
            if not name: issues.append({'field':'product_name','message':'缺少商品名称'})
            if len(code)>160: issues.append({'field':'sku_code','message':'供应商货号超过 160 字符'})
            if len(name)>240: issues.append({'field':'product_name','message':'商品名称超过 240 字符'})
            if len(value('label'))>240: issues.append({'field':'label','message':'规格名称超过 240 字符'})
            facts={k:value(k) for k in mapping if k not in ('product_name','sku_code','label') and value(k)}
            price_suggested=bool('price' in facts and re.search(r'建议|参考|suggest|recommend',str(mapping.get('price','')),re.I))
            if 'price' in facts: facts['price_provenance_status']='suggested' if price_suggested else 'source_sales_price'
            numeric_fields=('weight_g','cost_price','price','suggested_price','stock','length_cm','width_cm','height_cm','natural_height_cm','compressed_height_cm','filling_weight_g','quantity','package_weight_g')
            for key in numeric_fields:
                if key in facts:
                    try:
                        number=float(facts[key])
                        if not math.isfinite(number) or number<0 or (key in ('stock','quantity') and number!=int(number)): raise ValueError
                        facts[key]=int(number) if key in ('stock','quantity') else number
                    except ValueError:
                        # Do not strip an approximation/range or infer units to
                        # manufacture a scalar fact. Keep that source in raw.
                        issues.append({'field':key,'message':'该有单位数值列需要明确非负数字；近似值或范围请映射到尺寸/重量原文，不应自动取均值。'})
            with db.transaction() as cur:
                source_identity=sha256(f'{batch["source_asset_id"]}:{row["sheet_name"]}:{row["row_no"]}'.encode()).hexdigest()[:24]
                internal=f'S{batch["supplier_id"]}-{code}' if code else f'S{batch["supplier_id"]}-SRC-{source_identity}'
                if len(internal)>120: internal=f'S{batch["supplier_id"]}-'+sha256(code.encode()).hexdigest()[:24]
                existing=db.one(cur,'SELECT * FROM skus WHERE supplier_id=%s AND supplier_code=%s FOR UPDATE',(batch['supplier_id'],code)) if code else db.one(cur,'SELECT * FROM skus WHERE code=%s FOR UPDATE',(internal,))
                original=(row['images'][0]['asset_id'] if len(row['images'])==1 else None)
                if len(row['images'])>1: issues.append({'field':'image','message':'当前行有多张图，需要在商品档案明确原图归属'})
                if issues and any(i['field']!='image' for i in issues):
                    db.update(cur,'import_rows',row['id'],{'issues':issues,'status':'failed'}); counts['failed']+=1; continue
                provenance={k:{'import_id':batch_id,'asset_id':batch['source_asset_id'],'sheet':row['sheet_name'],
                    'row':row['raw'].get('_source_cells',{}).get(mapping.get(k),{}).get('row',row['row_no']),
                    'column':row['raw'].get('_source_cells',{}).get(mapping.get(k),{}).get('column',mapping.get(k)),
                    'header':mapping.get(k),'raw':row['raw'].get(mapping.get(k))} for k in dict.fromkeys([*[k for k in facts if k!='price_provenance_status'],*[k for k in ('product_name','sku_code','label') if mapping.get(k)]])}
                provenance['identity']={'kind':'supplier_code' if code else 'internal_source_identity','supplier_code':code or None,
                    'internal_code':internal,'import_id':batch_id,'sheet':row['sheet_name'],'row':row['row_no'],
                    'note':'供应商未提供货号；此编号仅为系统内部来源身份。' if not code else '使用供应商提供的货号匹配。'}
                if 'price' in facts:
                    provenance['price']['meaning']='suggested_price' if price_suggested else 'source_sales_price'
                    provenance['price']['listing_confirmation_required']=True
                if 'cost_price' in facts: provenance['cost_price']['meaning']='supplier_cost'
                if not code: issues.append({'field':'sku_code','message':'供应商货号缺失，已使用内部来源身份，不是供应商货号。','severity':'info'})
                if existing:
                    changed={k:v for k,v in facts.items() if existing['facts'].get(k)!=v}
                    if original and existing['original_asset_id']!=original: changed['original_asset_id']=original
                    incoming_label=value('label') or code or name
                    if existing['label']!=incoming_label: changed['label']=incoming_label
                    existing_product=required(cur,'products',existing['product_id'])
                    if existing_product['name']!=name:
                        db.update(cur,'import_rows',row['id'],{'status':'conflict','issues':[{'field':'product_name','message':'相同供应商货号对应不同商品名称，请在商品档案核对身份后重试','current':existing_product['name'],'incoming':name}], 'sku_id':existing['id'],'product_id':existing['product_id']})
                        counts['conflicts']+=1; continue
                    decision=decisions.get(str(row['id']))
                    if changed and decision not in ('keep','update'):
                        db.update(cur,'import_rows',row['id'],{'status':'conflict','issues':[{'field':k,'message':'来源值发生变化，选择保留现值或接受此行更新','current':existing['facts'].get(k),'incoming':v} for k,v in changed.items()], 'sku_id':existing['id'],'product_id':existing['product_id']})
                        counts['conflicts']+=1; continue
                    if changed and decision=='update':
                        db.update(cur,'skus',existing['id'],{'label':incoming_label,'facts':{**existing['facts'],**facts},'provenance':{**existing['provenance'],**provenance},'original_asset_id':original or existing['original_asset_id'],'revision':existing['revision']+1})
                        counts['updated']+=1
                    else: counts['unchanged']+=1
                    pid=existing['product_id']; sid=existing['id']
                else:
                    if not code:
                        candidates=db.rows(cur,'SELECT s.id,s.code,s.label FROM skus s JOIN products p ON p.id=s.product_id WHERE s.supplier_id=%s AND p.name=%s AND s.label=%s LIMIT 20',(batch['supplier_id'],name,value('label') or name))
                        decision=decisions.get(str(row['id']))
                        if candidates and decision!='create_new':
                            db.update(cur,'import_rows',row['id'],{'status':'conflict','issues':[{'field':'identity','message':'新文件中有无货号的同名同规格候选。确认是新规格可选择 create_new；若为旧规格，请先补充真实供应商货号再关联。','candidates':candidates}]})
                            counts['conflicts']+=1; continue
                    product=db.one(cur,"SELECT p.id FROM products p JOIN skus s ON s.product_id=p.id WHERE p.name=%s AND p.category_id=%s AND s.supplier_id=%s ORDER BY p.id LIMIT 1",(name,category_id,batch['supplier_id']))
                    pid=product['id'] if product else db.insert(cur,'products',{'name':name,'category_id':int(category_id),'origin':'supplier','description':{}})
                    sid=db.insert(cur,'skus',dict(product_id=pid,code=internal,label=value('label') or code or name,facts=facts,components=[],original_asset_id=original,provenance=provenance,supplier_id=batch['supplier_id'],supplier_code=code or None))
                    counts['created']+=1
                db.update(cur,'import_rows',row['id'],{'status':'applied','issues':issues,'sku_id':sid,'product_id':pid})
    with db.transaction() as cur:
        summary={**batch['summary'],**counts,'mapping_issues':[],'ignored_missing_fields':ignored,'category_id':category_id,'category_source':'batch','product_name':str(body.get('product_name') or '')}
        if mapping!=batch['mapping'] or ignored:summary['mapping_source']='batch_override'
        db.update(cur,'imports',batch_id,{'mapping':mapping,'summary':summary,'status':'needs_attention' if counts['conflicts'] or counts['failed'] else 'applied','revision':batch.get('revision',1)+1})
    return import_detail(batch_id)

def inspect_template(raw,name):
    if Path(name).suffix.lower() not in ('.xlsx','.xlsm'): fail('原生模板当前支持 XLSX 与 XLSM。其他文件结构需要独立适配。')
    validate_archive(raw)
    book=load_workbook(BytesIO(raw),read_only=False,keep_links=False,data_only=True)
    try:
        sheet=book['Template'] if 'Template' in book.sheetnames else book.worksheets[0]
        candidates=list(sheet.iter_rows(min_row=1,max_row=min(12,sheet.max_row),values_only=True))
        if not candidates: fail('原生模板没有可读取的表头。')
        head=max(range(len(candidates)),key=lambda i:sum(v is not None for v in candidates[i]))+1
        data_row=head+1
        metadata=' '.join(str(v) for r in candidates[:3] for v in r if v is not None)
        match=re.search(r'(?:dataRow|data_row)["\']?\s*[=:]\s*["\']?(\d+)',metadata,re.I)
        if match:
            data_row=int(match.group(1))
            if data_row<=1: fail('模板 metadata 的数据起始行无效。')
            machine=next((i+1 for i,r in enumerate(candidates[:data_row-1]) if any(re.match(r'^(?:item_sku|seller_sku|contribution_sku)(?:$|[#.\[])',str(v).strip(),re.I) for v in r if v is not None)),None)
            if machine: head=machine
            elif head>=data_row: head=data_row-1
        # Amazon's current workbook separates human names (row 4) from exact,
        # marketplace-specific machine identifiers (row 5). Keep both identities.
        display=candidates[head-2] if head>1 and any('#' in str(v) for v in candidates[head-1] if v) else candidates[head-1]
        columns=[{'column':get_column_letter(i),'label':str(display[i-1] or v),'identifier':str(v)} for i,v in enumerate(candidates[head-1],1) if v is not None]
        patterns={'sku':['seller sku','sku','item_sku','seller_sku'],'title':['product name','item name','item_name','产品名称'],'brand':['brand name','brand_name'],
                  'description':['product description','product_description'],'price':['your price','standard_price'],'stock':['quantity','inventory'],
                  'image_url':['main image url','main_image_url'],'parent_sku':['parent sku','parent_sku'],'variation':['variation theme','variation_theme']}
        mapping={k:next((c['column'] for c in columns if c['label'].strip().casefold() in pats),None) for k,pats in patterns.items()}
        mapping={k:v for k,v in mapping.items() if v}
        machine_patterns={'sku':r'^contribution_sku#1\.value$', 'title':r'^item_name(?:\[.*\])?#1\.value$',
            'brand':r'^brand(?:\[.*\])?#1\.value$', 'description':r'^product_description(?:\[.*\])?#1\.value$',
            'bullet_1':r'^bullet_point(?:\[.*\])?#1\.value$', 'product_id_type':r'^amzn1\.volt\.ca\.product_id_type$',
            'fabric':r'^fabric_type(?:\[.*\])?#1\.value$', 'origin_country':r'^country_of_origin(?:\[.*\])?#1\.value$',
            'dangerous_goods_regulation':r'^supplier_declared_dg_hz_regulation(?:\[.*\])?#1\.value$',
            'price':r'^purchasable_offer(?:\[.*\])?#1\.our_price#1\.schedule#1\.value_with_tax$',
            'stock':r'^fulfillment_availability#1\.quantity$', 'image_url':r'^main_product_image_locator(?:\[.*\])?#1\.media_location$'}
        machine_patterns.update({'product_type':r'^product_type#1\.value$','listing_action':r'^::record_action$',
            'parentage':r'^parentage_level(?:\[.*\])?#1\.value$','parent_sku':r'^child_parent_sku_relationship(?:\[.*\])?#1\.parent_sku$',
            'variation':r'^variation_theme#1\.name$','product_id':r'^amzn1\.volt\.ca\.product_id_value$',
            'fulfillment_channel':r'^fulfillment_availability#1\.fulfillment_channel_code$',
            'handling_days':r'^fulfillment_availability#1\.lead_time_to_ship_max_days$',
            'condition':r'^condition_type(?:\[.*\])?#1\.value$','item_type_keyword':r'^item_type_keyword(?:\[.*\])?#1\.value$',
            'manufacturer':r'^manufacturer(?:\[.*\])?#1\.value$','model_number':r'^model_number(?:\[.*\])?#1\.value$',
            'model_name':r'^model_name(?:\[.*\])?#1\.value$','search_terms':r'^generic_keyword(?:\[.*\])?#1\.value$',
            'color':r'^color(?:\[.*\])?#1\.value$','size':r'^size(?:\[.*\])?#1\.value$',
            'quantity':r'^number_of_items(?:\[.*\])?#1\.value$',
            'item_length':r'^item_length_width(?:\[.*\])?#1\.length\.value$',
            'length_unit':r'^item_length_width(?:\[.*\])?#1\.length\.unit$',
            'item_width':r'^item_length_width(?:\[.*\])?#1\.width\.value$',
            'width_unit':r'^item_length_width(?:\[.*\])?#1\.width\.unit$',
            'item_weight':r'^item_weight(?:\[.*\])?#1\.value$', 'weight_unit':r'^item_weight(?:\[.*\])?#1\.unit$',
            'package_length':r'^item_package_dimensions(?:\[.*\])?#1\.length\.value$',
            'package_length_unit':r'^item_package_dimensions(?:\[.*\])?#1\.length\.unit$',
            'package_width':r'^item_package_dimensions(?:\[.*\])?#1\.width\.value$',
            'package_width_unit':r'^item_package_dimensions(?:\[.*\])?#1\.width\.unit$',
            'package_height':r'^item_package_dimensions(?:\[.*\])?#1\.height\.value$',
            'package_height_unit':r'^item_package_dimensions(?:\[.*\])?#1\.height\.unit$',
            'package_weight':r'^item_package_weight(?:\[.*\])?#1\.value$',
            'package_weight_unit':r'^item_package_weight(?:\[.*\])?#1\.unit$'})
        for index in range(2,6): machine_patterns['bullet_'+str(index)]=rf'^bullet_point(?:\[.*\])?#{index}\.value$'
        for index in range(1,10): machine_patterns['image_'+str(index+1)]=rf'^other_product_image_locator_{index}(?:\[.*\])?#1\.media_location$'
        for field,pattern in machine_patterns.items():
            match=next((c['column'] for c in columns if re.fullmatch(pattern,c['identifier'],re.I)),None)
            if match: mapping[field]=match
        official=[]
        for definitions in book.worksheets:
            if definitions is sheet or not re.search(r'definition|数据定义|字段说明',definitions.title,re.I): continue
            records=list(definitions.iter_rows(max_row=min(definitions.max_row,1000),values_only=True))
            for index,record in enumerate(records[:15]):
                labels=[str(v or '').strip().casefold() for v in record]
                required_index=next((j for j,v in enumerate(labels) if v in ('required','required?','requirement','必填','是否必填')),None)
                field_index=next((j for j,v in enumerate(labels) if v in ('field name','attribute name','字段名称','字段名')),None)
                if required_index is None or field_index is None: continue
                for values in records[index+1:]:
                    if max(required_index,field_index)>=len(values): continue
                    if str(values[required_index] or '').strip().casefold() not in ('required','yes','必填','是'): continue
                    identifier=str(values[field_index] or '').strip()
                    column=next((c['column'] for c in columns if c['identifier'].strip().casefold()==identifier.casefold() or c['label'].strip().casefold()==identifier.casefold()),None)
                    official.append({'identifier':identifier,'column':column,'source_sheet':definitions.title})
                break
        rules=native_rules.extract(book,sheet,columns)
        # Conditional fields need an editable mapping as soon as a condition
        # becomes applicable. Preserve exact original columns and labels.
        for rule in rules.get('rules',[]):
            if rule.get('required_formula') and rule['column'] not in mapping.values():mapping['native_'+rule['column']]=rule['column']
        return {'sheet':sheet.title,'header_row':head,'data_row':data_row,'columns':columns,'sheets':book.sheetnames,'mapping':mapping,'native_rules':rules,
                'required':['sku','title','price','image_url'],'official_required':official,
                'rule_coverage':rules.get('coverage') or ('explicit_definition_required_only' if official else 'official_required_not_discovered')}
    finally: book.close()

@router.get('/templates')
def templates(target_id:int|None=None,page:int=1,page_size:int=24):
    return page_rows('templates','WHERE target_id=%s' if target_id else '',(target_id,) if target_id else (),page,page_size)

@router.post('/templates')
async def upload_template(file:UploadFile=File(...),target_id:int=Form(...),config:str=Form('{}')):
    raw=await read_upload(file)
    try: overrides=json.loads(config)
    except ValueError: fail('模板配置 JSON 无效。')
    with db.transaction() as cur: required(cur,'targets',target_id)
    info=inspect_template(raw,file.filename or 'template.xlsx')
    info.update({k:v for k,v in overrides.items() if k not in ('official_required','rule_coverage','native_rules')})
    validate_template_config(info)
    asset=domain.store_asset(raw,file.filename or 'template.xlsx',kind='template')
    with db.transaction() as cur:
        tid=db.insert(cur,'templates',{'target_id':target_id,'name':file.filename or '原生模板','asset_id':asset['id'],'config':info,'capability':'registered'})
        return required(cur,'templates',tid)

def validate_template_config(config,for_export=False):
    if not isinstance(config.get('mapping'),dict) or not config['mapping']: fail('模板尚未设置字段映射。')
    if not config.get('sheet') or int(config.get('data_row',0))<=int(config.get('header_row',0)): fail('数据行必须位于表头之后。')
    columns=list(config['mapping'].values())
    if len(columns)!=len(set(columns)) or any(not re.fullmatch('[A-Z]{1,3}',str(c)) for c in columns): fail('映射列必须是唯一的 Excel 列字母。')
    if any(column_index_from_string(c)>16384 for c in columns): fail('映射列越界。')
    declared={c['column'] for c in config.get('columns',[]) if isinstance(c,dict) and c.get('column')}
    if declared and any(c not in declared for c in columns):fail('映射列不在原生模板表头中，请选择真实存在的列。')
    if (config.get('native_rules') or {}).get('adapter')=='amazon_pillow_us_v1' and config.get('sheet')!='Template':
        fail('Amazon原生字段规则仅适用于Template工作表，不能写入其他工作表。')
    image_columns={c['column'] for c in config.get('columns',[]) if c.get('identifier','').startswith(('main_product_image_locator','other_product_image_locator'))}
    if any(column in image_columns and not re.fullmatch(r'(?:image_url|image_\d+|detail_image_\d+)',field) for field,column in config['mapping'].items()):
        fail('原生图片列必须映射到明确的资产图槽image_url/image_N/detail_image_N，不能用普通文本地址代替资产关联。')
    if for_export:
        missing=[f for f in config.get('required',[]) if f not in config['mapping']]
        if missing: fail('必填字段没有对应模板列，不能生成文件。',{'mapping':missing})
        for rule in config.get('official_required',[]):
            col=rule.get('column') if isinstance(rule,dict) else config['mapping'].get(rule,rule)
            if col not in columns: fail('原生模板的必填字段尚未映射。',{'official_required':rule})

@router.get('/templates/{template_id}')
def template_detail(template_id:int):
    with db.transaction() as cur: return required(cur,'templates',template_id)

@router.put('/templates/{template_id}')
def save_template(template_id:int,body:dict):
    with db.transaction() as cur:
        old=required(cur,'templates',template_id)
        if old['revision']!=body.get('revision'): fail('模板已变化，请重新读取。',status=409)
        config={**old['config'],**{k:v for k,v in body.get('config',{}).items() if k not in ('official_required','rule_coverage','native_rules')}}; validate_template_config(config)
        db.update(cur,'templates',template_id,{'config':config,'revision':old['revision']+1,'capability':'registered'})
        return required(cur,'templates',template_id)


@router.post('/templates/{template_id}/refresh-rules')
def refresh_template_rules(template_id:int,body:dict):
    with db.transaction() as cur:old=required(cur,'templates',template_id)
    rebuilt=inspect_template(asset_bytes(old['asset_id']),old['name'])
    mapping=dict(old['config'].get('mapping',{}))
    for field,column in rebuilt['mapping'].items():
        if field not in mapping and column not in mapping.values():mapping[field]=column
    config={**rebuilt,**old['config'],'mapping':mapping}
    for key in ('official_required','rule_coverage','native_rules','columns'):
        config[key]=rebuilt[key]
    if config.get('sheet')!=rebuilt['sheet'] or config.get('header_row')!=rebuilt['header_row']:
        fail('当前自定义工作表/表头与原生规则不一致，请先恢复正确选区。')
    validate_template_config(config)
    with db.transaction() as cur:
        current=required(cur,'templates',template_id)
        if current['revision']!=body.get('revision'):fail('模板已变化，请刷新后再读取规则。',status=409)
        db.update(cur,'templates',template_id,{'config':config,'revision':current['revision']+1,'capability':'registered'})
        return required(cur,'templates',template_id)

def native_workbook(raw,config,values):
    """Replace only the declared data region. Preserve VBA, all other ZIP members and template headers."""
    validate_template_config(config,for_export=True)
    with ZipFile(BytesIO(raw)) as archive:
        workbook=ET.fromstring(archive.read('xl/workbook.xml'))
        sheet=next((s for s in workbook.findall(f'{{{NS}}}sheets/{{{NS}}}sheet') if s.get('name')==config['sheet']),None)
        if sheet is None: fail('模板工作表不存在。')
        rels=ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        link=next((r.get('Target') for r in rels if r.get('Id')==sheet.get(f'{{{REL}}}id')),None)
        path=posixpath.normpath(link.lstrip('/') if link.startswith('/') else 'xl/'+link)
        original_sheet=archive.read(path)
        document=ET.fromstring(original_sheet); data=document.find(f'{{{NS}}}sheetData')
        if data is None: fail('模板缺少工作表数据区域。')
        first=int(config['data_row']); base=next((deepcopy(r) for r in data if int(r.get('r','0'))==first),None)
        data_end=int(config.get('data_end_row') or 1048576)
        if data_end<first or first+len(values)-1>data_end: fail('输出超出模板明确的数据区域。')
        for row in list(data):
            if first<=int(row.get('r','0'))<=data_end: data.remove(row)
        for offset,item in enumerate(values):
            number=first+offset; row=deepcopy(base) if base is not None else ET.Element(f'{{{NS}}}row')
            row.set('r',str(number))
            omitted=set()
            if item.get('parentage')=='Parent' and (config.get('native_rules') or {}).get('adapter')=='amazon_pillow_us_v1':
                from .families import parent_omitted_columns
                omitted=parent_omitted_columns(config)
            styles={re.sub(r'\d','',c.get('r','')):c.get('s') for c in (base if base is not None else [])}
            for cell in list(row):
                old_ref=cell.get('r',''); col=re.sub(r'\d','',old_ref)
                if col in omitted or col in config['mapping'].values(): row.remove(cell); continue
                cell.set('r',f'{col}{number}')
                formula=cell.find(f'{{{NS}}}f')
                has_literal=cell.find(f'{{{NS}}}v') is not None or cell.find(f'{{{NS}}}is') is not None
                if formula is None and has_literal and col not in config.get('preserve_columns',[]):
                    fail(f'模板数据首行 {col}{first} 有未映射的示例或固定值。请映射该列，或明确将其列入 preserve_columns 后再导出。')
                if formula is not None and formula.text:
                    if formula.get('t') in ('shared','array'): fail('模板数据区有共享/数组公式，需要专用模板适配。')
                    formula.text=Translator('='+formula.text,origin=old_ref).translate_formula(f'{col}{number}')[1:]
                    cached=cell.find(f'{{{NS}}}v')
                    if cached is not None: cell.remove(cached)
            for field,col in sorted(config['mapping'].items(),key=lambda x:column_index_from_string(x[1])):
                if col in omitted:continue
                value=item.get(field,'')
                if isinstance(value,(dict,list)): fail(f'字段 {field} 需要映射为原生模板的单个值，不能直接输出对象或列表。')
                cell=ET.SubElement(row,f'{{{NS}}}c',r=f'{col}{number}')
                if styles.get(col): cell.set('s',styles[col])
                if isinstance(value,(int,float)) and not isinstance(value,bool):
                    if not math.isfinite(value): fail('导出包含无效数字。')
                    ET.SubElement(cell,f'{{{NS}}}v').text=str(value)
                else:
                    text=str(value if value is not None else '')
                    if len(text)>32767: fail(f'字段 {field} 超过 Excel 单元格长度。')
                    cell.set('t','inlineStr'); inline=ET.SubElement(cell,f'{{{NS}}}is'); node=ET.SubElement(inline,f'{{{NS}}}t'); node.text=text
                    if text!=text.strip(): node.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
            row[:]=sorted(row,key=lambda cell:column_index_from_string(re.sub(r'\d','',cell.get('r','A'))))
            data.append(row)
        data[:]=sorted(data,key=lambda row:int(row.get('r','0')))
        # Preserve the original worksheet root and all namespace declarations.
        # Re-serializing its entire tree drops prefixes used in mc:Ignorable.
        replacement=ET.tostring(data,encoding='utf-8')
        pattern=rb'<(?:[A-Za-z_][\w.-]*:)?sheetData\b[^>]*(?:/>|>.*?</(?:[A-Za-z_][\w.-]*:)?sheetData\s*>)'
        rewritten,count=re.subn(pattern,lambda _:replacement,original_sheet,count=1,flags=re.S)
        if count!=1: fail('无法定位模板的唯一数据区域。')
        refs=[cell.get('r','A1') for row in data for cell in row if cell.get('r')]
        last_col=max((re.sub(r'\d','',r) for r in refs),key=column_index_from_string,default='A')
        last_row=max((int(re.search(r'\d+',r)[0]) for r in refs),default=1)
        rewritten=re.sub(rb'(<(?:[A-Za-z_][\w.-]*:)?dimension\b[^>]*\bref=")[^"]*(")',lambda m:m[1]+f'A1:{last_col}{last_row}'.encode()+m[2],rewritten,count=1)
        output=BytesIO()
        with ZipFile(output,'w') as result:
            for info in archive.infolist(): result.writestr(info,rewritten if info.filename==path else archive.read(info.filename))
        return output.getvalue()

def public_image(url,expected_hash):
    for _ in range(4):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443): fail('图片需要公开 HTTPS 地址。')
        try: addresses=socket.getaddrinfo(parsed.hostname,443)
        except OSError: fail('图片地址域名无法解析。')
        if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses): fail('平台图片地址不能指向本机或内部网络。')
        with httpx.stream('GET',url,follow_redirects=False,timeout=15) as response:
            if response.is_redirect:
                url=urljoin(url,response.headers.get('location','')); continue
            if not response.is_success: fail(f'图片地址返回 HTTP {response.status_code}。')
            content=bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content)>MAX_FILE: fail('平台图片超过 32 MB。')
        if sha256(content).hexdigest()!=expected_hash: fail('图片地址内容与选定设计资产不一致，请关联正确的图片地址。')
        return url
    fail('图片地址重定向过多。')

def export_image_slots(listing):
    images=listing.get('images') or []
    main=next((i for role in ('main','hero','sales-1','sales_1') for i in images if i.get('role')==role),None)
    def rank(image):
        role=str(image.get('role',''));match=re.search(r'(\d+)$',role)
        return (int(match[1]) if match else 1000,int(image.get('design_asset_id') or image.get('asset_id') or 0))
    gallery=sorted([i for i in images if i is not main and (re.match(r'^sales(?:[-_]\d+)?$',str(i.get('role',''))) or str(i.get('role','')).startswith(('gallery','scene')))],key=rank)
    details=sorted([i for i in images if str(i.get('role','')).startswith('detail')],key=rank)
    slots={'image_url':main,'image_1':main} if main else {}
    slots.update({'image_'+str(index):item for index,item in enumerate(gallery,2)})
    slots.update({'detail_image_'+str(index):item for index,item in enumerate(details,1)})
    return slots


def mapped_export_images(config,listing):
    slots=export_image_slots(listing);result=[];seen=set()
    for field in config.get('mapping',{}):
        image=slots.get(field)
        if image:
            identity=(image.get('design_asset_id'),image.get('asset_id'),image.get('url'))
            if identity not in seen:result.append(image);seen.add(identity)
    return result


def export_values(listing,sku):
    fields=listing['fields']; images=listing['images']
    values={**sku['facts'],**fields,**fields.get('attributes',{}),'sku':sku['code']}
    for key in list(values):
        if key.startswith('_') or re.fullmatch(r'(?:image_url|image_\d+|detail_image_\d+)',key):values.pop(key,None)
    # Commercial decisions are explicit per target listing. Neither supplier
    # cost, source suggested prices nor arbitrary attributes can supply them.
    values['price']=fields.get('price')
    values['stock']=fields.get('stock')
    for key in ('product_type','listing_action','parentage','product_id_type','product_id','fulfillment_channel','condition'):
        values[key]=fields.get(key,fields.get('attributes',{}).get(key,''))
    for dest,source,unit_key,factors in (
        ('item_length','length_cm','length_unit',{'Inches':1/2.54,'Centimeters':1,'Millimeters':10,'Meters':.01}),
        ('item_width','width_cm','width_unit',{'Inches':1/2.54,'Centimeters':1,'Millimeters':10,'Meters':.01}),
        ('item_weight','weight_g','weight_unit',{'Pounds':1/453.59237,'Ounces':1/28.349523125,'Grams':1,'Kilograms':.001}),
        ('package_weight','package_weight_g','package_weight_unit',{'Pounds':1/453.59237,'Ounces':1/28.349523125,'Grams':1,'Kilograms':.001})):
        if values.get(dest) in (None,'') and values.get(unit_key) in factors:
            original=sku['facts'].get(source)
            if isinstance(original,(int,float)) and not isinstance(original,bool) and math.isfinite(original):
                values[dest]=round(original*factors[values[unit_key]],6)
    values['description']=fields.get('description','')
    values['image_url']=''
    for field,item in export_image_slots(listing).items():values[field]=item.get('url','')
    for index,bullet in enumerate(fields.get('bullets',[]),1): values[f'bullet_{index}']=bullet
    return values


def resolve_listing_template(cur,listing,requested_id=None):
    membership=db.one(cur,'SELECT f.template_id FROM listing_families f JOIN family_members m ON m.family_id=f.id WHERE m.listing_id=%s',(listing['id'],))
    selected=listing.get('fields',{}).get('_template_id')
    if membership:
        if requested_id and int(requested_id)!=membership['template_id']:raise domain.Problem('该刊登按父子分组绑定模板，请在分组中明确更换后再校验')
        selected=membership['template_id']
    elif requested_id:
        if selected and int(selected)!=int(requested_id):raise domain.Problem('请先保存当前刊登的模板选择，再校验该模板','template_conflict',409)
        selected=int(requested_id)
    template=required(cur,'templates',int(selected)) if selected else db.one(cur,'SELECT * FROM templates WHERE target_id=%s ORDER BY id DESC LIMIT 1',(listing['target_id'],))
    if template and template['target_id']!=listing['target_id']:raise domain.Problem('刊登模板不属于当前目标店铺')
    return template


def listing_template_issues(cur,listing):
    family=db.one(cur,'SELECT f.* FROM listing_families f JOIN family_members m ON m.family_id=f.id WHERE m.listing_id=%s',(listing['id'],))
    template=resolve_listing_template(cur,listing)
    if not template:return []
    sku=required(cur,'skus',listing['sku_id']);target=required(cur,'targets',listing['target_id'])
    values=export_values(listing,sku);values['currency']=target['currency']
    if family:
        from .families import child_values
        values=child_values(family,values)
    return [{**item,'href':f"#/listings/{listing['id']}?field={item['field']}",'template_id':template['id']} for item in native_rules.issues(template['config'],values,target)]

@router.get('/exports')
def exports(page:int=1,page_size:int=24,target_id:int|None=None):
    return page_rows('exports','WHERE target_id=%s' if target_id else '',(target_id,) if target_id else (),page,page_size)

@router.post('/exports')
def create_export(body:dict):
    from . import families
    ids=list(dict.fromkeys(int(v) for v in body.get('listing_ids',[])))
    family_ids=list(dict.fromkeys(int(v) for v in body.get('family_ids',[])))
    if not (ids or family_ids) or len(ids)>1000 or len(family_ids)>500: fail('请选择刊登草稿或完整父子分组，每批最多1000个真实子SKU。')
    selection={'listing_ids':ids,'family_ids':family_ids}
    intent=str(body.get('intent_key') or '')
    if len(intent)>160: fail('导出请求标识超过长度上限。')
    with db.transaction() as cur:
        if intent:
            previous=db.one(cur,'SELECT * FROM exports WHERE intent_key=%s',(intent,))
            if previous:
                old_selection=previous['snapshot'].get('selection') or {'listing_ids':[row['listing']['id'] for row in previous['snapshot']['rows'] if row.get('listing')],'family_ids':[]}
                if old_selection!=selection or previous['template_id']!=int(body.get('template_id',0)) or previous['target_id']!=int(body.get('target_id',0)):
                    fail('此请求标识已用于另一导出操作，请重新创建导出。',status=409)
                return previous
        template=required(cur,'templates',int(body.get('template_id',0))); target=required(cur,'targets',int(body.get('target_id',0)))
        if template['target_id']!=target['id']: fail('模板与目标店铺不一致。')
        if not target['enabled']:fail('目标店铺已停用，不能生成新导出。')
        config=template['config']; validate_template_config(config,for_export=True)
        snapshots=[]; rows=[]; errors=[];planned=[];grouped_ids=set()
        for fid in family_ids:
            family=required(cur,'listing_families',fid)
            if family['target_id']!=target['id'] or family['template_id']!=template['id']:fail('分组与本次导出的店铺或模板不一致。',{'family_id':fid})
            children=families._children(cur,fid)
            if len(children)<2:fail('父子分组至少需要两个真实子刊登。',{'family_id':fid})
            problems=families._structural_issues(cur,family,children,config)
            if problems:fail('父子分组关系或变体属性已变化，请先修正。',problems)
            planned.append(('parent',family,None))
            for listing in children:
                grouped_ids.add(listing['id']);planned.append(('child',listing,family))
        for lid in ids:
            membership=db.one(cur,'SELECT family_id FROM family_members WHERE listing_id=%s',(lid,))
            if membership and membership['family_id'] not in family_ids:fail('此刊登属于父子分组，请选择完整分组导出，不能只导出孤立子SKU。',{'listing_id':lid,'family_id':membership['family_id']})
            if lid not in grouped_ids:planned.append(('single',required(cur,'listings',lid),None))
        if sum(kind!='parent' for kind,_,_ in planned)>1000:fail('本批真实子SKU超过1000，请按完整分组拆分。')
        for row_type,record,family in planned:
            if row_type=='parent':
                values=families.parent_values(record,config)
                for issue in native_rules.issues(config,values,target):errors.append({**issue,'family_id':record['id'],'row_type':'parent','href':f'#/families/{record["id"]}?field={issue["field"]}'})
                snapshots.append({'row_type':'parent','family':record,'values':values,'row':config['data_row']+len(rows)})
                rows.append(values)
                continue
            listing=record;lid=listing['id'];sku=required(cur,'skus',listing['sku_id']); product=required(cur,'products',listing['product_id'])
            if not family and listing['fields'].get('_template_id') and int(listing['fields']['_template_id'])!=template['id']:
                fail('刊登当前绑定另一原生模板，请先明确切换并完成对应校验。',{'listing_id':lid,'template_id':listing['fields']['_template_id']})
            checked=domain.listing_record(cur,listing)
            if checked.get('stale'): fail('刊登引用的商品或设计版本已变化，请先处理版本差异。',{'listing_id':lid})
            if not listing['images']: fail('刊登没有关联设计图片，请从设计成果建立刊登。',{'listing_id':lid})
            if sum(image.get('role') in ('main','hero','sales-1','sales_1') for image in listing['images'])!=1:
                errors.append({'field':'images.main','listing_id':lid,'message':'需要明确且唯一的销售主图角色。','href':f'#/listings/{lid}?field=images'})
            if listing['target_id']!=target['id']: fail('选中的刊登不属于同一目标店铺。')
            for issue in checked.get('diagnostics',[]):
                if issue['field'] in ('title','price','stock','images.main'):
                    errors.append({**issue,'listing_id':lid})
            values=export_values(listing,sku)
            values['currency']=target['currency']
            if family:values=families.child_values(family,values)
            for issue in native_rules.issues(config,values,target):
                errors.append({**issue,'listing_id':lid,'href':f'#/listings/{lid}?field={issue["field"]}'})
            for field in config.get('required',[]):
                if values.get(field) in ('',None,[]): errors.append({'field':field,'listing_id':lid,'message':'缺少模板必填字段','href':f'#/listings/{lid}?field={field}'})
            for rule in config.get('official_required',[]):
                col=rule.get('column') if isinstance(rule,dict) else config['mapping'].get(rule,rule)
                field=next((k for k,v in config['mapping'].items() if v==col),None)
                if not field or values.get(field) in ('',None,[]): errors.append({'field':field or col,'listing_id':lid,'message':'原生模板定义的必填字段缺失','href':f'#/listings/{lid}'})
            snapshot={'row_type':row_type,'listing':listing,'sku':sku,'product':product,'values':values,'export_images':mapped_export_images(config,listing),'row':config['data_row']+len(rows)}
            if family:snapshot['family_id']=family['id'];snapshot['family_revision']=family['revision']
            snapshots.append(snapshot); rows.append(values)
        template_asset=required(cur,'assets',template['asset_id'])
    for issue in native_rules.batch_issues(config,rows):
        snapshot=snapshots[issue['row_index']]
        if snapshot['row_type']=='parent':
            family_id=snapshot['family']['id'];errors.append({**issue,'family_id':family_id,'href':f'#/families/{family_id}?field={issue["field"]}'})
        else:
            listing_id=snapshot['listing']['id'];errors.append({**issue,'listing_id':listing_id,'href':f'#/listings/{listing_id}?field={issue["field"]}'})
    if errors: fail('存在未补齐的刊登字段。',errors)
    for snapshot in snapshots:
        for image in snapshot.get('export_images',[]):
            if not image.get('asset_id') or not image.get('url'): fail('每个导出图片槽位都需要设计资产及公开地址。')
            info=domain.get_asset(int(image['asset_id'])); public_image(image['url'],info['sha256'])
            image.update(url_status='content_verified',asset_hash=info['sha256'])
    frozen={'target':target,'template':template,'template_hash':template_asset['sha256'],'selection':selection,'rows':snapshots}
    fingerprint=sha256(db.dump({k:v for k,v in frozen.items() if k!='selection'}).encode()).hexdigest()
    with db.transaction() as cur:
        previous=db.one(cur,'SELECT * FROM exports WHERE fingerprint=%s',(fingerprint,))
        if previous: return previous
    output=native_workbook(asset_bytes(template['asset_id']),config,rows)
    asset=domain.store_asset(output,f'export-{target["id"]}-{fingerprint[:10]}.{template_asset["extension"]}',kind='export')
    with db.transaction() as cur:
        cur.execute("INSERT INTO exports(target_id,template_id,asset_id,fingerprint,intent_key,status,snapshot) VALUES(%s,%s,%s,%s,%s,'file_generated',%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",
            (target['id'],template['id'],asset['id'],fingerprint,intent or fingerprint,db.dump(frozen)))
        result=required(cur,'exports',cur.lastrowid)
        if result['fingerprint']!=fingerprint: fail('此请求标识在导出期间已用于另一操作，请读取已创建的批次。',status=409)
        return result

@router.get('/exports/{export_id}')
def export_detail(export_id:int):
    with db.transaction() as cur:
        item=required(cur,'exports',export_id); item['receipts']=db.rows(cur,'SELECT * FROM receipts WHERE export_id=%s ORDER BY id DESC LIMIT 100',(export_id,))
    return item

@router.post('/exports/{export_id}/receipts')
async def attach_receipt(export_id:int,file:UploadFile=File(...),batch_reference:str=Form('')):
    raw=await read_upload(file)
    with db.transaction() as cur: batch=required(cur,'exports',export_id)
    columns,rows,sheets=parse_receipt(raw,file.filename or 'report.xlsx')
    normalized=lambda value:re.sub(r'[^a-z0-9\u4e00-\u9fff]','',str(value).casefold())
    def find_col(names): return next((c for c in columns if normalized(c) in names),None)
    sku_col=find_col({'sku','sellersku','itemsku','商品编码','卖家sku'})
    status_col=find_col({'status','resultcode','severity','messagetype','状态','处理状态','错误类型'})
    code_col=find_col({'errorcode','resultmessagecode','messagecode','错误代码','错误码'})
    message_col=find_col({'errormessage','resultdescription','message','errordescription','错误信息','错误描述','处理信息'})
    field_col=find_col({'attributename','attribute','field','fieldname','字段','字段名称'})
    known={}
    for row in batch['snapshot']['rows']:
        if row.get('row_type')=='parent':
            family_id=row['family']['id'];known[row['values']['sku']]={'listing_id':None,'family_id':family_id,'row_type':'parent','href':f'#/families/{family_id}'}
        else:
            listing_id=row['listing']['id'];known[row['sku']['code']]={'listing_id':listing_id,'family_id':row.get('family_id'),'row_type':row.get('row_type','single'),'href':f'#/listings/{listing_id}'}
    matches=[]
    for r in rows:
        sku=str(r['raw'].get(sku_col) or '').strip(); target=known.get(sku,{})
        matches.append({'row':r['row_no'],'sheet':r['sheet_name'],'sku':sku,'listing_id':target.get('listing_id'),'family_id':target.get('family_id'),'row_type':target.get('row_type'),
            'status':r['raw'].get(status_col,''),'code':r['raw'].get(code_col,''),'message':r['raw'].get(message_col,''),
            'field':r['raw'].get(field_col,''),'href':target.get('href'),'raw':r['raw']})
    asset=domain.store_asset(raw,file.filename or 'report.xlsx',kind='receipt')
    # The file is retained as evidence. SKU coincidence and a typed batch label cannot prove a new upload.
    parsed={'columns':columns,'sheets':sheets,'rows':matches,'matched_count':sum(bool(r['listing_id'] or r.get('family_id')) for r in matches),'reason':'仅完成文件解析与父体/子SKU对应；尚无可核对的平台提交批次身份。'}
    with db.transaction() as cur:
        previous=db.one(cur,'SELECT * FROM receipts WHERE export_id=%s AND asset_id=%s',(export_id,asset['id']))
        if previous: return previous
        rid=db.insert(cur,'receipts',dict(export_id=export_id,asset_id=asset['id'],batch_reference=batch_reference,status='awaiting_batch_evidence',parsed=parsed))
        return required(cur,'receipts',rid)

def parse_receipt(raw,name):
    """Parse report evidence without asserting it belongs to this upload."""
    suffix=Path(name).suffix.lower()
    if suffix=='.xml':
        try: document=ET.fromstring(raw)
        except ET.ParseError: fail('回执 XML 无法读取。')
        rows=[]
        for index,node in enumerate((n for n in document.iter() if n.tag.split('}')[-1]=='Result'),1):
            values={c.tag.split('}')[-1]:str(c.text or '').strip() for c in node.iter() if len(c)==0}
            rows.append({'row_no':index,'sheet_name':'XML','raw':values,'images':[]})
        if not rows: fail('XML 未找到逐条 Result 处理结果。')
        return list(dict.fromkeys(k for r in rows for k in r['raw'])),rows,['XML']
    if suffix in ('.txt','.tsv','.csv'):
        try: text=raw.decode('utf-8-sig')
        except UnicodeDecodeError: text=raw.decode('gb18030')
        delimiter='\t' if suffix in ('.txt','.tsv') else ','
        records=list(csv.reader(StringIO(text),delimiter=delimiter))
        if len(records)>10020: fail('回执超过 10000 条处理结果。')
        normalize=lambda v:re.sub('[^a-z0-9]','',str(v).casefold())
        head=next((i for i,row in enumerate(records[:20]) if any(normalize(c) in ('sku','sellersku','itemsku') for c in row)),None)
        if head is None: fail('回执未找到明确 SKU 列，不能自动归属结果。')
        columns=records[head]
        if len(set(columns))!=len(columns): fail('回执表头重复，不能可靠对应错误字段。')
        rows=[{'row_no':i+1,'sheet_name':'Report','raw':dict(zip(columns,row)),'images':[]} for i,row in enumerate(records) if i>head and any(row)]
        return columns,rows,['Report']
    return parse_rows(raw,name)

def production_record(row):
    conf=row.get('config') or {}
    complete=bool(conf.get('process') and conf.get('evidence'))
    return {**row,'width':conf.get('pixel_width'),'height':conf.get('pixel_height'),
        'output_status':'file_generated' if complete else 'legacy_unverified',
        'factory_approval':'not_verified','output_kind':conf.get('output_kind','legacy_unverified')}


@router.get('/designs/{design_id}/production')
def production_history(design_id:int,page:int=1,page_size:int=24):
    with db.transaction() as cur:
        required(cur,'designs',design_id)
        result=domain.page(cur,'production_outputs p JOIN assets a ON a.id=p.asset_id','p.design_id=%s',(design_id,),page,page_size,
            select='p.*,a.name,a.sha256,a.byte_size',order='p.id DESC')
    result['items']=[production_record(row) for row in result['items']]
    return result


@router.get('/production/{output_id}')
def production_detail(output_id:int):
    with db.transaction() as cur:
        row=required(cur,'production_outputs',output_id);row['asset']=required(cur,'assets',row['asset_id'])
    return production_record(row)


def production_spec(cur,body):
    body=deepcopy(body);preset=None
    if body.get('preset_id') not in (None,''):
        try:
            ident=int(body['preset_id']);revision=int(body['preset_revision'])
            if isinstance(body['preset_id'],bool) or isinstance(body['preset_revision'],bool) or ident<1 or revision<1:raise ValueError
        except (ValueError,TypeError,KeyError):fail('选择生产方案时必须携带有效的方案身份与修订。')
        preset=required(cur,'directories',ident)
        if preset['kind']!='production_presets' or not preset['enabled']:fail('生产方案不存在或已停用。')
        if preset['revision']!=revision:fail('生产方案已变化，请重新读取并核对当前工艺后生成。',status=409)
        base=preset['config']
        for field in ('process','evidence','width_cm','height_cm','dpi','bleed_mm','format'):
            if field not in body and field in base:body[field]=deepcopy(base[field])
        body['preset_id']=ident;body['preset_revision']=revision
    else:
        body.pop('preset_id',None);body.pop('preset_revision',None)
    for field,label in [('process','工艺名称'),('evidence','工艺来源依据')]:
        value=body.get(field)
        if not isinstance(value,str) or not value.strip():fail('缺少'+label+'，请记录实际供应商工艺或明确的工艺测试方案，不能生成生产文件。',{field:'必填'})
        if len(value)>10000:fail(label+'超过长度限制。')
        body[field]=value.strip()
    kind=body.get('output_kind','factory')
    if kind not in ('factory','process_test'):fail('请明确选择生产参数文件或工艺测试样稿。')
    body['output_kind']=kind
    body['preset_snapshot']={key:deepcopy(preset[key]) for key in ('id','name','revision','config')} if preset else None
    body['factory_approval']='not_verified'
    return body


@router.post('/designs/{design_id}/production')
def production(design_id:int,body:dict):
    with db.transaction() as cur:
        design=required(cur,'designs',design_id)
        sku=required(cur,'skus',design['sku_id'])
        matches=db.rows(cur,'SELECT * FROM design_assets WHERE design_id=%s AND asset_id=%s',(design_id,body.get('asset_id')))
        if body.get('design_asset_id'): matches=[a for a in matches if a['id']==int(body['design_asset_id'])]
        if len(matches)>1 and body.get('component_key'):
            matches=[a for a in matches if (a.get('meta') or {}).get('component_key')==body['component_key']]
        if not matches: fail('所选图案不属于当前设计版本或组件。')
        if len(matches)>1: fail('该图案有多份组件或用途关联，请明确选择设计资产记录。',{'design_asset_id':'请选择具体关联'})
        art=matches[0]
        artifact_kind=art['role'].split(':')[-1]
        if artifact_kind not in ('master','repeat','pattern','tile'): fail('生产文件必须来自平面母稿或循环单元，不能从销售效果图反推。')
        if design['mode']=='supplier': fail('供应商中性实物母稿不是平面印刷源；不能用实物展示图生成生产文件。')
        body=production_spec(cur,body)
    metadata=art.get('meta') or {}
    component_key=metadata.get('component_key')
    component_plan=metadata.get('component_plan') or next((p for p in design['config'].get('component_plans',[]) if p['component_key']==component_key),{})
    if component_key and body.get('component_key') not in (None,'',component_key):
        fail('所选图案属于其他组件，请使用同一组件的母稿或循环单元。',{'component_key':component_key})
    if component_key: body={**body,'component_key':component_key}
    frozen_sku=(design.get('input_snapshot') or {}).get('sku') or sku
    selected_key=body.get('component_key')
    component=component_plan.get('component_snapshot') or next((c for c in frozen_sku.get('components',[]) if c.get('key')==selected_key),None)
    if selected_key and not component:fail('组件身份不属于此设计冻结的SKU，请明确选择真实组件。')
    if not component and body['output_kind']=='factory':fail('生产参数文件需要设计冻结的真实组件与尺寸；概念尺寸只能明确选择工艺测试样稿。',{'component_key':'缺少真实组件'})
    pattern_mode=metadata.get('pattern_mode') or component_plan.get('mode') or design['mode']
    try:
        width=float(body['width_cm']); height=float(body['height_cm']); dpi=int(body['dpi']); bleed=float(body.get('bleed_mm',0))
        if any(isinstance(body.get(k),bool) for k in ('width_cm','height_cm','dpi','bleed_mm')) or not all(math.isfinite(v) for v in (width,height,bleed)) or min(width,height)<=0 or not 72<=dpi<=1200 or float(body['dpi'])!=dpi or not 0<=bleed<=50: raise ValueError
    except (ValueError,TypeError,KeyError): fail('请填写实际宽高、72–1200 DPI 和有效出血。')
    if component:
        try:
            actual_width=float(component['width_cm']);actual_height=float(component['height_cm'])
            if not all(math.isfinite(v) and v>0 for v in (actual_width,actual_height)):raise ValueError
        except (ValueError,TypeError,KeyError):fail('所选真实组件没有明确有限宽高，不能以本次手填尺寸代替。')
        if abs(width-actual_width)>max(.01,actual_width*.001) or abs(height-actual_height)>max(.01,actual_height*.001):
            fail('组件生产宽高与冻结的实际组件尺寸不一致，请核对商品规格并建立正确版本。')
    fmt=str(body.get('format','PNG')).upper()
    if fmt not in ('PNG','TIFF'): fail('当前支持 RGB PNG/TIFF；CMYK 需要供应商 ICC 配置后适配。')
    pw=round((width+bleed/5)/2.54*dpi); ph=round((height+bleed/5)/2.54*dpi)
    if min(pw,ph)<1 or pw*ph>80000000: fail('输出尺寸需至少 1 像素且不超过 8000 万像素，请核对尺寸或按组件分块。')
    source=Image.open(BytesIO(asset_bytes(art['asset_id']))).convert('RGBA')
    settings=metadata.get('repeat') or component_plan.get('repeat') or design['config'].get('repeat',{})
    mode=str(body.get('repeat_mode') or settings.get('mode') or 'basic')
    if pattern_mode=='repeat':
        try: settings=normalize_repeat(settings)
        except PatternError as exc: fail(str(exc))
        try:
            tw=float(body.get('unit_width_mm'))/10 if body.get('unit_width_mm') is not None else float(body.get('print_width_cm') or settings.get('width_cm') or 0)
            th=float(body.get('unit_height_mm'))/10 if body.get('unit_height_mm') is not None else float(body.get('print_height_cm') or settings.get('height_cm') or 0)
            if not all(math.isfinite(v) and v>0 for v in (tw,th)): raise ValueError
        except (ValueError,TypeError): fail('需要循环单元的有限实际宽高，不能用组件尺寸代替。')
        tx=round(tw/2.54*dpi); ty=round(th/2.54*dpi)
        if min(tx,ty)<1: fail('循环单元在当前 DPI 下不足 1 像素。')
        if source.width<tx or source.height<ty: fail('循环单元真实像素不足；提高 DPI 元数据不能补足细节。')
        if abs(source.width/source.height-tw/th)/(tw/th)>.02: fail('单元像素比例与物理宽高不匹配；请重设循环单元尺寸或重做原图，禁止拉伸花型。')
        tile=source.resize((tx,ty),Image.Resampling.LANCZOS)
        if mode not in ('basic','half-drop'): fail('当前实际支持基础循环和半落位循环。')
        from .engine import repeat_canvas
        canvas=repeat_canvas(tile,pw,ph,mode)
    else:
        if source.width<pw or source.height<ph: fail('母稿像素不足以覆盖该尺寸和 DPI；请生成足够分辨率的图案。')
        ratio=abs(source.width/source.height-pw/ph)/(pw/ph)
        if ratio>.02: fail('母稿比例与生产区域不匹配，请先调整构图，避免拉伸或裁切主体。')
        canvas=source.resize((pw,ph),Image.Resampling.LANCZOS)
    config={**body,'design_id':design_id,'design_asset_id':art['id'],'source_asset_id':art['asset_id'],
        'width_cm':width,'height_cm':height,'dpi':dpi,'bleed_mm':bleed,'format':fmt,
        'pattern_mode':pattern_mode,'component_plan':component_plan,'component_snapshot':deepcopy(component),
        'dimension_source':'frozen_component' if component else 'explicit_test_size','sku_revision':frozen_sku['revision'],
        'pixel_width':pw,'pixel_height':ph,'source_pixel_width':source.width,'source_pixel_height':source.height,
        'color_space':'RGB','design_revision':design['revision']}
    if pattern_mode=='repeat':
        config.update(repeat=deepcopy(settings),repeat_mode=mode,print_width_cm=tw,print_height_cm=th,unit_width_mm=tw*10,unit_height_mm=th*10,
            source_effective_dpi_x=source.width/tw*2.54,source_effective_dpi_y=source.height/th*2.54)
    else:
        config.update(source_effective_dpi_x=source.width/(width+bleed/5)*2.54,source_effective_dpi_y=source.height/(height+bleed/5)*2.54)
    fingerprint=sha256(db.dump(config).encode()+str(art['asset_id']).encode()).hexdigest()
    with db.transaction() as cur:
        old=db.one(cur,'SELECT * FROM production_outputs WHERE fingerprint=%s',(fingerprint,))
        if old:return production_record(old)
    output=BytesIO();options={}
    if fmt=='PNG':
        from PIL.PngImagePlugin import PngInfo
        metadata=PngInfo();metadata.add_itxt('production_metadata',db.dump(config));options['pnginfo']=metadata
    else:options['tiffinfo']={270:json.dumps(config,ensure_ascii=True,sort_keys=True,separators=(',',':'))}
    canvas.convert('RGB').save(output,format=fmt,dpi=(dpi,dpi),**options)
    asset=domain.store_asset(output.getvalue(),f'{"process-test" if body["output_kind"]=="process_test" else "production"}-{design_id}-{fingerprint[:8]}.{fmt.lower()}',kind='production')
    with db.transaction() as cur:
        if body.get('preset_id') and required(cur,'directories',body['preset_id'])['revision']!=body['preset_revision']:
            fail('文件生成期间生产方案已变化，请重新核对后生成；旧输出记录保留。',status=409)
        cur.execute('INSERT INTO production_outputs(design_id,asset_id,source_asset_id,fingerprint,config) VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)',
            (design_id,asset['id'],art['asset_id'],fingerprint,db.dump(config)))
        return production_record({**required(cur,'production_outputs',cur.lastrowid),'asset':asset})
