"""Own only this workspace's local MySQL and loopback application."""
from configparser import ConfigParser
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('ECOMMERCE1_DATA_DIR', ROOT / 'data')).resolve()

def busy(port):
    with socket.socket() as sock:
        sock.settimeout(.5)
        return sock.connect_ex(('127.0.0.1', port)) == 0

def admin(port, password):
    import pymysql
    conn = pymysql.connect(host='127.0.0.1', port=port, user='root', password=password, autocommit=True, connect_timeout=3)
    cfg = ConfigParser()
    cfg.read(DATA / 'mysql' / 'auto.cnf', encoding='ascii')
    with conn.cursor() as cur:
        cur.execute('SELECT @@server_uuid, @@port')
        identity, actual_port = cur.fetchone()
    if identity != cfg.get('auto','server-uuid',fallback=None) or actual_port != port:
        conn.close()
        raise RuntimeError('数据库身份与本项目不一致。')
    return conn

def main(args):
    import pymysql
    import uvicorn
    candidates = [Path(args.mysql)] if args.mysql else [ROOT/'mysql/bin/mysqld.exe']
    located = shutil.which('mysqld')
    if located:
        candidates.append(Path(located))
    mysql = next((p for p in candidates if p.is_file()), None)
    if not mysql:
        raise RuntimeError('未找到 MySQL 8.4，请使用 -MySql 指定程序位置。')
    if busy(args.port):
        raise RuntimeError('应用端口已占用，未启动重复实例。')
    database_running = busy(args.db_port)
    DATA.mkdir(parents=True, exist_ok=True)
    credential_file = DATA/'local-credentials.json'
    database_dir = DATA/'mysql'
    if not credential_file.exists():
        if database_dir.exists() or database_running:
            raise RuntimeError('已有数据库缺少原凭据，不能重新初始化。')
        credential_file.write_text(json.dumps({'root':secrets.token_hex(24),'app':secrets.token_hex(24)}), encoding='utf-8')
    credentials = json.loads(credential_file.read_text(encoding='utf-8'))
    if database_running:
        # Reuse only the exact workspace instance; a web-process restart must
        # not stop a database also serving an isolated acceptance process.
        with admin(args.db_port, credentials['root']):
            pass
    config = DATA/'mysql.ini'
    config.write_text(f'[mysqld]\nbasedir="{mysql.parent.parent.as_posix()}"\ndatadir="{database_dir.as_posix()}"\n'
                      f'bind-address=127.0.0.1\nport={args.db_port}\nmysqlx=0\nlocal-infile=OFF\n'
                      'skip-log-bin\nsecure-file-priv=NULL\ninnodb-buffer-pool-size=96M\ninnodb-redo-log-capacity=64M\n'
                      'max-connections=32\ngeneral-log=OFF\nslow-query-log=OFF\n', encoding='mbcs' if os.name=='nt' else 'utf-8')
    command=['mysqld', '--defaults-file=mysql.ini']
    native=dict(executable=str(mysql),cwd=DATA,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    process = None
    with (DATA/'mysql.log').open('ab') as log:
        if not database_dir.exists():
            database_dir.mkdir()
            subprocess.run([*command,'--initialize-insecure','--console'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180,**native)
        elif not (database_dir/'auto.cnf').is_file():
            raise RuntimeError('数据库初始化未完成，请检查日志。')
        if not database_running:
            process=subprocess.Popen([*command,'--standalone','--console'],stdout=log,stderr=subprocess.STDOUT,**native)
    password=credentials['root']
    try:
        deadline=time.monotonic()+50
        while True:
            if (process is not None and process.poll() is not None) or time.monotonic()>deadline:
                raise RuntimeError('MySQL 启动失败，见数据目录下 mysql.log。')
            try:
                connection=admin(args.db_port,password)
                break
            except pymysql.OperationalError as exc:
                if exc.args[0]==1045:
                    connection=admin(args.db_port,'')
                    password=''
                    break
                if exc.args[0] not in (2003,2013):
                    raise
                time.sleep(.3)
        with connection, connection.cursor() as cur:
            if not password:
                cur.execute('ALTER USER CURRENT_USER() IDENTIFIED BY %s',(credentials['root'],))
                password=credentials['root']
            cur.execute('CREATE DATABASE IF NOT EXISTS studio_commerce CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
            cur.execute("CREATE USER IF NOT EXISTS 'studio_app'@'127.0.0.1' IDENTIFIED BY %s",(credentials['app'],))
            cur.execute("GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,INDEX,REFERENCES ON studio_commerce.* TO 'studio_app'@'127.0.0.1'")
        os.environ.update(STUDIO_DB_HOST='127.0.0.1',STUDIO_DB_PORT=str(args.db_port),STUDIO_DB_USER='studio_app',STUDIO_DB_PASSWORD=credentials['app'],STUDIO_DB_SCHEMA='studio_commerce',STUDIO_SCHEMA='studio_commerce',ECOMMERCE1_DATA_DIR=str(DATA))
        from studio import db
        db.initialize()
        if args.prepare:
            print('新系统 MySQL 结构已准备。', flush=True)
        else:
            from studio.runtime_logging import configuration
            uvicorn.run('studio.api:app',host='127.0.0.1',port=args.port,access_log=False,log_config=configuration(DATA))
    finally:
        if process is not None and process.poll() is None:
            try:
                with admin(args.db_port,password) as connection, connection.cursor() as cur:
                    cur.execute('SHUTDOWN')
                process.wait(timeout=20)
            except Exception:
                process.terminate()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8770)
    parser.add_argument('--db-port',type=int,default=13320)
    parser.add_argument('--mysql')
    parser.add_argument('--library',action='append',default=[])
    parser.add_argument('--prepare',action='store_true')
    args=parser.parse_args()
    if not (1024<=args.port<=65535 and 1024<=args.db_port<=65535 and args.port!=args.db_port):
        parser.error('端口必须为两个不同的非系统端口。')
    sys.path.extend(args.library)
    main(args)
