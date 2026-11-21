"""Public-source research with observable provider attempts and verified page images.

Legacy search settings are read only. Public search attempts are observable;
unavailable search does not fall back to an unverified model answer.
Source text is data, never executable instructions. No supplier facts are sent.
"""
from __future__ import annotations
import base64
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlsplit

ROOT=Path(__file__).resolve().parents[1]
class ResearchFailure(RuntimeError):
    def __init__(self,message,unknown=False):
        super().__init__(message); self.unknown=unknown


class PageImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.urls=[]; self.ld=False; self.ld_text=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta' and (a.get('property') or a.get('name') or '').lower() in {'og:image','og:image:url','twitter:image'}:
            self.urls.append(a.get('content',''))
        if tag=='img' and a.get('id') in {'landingImage','imgBlkFront'}:
            self.urls.append(a.get('data-old-hires') or a.get('src',''))
        if tag=='script' and a.get('type')=='application/ld+json': self.ld=True; self.ld_text=[]
    def handle_data(self,data):
        if self.ld: self.ld_text.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.ld:
            self.ld=False
            try: self._images(json.loads(''.join(self.ld_text)))
            except (ValueError,TypeError): pass
    def _images(self,value):
        if isinstance(value,list):
            for item in value: self._images(item)
        elif isinstance(value,dict):
            kind=value.get('@type',[])
            if kind=='Product' or isinstance(kind,list) and 'Product' in kind:
                image=value.get('image',[])
                for item in image if isinstance(image,list) else [image]:
                    self.urls.append(item.get('url','') if isinstance(item,dict) else str(item))
            if '@graph' in value:self._images(value['@graph'])


def _service():
    source = os.environ.get('STUDIO_RESEARCH_SOURCE', '').strip()
    if not source or not (Path(source) / 'app/research_runtime.py').is_file():
        raise ResearchFailure('请设置 STUDIO_RESEARCH_SOURCE 指向已安装的公开网页研究模块。')
    sys.path.insert(0, str(Path(source).resolve()))
    from app.research_runtime import ResearchService, _ReadableHTML
    config = {'timeout_seconds': 12, 'request_attempts': 1,
              'search_duckduckgo_endpoint': 'https://html.duckduckgo.com/html/?q={query}'}
    service=ResearchService(None,{'research':config})
    return service,_ReadableHTML


def _scope(url,domains):
    p=urlsplit(str(url)); host=(p.hostname or '').lower()
    return p.scheme in {'http','https'} and bool(host) and not p.username and not p.password and (not domains or any(host==d or host.endswith('.'+d) for d in domains))


def _page(service,reader,url):
    images=[]
    try:
        final,mime,raw,truncated=service._get(url,allow_truncate=True)
        if not any(t in mime.lower() for t in ('html','text/plain','xml')): raise ResearchFailure('source_not_text')
        match=re.search(r'charset=([\w.-]+)',mime,re.I)
        try: content=raw.decode(match.group(1) if match else 'utf-8',errors='replace')
        except LookupError: content=raw.decode('utf-8',errors='replace')
        parser=reader();parser.feed(content);title,text=parser.result()
        image_parser=PageImages();image_parser.feed(content)
        images=[urljoin(final,value) for value in image_parser.urls if value]
        page={'url':url,'resolved_url':final,'title':title,'text':text,'fetched_via':'direct','truncated':truncated}
    except Exception:
        page=service.read_page(url)
        images=re.findall(r'!\[[^\]]*\]\((https?://[^\s)]+)\)',page['text'])
    if len(page.get('text','').strip())<120 or any(marker in (page.get('title','')+' '+page.get('text','')[:1000]).lower() for marker in ('robot check','verify you are human','enter the characters you see below','access denied')):
        raise ResearchFailure('页面未返回可用正文，可能为登录、访问限制或验证页')
    return page,list(dict.fromkeys(images))[:8]


def _thumbnail(service,urls):
    from PIL import Image
    last='来源页没有可验证的产品图片地址'
    prior=service.max_response_bytes;service.max_response_bytes=4_000_000
    try:
        for url in urls[:3]:
            if not _scope(url,[]):continue
            try:
                final,mime,raw,truncated=service._get(url)
                if truncated:raise ValueError('truncated')
                with Image.open(io.BytesIO(raw)) as image:
                    width,height=image.size
                    if min(width,height)<160 or width*height>25_000_000:raise ValueError('not_product_resolution')
                    detected=Image.MIME.get(image.format,mime);image.verify()
                return {'url':final,'source_image_url':url,'mime':detected,'width':width,'height':height,'data':base64.b64encode(raw).decode()},None
            except Exception as exc:last='来源图未取得：'+type(exc).__name__
    finally:service.max_response_bytes=prior
    return None,last


def _is_detail(url,domains):
    host=(urlsplit(url).hostname or '').lower();path=urlsplit(url).path
    if 'amazon.com' in domains:return bool(re.search(r'/(?:dp|gp/product)/[A-Za-z0-9]{10}',path))
    if 'alibaba.com' in domains:return '/product-detail/' in path or '/pla/' in path
    if 'aliexpress.com' in domains:return '/item/' in path
    if 'temu.com' in domains:return bool(re.search(r'[-/]g-\d+|goods_id=\d+',url))
    return True


def _public_search(service,query,domains,limit,attempts):
    from urllib.parse import quote_plus
    from app.research_runtime import parse_duckduckgo_results,filter_search_results,filter_relevant_results,normalize_search_query
    effective=normalize_search_query(query,domains)
    queries=[effective]
    path_hint=next((hint for domain,hint in [('amazon.com','inurl:dp'),('alibaba.com','inurl:product-detail'),('aliexpress.com','inurl:item')] if domain in domains),None)
    if path_hint:queries.append(effective+' '+path_hint)
    directories=[]
    for actual in queries:
        endpoint=service.search_duckduckgo_endpoint.format(query=quote_plus(actual),offset=0)
        try:
            _,_,raw,_=service._get(endpoint,allow_truncate=True)
            html=raw.decode('utf-8',errors='replace')
            if 'bots use DuckDuckGo' in html or 'challenge-form' in html:
                attempts.append({'provider':'duckduckgo_html','query':actual,'outcome':'blocked','error':'搜索服务要求人工验证；未尝试绕过'})
                service.search_duckduckgo_endpoint=''
                break
            results=filter_relevant_results(filter_search_results(parse_duckduckgo_results(html),domains),query)
            details=[r for r in results if _is_detail(r['url'],domains)]
            directories.extend(r for r in results if not _is_detail(r['url'],domains))
            attempts.append({'provider':'duckduckgo_html','query':actual,'outcome':'success' if details else 'empty','scoped_count':len(results),'detail_count':len(details)})
            if details:return {'results':details[:limit],'provider':'duckduckgo_html','effective_query':actual}
        except Exception as exc:attempts.append({'provider':'duckduckgo_html','query':actual,'outcome':'failure','error':str(exc)[:180]})
    # Search engines often index a marketplace's public category pages instead
    # of individual products. Follow only observed in-scope links from those
    # pages; no ASIN or product URL is guessed.
    from app.research_runtime import _ReadableHTML
    seen=set();products={}
    for directory in directories:
        parent=directory['url']
        if parent in seen:continue
        seen.add(parent)
        try:
            final,_,raw,_=service._get(parent,allow_truncate=True)
            if not _scope(final,domains):continue
            parser=_ReadableHTML();parser.feed(raw.decode('utf-8',errors='replace'))
            for link in parser.links:
                url=urljoin(final,link['url']).split('#')[0];title=link.get('text','').strip()
                if not _scope(url,domains) or not _is_detail(url,domains) or len(title)<20:continue
                if 'amazon.com' in domains:
                    asin=re.search(r'/(?:dp|gp/product)/([A-Za-z0-9]{10})',urlsplit(url).path)
                    url='https://www.amazon.com/dp/'+asin.group(1)
                if url not in products or len(title)>len(products[url]['title']):products[url]={'url':url,'title':title,'snippet':'','discovered_from':parent}
            attempts.append({'provider':'marketplace_public_links','parent_url':parent,'outcome':'success' if products else 'empty','detail_count':len(products)})
            if products:return {'results':list(products.values())[:limit],'provider':'duckduckgo_html+marketplace_links','effective_query':effective}
        except Exception as exc:attempts.append({'provider':'marketplace_public_links','parent_url':parent,'outcome':'failure','error':str(exc)[:180]})
        if len(seen)>=2:break
    # The legacy general-purpose search relaxer appends event/recap/attendees.
    # Product research must preserve its query, so call these parsers directly.
    from app.research_runtime import parse_brave_results,_BingResultsHTML
    import xml.etree.ElementTree as ET
    endpoints=[('brave_html',service.search_brave_endpoint),('bing_html',service.search_html_endpoint),('bing_rss',service.search_endpoint)]
    for provider,template in endpoints:
        try:
            endpoint=template.format(query=quote_plus(effective),offset=0,first=1)
            _,_,raw,_=service._get(endpoint,allow_truncate=True)
            text=raw.decode('utf-8',errors='replace')
            if provider=='brave_html':results=parse_brave_results(text)
            elif provider=='bing_html':
                parser=_BingResultsHTML();parser.feed(text);results=parser.results
            else:
                parsed=ET.fromstring(raw)
                results=[{'url':row.findtext('link') or '', 'title':row.findtext('title') or '', 'snippet':re.sub('<[^>]+>',' ',row.findtext('description') or '')[:1000]} for row in parsed.findall('.//item')]
            results=filter_relevant_results(filter_search_results(results,domains),query)
            results=[r for r in results if _is_detail(r['url'],domains)]
            attempts.append({'provider':provider,'query':effective,'outcome':'success' if results else 'empty','detail_count':len(results)})
            if results:return {'results':results[:limit],'provider':provider,'effective_query':effective}
        except Exception as exc:attempts.append({'provider':provider,'query':effective,'outcome':'failure','error':str(exc)[:180]})
    return {'results':[],'provider':'public_search','effective_query':effective}


def search(query,domains,hotspot=False,limit=6,checkpoint=lambda _:None,urls=None):
    service,reader=_service();attempts=[]
    service._record_provider_health=lambda name,**values:attempts.append({'provider':name,**values})
    limit=min(20,max(1,int(limit)));trace=[]
    if urls:
        candidates=[{'url':url} for url in urls[:limit]];provider='direct_url';actual_query=query
    else:
        try:
            direct=service.search(query,max_results=limit,domains=[],research_role='live_event') if hotspot else _public_search(service,query,domains,limit,attempts)
            candidates=[r for r in (direct.get('results') or []) if hotspot or _is_detail(r['url'],domains)]
            provider=direct.get('provider');actual_query=direct.get('relaxed_query') or direct.get('effective_query') or query
        except Exception as exc:
            candidates=[];provider='public_search';actual_query=query;attempts.append({'provider':provider,'outcome':'failure','error':type(exc).__name__})
    items=[];seen=set()
    for candidate in candidates:
        url=str(candidate.get('url') or '').split('#')[0]
        if len(url)>2000:
            attempts.append({'provider':'source_reader','outcome':'rejected','error':'来源地址超过2000字，未截断保存；请使用原始公开页面链接'})
            continue
        if not _scope(url,domains) or url in seen:continue
        seen.add(url)
        item={'url':url,'title':str(candidate.get('title') or url)[:500],'snippet':str(candidate.get('snippet') or '')[:1000],
            'published_at':candidate.get('published_at'),'body':'','access_status':'unavailable' if urls else 'search_only','image_status':'unavailable','acquisition':'direct_url' if urls else 'automatic_search'}
        if candidate.get('discovered_from'):item['discovered_from']=candidate['discovered_from']
        try:
            page,image_urls=_page(service,reader,url)
            if not _scope(page['resolved_url'],domains):raise ResearchFailure('来源跳转出所选平台范围')
            item.update(body=page['text'][:12000],resolved_url=page['resolved_url'],access_status='read',page_reader=page['fetched_via'],image_urls=image_urls)
            if page.get('title'):item['title']=page['title'][:500]
            thumbnail,error=_thumbnail(service,image_urls)
            if thumbnail:item.update(thumbnail=thumbnail,image_status='verified_source_image')
            else:item['image_error']=error
        except Exception as exc:item['access_error']=str(exc)[:180]
        items.append(item)
        if len(items)>=limit:break
    return {'items':items,'provider':provider,'query':actual_query,'attempts':attempts,'research_trace':trace,
        'scope':'public_source_observation','limitations':['搜索摘要与页面正文分别标记；缺图片不伪造占位图。']}

