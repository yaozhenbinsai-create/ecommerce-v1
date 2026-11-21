"""MySQL access for the rebuilt studio. No external project database is used."""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import re

import pymysql

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv('ECOMMERCE1_DATA_DIR', ROOT / 'data')).resolve()
SCHEMA = os.getenv('STUDIO_DB_SCHEMA', 'studio_commerce')
JSON_FIELDS = {'data','facts','components','provenance','description','elements','source_ids','config','input_revision','input_snapshot','payload','steps','result','meta','fields','images','snapshot','mapping','raw','issues','summary','report','settings','value','diagnostics','parsed'}


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=lambda v: v.isoformat() if isinstance(v, (datetime,date)) else float(v) if isinstance(v,Decimal) else str(v))


def unpack(row):
    if row is None: return None
    row = dict(row)
    for key, value in row.items():
        if key in JSON_FIELDS and isinstance(value, (str,bytes)):
            try: row[key] = json.loads(value)
            except (ValueError,TypeError): pass
        elif isinstance(value,(datetime,date)): row[key] = value.isoformat()
        elif isinstance(value,Decimal): row[key] = float(value)
    return row


def connect(database=True):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', SCHEMA): raise RuntimeError('数据库名称无效')
    return pymysql.connect(host=os.getenv('STUDIO_DB_HOST','127.0.0.1'), port=int(os.getenv('STUDIO_DB_PORT','13320')),
        user=os.getenv('STUDIO_DB_USER','studio_app'), password=os.getenv('STUDIO_DB_PASSWORD',''),
        database=SCHEMA if database else None, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5, read_timeout=30, write_timeout=30, autocommit=False)


@contextmanager
def transaction():
    conn = connect()
    try:
        with conn.cursor() as cur: yield cur
        conn.commit()
    except BaseException:
        conn.rollback(); raise
    finally: conn.close()


def one(cur, sql, args=()):
    cur.execute(sql,args); return unpack(cur.fetchone())


def rows(cur, sql, args=()):
    cur.execute(sql,args); return [unpack(row) for row in cur.fetchall()]


def _identifier(value):
    if not re.fullmatch(r'[a-z][a-z0-9_]*', value): raise ValueError('Invalid SQL identifier')
    return '`'+value+'`'


def insert(cur, table, values):
    keys=list(values)
    cur.execute('INSERT INTO '+_identifier(table)+' ('+','.join(map(_identifier,keys))+') VALUES ('+','.join(['%s']*len(keys))+')',
        tuple(dump(values[k]) if isinstance(values[k],(dict,list)) else values[k] for k in keys))
    return cur.lastrowid


def update(cur, table, entity_id, values):
    keys=list(values)
    cur.execute('UPDATE '+_identifier(table)+' SET '+','.join(_identifier(k)+'=%s' for k in keys)+' WHERE id=%s',
        tuple(dump(values[k]) if isinstance(values[k],(dict,list)) else values[k] for k in keys)+(entity_id,))
    return cur.rowcount


def initialize():
    DATA.mkdir(parents=True,exist_ok=True); (DATA/'assets').mkdir(exist_ok=True)
    sql=(Path(__file__).with_name('schema.sql')).read_text(encoding='utf-8')
    with transaction() as cur:
        for statement in sql.split(';'):
            if statement.strip(): cur.execute(statement)
        defaults={'storage':{'temporary_days':14,'source_cache_days':30}, 'routing':{}, 'display':{'page_size':24},'design_defaults':{}}
        for name,value in defaults.items():
            cur.execute('INSERT IGNORE INTO settings (name,value) VALUES (%s,%s)',(name,dump(value)))
        for name,components in [('被套',['被套']),('靠枕',['枕套']),('服装',['前片','后片']),('茶杯',['杯身'])]:
            cur.execute('INSERT IGNORE INTO directories (kind,name,config) VALUES (%s,%s,%s)',('categories',name,dump({'components':components})))
        for item in rows(cur,'SELECT * FROM directories'):
            record_configuration(cur,item['kind'],item['id'],item['revision'],item)
        for item in rows(cur,'SELECT * FROM settings'):
            record_configuration(cur,'settings:'+item['name'],0,item['revision'],item)


def record_configuration(cur,kind,resource_id,revision,snapshot):
    cur.execute('INSERT IGNORE INTO configuration_revisions(resource_kind,resource_id,revision,snapshot) VALUES(%s,%s,%s,%s)',
        (kind,resource_id,revision,dump(snapshot)))


def get_job(job_id):
    with transaction() as cur: return one(cur,'SELECT * FROM jobs WHERE id=%s',(job_id,))


def create_job(kind,entity_id,payload,intent_key):
    with transaction() as cur:
        cur.execute("INSERT INTO jobs(kind,entity_id,intent_key,payload,steps) VALUES(%s,%s,%s,%s,'[]') ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",
            (kind,entity_id,intent_key,dump(payload)))
        job=one(cur,'SELECT * FROM jobs WHERE id=%s',(cur.lastrowid,))
        if job['kind']!=kind or job['entity_id']!=entity_id:
            from studio.domain import Problem
            raise Problem('重复请求标识属于另一操作','intent_conflict',409)
        return job


def update_job(job_id,**changes):
    with transaction() as cur:
        update(cur,'jobs',job_id,changes)
        return one(cur,'SELECT * FROM jobs WHERE id=%s',(job_id,))

