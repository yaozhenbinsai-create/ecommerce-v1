"""Stream a consistent MySQL snapshot and immutable files; restore only to new targets."""
from __future__ import annotations
import argparse
import base64
from datetime import datetime,date,timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import uuid

import pymysql
from fastapi import APIRouter
from fastapi.responses import FileResponse
from . import db

FORMAT='studio-backup-v1'
ID_PATTERN=re.compile(r'^\d{8}T\d{6}-[a-f0-9]{8}$')
router=APIRouter(prefix='/api/backups')


def now():return datetime.now(timezone.utc).isoformat()
def identifier(value):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',str(value)):raise ValueError('Invalid SQL identity')
    return '`'+str(value)+'`'
def encode(value):
    if isinstance(value,datetime):return {'$type':'datetime','value':value.isoformat(sep=' ')}
    if isinstance(value,date):return {'$type':'date','value':value.isoformat()}
    if isinstance(value,Decimal):return {'$type':'decimal','value':str(value)}
    if isinstance(value,bytes):return {'$type':'bytes','value':base64.b64encode(value).decode()}
    raise TypeError(type(value).__name__)
def decode(value):
    if not isinstance(value,dict) or set(value)!={'$type','value'}:return value
    if value['$type']=='datetime':return datetime.fromisoformat(value['value'])
    if value['$type']=='date':return date.fromisoformat(value['value'])
    if value['$type']=='decimal':return Decimal(value['value'])
    if value['$type']=='bytes':return base64.b64decode(value['value'],validate=True)
    raise ValueError('Unsupported backed-up value type')
def safe_error(exc):
    if isinstance(exc,pymysql.MySQLError):return type(exc).__name__+' code='+str(exc.args[0] if exc.args else 'unknown')
    return type(exc).__name__+': '+str(exc)[:250]
def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('w',encoding='utf-8',newline='\n') as out:
        json.dump(value,out,ensure_ascii=False,indent=2,default=encode);out.flush();os.fsync(out.fileno())
    temporary.replace(path)
def digest_file(path):
    digest=hashlib.sha256();size=0
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):digest.update(block);size+=len(block)
    return digest.hexdigest(),size
def rooted(path,root,must_exist=True):
    root=root.resolve();path=Path(path)
    if not path.is_absolute():path=root/path
    resolved=path.resolve()
    if resolved==root or not resolved.is_relative_to(root):raise ValueError('Path is outside the dedicated project directory')
    if must_exist and not resolved.exists():raise ValueError('Expected backup path is missing')
    current=path
    while current!=root and current!=current.parent:
        if current.exists() and (current.is_symlink() or getattr(current,'is_junction',lambda:False)()):raise ValueError('Links and junctions are not backup inputs')
        current=current.parent
    return resolved
def backup_dir(backup_id):
    if not ID_PATTERN.fullmatch(str(backup_id)):raise ValueError('Invalid backup id')
    return rooted(db.DATA/'backups'/backup_id,db.DATA,must_exist=False)
def _credentials():
    from run import DATA as runtime_data
    return json.loads((runtime_data/'local-credentials.json').read_text(encoding='utf-8'))
def _admin():
    if os.getenv('STUDIO_DB_HOST','127.0.0.1')!='127.0.0.1':raise ValueError('Backup requires this project local MySQL instance')
    from run import admin
    return admin(int(os.getenv('STUDIO_DB_PORT','13320')),_credentials()['root'])
def _tables(connection):
    with connection.cursor() as cur:
        cur.execute('SHOW FULL TABLES WHERE Table_type=%s',('BASE TABLE',))
        return sorted(next(iter(row.values())) for row in cur.fetchall())


def create_snapshot(backup_id=None,progress=None):
    """No buffered SELECT of table data; the transaction remains open through export."""
    if not db.SCHEMA.startswith('studio_'):raise ValueError('Only project studio schemas can be backed up')
    with _admin() as check,check.cursor() as cur:
        cur.execute('SELECT @@server_uuid');server_uuid=cur.fetchone()[0]
    backup_id=backup_id or datetime.now().strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    target=backup_dir(backup_id)
    if target.exists():raise ValueError('Backup directory already exists; existing snapshots are never overwritten')
    target.mkdir(parents=True)
    state={'id':backup_id,'status':'running','created_at':now(),'source_schema':db.SCHEMA,'directory':str(target)}
    atomic_json(target/'state.json',state)
    manifest={'format':FORMAT,'id':backup_id,'created_at':state['created_at'],'source_schema':db.SCHEMA,'server_uuid':server_uuid,
        'consistency':'InnoDB REPEATABLE READ WITH CONSISTENT SNAPSHOT','tables':[],'assets':0,'recovery_files':0,'rows':0,
        'restore_policy':'New schema and new data directory only; queued/running jobs become unknown and are never auto-dispatched.'}
    conn=None
    try:
        conn=db.connect()
        with conn.cursor() as cur:
            cur.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            cur.execute('SELECT TABLE_NAME,ENGINE FROM information_schema.tables WHERE table_schema=%s',(db.SCHEMA,))
            if any(r['ENGINE']!='InnoDB' for r in cur.fetchall()):raise ValueError('All source tables must be InnoDB')
        tables=_tables(conn);(target/'tables').mkdir();(target/'assets').mkdir()
        with (target/'assets.jsonl').open('w',encoding='utf-8',newline='\n') as catalog:
            for index,table in enumerate(tables):
                with conn.cursor() as cur:
                    cur.execute('SHOW CREATE TABLE '+identifier(table));ddl=next(v for k,v in cur.fetchone().items() if k.lower().startswith('create '))
                    cur.execute('SHOW KEYS FROM '+identifier(table)+" WHERE Key_name='PRIMARY'");keys=sorted(cur.fetchall(),key=lambda x:x['Seq_in_index'])
                ddl_path=target/'tables'/(table+'.sql');ddl_path.write_text(ddl,encoding='utf-8')
                data_path=target/'tables'/(table+'.jsonl');count=0
                order=' ORDER BY '+','.join(identifier(x['Column_name']) for x in keys) if keys else ''
                with conn.cursor(pymysql.cursors.SSDictCursor) as stream,data_path.open('w',encoding='utf-8',newline='\n') as out:
                    stream.execute('SELECT * FROM '+identifier(table)+order)
                    for row in stream:
                        out.write(json.dumps(row,ensure_ascii=False,separators=(',',':'),default=encode)+'\n');count+=1
                        if progress and count%100==0:progress(index,len(tables),table)
                        if table=='assets':
                            filename=str(row['sha256'])+'.'+str(row['extension'])
                            source=rooted(db.DATA/'assets'/filename,db.DATA/'assets');destination=target/'assets'/filename
                            shutil.copyfile(source,destination)
                            actual,size=digest_file(destination)
                            if actual!=row['sha256'] or size!=row['byte_size']:raise ValueError('Asset checksum/size differs from snapshot: '+filename)
                            catalog.write(json.dumps({'asset_id':row['id'],'file':'assets/'+filename,'sha256':actual,'bytes':size},separators=(',',':'))+'\n')
                            manifest['assets']+=1
                dh,ds=digest_file(data_path);sh,ss=digest_file(ddl_path)
                manifest['tables'].append({'name':table,'rows':count,'data_file':'tables/'+table+'.jsonl','data_sha256':dh,'data_bytes':ds,'ddl_file':'tables/'+table+'.sql','ddl_sha256':sh,'ddl_bytes':ss})
                manifest['rows']+=count
                if progress:progress(index+1,len(tables),table)
        if _tables(conn)!=tables:raise ValueError('Schema changed while backup was running; snapshot not published')
        conn.commit();conn.close();conn=None
        # Final engine checkpoints are atomically published and then immutable.
        # In-flight *.tmp files are omitted; restored jobs remain stopped.
        with (target/'recovery.jsonl').open('w',encoding='utf-8',newline='\n') as catalog:
            checkpoint_root=db.DATA/'checkpoints'
            if checkpoint_root.is_dir():
                for base,dirs,files in os.walk(checkpoint_root,followlinks=False):
                    dirs[:]=[x for x in dirs if not (Path(base)/x).is_symlink() and not getattr(Path(base)/x,'is_junction',lambda:False)()]
                    for name in files:
                        if name.endswith('.tmp'):continue
                        source=rooted(Path(base)/name,checkpoint_root);relative=source.relative_to(db.DATA)
                        destination=target/'recovery'/relative;destination.parent.mkdir(parents=True,exist_ok=True)
                        try:shutil.copyfile(source,destination)
                        except FileNotFoundError:continue
                        digest,size=digest_file(destination)
                        catalog.write(json.dumps({'file':destination.relative_to(target).as_posix(),'restore_file':relative.as_posix(),'sha256':digest,'bytes':size},separators=(',',':'))+'\n');manifest['recovery_files']+=1
        for name in ('assets','recovery'):
            h,size=digest_file(target/(name+'.jsonl'));manifest[name+'_catalog']={'file':name+'.jsonl','sha256':h,'bytes':size}
        manifest['completed_at']=now();manifest['table_count']=len(tables)
        # Payload excludes bookkeeping; the live directory size is reported
        # separately because verification reports are created after completion.
        manifest['payload_bytes']=sum(p.stat().st_size for p in target.rglob('*') if p.is_file() and p.name!='state.json')
        atomic_json(target/'manifest.json',manifest)
        manifest_hash,_=digest_file(target/'manifest.json');(target/'manifest.sha256').write_text(manifest_hash,encoding='ascii')
        state.update(status='complete',completed_at=manifest['completed_at'],table_count=len(tables),rows=manifest['rows'],assets=manifest['assets'],payload_bytes=manifest['payload_bytes'],total_bytes=0)
        atomic_json(target/'state.json',state)
        for _ in range(4):
            size=sum(p.stat().st_size for p in target.rglob('*') if p.is_file())
            if state['total_bytes']==size:break
            state['total_bytes']=size;atomic_json(target/'state.json',state)
        return state
    except BaseException as exc:
        if conn:
            try:conn.rollback()
            finally:conn.close()
        state.update(status='cancelled' if type(exc).__name__=='Cancelled' else 'failed',error=safe_error(exc));atomic_json(target/'state.json',state)
        raise


def verify_snapshot(directory):
    target=rooted(Path(directory),db.DATA);errors=[];error_count=0;checked=checked_bytes=0
    def error(message):
        nonlocal error_count
        error_count+=1
        if len(errors)<30:errors.append(message)
    manifest_path=rooted(target/'manifest.json',target)
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('format')!=FORMAT:raise ValueError('Unsupported backup format')
    mh,ms=digest_file(manifest_path)
    if (target/'manifest.sha256').read_text(encoding='ascii').strip()!=mh:error('Manifest checksum mismatch')
    checked+=1;checked_bytes+=ms
    def check(relative,expected,size=None):
        nonlocal checked,checked_bytes
        try:
            path=rooted(relative,target);actual,count=digest_file(path);checked+=1;checked_bytes+=count
            if actual!=expected or size is not None and count!=size:error('Checksum/size mismatch: '+relative)
            return path
        except (OSError,ValueError) as exc:error('File validation failed: '+str(relative));return None
    for table in manifest['tables']:
        identifier(table['name']);check(table['ddl_file'],table['ddl_sha256'],table['ddl_bytes'])
        path=check(table['data_file'],table['data_sha256'],table['data_bytes'])
        if path:
            count=0
            try:
                with path.open(encoding='utf-8') as rows:
                    for line in rows:
                        if not isinstance(json.loads(line),dict):raise ValueError('row shape')
                        count+=1
                if count!=table['rows']:error('Row count mismatch: '+table['name'])
            except (ValueError,OSError):error('Invalid row stream: '+table['name'])
    for name in ('assets','recovery'):
        item=manifest[name+'_catalog'];path=check(item['file'],item['sha256'],item['bytes'])
        if path:
            with path.open(encoding='utf-8') as ledger:
                for line in ledger:
                    entry=json.loads(line);check(entry['file'],entry['sha256'],entry['bytes'])
    result={'ok':error_count==0,'verified_at':now(),'checked_files':checked,'checked_bytes':checked_bytes,'errors':errors,'error_count':error_count,'manifest_sha256':mh}
    atomic_json(target/'verification.json',result)
    return result


def restore_snapshot(directory,schema,data_directory):
    if not re.fullmatch(r'studio_restore_[a-z0-9_]{1,40}',str(schema)):raise ValueError('Restore schema must be a NEW studio_restore_* name')
    source=rooted(Path(directory),db.DATA);verified=verify_snapshot(source)
    if not verified['ok']:raise ValueError('Backup verification failed; no restore target was created')
    target=rooted(Path(data_directory),db.DATA/'restores',must_exist=False)
    if target.exists():raise ValueError('Restore directory already exists; refusing to overwrite')
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    user='restore_'+uuid.uuid4().hex[:16];password=secrets.token_hex(24);created_user=False;conn=None
    report={'status':'running','created_at':now(),'source_backup':str(source),'manifest_sha256':verified['manifest_sha256'],'schema':schema,'data_directory':str(target),'counts':{},'table_checksums':{},'foreign_key_checks':[],'json_reference_checks':{},'paused_jobs':0}
    with _admin() as admin,admin.cursor() as cur:
        cur.execute('SELECT SCHEMA_NAME FROM information_schema.schemata WHERE SCHEMA_NAME=%s',(schema,))
        if cur.fetchone():raise ValueError('Restore schema already exists; refusing to overwrite')
        target.mkdir(parents=True)
        try:
            cur.execute('CREATE DATABASE '+identifier(schema)+' CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
            cur.execute('CREATE USER %s@%s IDENTIFIED BY %s',(user,'127.0.0.1',password));created_user=True
            cur.execute('GRANT SELECT,INSERT,UPDATE,CREATE,INDEX,REFERENCES ON '+identifier(schema)+'.* TO %s@%s',(user,'127.0.0.1'))
            conn=pymysql.connect(host='127.0.0.1',port=int(os.getenv('STUDIO_DB_PORT','13320')),user=user,password=password,database=schema,charset='utf8mb4',cursorclass=pymysql.cursors.DictCursor,autocommit=False)
            with conn.cursor() as dest:
                dest.execute('SET FOREIGN_KEY_CHECKS=0')
                for table in manifest['tables']:
                    ddl=rooted(table['ddl_file'],source).read_text(encoding='utf-8')
                    if not re.match(r'^CREATE TABLE\s+`'+re.escape(table['name'])+r'`\s*\(',ddl,re.I):raise ValueError('Unexpected table definition')
                    dest.execute(ddl)
                for table in manifest['tables']:
                    batch=[];batch_bytes=0;columns=None
                    with rooted(table['data_file'],source).open(encoding='utf-8') as rows:
                        for line in rows:
                            values=json.loads(line);keys=list(values)
                            if columns is None:columns=keys
                            if keys!=columns:raise ValueError('Row columns changed within '+table['name'])
                            batch.append(tuple(decode(values[k]) for k in columns));batch_bytes+=len(line)
                            if len(batch)>=100 or batch_bytes>=1024*1024:
                                dest.executemany('INSERT INTO '+identifier(table['name'])+' ('+','.join(identifier(k) for k in columns)+') VALUES ('+','.join(['%s']*len(columns))+')',batch);batch=[];batch_bytes=0
                        if batch:dest.executemany('INSERT INTO '+identifier(table['name'])+' ('+','.join(identifier(k) for k in columns)+') VALUES ('+','.join(['%s']*len(columns))+')',batch)
                    dest.execute('SELECT COUNT(*) n FROM '+identifier(table['name']));count=dest.fetchone()['n'];report['counts'][table['name']]=count
                    if count!=table['rows']:raise ValueError('Restored row count differs: '+table['name'])
                    conn.commit()
                    dest.execute('SHOW KEYS FROM '+identifier(table['name'])+" WHERE Key_name='PRIMARY'");keys=sorted(dest.fetchall(),key=lambda x:x['Seq_in_index'])
                    order=' ORDER BY '+','.join(identifier(x['Column_name']) for x in keys) if keys else ''
                    checksum=hashlib.sha256()
                    with conn.cursor(pymysql.cursors.SSDictCursor) as stream:
                        stream.execute('SELECT * FROM '+identifier(table['name'])+order)
                        for row in stream:checksum.update((json.dumps(row,ensure_ascii=False,separators=(',',':'),default=encode)+'\n').encode('utf-8'))
                    report['table_checksums'][table['name']]=checksum.hexdigest()
                    if checksum.hexdigest()!=table['data_sha256']:raise ValueError('Restored row values differ: '+table['name'])
                dest.execute('SET FOREIGN_KEY_CHECKS=1')
                dest.execute('SELECT TABLE_NAME,COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME,CONSTRAINT_NAME,ORDINAL_POSITION FROM information_schema.KEY_COLUMN_USAGE WHERE CONSTRAINT_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY TABLE_NAME,CONSTRAINT_NAME,ORDINAL_POSITION',(schema,))
                constraints={}
                for relation in dest.fetchall():constraints.setdefault((relation['TABLE_NAME'],relation['CONSTRAINT_NAME']),[]).append(relation)
                for (table,name),relations in constraints.items():
                    reference=relations[0]['REFERENCED_TABLE_NAME']
                    joined=' AND '.join('c.'+identifier(r['COLUMN_NAME'])+'=p.'+identifier(r['REFERENCED_COLUMN_NAME']) for r in relations)
                    populated=' AND '.join('c.'+identifier(r['COLUMN_NAME'])+' IS NOT NULL' for r in relations)
                    dest.execute('SELECT COUNT(*) n FROM '+identifier(table)+' c LEFT JOIN '+identifier(reference)+' p ON '+joined+' WHERE '+populated+' AND p.'+identifier(relations[0]['REFERENCED_COLUMN_NAME'])+' IS NULL')
                    missing=dest.fetchone()['n'];report['foreign_key_checks'].append({'table':table,'constraint':name,'missing':missing})
                    if missing:raise ValueError('Broken restored relation: '+table+'.'+name)
                checks={
                    'listing_image_assets':"SELECT COUNT(*) n FROM listings l JOIN JSON_TABLE(l.images,'$[*]' COLUMNS(aid BIGINT PATH '$.asset_id')) r LEFT JOIN assets a ON a.id=r.aid WHERE r.aid IS NOT NULL AND a.id IS NULL",
                    'design_sources':"SELECT COUNT(*) n FROM designs d JOIN JSON_TABLE(d.source_ids,'$[*]' COLUMNS(sid BIGINT PATH '$')) r LEFT JOIN sources s ON s.id=r.sid WHERE r.sid IS NOT NULL AND s.id IS NULL",
                    'source_image_assets':"SELECT COUNT(*) n FROM sources s LEFT JOIN assets a ON a.id=CAST(JSON_UNQUOTE(JSON_EXTRACT(s.meta,'$.image_asset_id')) AS UNSIGNED) WHERE JSON_EXTRACT(s.meta,'$.image_asset_id') IS NOT NULL AND JSON_TYPE(JSON_EXTRACT(s.meta,'$.image_asset_id'))!='NULL' AND a.id IS NULL"}
                for name,sql in checks.items():
                    dest.execute(sql);missing=dest.fetchone()['n'];report['json_reference_checks'][name]=missing
                    if missing:raise ValueError('Broken restored JSON relation: '+name)
                dest.execute("UPDATE jobs SET status='unknown',stage='从备份恢复，等待核对原任务',error='恢复实例不自动重发外部请求；须先核对原请求结果。' WHERE status IN ('queued','running')")
                report['paused_jobs']=dest.rowcount
                dest.execute("UPDATE designs d JOIN jobs j ON j.id=d.job_id SET d.status='unknown' WHERE j.status='unknown'")
                conn.commit()
            for name in ('assets','recovery'):
                with rooted(manifest[name+'_catalog']['file'],source).open(encoding='utf-8') as ledger:
                    for line in ledger:
                        entry=json.loads(line);relative=entry['file'] if name=='assets' else entry['restore_file']
                        destination=rooted(target/relative,target,must_exist=False);destination.parent.mkdir(parents=True,exist_ok=True)
                        shutil.copyfile(rooted(entry['file'],source),destination)
                        if digest_file(destination)!=(entry['sha256'],entry['bytes']):raise ValueError('Restored file checksum differs')
            report.update(status='verified',completed_at=now(),assets=manifest['assets'],recovery_files=manifest['recovery_files'],automatic_worker_started=False)
            atomic_json(target/'restore-report.json',report)
            return report
        except BaseException as exc:
            report.update(status='failed',error=safe_error(exc));atomic_json(target/'restore-report.json',report)
            raise
        finally:
            if conn:conn.close()
            if created_user:cur.execute('DROP USER %s@%s',(user,'127.0.0.1'))


def run_backup(job):
    def progress(done,total,table):
        current=db.get_job(job['id'])
        if current['cancel_requested']:
            from .engine import Cancelled
            raise Cancelled('备份已取消；未完成目录保留，不能用于恢复。')
        db.update_job(job['id'],stage=f'备份表 {table} ({done}/{total})',progress=min(90,int(done/total*90)))
    return create_snapshot(job['payload']['backup_id'],progress)


def summary(backup_id,job=None):
    path=backup_dir(backup_id);state={}
    if (path/'state.json').is_file():state=json.loads((path/'state.json').read_text(encoding='utf-8'))
    result={'id':backup_id,'status':job['status'] if job else 'unknown','source_schema':db.SCHEMA,'directory':str(path),'rows':0,'assets':0,'table_count':0,'verification':None,**state}
    result['total_bytes']=sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.exists() else 0
    if job:
        result.update(job_id=job['id'],job_status=job['status'],stage=job['stage'],progress=job['progress'])
        if job['error']:result['error']=job['error']
        if not state:result['created_at']=job['created_at']
    if (path/'verification.json').is_file():result['verification']=json.loads((path/'verification.json').read_text(encoding='utf-8'));result['verified_at']=result['verification']['verified_at']
    return result


@router.get('')
def list_backups():
    with db.transaction() as cur:jobs=db.rows(cur,"SELECT * FROM jobs WHERE kind='backup' ORDER BY id DESC LIMIT 100")
    return {'items':[summary(j['payload']['backup_id'],j) for j in jobs]}


@router.post('')
def request_backup(body:dict):
    from . import domain as d
    token=str(d.required(body.get('intent_key'),'请求标识'))
    backup_id=datetime.now().strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    job=db.create_job('backup',None,{'backup_id':backup_id},'backup:'+hashlib.sha256(token.encode()).hexdigest())
    return {'backup_id':job['payload']['backup_id'],'job':d.public_job(job)}


@router.get('/{backup_id}')
def backup_detail(backup_id:str):
    from . import domain as d
    with db.transaction() as cur:job=db.one(cur,"SELECT * FROM jobs WHERE kind='backup' AND JSON_UNQUOTE(JSON_EXTRACT(payload,'$.backup_id'))=%s ORDER BY id DESC LIMIT 1",(backup_id,))
    if not job:raise d.Problem('备份不存在','not_found',404)
    result=summary(backup_id,job);path=backup_dir(backup_id)
    if (path/'manifest.json').is_file():result['manifest']=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    name='studio_restore_'+datetime.now().strftime('%Y%m%d_%H%M%S')
    restore_data=db.DATA/'restores'/name
    quote=lambda value:"'"+str(value).replace("'","''")+"'"
    result['restore_command']='& '+quote(sys.executable)+' -m studio.backups restore --backup '+quote(path)+' --schema '+name+' --data '+quote(restore_data)
    result['restore_note']='恢复仅写入新库和新目录，拒绝覆盖。恢复后任务不会自动运行。完整备份是此目录，下载按钮只提供校验清单。'
    return result


@router.post('/{backup_id}/verify')
def verify_backup(backup_id:str):
    from . import domain as d
    path=backup_dir(backup_id)
    if not (path/'manifest.json').is_file():raise d.Problem('备份尚未完整生成，不能校验','backup_incomplete',409)
    try:return verify_snapshot(path)
    except (OSError,ValueError) as exc:raise d.Problem(safe_error(exc),'backup_invalid',422) from None


@router.get('/{backup_id}/download')
def download_manifest(backup_id:str):
    from . import domain as d
    path=backup_dir(backup_id)/'manifest.json'
    if not path.is_file():raise d.Problem('备份清单尚未生成','not_found',404)
    return FileResponse(path,media_type='application/json',filename=backup_id+'-manifest.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('create');check=sub.add_parser('verify');check.add_argument('--backup',required=True)
    restore=sub.add_parser('restore');restore.add_argument('--backup',required=True);restore.add_argument('--schema',required=True);restore.add_argument('--data',required=True)
    args=parser.parse_args();credentials=_credentials()
    os.environ.setdefault('STUDIO_DB_PASSWORD',credentials['app'])
    try:
        result=create_snapshot() if args.command=='create' else verify_snapshot(args.backup) if args.command=='verify' else restore_snapshot(args.backup,args.schema,args.data)
        print(json.dumps(result,ensure_ascii=False,indent=2,default=encode))
    except Exception as exc:
        print(json.dumps({'ok':False,'error':safe_error(exc)},ensure_ascii=False));sys.exit(1)


if __name__=='__main__':main()
