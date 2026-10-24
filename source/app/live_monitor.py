"""Read-only live OPC UA subscriptions and Modbus polling with bounded evidence."""
import asyncio
import copy
import math
import time
import uuid
from collections import deque
from asyncua import ua
from drivers import OPC,Modbus,MODBUS_EXCEPTIONS
from communication_health import RecoveryController,OWNER
from model import default_project,validate_project,view_value


def safe(value):
    if value is None or isinstance(value,(str,bool,int)):return value
    if isinstance(value,float):return value if math.isfinite(value) else str(value)
    if isinstance(value,(list,tuple)):return [safe(v) for v in value]
    if hasattr(value,'to_string'):return value.to_string()
    if hasattr(value,'isoformat'):return value.isoformat()
    return str(value)


class LiveMonitor:
    def __init__(self,connection,tags,settings):
        self.id=uuid.uuid4().hex;self.connection=copy.deepcopy(connection);self.tags=copy.deepcopy(tags);self.settings=settings
        self.state='STOPPED';self.error='';self.task=None;self.events=0;self.reconnects=0;self.cycles=0;self.last_check=None;self.duration=None
        self.values={};self.history={};self.traffic=deque(maxlen=200);self.logs=deque(maxlen=50);self.started=time.time();self.revised={};self.subscription_fault=None;self.pending={};self.requests=0;self.exception_responses=0
        self.driver_factory=None;self.demo=False;self.recovery_controller=None
        self.depth=max(2,min(300,50000//len(tags)));self.by_node={};self.recorder=None
    def log(self,message):self.logs.append({'time':time.time(),'message':message})
    def trace(self,kind,sending,data):
        row={'time':time.time(),'direction':'TX' if sending else 'RX','kind':kind}
        if kind=='bytes':row.update(hex=data[:512].hex(' ').upper(),bytes=len(data),truncated=len(data)>512)
        else:row.update(function=data.function_code,device=data.dev_id,transaction=data.transaction_id,address=data.address if sending else None,count=data.count if sending else None,exception=getattr(data,'exception_code',0),description=type(data).__name__)
        if kind=='pdu':
            if sending:
                self.requests+=1;self.pending[data.transaction_id]=row['time']
                if len(self.pending)>1024:self.pending.pop(next(iter(self.pending)))
            else:
                sent=self.pending.pop(data.transaction_id,None)
                if sent is not None:row['latency_ms']=round((row['time']-sent)*1000,3)
                if row.get('exception'):self.exception_responses+=1
        if row.get('exception'):row['exception_meaning']=MODBUS_EXCEPTIONS.get(row['exception'],'Device exception')
        self.traffic.append(row)
        return data
    def record(self,tag,value,status='Good',source=None,server=None,raw=None,good=None):
        now=time.time();good=status.startswith('Good') if good is None else good;previous=self.values.get(tag['id'],{})
        row={'id':tag['id'],'tag':tag['tag'],'dtype':tag['dtype'],'units':tag.get('units',''),'value':view_value(value,tag['dtype']) if value is not None else None,'quality':status,'received':now,'source_timestamp':safe(source),'server_timestamp':safe(server),'raw':raw,'last_good':previous.get('last_good')}
        if good:row['last_good']=row['value']
        self.values[tag['id']]=row;self.events+=1
        if self.recorder:self.recorder.record(row)
        history=self.history.setdefault(tag['id'],deque(maxlen=self.depth));history.append({'time':now,'value':row['value'] if good else None,'quality':status})
    def datachange_notification(self,node,value,data):
        tag=self.by_node.get(node.nodeid.to_string())
        if tag:
            dv=data.monitored_item.Value
            self.record(tag,value,dv.StatusCode.name,dv.SourceTimestamp,dv.ServerTimestamp,good=dv.StatusCode.is_good())
    def status_change_notification(self,status):
        if not status.Status.is_good():self.log(f'Subscription status: {status.Status}');self.error=str(status.Status);self.subscription_fault=str(status.Status)
    def start(self):self.state='CONNECTING';self.task=asyncio.create_task(self.run())
    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
        self.state='STOPPED'
        if self.recorder:self.recorder.stop('Monitor stopped')
    async def run(self):
        token=OWNER.set('monitor:'+self.id);self.recovery_controller=RecoveryController(self.connection,OWNER.get(),self.connection.get('name','Monitor'),read_only=True)
        try:await self.run_monitor()
        finally:self.recovery_controller.close();OWNER.reset(token)
    async def run_monitor(self):
        while True:
            client=None;sub=None
            try:
                self.state='CONNECTING';self.error='';self.subscription_fault=None
                c=self.connection;client=self.driver_factory(c) if self.driver_factory else OPC(c) if c['protocol']=='opcua' else Modbus(c,trace=self.trace)
                await client.connect();await client.prepare(self.tags)
                if c['protocol']=='opcua':
                    self.by_node={client.nodes[t['id']].nodeid.to_string():t for t in self.tags}
                    if len(self.by_node)!=len(self.tags):raise ValueError('Duplicate monitored NodeIds; select one tag per node')
                    sub=await client.client.create_subscription(self.settings['publishing_ms'],self)
                    self.revised={'publishing_ms':sub.parameters.RequestedPublishingInterval,'sampling_ms_requested':self.settings['sampling_ms'],'queue_size_requested':self.settings['queue_size']}
                    for n in range(0,len(self.tags),50):
                        handles=await sub.subscribe_data_change([client.nodes[t['id']] for t in self.tags[n:n+50]],queuesize=self.settings['queue_size'],sampling_interval=self.settings['sampling_ms'])
                        for handle in handles:
                            if isinstance(handle,ua.StatusCode):handle.check()
                    self.state='RUNNING';self.last_check=time.time();self.recovery_controller.success();self.log('OPC UA data-change subscription established')
                    while True:
                        await asyncio.sleep(1)
                        if self.subscription_fault:raise ConnectionError(self.subscription_fault)
                        await client.healthcheck()
                        self.last_check=time.time();self.cycles+=1;self.recovery_controller.success()
                else:
                    self.log('Modbus '+('RTU' if c.get('transport')=='rtu' else 'TCP')+' polling connected')
                    while True:
                        start=time.monotonic();values=await client.read(self.tags)
                        for t in self.tags:self.record(t,values[t['id']],raw=client.last_raw.get(t['id']))
                        self.duration=time.monotonic()-start;self.last_check=time.time();self.cycles+=1;self.state='RUNNING';self.error='';self.recovery_controller.success()
                        await asyncio.sleep(max(.01,self.settings['poll_seconds']-self.duration))
            except asyncio.CancelledError:raise
            except Exception as error:
                self.error=type(error).__name__;self.reconnects+=1;self.log(self.error)
                for history in self.history.values():history.append({'time':time.time(),'value':None,'quality':'Disconnected'})
                if self.recorder:
                    for tag in self.tags:self.recorder.record({'received':time.time(),'tag':tag['tag'],'dtype':tag['dtype'],'units':tag.get('units',''),'value':None,'quality':'Disconnected'})
                if sub:
                    try:await asyncio.wait_for(sub.delete(),3)
                    except Exception:pass
                    sub=None
                if client:
                    try:await asyncio.wait_for(client.close(),3)
                    except Exception:pass
                    client=None
                if not await self.recovery_controller.wait(error,self.__dict__):
                    if self.recorder:self.recorder.stop('Monitor error')
                    return
            finally:
                if sub:
                    try:await asyncio.wait_for(sub.delete(),3)
                    except Exception:pass
                if client:
                    try:await asyncio.wait_for(client.close(),3)
                    except Exception:pass
    def summary(self):
        return {'id':self.id,'temporary_demo':self.demo,'connection':self.connection['id'],'name':self.connection['name'],'protocol':self.connection['protocol'],'mode':'subscription' if self.connection['protocol']=='opcua' else ('RTU polling' if self.connection.get('transport')=='rtu' else 'TCP polling'),'transport':self.connection.get('transport','tcp'),'state':self.state,'error':self.error,'points':len(self.tags),'events':self.events,'cycles':self.cycles,'reconnects':self.reconnects,'last_check':self.last_check,'duration':self.duration,'requests':self.requests,'exception_responses':self.exception_responses,'history_depth':self.depth,'revised':self.revised,'recording':self.recorder.meta if self.recorder else None}
    def snapshot(self,page=0,query='',tag=None,trends=None,quality='all'):
        if quality not in ('all','good','attention'):raise ValueError('Unknown quality filter')
        def good(t):return self.state=='RUNNING' and self.values.get(t['id'],{}).get('quality','').startswith('Good')
        rows=[t for t in self.tags if query.casefold() in t['tag'].casefold() and (quality=='all' or (good(t) if quality=='good' else not good(t)))];total=len(rows)
        page=max(0,min(page,max(0,(total-1)//50)))
        visible=[]
        for t in rows[page*50:page*50+50]:
            row=copy.deepcopy(self.values.get(t['id'],{'id':t['id'],'tag':t['tag'],'dtype':t['dtype'],'units':t.get('units',''),'value':None,'quality':'Waiting for data'}));row.update(address=t.get('address'),area=t.get('area'),order=t.get('order'),nodeid=t.get('nodeid'),unit=self.connection.get('device_id',1),writable_area=not self.demo and (self.connection['protocol']=='opcua' or self.connection.get('transport')!='rtu' and t.get('area','holding') in ('holding','coil')));row['connected']=self.state=='RUNNING';row['age']=round(time.time()-row['received'],2) if row.get('received') else None;visible.append(row)
        selected=tag or (rows[0]['id'] if rows else None)
        requested=trends if trends is not None else ([selected] if selected else [])
        if len(requested)>6 or len(set(requested))!=len(requested) or any(k not in {t['id'] for t in self.tags} for k in requested):raise ValueError('Choose up to six monitored trend tags')
        series=[{'id':k,'name':next(t['tag'] for t in self.tags if t['id']==k),'units':next(t.get('units','') for t in self.tags if t['id']==k),'samples':list(self.history.get(k,[]))} for k in requested]
        return {**self.summary(),'series':series,'rows':visible,'total':total,'page':page,'trend':list(self.history.get(selected,[])),'trend_tag':selected,'trend_name':next((t['tag'] for t in self.tags if t['id']==selected),''),'traffic':list(self.traffic),'logs':list(self.logs),'settings':self.settings}


class MonitorHub:
    def __init__(self):self.monitors={}
    def start(self,connection,selected,settings):
        p=default_project();p['connections']=[connection];validate_project(p)
        if sum(m.task is not None and not m.task.done() for m in self.monitors.values())>=8:raise ValueError('Stop a monitor before opening another (8 simultaneous monitors supported)')
        # Drop older stopped sessions when retaining more than 16 bounded buffers.
        if len(self.monitors)>=16:
            key=next((k for k,m in self.monitors.items() if not m.task or m.task.done()),None)
            if key:self.monitors.pop(key)
        chosen=set(selected)
        if len(chosen)!=len(selected) or not chosen:raise ValueError('Select at least one unique tag')
        tags=[t for t in connection['tags'] if t['id'] in chosen]
        if len(tags)!=len(chosen):raise ValueError('Unknown selected tag')
        values=monitor_settings(settings)
        m=LiveMonitor(connection,tags,values);self.monitors[m.id]=m;m.start();return m
    async def stop_all(self):
        for m in self.monitors.values():await m.stop()


def monitor_settings(settings):
    queue=settings.get('queue_size',10)
    if isinstance(queue,bool) or not math.isfinite(float(queue)) or float(queue)!=int(float(queue)):raise ValueError('Queue size must be a whole number')
    values={'publishing_ms':float(settings.get('publishing_ms',1000)),'sampling_ms':float(settings.get('sampling_ms',1000)),'poll_seconds':float(settings.get('poll_seconds',1)),'queue_size':int(settings.get('queue_size',10))}
    for key in ('publishing_ms','sampling_ms'):
        if not math.isfinite(values[key]) or not 100<=values[key]<=60000:raise ValueError('Publishing and sampling intervals must be 100..60000 ms')
    if not math.isfinite(values['poll_seconds']) or not .1<=values['poll_seconds']<=3600:raise ValueError('Polling interval must be 0.1..3600 seconds')
    if not 1<=values['queue_size']<=1000:raise ValueError('Queue size must be 1..1000')
    return values
