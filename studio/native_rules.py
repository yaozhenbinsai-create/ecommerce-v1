"""Read-only Amazon workbook constraints. Never executes macros or Excel code."""
from fnmatch import fnmatchcase
from functools import lru_cache
import math
import re

from openpyxl.utils import range_boundaries,get_column_letter


class FormulaUnsupported(ValueError):
    pass


TOKEN=re.compile(r'\s*(?:("(?:[^"]|"")*")|([0-9]+(?:\.[0-9]+)?)|([A-Za-z_$][A-Za-z0-9_.$!\[\]]*)|(<>|<=|>=|[=<>(),]))')


@lru_cache(maxsize=4096)
def _tokens(formula):
    tokens=[];position=0
    while position<len(formula):
        found=TOKEN.match(formula,position)
        if not found: raise FormulaUnsupported('unsupported formula token')
        position=found.end();tokens.append(next(v for v in found.groups() if v is not None))
    return tuple(tokens)


def evaluate(formula,cells,names,ranges,stack=()):
    """Small, closed expression grammar for this template's required rules."""
    tokens=_tokens(formula)
    cursor=0
    def expr():
        nonlocal cursor
        left=atom()
        if cursor<len(tokens) and tokens[cursor] in ('=','<>','>','<','>=','<='):
            op=tokens[cursor];cursor+=1;right=atom()
            if op in ('=','<>'):
                equal=(left==right) if isinstance(left,(int,float)) and isinstance(right,(int,float)) else str(left).casefold()==str(right).casefold()
                return equal if op=='=' else not equal
            try: a,b=float(left),float(right)
            except (TypeError,ValueError): a,b=str(left).casefold(),str(right).casefold()
            return {'>':lambda:a>b,'<':lambda:a<b,'>=':lambda:a>=b,'<=':lambda:a<=b}[op]()
        return left
    def atom():
        nonlocal cursor
        if cursor>=len(tokens): raise FormulaUnsupported('incomplete formula')
        value=tokens[cursor];cursor+=1
        if value=='(':
            result=expr()
            if cursor>=len(tokens) or tokens[cursor]!=')':raise FormulaUnsupported('missing closing parenthesis')
            cursor+=1;return result
        if value.startswith('"'):return value[1:-1].replace('""','"')
        if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',value):return float(value)
        if cursor<len(tokens) and tokens[cursor]=='(':
            cursor+=1;args=[]
            while cursor<len(tokens) and tokens[cursor]!=')':
                args.append(expr())
                if cursor<len(tokens) and tokens[cursor]==',':cursor+=1
                elif cursor>=len(tokens) or tokens[cursor]!=')':raise FormulaUnsupported('invalid function arguments')
            if cursor>=len(tokens):raise FormulaUnsupported('unterminated function')
            cursor+=1
            if value=='AND':return all(bool(v) for v in args)
            if value=='OR':return any(bool(v) for v in args)
            if value=='NOT' and len(args)==1:return not bool(args[0])
            if value=='LEN' and len(args)==1:return len(str(args[0] if args[0] is not None else ''))
            if value=='IF' and len(args)==3:return args[1] if args[0] else args[2]
            if value=='COUNTIF' and len(args)==2:
                return sum(fnmatchcase(str(v).casefold(),str(args[1]).casefold()) for v in (args[0] if isinstance(args[0],list) else [args[0]]))
            raise FormulaUnsupported('unsupported formula function '+value)
        if value=='TRUE':return True
        if value=='FALSE':return False
        match=re.fullmatch(r'(?:Template!)?\$?([A-Z]{1,3})\$?[0-9]+',value)
        if match:return cells.get(match[1],'') if cells.get(match[1]) is not None else ''
        if value in ranges:return ranges[value]
        if value in names:
            if value in stack or len(stack)>30:raise FormulaUnsupported('cyclic named formula')
            return evaluate(names[value],cells,names,ranges,(*stack,value))
        raise FormulaUnsupported('unknown named formula '+value)
    result=expr()
    if cursor!=len(tokens):raise FormulaUnsupported('trailing formula tokens')
    return result


def _range_values(book,name):
    defined=book.defined_names.get(name)
    if not defined or defined.type!='RANGE':return []
    values=[]
    for sheet,area in defined.destinations:
        if sheet not in book.sheetnames:return []
        try:min_col,min_row,max_col,max_row=range_boundaries(area)
        except ValueError:return []
        if (max_row-min_row+1)*(max_col-min_col+1)>5000:return []
        for row in book[sheet].iter_rows(min_row=min_row,max_row=max_row,min_col=min_col,max_col=max_col,values_only=True):
            for value in row:
                if value is not None and str(value) not in values:values.append(str(value))
    return values


def extract(book,sheet,columns):
    identifiers={c['identifier'] for c in columns}
    if 'product_type#1.value' not in identifiers or '::record_action' not in identifiers:return {}
    product_types=_range_values(book,'product_type1.value')
    if product_types!=['PILLOW']:return {'adapter':'unsupported_amazon_product_type','product_types':product_types,'rules':[]}
    definitions={}
    if 'Data Definitions' in book.sheetnames:
        for row in book['Data Definitions'].iter_rows(min_row=3,values_only=True):
            if len(row)>5 and row[1]:definitions[str(row[1])]={'requirement':str(row[5] or ''),'description':str(row[3] or '')}
    names={n:d.attr_text for n,d in book.defined_names.items() if n.startswith(('PTList','reqPTList','form_rgPTList','only_enfPTList'))}
    ranges={n:_range_values(book,n) for n in book.defined_names if n.startswith('CONDITION_LIST_')}
    validations={}
    for validation in sheet.data_validations.dataValidation:
        if validation.type!='list' or not validation.formula1:continue
        for area in validation.sqref.ranges:
            if area.min_col!=area.max_col:continue
            col=get_column_letter(area.min_col);formula=validation.formula1;enums={}
            for product in product_types:
                name=formula.lstrip('=')
                if name.startswith('INDIRECT('):
                    match=re.search(r'&"([^"]+)"\)\s*$',name)
                    if not match:continue
                    name=re.sub(r'[- ]','_',product)+match[1]
                values=_range_values(book,name)
                if values:enums[product]=values
            if enums:validations[col]={'strict_enum':bool(validation.showErrorMessage),'enums':enums}
    rules=[]
    for column in columns:
        identifier=column['identifier'];token=re.sub(r'[^A-Za-z0-9_.]','',identifier)
        rule={**column,**definitions.get(identifier,{}),**validations.get(column['column'],{})}
        required='reqPTList0'+token
        if required in names:
            rule['required_formula']=names[required]
            rule['exempt_formulas']=[names[prefix+token] for prefix in ('form_rgPTList0','only_enfPTList0') if prefix+token in names]
        rules.append(rule)
    price=next((c for c in columns if c['identifier'].startswith('purchasable_offer[') and '[audience=ALL]' in c['identifier'] and '.our_price#1.' in c['identifier']),{})
    currency=re.search(r'Your Price ([A-Z]{3})',price.get('label',''))
    return {'adapter':'amazon_pillow_us_v1','product_types':product_types,'marketplace_id':'ATVPDKIKX0DER',
        'currency':currency[1] if currency else None,'rules':rules,'named_formulas':names,'named_ranges':ranges,
        'coverage':'workbook_required_conditions_and_dropdowns','platform_acceptance':'not_verified'}


def issues(config,values,target=None):
    native=config.get('native_rules') or {}
    if not native:
        amazon=any(str(rule.get('identifier','')).startswith('contribution_sku#') for rule in config.get('official_required',[]) if isinstance(rule,dict))
        return [{'field':'template','message':'此Amazon模板尚未读取条件/枚举规则，请在模板配置重新读取原生规则后再导出。'}] if amazon else []
    if native.get('adapter')!='amazon_pillow_us_v1':return [{'field':'template','message':'该Amazon产品类型尚未完成原生规则适配，不能宣称直接导入。'}]
    mapping=config.get('mapping') or {};reverse={col:field for field,col in mapping.items()}
    cells={col:values.get(field,'') for field,col in mapping.items()};problems=[]
    def add(column,message,allowed=None):
        row={'field':reverse.get(column,'native_'+column),'column':column,'message':message}
        if allowed is not None:row['allowed']=allowed
        problems.append(row)
    for col in ('B','C'):
        if cells.get(col) in ('',None):add(col,('产品类型' if col=='B' else '刊登操作')+'必须显式填写，不能导出空白核心列。')
    product=str(cells.get('B') or '')
    if product and product not in native.get('product_types',[]):add('B','产品类型不属于当前原生模板。',native.get('product_types',[]))
    if target and native.get('currency') and target.get('currency')!=native['currency']:
        problems.append({'field':'currency','message':f"模板报价列使用 {native['currency']}，与目标店铺 {target.get('currency')} 不一致。"})
    for rule in native['rules']:
        column=rule['column'];value=cells.get(column,'');enum=(rule.get('enums') or {}).get(product,[])
        if rule.get('strict_enum') and enum and value not in ('',None) and str(value) not in enum:
            add(column,rule['label']+' 不属于该原生模板允许的值。',enum)
        identifier=rule['identifier']
        numeric=bool(re.search(r'(?:price.*\.value(?:_with_tax)?$|dimensions.*\.value$|item_length_width.*\.value$|(?:item|package|battery).*weight.*\.value$|\.quantity$|\.lead_time_to_ship_max_days$|number_of_items[^.]*\.value$|unit_count[^.]*\.value$)',identifier))
        if numeric and value not in ('',None):
            integer=identifier.endswith(('.quantity','.lead_time_to_ship_max_days')) or identifier.startswith('number_of_items')
            try:
                number=float(value)
                if isinstance(value,bool) or not math.isfinite(number) or number<0 or integer and number%1:raise ValueError
            except (ValueError,TypeError):add(column,rule['label']+' 需要有限非负数值'+('，该字段必须是整数。' if integer else '。'))
        if not product or value not in ('',None):continue
        condition=rule.get('required_formula')
        if not condition:continue
        try:
            required=bool(evaluate(condition,cells,native['named_formulas'],native['named_ranges']))
            exempt=any(bool(evaluate(f,cells,native['named_formulas'],native['named_ranges'])) for f in rule.get('exempt_formulas',[]))
        except FormulaUnsupported:
            add(column,rule['label']+' 的原生条件尚不能可靠求值，请补适配后再导出。');continue
        if required and not exempt:add(column,rule['label']+' 在当前产品/变体/配送条件下必填。')
    if cells.get('D')=='Child' and not cells.get('E'):add('E','子SKU必须填写真实父SKU。')
    if cells.get('E') and cells.get('E')==cells.get('A'):add('E','SKU不能把自己作为父SKU。')
    if cells.get('D')=='Parent' and cells.get('E'):add('E','父SKU不能再指向另一个父SKU。')
    if not cells.get('D') and (cells.get('E') or cells.get('F')):add('D','填写了父SKU或变体主题时，必须明确父/子关系。')
    if cells.get('D') in ('Parent','Child') and not cells.get('F'):add('F','父子变体必须填写该模板支持的变体主题。')
    action=cells.get('C')
    if action=='Delete':add('C','当前工作流用于创建或更新刊登，不生成删除操作文件。')
    return problems


def batch_issues(config,rows):
    """Validate relationships which can be established from this actual file."""
    if (config.get('native_rules') or {}).get('adapter')!='amazon_pillow_us_v1':return []
    by_sku={str(row.get('sku') or ''):row for row in rows};problems=[];variant_keys={}
    columns=config.get('columns',[]);mapping=config.get('mapping',{});reverse={col:key for key,col in mapping.items()}
    for index,row in enumerate(rows):
        if row.get('parentage')!='Child':continue
        parent_sku=str(row.get('parent_sku') or '');parent=by_sku.get(parent_sku)
        if parent:
            if parent.get('parentage')!='Parent':problems.append({'row_index':index,'field':'parent_sku','message':'本批次引用的父SKU没有声明为Parent。'})
            if parent.get('variation')!=row.get('variation'):problems.append({'row_index':index,'field':'variation','message':'本批次父子SKU的变体主题不一致。'})
        dimensions=[]
        for token in str(row.get('variation') or '').split('/'):
            root=token.casefold()
            column=next((c['column'] for c in columns if re.split(r'[\[#.]',c.get('identifier',''),maxsplit=1)[0].casefold()==root),None)
            key=reverse.get(column)
            if not key:
                problems.append({'row_index':index,'field':'variation','column':column,'message':f'变体主题 {row.get("variation")} 的 {token} 属性没有原生列映射。'})
                continue
            value=row.get(key)
            if value in (None,''):problems.append({'row_index':index,'field':key,'column':column,'message':f'变体主题 {row.get("variation")} 需要填写 {token} 属性。'})
            dimensions.append((token,str(value or '').casefold()))
        if parent_sku and dimensions and all(value for _,value in dimensions):
            signature=(parent_sku,str(row.get('variation') or ''),tuple(dimensions))
            if signature in variant_keys:problems.append({'row_index':index,'field':'variation','message':'同一父SKU下出现重复的变体属性组合，请核对真实规格。'})
            else:variant_keys[signature]=index
    return problems
