"""Physical pattern contracts shared by validation, generation and production."""
from copy import deepcopy
from hashlib import sha256
import math
import re


class PatternError(ValueError):
    pass


def positive(value,label):
    try:
        number=float(value)
        if isinstance(value,bool) or not math.isfinite(number) or number<=0: raise ValueError
    except (ValueError,TypeError): raise PatternError(label+'必须是有限正数。') from None
    return number


def normalize_repeat(value):
    if not isinstance(value,dict): raise PatternError('循环方案必须包含实际单元宽高。')
    result=deepcopy(value)
    mode=result.get('mode','basic')
    if mode not in ('basic','half-drop'): raise PatternError('循环排列仅支持规则平铺和半落位。')
    for axis in ('width','height'):
        mm=result.get('unit_'+axis+'_mm')
        if mm in (None,''): mm=positive(result.get(axis+'_cm'),'循环单元'+axis+' / cm')*10
        else: mm=positive(mm,'循环单元'+axis+' / mm')
        result['unit_'+axis+'_mm']=mm
        result[axis+'_cm']=mm/10
    result['mode']=mode
    return result


def requested_canvas(width_mm,height_mm,long_edge=1536):
    width=positive(width_mm,'图案物理宽'); height=positive(height_mm,'图案物理高')
    longest=max(width,height)
    pw=max(1,round(width/longest*long_edge)); ph=max(1,round(height/longest*long_edge))
    if min(pw,ph)<64: raise PatternError('图案比例过于狭长，当前生成画布短边不足64像素，请按明确组件分块。')
    return {'width':pw,'height':ph,'size':f'{pw}x{ph}','physical_width_mm':width,'physical_height_mm':height}


def raster_measurements(width,height,physical_width_mm,physical_height_mm):
    physical_width_mm=positive(physical_width_mm,'图案物理宽')
    physical_height_mm=positive(physical_height_mm,'图案物理高')
    actual_ratio=width/height; expected_ratio=physical_width_mm/physical_height_mm
    error=abs(actual_ratio-expected_ratio)/expected_ratio
    if error>.02:
        raise PatternError(f'模型返回 {width}×{height} 像素，与声明的 {physical_width_mm:g}×{physical_height_mm:g} mm 比例不符。原始返回已保留；不能拉伸、裁切或标记为合格单元。请创建修正后的设计版本。')
    return {'pixel_width':width,'pixel_height':height,'physical_width_mm':physical_width_mm,'physical_height_mm':physical_height_mm,
        'effective_dpi_x':round(width/physical_width_mm*25.4,3),'effective_dpi_y':round(height/physical_height_mm*25.4,3),
        'aspect_ratio_error':error,'resampled':False}


def component_role(key,kind):
    token=str(key)
    if len(token)>32 or not re.fullmatch(r'[A-Za-z0-9_-]+',token): token=sha256(token.encode()).hexdigest()[:16]
    return 'component:'+token+':'+kind


def normalize_design_patterns(config,sku,mode):
    result=deepcopy(config)
    plans=result.get('component_plans') or []
    if not isinstance(plans,list): raise PatternError('组件设计方案必须为列表。')
    if len(plans)>5: raise PatternError('当前图片通道最多同时引用5份组件母稿，请将更多组件拆成明确设计任务。')
    if mode=='supplier' and plans: raise PatternError('供应商实物展示不能把真实组件改成原创混合图案。')
    known={str(c['key']):c for c in sku.get('components',[]) if isinstance(c,dict) and c.get('key')}
    normalized=[]; seen=set()
    for plan in plans:
        if not isinstance(plan,dict): raise PatternError('每个组件方案必须为对象。')
        key=str(plan.get('component_key',''))
        if key not in known or key in seen: raise PatternError('组件方案必须对应当前SKU的不重复真实组件。')
        seen.add(key); component=known[key]; method=plan.get('mode')
        if method not in ('large','repeat'): raise PatternError('组件设计方式必须为完整构图或循环花型。')
        width=positive(component.get('width_cm'),'组件 '+component.get('name',key)+' 宽度 / cm')
        height=positive(component.get('height_cm'),'组件 '+component.get('name',key)+' 高度 / cm')
        clean={**plan,'component_key':key,'mode':method,'brief':str(plan.get('brief') or ''),
            'component_snapshot':deepcopy(component),'width_cm':width,'height_cm':height}
        if method=='repeat': clean['repeat']=normalize_repeat(plan.get('repeat') or result.get('repeat'))
        else: clean.pop('repeat',None)
        normalized.append(clean)
    if normalized:
        result['component_plans']=normalized
    elif 'component_plans' in result:
        result['component_plans']=[]
    if mode=='repeat' and not normalized:
        result['repeat']=normalize_repeat(result.get('repeat'))
    elif mode=='repeat' and isinstance(result.get('repeat'),dict) and result['repeat']:
        result['repeat']=normalize_repeat(result['repeat'])
    else:
        # Legacy draft forms also posted hidden repeat controls. Those controls
        # have no meaning for a supplier/large master; per-component repeats
        # already carry their own normalized parameters above.
        result.pop('repeat',None)
    return result
