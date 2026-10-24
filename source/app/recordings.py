"""Bounded local CSV acquisition, separate from the rolling trend buffer."""
import csv
import json
import os
import time
import uuid

FIELDS=['received','source_timestamp','server_timestamp','tag','dtype','units','value','quality']
class Recorder:
    def __init__(self,folder,name,limit=100000):
        self.folder=folder;folder.mkdir(exist_ok=True,parents=True);self.id=uuid.uuid4().hex
        self.meta={'id':self.id,'name':name,'started':time.time(),'finished':None,'rows':0,'limit':limit,'state':'RECORDING','reason':''}
        self.file=(folder/(self.id+'.csv')).open('w',newline='',encoding='utf-8');self.writer=csv.DictWriter(self.file,FIELDS,extrasaction='ignore');self.writer.writeheader();self.last_flush=time.monotonic();self.error=''
        self.persist()
        if os.name!='nt':
            (folder/(self.id+'.csv')).chmod(0o600)
    def persist(self):
        path=self.folder/(self.id+'.json');temp=path.with_suffix('.tmp');temp.write_text(json.dumps(self.meta),encoding='utf-8');temp.replace(path)
        if os.name!='nt':path.chmod(0o600)
    def record(self,row):
        if self.meta['state']!='RECORDING':return
        try:
            # Spreadsheet-safe text labels; numeric values remain numeric.
            safe=dict(row)
            for k in ('tag','dtype','units','quality'):
                v=str(safe.get(k,''));safe[k]="'"+v if v.startswith(('=','+','-','@','\t','\r')) else v
            self.writer.writerow(safe);self.meta['rows']+=1
            if self.meta['rows']>=self.meta['limit']:self.stop('Row limit reached')
            elif time.monotonic()-self.last_flush>=1:self.file.flush();self.persist();self.last_flush=time.monotonic()
        except OSError as error:
            self.error=str(error);self.meta.update(state='ERROR',reason=self.error,finished=time.time())
            try:self.file.close();self.persist()
            except OSError:pass
    def stop(self,reason='Stopped by engineer'):
        if self.meta['state']!='RECORDING':return
        self.meta.update(state='SAVED',reason=reason,finished=time.time())
        try:self.file.flush();self.file.close();self.persist()
        except OSError as error:
            self.error=str(error);self.meta.update(state='ERROR',reason=self.error)
            try:self.file.close()
            except OSError:pass
            try:self.persist()
            except OSError:pass

def catalogue(folder):
    if not folder.exists():return []
    rows=[]
    for p in folder.glob('*.json'):
        try:rows.append(json.loads(p.read_text()))
        except (ValueError,OSError):continue
    return sorted(rows,key=lambda r:r['started'],reverse=True)
