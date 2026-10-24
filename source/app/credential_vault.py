"""Encrypted local secrets, bound to exact OPC UA endpoint/user/security mode."""
from contextvars import ContextVar
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
from cryptography.fernet import Fernet
from aiohttp import web
from access_control import KEY

CONTEXT=ContextVar('credential_vault',default=None)


class Vault:
    def __init__(self,folder):
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True,mode=0o700);folder.chmod(0o700);key=folder/'vault.key'
        try:
            fd=os.open(key,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError:pass
        else:
            with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
        key.chmod(0o600);self.cipher=Fernet(key.read_bytes())
        path=folder/'vault.sqlite3';self.db=sqlite3.connect(path);path.chmod(0o600)
        self.db.execute('CREATE TABLE IF NOT EXISTS secrets (id TEXT PRIMARY KEY,binding TEXT NOT NULL,value BLOB NOT NULL)');self.db.commit()
    def binding(self,c):return json.dumps([c.get('url'),c.get('username',''),c.get('security','None')],separators=(',',':'))
    def seal(self,c):
        value=c.get('password')
        if not value:return c
        if not isinstance(value,str) or len(value)>1024:raise ValueError('Invalid protocol password')
        if value.startswith('secret:'):self.resolve(c);return c
        binding=self.binding(c)
        # Compare decrypted values only within the same bound endpoint; never expose them.
        for key,encrypted in self.db.execute('SELECT id,value FROM secrets WHERE binding=?',(binding,)):
            if self.cipher.decrypt(encrypted).decode()==value:return {**c,'password':'secret:'+key}
        key=secrets.token_hex(16);self.db.execute('INSERT INTO secrets VALUES(?,?,?)',(key,binding,self.cipher.encrypt(value.encode())));self.db.commit()
        return {**c,'password':'secret:'+key}
    def resolve(self,c):
        c=copy.deepcopy(c);value=c.get('password','')
        if not isinstance(value,str):raise ValueError('Invalid protocol password')
        if value.startswith('secret:'):
            row=self.db.execute('SELECT binding,value FROM secrets WHERE id=?',(value[7:],)).fetchone()
            if not row or row[0]!=self.binding(c):raise ValueError('Stored credential belongs to another endpoint, user or security mode; enter the password again')
            c['password']=self.cipher.decrypt(row[1]).decode()
        if c.get('username') and c.get('password') and 'SignAndEncrypt' not in c.get('security','None'):
            raise ValueError('Username/password access requires OPC UA SignAndEncrypt in authenticated mode')
        from event_log import CONTEXT as EVENTS
        events=EVENTS.get()
        if events:events.learn({'connections':[c]})
        return c
    def protect(self,value):
        if isinstance(value,list):return [self.protect(v) for v in value]
        if isinstance(value,dict):
            result={k:self.protect(v) for k,v in value.items()}
            if result.get('protocol')=='opcua' and result.get('password'):result=self.seal(result)
            # Never emit raw password keys from logs, recovery records, or incomplete configurations.
            elif 'password' in result:result['password']='[redacted]'
            return result
        return value
    def close(self):self.db.close()


def config(value):
    vault=CONTEXT.get()
    return vault.resolve(value) if vault else value


@web.middleware
async def boundary(request,handler):
    store=request.app[KEY];vault=request.app['credential_vault']
    scope=CONTEXT.set(vault if store.enabled else None)
    try:
        if store.enabled and request.path.startswith('/api/') and not request.path.startswith('/api/security/') and request.body_exists and request.content_type=='application/json':
            body=vault.protect(await request.json());request._read_bytes=json.dumps(body,allow_nan=False).encode()
        response=await handler(request)
        if store.enabled and request.path.startswith('/api/') and not request.path.startswith('/api/security/') and response.content_type=='application/json' and response.body:
            # Return opaque references, preserving edit/save round trips without plaintext exposure.
            body=vault.protect(json.loads(response.body));response.body=json.dumps(body,allow_nan=False).encode()
        return response
    finally:CONTEXT.reset(scope)
