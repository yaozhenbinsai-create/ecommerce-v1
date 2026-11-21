"""Read an existing model request; never submit a replacement during inspection."""
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
import base64
import json
from pathlib import Path
import uuid

from fastapi import APIRouter
from . import db,domain,engine,providers

router=APIRouter(prefix='/api')


def inspect_original(bridge,ref,is_image,paths):
    remote=ref.get('codex') or {}
    safe={'thread_id':remote.get('thread_id'),'turn_id':remote.get('turn_id')}
    def result(outcome,message,**extra):return {'outcome':outcome,'message':message,**safe,**extra}
    if not all(safe.values()):return result('insufficient_evidence','原请求未保存完整线程与轮次身份，不能猜测归属或重新提交。')
    prompt=ref.get('prompt','')
    if sha256(prompt.encode()).hexdigest()!=ref.get('prompt_sha256'):
        return result('insufficient_evidence','原请求提示词与保存的哈希不一致。')
    thread=bridge.request('thread/read',{'threadId':safe['thread_id'],'includeTurns':True},timeout=30)['thread']
    if thread.get('id')!=safe['thread_id'] or bridge._local_path(thread.get('cwd',''))!=bridge._local_path(providers.ROOT):
        return result('insufficient_evidence','原线程身份或工作目录不匹配。')
    turn=next((t for t in thread.get('turns',[]) if t.get('id')==safe['turn_id']),None)
    if not turn:return result('insufficient_evidence','原线程没有找到保存的轮次。')
    safe['turn_status']=turn.get('status')
    if turn.get('status')=='inProgress':return result('pending','原请求仍在执行，仅保留查询结果，没有再次提交。')
    if turn.get('status') not in ('completed','failed','interrupted'):
        return result('insufficient_evidence','原请求尚无可核对的终态。')
    trace=bridge._validated_trace_path(thread.get('path') or remote.get('trace_path'),safe['thread_id'])
    if trace and not trace.is_file():
        trace=bridge._validated_trace_path(bridge._codex_home()/'archived_sessions'/trace.name,safe['thread_id'])
    if not trace or not trace.is_file():return result('insufficient_evidence','原请求终态已读取，但缺少原始执行记录，不能证明没有出图。')
    identity=False;cwd_match=False;current=None;prompt_match=False;terminal=False;tool_seen=False;output_seen=False
    with trace.open(encoding='utf-8') as stream:
        for line in stream:
            row=json.loads(line);payload=row.get('payload') or {};kind=row.get('type');event=payload.get('type')
            if kind=='session_meta':
                identity=(payload.get('id') or payload.get('session_id'))==safe['thread_id']
                cwd_match=bridge._local_path(payload.get('cwd',''))==bridge._local_path(providers.ROOT)
            if kind=='turn_context' or kind=='event_msg' and event=='task_started':current=payload.get('turn_id') or current
            if current!=safe['turn_id']:continue
            if kind=='event_msg' and event in ('turn_aborted','task_complete','task_failed'):terminal=True
            if kind=='response_item' and event=='message' and payload.get('role')=='user':
                prompt_match=prompt_match or any(sha256(c.get('text','').encode()).hexdigest()==ref['prompt_sha256'] for c in payload.get('content',[]) if isinstance(c,dict) and c.get('type') in ('text','input_text'))
            if kind=='response_item' and event not in ('message','reasoning'):tool_seen=True
            if kind=='event_msg' and event=='item_completed':
                typ=str((payload.get('item') or {}).get('type','')).casefold()
                if typ not in ('usermessage','reasoning','agentmessage','plan'):tool_seen=True
                if any(x in typ for x in ('image','tool','command')):output_seen=True
            if kind=='response_item' and event=='message' and payload.get('role')=='assistant':output_seen=True
    safe['trace_sha256']=sha256(trace.read_bytes()).hexdigest()
    safe['trace_identity_verified']=bool(identity and cwd_match and prompt_match)
    if not safe['trace_identity_verified']:return result('insufficient_evidence','原始记录与冻结提示词/线程身份未完全匹配。')
    items=turn.get('items') or []
    permitted={'usermessage','reasoning'}
    turn_empty=all(str(item.get('type','')).casefold() in permitted for item in items)
    if terminal and turn_empty and not tool_seen and not output_seen:
        return result('known_no_output','原请求已结束（'+turn['status']+'），原记录无工具执行和输出；可由用户显式接续。',terminal_event_verified=True,tool_execution_count=0)
    if turn.get('status')=='completed':
        if is_image:
            # remote= always takes the existing-turn branch of this bridge.
            # Guard the paid method as a second independent protection.
            def forbidden(*args,**kwargs):raise RuntimeError('reconciliation_must_not_submit')
            bridge.run_turn=forbidden
            path=bridge.generate_image(prompt,ref.get('model',''),paths,remote={**remote,'trace_path':str(trace)})
            return result('restored','已取回原请求图片，没有生成新图。',image=base64.b64encode(Path(path).read_bytes()).decode())
        text=bridge.final_text(turn)
        return result('restored','已取回原请求文本，没有再次调用模型。',text=text)
    return result('insufficient_evidence','原请求已经结束，但执行过工具或证据不完整；继续保持未知，禁止重复提交。')


@router.post('/jobs/{job_id}/reconcile')
def reconcile_job(job_id:int,body:dict):
    key=str(body.get('step_key') or '')
    with db.transaction() as cur:
        job=domain.get(cur,'jobs',job_id)
        if job['status']!='unknown':raise domain.Problem('只核对结果未知的原请求，不会改动运行中或已完成任务','not_unknown',409)
        step=next((s for s in job['steps'] if s['key']==key),None)
        if not step or step['status']!='unknown':raise domain.Problem('请选择结果未知的步骤')
        ref=deepcopy(step.get('request_ref') or {})
        paths=[]
        for item in ref.get('references',[]):
            asset=db.one(cur,'SELECT * FROM assets WHERE sha256=%s',(item.get('sha256'),))
            if not asset:raise domain.Problem('原请求参考资产缺失，不能猜测替代')
            paths.append(domain.asset_record(asset)['path'])
    is_image=bool(ref.get('size'))
    audit_id=uuid.uuid4().hex
    try:
        remote=ref.get('codex') or {}
        if ref.get('protocol')!='codex' or not remote.get('thread_id') or not remote.get('turn_id'):
            outcome={'outcome':'insufficient_evidence','message':'原请求未保存完整线程与轮次身份，不能猜测归属或重新提交。'}
        else:outcome=providers.reconcile(ref,is_image=is_image,paths=paths)
    except Exception as exc:
        failure,message=providers.codex_failure(exc)
        outcome={'outcome':'insufficient_evidence','message':'核对原请求失败：'+message,'failure':failure}
    raw=outcome.pop('image',None);text=outcome.pop('text',None)
    content=None
    if outcome['outcome']=='restored':
        try:
            if is_image:
                content=base64.b64decode(raw or '',validate=True)
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(content)) as image:image.verify()
            elif not isinstance(text,str) or not text.strip():raise ValueError()
        except Exception:
            outcome={**outcome,'outcome':'insufficient_evidence','message':'原返回缺少可恢复的有效内容，未解除未知状态。'}
    audit={'id':audit_id,'at':datetime.now(timezone.utc).isoformat(),'request_id':ref.get('id'),**outcome}
    with db.transaction() as cur:
        current=domain.get(cur,'jobs',job_id,True)
        latest=next((s for s in current['steps'] if s['key']==key),None)
        if current['status']!='unknown' or not latest or latest.get('request_ref')!=ref:
            raise domain.Problem('任务在核对期间已变化，未覆盖当前请求','request_changed',409)
        latest.setdefault('reconciliations',[]).append(audit)
        changes={}
        if outcome['outcome'] in ('known_no_output','restored'):
            if outcome['outcome']=='restored':
                if is_image:
                    engine._atomic(engine._checkpoint_path(job_id,key,'image'),content)
                elif isinstance(text,str) and text.strip():engine._atomic(engine._checkpoint_path(job_id,key,'txt'),text.encode('utf-8'))
                else:raise domain.Problem('原返回缺少可恢复的内容，未解除未知状态')
            latest.setdefault('attempt_history',[]).append({'request_ref':deepcopy(ref),'status':outcome['outcome'],'reconciliation_id':audit_id})
            latest.update(status='failed',recovery_status=outcome['outcome'],error=outcome['message'])
            changes.update(status='failed',stage='原请求已核对，等待显式接续',error=outcome['message'])
        db.update(cur,'jobs',job_id,{'steps':current['steps'],**changes})
        if changes and current['kind'] in ('design','image_revision'):
            db.update(cur,'designs',current['entity_id'],{'status':'failed'})
        result=domain.get(cur,'jobs',job_id)
    return {'job':domain.public_job(result),'reconciliation':audit}
