"""Project-owned model settings and explicit source integrations."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import urlparse

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {
    'Amazon': ['amazon.com'], 'Temu': ['temu.com'],
    'Alibaba.com': ['alibaba.com'], 'AliExpress': ['aliexpress.com'],
    'Pinterest': ['pinterest.com'], 'Instagram': ['instagram.com'],
    'TikTok': ['tiktok.com'], 'YouTube': ['youtube.com'],
    'Reddit': ['reddit.com'], '开放热点': [],
}


class ProviderError(RuntimeError):
    def __init__(self, message, *, unknown=False, request_ref=None):
        super().__init__(message)
        self.unknown = unknown
        self.request_ref = request_ref or {}


def _config():
    data_root = Path(os.environ.get('ECOMMERCE1_DATA_DIR', ROOT / 'data'))
    path = data_root / 'model-connections.json'
    if not path.exists():
        return [], []
    try:
        source = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(source, dict) or set(source) - {'models', 'plans'}:
            raise ValueError
        entries, plans = source.get('models', []), source.get('plans', [])
        if not isinstance(entries, list) or not isinstance(plans, list):
            raise ValueError
        model_ids = set()
        for model in entries:
            ident = model['id']
            if type(ident) is not int or ident <= 0 or ident in model_ids:
                raise ValueError
            model_ids.add(ident)
            if not all(isinstance(model.get(field), str) and model[field].strip() for field in ('name', 'model_identifier', 'protocol', 'capability')):
                raise ValueError
            if model['capability'] not in {'文本', '图片'} or model['protocol'] not in {'chat_completions', 'responses', 'anthropic', 'images', 'codex'}:
                raise ValueError
            if model['protocol'] != 'codex':
                address = urlparse(model.get('base_url') or '')
                if (not address.hostname or address.username or address.password or
                        (address.scheme != 'https' and not (address.scheme == 'http' and address.hostname in {'127.0.0.1', 'localhost'}))):
                    raise ValueError
        for plan in plans:
            if (not isinstance(plan.get('plan_type'), str) or not isinstance(plan.get('model_ids'), list)
                    or not all(type(item) is int and item in model_ids for item in plan['model_ids'])):
                raise ValueError
        return entries, plans
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        raise ProviderError('本项目的 model-connections.json 格式有误，请检查模型编号、能力、协议与方案。') from None


def _is_codex(model):
    return model.get('protocol') == 'codex'


def _key(model):
    return os.environ.get(f"STUDIO_MODEL_KEY_{model['id']}", '').strip()


def models():
    try:
        rows, plans = _config()
        items = [{field: row.get(field) for field in ('id', 'name', 'provider', 'model_identifier', 'capability', 'protocol')}
                 | {'configured': bool(os.environ.get('STUDIO_CODEX_BRIDGE_SOURCE')) if _is_codex(row) else bool(_key(row))}
                 for row in rows]
        public_plans = [{field: plan.get(field) for field in ('id', 'name', 'plan_type', 'model_ids')} for plan in plans]
        return {'items': items, 'plans': public_plans, 'source': '本项目手工配置', 'error': None}
    except ProviderError as exc:
        return {'items': [], 'plans': [], 'source': '本项目手工配置', 'error': str(exc)}


def select_model(image=False, model_id=None):
    rows, plans = _config()
    capability = '图片' if image else '文本'
    available = {row['id']: row for row in rows if row['capability'] == capability}
    if model_id:
        selected = [int(model_id)]
    else:
        kinds = ('图片生成', '图片精修') if image else ('商品文案', '通用')
        plan = next((p for kind in kinds for p in plans if p['plan_type'] == kind), None)
        selected = plan['model_ids'] if plan else []
    for ident in selected:
        row = available.get(ident)
        if not row:
            continue
        if _is_codex(row):
            if os.environ.get('STUDIO_CODEX_BRIDGE_SOURCE'):
                return row, ''
        else:
            key = _key(row)
            if key:
                return row, key
    raise ProviderError(f'请手工配置{capability}模型、对应凭据，并在任务分工中选择模型或设置自己的执行方案。')


def _request_ref(model, prompt, paths):
    return {'id': str(uuid.uuid4()), 'model_id': model['id'], 'model': model['model_identifier'],
            'protocol': model['protocol'], 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'references': [{'name': Path(p).name, 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in paths]}


def _image_content(path):
    with Image.open(path) as image:
        image = image.convert('RGB')
        image.thumbnail((1600, 1600))
        stream = io.BytesIO()
        image.save(stream, 'JPEG', quality=90)
    return base64.b64encode(stream.getvalue()).decode()


def _check(response):
    if response.is_success:
        return
    raise ProviderError(f'模型服务返回 HTTP {response.status_code}。', unknown=response.status_code in (408, 504))


def _endpoint(base, suffix):
    result = suffix if suffix.startswith(('http://', 'https://')) else base.rstrip('/') + '/' + suffix.lstrip('/')
    parsed = urlparse(result)
    if not parsed.hostname or (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost'))):
        raise ProviderError('模型地址必须使用 HTTPS，本机代理可使用 HTTP。')
    if parsed.username or parsed.password:
        raise ProviderError('模型地址不能嵌入凭据。')
    return result


def text(prompt, paths=(), *, model_id=None, checkpoint=lambda _: None):
    model, key = select_model(False, model_id)
    paths = [Path(p) for p in paths]
    ref = _request_ref(model, prompt, paths)
    checkpoint(ref)
    if _is_codex(model):
        return _child('codex', {'image': False, 'prompt': prompt, 'model': model['model_identifier'],
                              'paths': [str(p) for p in paths]}, checkpoint)['text']
    protocol = model['protocol']
    images = [_image_content(p) for p in paths]
    headers = {'Authorization': 'Bearer ' + key}
    if protocol == 'anthropic':
        endpoint = _endpoint(model['base_url'], '/messages')
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'}
        body = {'model': model['model_identifier'], 'max_tokens': 10000, 'messages': [{'role': 'user', 'content':
                [{'type': 'text', 'text': prompt}] + [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': b}} for b in images]}]}
    elif protocol == 'responses':
        endpoint = _endpoint(model['base_url'], '/responses')
        body = {'model': model['model_identifier'], 'max_output_tokens': 10000, 'input': [{'role': 'user', 'content':
                [{'type': 'input_text', 'text': prompt}] + [{'type': 'input_image', 'image_url': 'data:image/jpeg;base64,' + b} for b in images]}]}
    else:
        endpoint = _endpoint(model['base_url'], '/chat/completions')
        body = {'model': model['model_identifier'], 'max_tokens': 10000, 'messages': [{'role': 'user', 'content':
                [{'type': 'text', 'text': prompt}] + [{'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + b}} for b in images]}]}
    try:
        response = httpx.post(endpoint, trust_env=False, headers=headers, json=body, timeout=300)
        _check(response)
        data = response.json()
        if data.get('status') == 'incomplete' or data.get('stop_reason') == 'max_tokens':
            raise ProviderError('文本模型输出被截断；已保存失败，不自动重复调用。')
        if protocol == 'anthropic':
            value = ''.join(x.get('text', '') for x in data.get('content', []) if x.get('type') == 'text')
        elif protocol == 'responses':
            value = data.get('output_text') or ''.join(c.get('text', '') for item in data.get('output', []) for c in item.get('content', []) if c.get('type') == 'output_text')
        else:
            choice = data.get('choices', [{}])[0]
            if choice.get('finish_reason') == 'length':
                raise ProviderError('文本模型输出被截断。')
            value = choice.get('message', {}).get('content', '')
        if not isinstance(value, str) or not value.strip():
            raise ProviderError('文本服务未返回可用正文。')
        return value
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise ProviderError('文本模型连接失败，请核对连接后接续。') from None
    except httpx.RequestError:
        raise ProviderError('文本请求连接中断，结果未知；没有重新发送。', unknown=True, request_ref=ref) from None
    except (ValueError, KeyError, IndexError):
        raise ProviderError('文本请求返回格式损坏，原请求结果未知。', unknown=True, request_ref=ref) from None


def image(prompt, paths=(), *, size='1536x1024', model_id=None, checkpoint=lambda _: None):
    model, key = select_model(True, model_id)
    paths = [Path(p) for p in paths]
    ref = _request_ref(model, prompt, paths) | {'size': size}
    checkpoint(ref)
    if _is_codex(model):
        result = _child('codex', {'image': True, 'prompt': prompt, 'model': model['model_identifier'],
                                'paths': [str(p) for p in paths]}, checkpoint)
        return base64.b64decode(result['image'])
    endpoint = _endpoint(model['base_url'], (model.get('image_edit_endpoint') or '/images/edits') if paths else (model.get('image_generation_endpoint') or '/images/generations'))
    params = {'model': model['model_identifier'], 'prompt': prompt, 'n': 1, 'size': size}
    try:
        if paths:
            files = [('image' if len(paths) == 1 else 'image[]', (p.name, p.read_bytes(), 'application/octet-stream')) for p in paths]
            response = httpx.post(endpoint, trust_env=False, headers={'Authorization': 'Bearer ' + key}, data=params, files=files, timeout=420)
        else:
            response = httpx.post(endpoint, trust_env=False, headers={'Authorization': 'Bearer ' + key}, json=params, timeout=420)
        _check(response)
        data = response.json()
        if data.get('error'):
            raise ProviderError('图片服务明确返回失败；已停止该步骤。')
        item = data['data'][0]
        if item.get('b64_json'):
            content = base64.b64decode(item['b64_json'], validate=True)
        elif item.get('url'):
            # Keep the returned handle in the protected job checkpoint. Public
            # job views must redact URLs with query strings.
            checkpoint({'download_url': item['url']})
            content = download_image(item['url'])
        else:
            raise ProviderError('图片服务已返回但没有可恢复的图片结果。', unknown=True)
        with Image.open(io.BytesIO(content)) as generated:
            generated.verify()
        return content
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise ProviderError('图片模型连接失败；未收到响应。') from None
    except httpx.RequestError:
        raise ProviderError('图片请求已发出但结果未知；不会自动再次生成。', unknown=True, request_ref=ref) from None
    except (ValueError, KeyError, IndexError, OSError):
        raise ProviderError('图片请求已返回但结果无法读取；不会自动再次生成。', unknown=True, request_ref=ref) from None


def download_image(url):
    # Research URL validator checks DNS/public addresses on every redirect.
    from urllib.parse import urljoin
    import ipaddress
    import socket
    for _ in range(6):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ProviderError('图片下载地址不符合 HTTPS 公网要求。', unknown=True)
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ProviderError('图片下载地址解析到非公网地址。', unknown=True)
        response = httpx.get(url, trust_env=False, follow_redirects=False, timeout=120)
        if response.is_redirect:
            url = urljoin(url, response.headers.get('location', ''))
            continue
        _check(response)
        if len(response.content) > 64 * 1024 * 1024:
            raise ProviderError('图片超过 64 MB 接收上限。', unknown=True)
        return response.content
    raise ProviderError('图片下载重定向次数过多。', unknown=True)


def research(query, platform, *, hotspot=False, limit=6, urls=None, checkpoint=lambda _: None):
    if platform not in PLATFORMS and not (urls and platform=='公开网页'):
        raise ProviderError('尚未配置该来源平台：' + str(platform))
    return _child('research', {'query': query, 'domains': [] if hotspot or platform=='公开网页' else PLATFORMS[platform],
                              'hotspot': hotspot, 'limit': min(20, max(1, int(limit))),'urls':urls or []},checkpoint)


def reconcile(reference,*,is_image,paths):
    return _child('reconcile',{'reference':reference,'is_image':is_image,'paths':[str(p) for p in paths]})


def _child(action, payload, checkpoint=lambda _: None):
    keep = {'PATH', 'PATHEXT', 'WINDIR', 'SYSTEMROOT', 'SYSTEMDRIVE', 'COMSPEC', 'PROGRAMDATA',
            'PROGRAMFILES', 'PROGRAMFILES(X86)', 'USERPROFILE', 'USERNAME', 'USER', 'HOME',
            'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LANGUAGE', 'TERM',
            'STUDIO_CODEX_BRIDGE_SOURCE', 'STUDIO_RESEARCH_SOURCE', 'ECOMMERCE1_DATA_DIR'}
    env = {name: value for name, value in os.environ.items() if name.upper() in keep or name.upper().startswith('LC_')}
    env['CODEX_HOME'] = str(ROOT / 'data' / 'official-client')
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    env['PYTHONPATH'] = str(ROOT)
    process = subprocess.Popen([sys.executable, '-B', str(Path(__file__)), action], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                               cwd=ROOT, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    process.stdin.write(json.dumps(payload, ensure_ascii=False))
    process.stdin.close()
    result = None
    submitted=False
    for line in process.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if 'checkpoint' in event:
            submitted=True
            checkpoint(event['checkpoint'])
        if 'result' in event:
            result = event['result']
        if 'error' in event:
            process.wait(timeout=20)
            raise ProviderError(event['error'], unknown=event.get('unknown', False),
                request_ref={'provider_failure':event['failure']} if event.get('failure') else None)
    process.wait(timeout=20)
    if result is None:
        raise ProviderError('适配进程未返回结果。' + (' 已发出的模型请求视为结果未知。' if action == 'codex' or submitted else ''), unknown=action == 'codex' or submitted)
    return result


def codex_failure(exc):
    """Return only known diagnostic classes, never raw provider URLs/tokens."""
    reason=str(exc);lower=reason.casefold();category='unclassified'
    for key,markers in (
        ('timeout',('任务执行超时','请求超时','timed out','timeout')),
        ('interrupted',('任务状态：interrupted','任务结束：interrupted','turn interrupted')),
        ('transport_exit',('app-server 已退出','app-server 当前不可用','无法向 codex app-server')),
        ('transport_stream',('stream disconnected','connection reset','connection closed','unexpected eof')),
        ('empty_output',('没有返回文本结果',)),
        ('input_contract',('图片工具输入证据','输入合同','input contract'))):
        if any(marker in lower for marker in markers):category=key;break
    status=re.search(r'(?:http(?: error)?|status(?: code)?)\s*[:=]?\s*(401|403|429|500|502|503|504)\b',lower)
    if status:category={'401':'authentication','403':'access_denied','429':'rate_limit'}.get(status[1],'provider_unavailable')
    dependency=getattr(exc,'name',None) if isinstance(exc,ModuleNotFoundError) else None
    if dependency and re.fullmatch(r'[A-Za-z0-9_.]+',dependency):category='missing_dependency'
    unknown=bool(getattr(exc,'unknown',False)) or getattr(exc,'failure_kind','')!='invalid'
    if not unknown:category='invalid_request'
    labels={'timeout':'等待原请求超时','interrupted':'原请求已中断','transport_exit':'模型适配进程退出或连接不可用',
        'transport_stream':'模型响应流中断','empty_output':'原请求未返回可用文本','input_contract':'原图输入依据未核对通过',
        'authentication':'模型身份认证失败','access_denied':'模型访问被拒绝','rate_limit':'模型服务限流',
        'provider_unavailable':'模型服务暂不可用','invalid_request':'模型请求未被接受','unclassified':'模型请求返回异常',
        'missing_dependency':'模型适配运行依赖缺失'+('（'+dependency+'）' if dependency else '')}
    message=labels[category]+('，结果待核对；未自动重发' if unknown else '；未自动重发')+'：'+type(exc).__name__
    return {'category':category,'exception':type(exc).__name__,'interrupted':bool(getattr(exc,'interrupted',False)),'unknown':unknown},message


def _child_main():
    action = sys.argv[1]
    payload = json.load(sys.stdin)
    def emit(value):
        print(json.dumps(value, ensure_ascii=False), flush=True)
    bridge = None
    try:
        if action == 'research':
            sys.path.insert(0,str(ROOT))
            from studio.research_adapter import search
            result=search(payload['query'],payload['domains'],hotspot=payload['hotspot'],limit=payload['limit'],urls=payload.get('urls',[]),
                checkpoint=lambda meta:emit({'checkpoint':meta}))
            emit({'result':result})
        elif action in ('codex','reconcile'):
            bridge_root = os.environ.get('STUDIO_CODEX_BRIDGE_SOURCE', '').strip()
            if not bridge_root or not (Path(bridge_root) / 'app/codex_bridge.py').is_file():
                raise ProviderError('请设置 STUDIO_CODEX_BRIDGE_SOURCE 指向已安装的官方客户端适配器源码。')
            sys.path.insert(0, str(Path(bridge_root).resolve()))
            from app.codex_bridge import get_codex_bridge
            bridge = get_codex_bridge(ROOT)
            paths = [Path(p) for p in payload['paths']]
            if action=='reconcile':
                sys.path.insert(0,str(ROOT))
                from studio.recovery import inspect_original
                emit({'result':inspect_original(bridge,payload['reference'],payload['is_image'],paths)})
                return
            if payload['image']:
                path = bridge.generate_image(payload['prompt'], payload['model'], paths, timeout=420,
                                             on_submitted=lambda meta: emit({'checkpoint': {'codex': meta}}))
                emit({'result': {'image': base64.b64encode(Path(path).read_bytes()).decode()}})
            else:
                turn=bridge.run_turn(payload['prompt'],payload['model'],images=paths,timeout=600,
                    on_submitted=lambda meta:emit({'checkpoint':{'codex':meta}}),
                    base_instructions='你是电商系统的结构化推理后端。不要修改文件，不要运行命令，不要调用外部工具。严格按用户要求输出结果，不添加解释性前后缀。')
                emit({'result':{'text':bridge.final_text(turn)}})
    except Exception as exc:
        # Exception strings from providers may contain signed URLs or keys. Only
        # safe class names are returned; frozen request metadata identifies work.
        if action in ('codex','reconcile'):
            failure,message=codex_failure(exc)
            emit({'error':message,'unknown':failure['unknown'],'failure':failure})
        else:
            message=str(exc) if type(exc).__name__=='ResearchFailure' else '来源适配执行失败：'+type(exc).__name__
            emit({'error':message,'unknown':bool(getattr(exc,'unknown',False))})
    finally:
        if bridge is not None:
            bridge.close()


if __name__ == '__main__':
    _child_main()
