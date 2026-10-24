"""Measured client health and bounded, cancellable worker recovery controls."""
import asyncio
import math
import time
import uuid
from collections import deque
from contextvars import ContextVar
from functools import wraps
from transactions import endpoint

POLICIES=('hold_uncertain','resume_latest','retry_failed','manual')


def policy(config):
    supplied=config.get('recovery');supplied={} if supplied is None else supplied
    if not isinstance(supplied,dict):raise ValueError('Recovery settings must be an object')
    p={'policy':'hold_uncertain','retry_limit':0,'backoff':'fixed','max_delay_seconds':300.,'repeat_safe':False}
    if set(supplied)-set(p):raise ValueError('Unknown recovery setting')
    p.update(supplied)
    if p['policy'] not in POLICIES or p['backoff'] not in ('fixed','exponential'):raise ValueError('Invalid recovery policy/backoff')
    if type(p['retry_limit']) is not int or not 0<=p['retry_limit']<=1000:raise ValueError('Retry limit must be 0..1000; 0 means unlimited')
    if type(p['repeat_safe']) is not bool:raise ValueError('Repeat-safe confirmation must be Boolean')
    if p['policy'] in ('resume_latest','retry_failed') and not p['repeat_safe']:raise ValueError('Automatic write resumption requires explicit repeat-safe confirmation')
    if isinstance(p['max_delay_seconds'],bool):raise ValueError('Recovery cap must be a number')
    try:base=float(config.get('retry_seconds',5));cap=float(p['max_delay_seconds'])
    except (TypeError,ValueError,OverflowError):raise ValueError('Recovery delays must be finite numbers')
    if not math.isfinite(base) or not .5<=base<=300 or not math.isfinite(cap) or not base<=cap<=3600:raise ValueError('Recovery cap must be >= retry delay and <= 3600 seconds')
    p.update(base_delay_seconds=base,max_delay_seconds=cap)
    return p


def classify(error,row=None):
    name=type(error).__name__
    if row:
        statuses=' '.join(p.get('status','') for p in row.get('points',[]))
        if any(x in statuses for x in ('BadUserAccessDenied','BadIdentity','BadCertificate','BadSecurity')):return 'AUTH_SECURITY'
        if any(x in statuses for x in ('BadTypeMismatch','BadNotWritable','BadNodeId','BadOutOfRange')):return 'CONFIGURATION_DATA'
        if any(x in statuses for x in ('BadServerTooBusy','BadResourceUnavailable')):return 'TRANSIENT_DEVICE'
        if any('Modbus exception '+str(n)+':' in b.get('error','') for b in row.get('batches',[]) for n in (4,5,6,10,11)):return 'TRANSIENT_DEVICE'
    if any(x in name for x in ('Certificate','Security','Identity','UserAccess','Authentication')):return 'AUTH_SECURITY'
    if name in ('BadServerTooBusy','BadResourceUnavailable','BadTooManySessions'):return 'TRANSIENT_DEVICE'
    if isinstance(error,TimeoutError) or 'Timeout' in name:return 'TIMEOUT'
    if isinstance(error,(ValueError,TypeError,KeyError,FileNotFoundError,PermissionError)):return 'CONFIGURATION_DATA'
    if isinstance(error,(ConnectionError,OSError)) or name in ('BadSessionClosed','BadSessionIdInvalid','BadSecureChannelClosed','BadSecureChannelIdInvalid','BadConnectionClosed','BadCommunicationError','BadServerNotConnected','BadNotConnected','BadShutdown','ModbusIOException','ConnectionException'):return 'TRANSPORT_SESSION'
    return 'INTERNAL'


class HealthHub:
    def __init__(self):self.sessions={};self.controllers={};self.events=deque(maxlen=1000)
    def event(self,owner,kind,**details):
        from event_log import emit
        emit('security' if details.get('category')=='AUTH_SECURITY' else 'recovery','WARNING' if 'FAIL' in kind or details.get('error') else 'INFO',kind,owner=owner,**details)
        self.events.append({'id':uuid.uuid4().hex,'time':time.time(),'owner':owner,'event':kind,**details})
    def session(self,config):
        s=ClientHealth(self,config);self.sessions[s.id]=s
        if len(self.sessions)>2048:
            key=next((k for k,v in self.sessions.items() if not v.open),None)
            if key:self.sessions.pop(key)
        return s
    def register(self,c):
        self.controllers[c.id]=c
        if len(self.controllers)>1024:
            key=next((k for k,v in self.controllers.items() if v.closed),None)
            if key:self.controllers.pop(key)
    def snapshot(self):
        return {'sessions':[s.snapshot() for s in reversed(list(self.sessions.values()))],
                'recovery':[c.snapshot() for c in reversed(list(self.controllers.values()))],
                'events':list(reversed(self.events)),'limits':{'recent_sessions':2048,'recent_recovery_workers':1024,'events':1000},
                'scope':'Process-local client/session observations and worker recovery; not passive device discovery'}


class ClientHealth:
    def __init__(self,hub,config):
        self.hub=hub;self.id=uuid.uuid4().hex;self.owner=OWNER.get();self.connection=config.get('id','');self.name=config.get('name','Client')
        self.endpoint=endpoint(config);self.protocol='OPC UA' if config.get('protocol')=='opcua' or 'url' in config else 'Modbus RTU' if config.get('transport')=='rtu' else 'Modbus TCP'
        self.state='CREATED';self.session_state='NOT_ESTABLISHED' if self.protocol=='OPC UA' else 'NOT_APPLICABLE';self.open=False;self.since=None;self.total_uptime=0.;self.last_success=None;self.latency_ms=None;self.successes=0;self.failures=0;self.error='';self.failure_category='';self.created=time.time();self.last_operation=None
    def connected(self):
        self.open=True;self.since=time.monotonic();self.state='CONNECTED';self.session_state='ESTABLISHED' if self.protocol=='OPC UA' else 'NOT_APPLICABLE';self.hub.event(self.owner,'CONNECTED',client=self.id,endpoint=self.endpoint)
    def success(self,duration,operation):
        self.last_operation=operation;self.last_success=time.time();self.latency_ms=duration*1000;self.successes+=1;self.error='';self.failure_category='';self.state='CONNECTED' if self.open else self.state
    def failure(self,error,row=None):
        self.failures+=1;self.error=type(error).__name__;self.failure_category=classify(error,row);self.state='DEGRADED'
        self.hub.event(self.owner,'COMMUNICATION_FAILURE',client=self.id,endpoint=self.endpoint,error=self.error,category=self.failure_category)
    def close(self):
        if self.open and self.since is not None:self.total_uptime+=time.monotonic()-self.since
        self.open=False;self.since=None;self.state='CLOSED';self.session_state='CLOSED' if self.protocol=='OPC UA' else 'NOT_APPLICABLE'
    def snapshot(self):
        return {'id':self.id,'owner':self.owner,'connection':self.connection,'name':self.name,'endpoint':self.endpoint,'protocol':self.protocol,'state':self.state,'session_state':self.session_state,'uptime_seconds':self.total_uptime+(time.monotonic()-self.since if self.open and self.since is not None else 0),'connected_uptime_seconds':time.monotonic()-self.since if self.open and self.since is not None else 0,'last_success':self.last_success,'last_operation':self.last_operation,'latency_ms':self.latency_ms,'successful_operations':self.successes,'failures':self.failures,'error':self.error,'failure_category':self.failure_category}


class RecoveryController:
    def __init__(self,config,owner,label,read_only=False):
        self.hub=HUB.get();self.config=config;self.settings=policy(config);self.id=uuid.uuid4().hex;self.owner=owner;self.label=label;self.endpoint=endpoint(config);self.read_only=read_only
        self.failures=0;self.total_retries=0;self.scheduled_retries=0;self.finished_outage_seconds=None;self.recoveries=0;self.last_recovery_seconds=None;self.outage=None;self.state='CONNECTING';self.category='';self.error='';self.unknown_writes=0;self.next_retry=None;self.closed=False;self.gate=asyncio.Event();self.hub.register(self)
    def snapshot(self):
        return {'id':self.id,'owner':self.owner,'label':self.label,'endpoint':self.endpoint,'settings':self.settings,'state':self.state,'consecutive_failures':self.failures,'reconnection_attempts':self.total_retries,'scheduled_retries':self.scheduled_retries,'recoveries':self.recoveries,'last_recovery_seconds':self.last_recovery_seconds,'current_outage_seconds':self.finished_outage_seconds if self.closed else time.monotonic()-self.outage if self.outage is not None else None,'category':self.category,'error':self.error,'unknown_writes':self.unknown_writes,'next_retry':self.next_retry,'closed':self.closed,'read_only':self.read_only}
    def success(self):
        if self.outage is not None:
            self.last_recovery_seconds=time.monotonic()-self.outage;self.recoveries+=1;self.hub.event(self.owner,'RECOVERED',controller=self.id,duration_seconds=self.last_recovery_seconds);self.outage=None
        self.failures=0;self.state='RUNNING';self.error='';self.category='';self.next_retry=None
    def close(self):
        self.finished_outage_seconds=time.monotonic()-self.outage if self.outage is not None else None
        self.closed=True
        if self.state not in ('ERROR','RETRY_EXHAUSTED'):self.state='STOPPED'
        self.next_retry=None;self.gate.set()
    def release(self,target):
        if self.closed or self.state!='WAITING_RECOVERY':raise ValueError('Worker is not awaiting manual recovery')
        if target!=self.endpoint:raise ValueError('Recovery target confirmation does not match the worker endpoint')
        self.hub.event(self.owner,'MANUAL_RELEASE',controller=self.id,endpoint=self.endpoint,unknown_writes=self.unknown_writes)
        self.gate.set()
    async def wait(self,error,state,row=None,write=False):
        self.failures+=1;self.error=type(error).__name__;self.category=classify(error,row);self.outage=self.outage if self.outage is not None else time.monotonic()
        unknown=sum(p.get('outcome')=='UNKNOWN' for p in (row or {}).get('points',[])) if write and row else 1 if write and row is None else 0
        self.unknown_writes+=unknown
        self.hub.event(self.owner,'RECOVERY_FAILURE',controller=self.id,error=self.error,category=self.category,unknown_writes=unknown)
        if self.category in ('AUTH_SECURITY','CONFIGURATION_DATA','INTERNAL') and not unknown:
            self.state='ERROR';state.update(state='ERROR',error=str(error));return False
        if self.settings['retry_limit'] and self.failures>self.settings['retry_limit']:
            self.state='RETRY_EXHAUSTED';state.update(state='ERROR',error='Recovery retry limit reached');self.hub.event(self.owner,'RETRY_EXHAUSTED',controller=self.id);return False
        hold=self.settings['policy']=='manual' or write and (not self.settings['repeat_safe'] or unknown and self.settings['policy']!='resume_latest' or self.category in ('AUTH_SECURITY','CONFIGURATION_DATA','INTERNAL'))
        if hold:
            self.state='WAITING_RECOVERY';self.gate.clear();state.update(state='WAITING_RECOVERY',error='Review connection/write outcome before resuming latest values')
            self.hub.event(self.owner,'HELD',controller=self.id,unknown_writes=unknown)
            await self.gate.wait()
            if self.closed:return False
            self.failures=0
        delay=self.settings['base_delay_seconds']
        if self.settings['backoff']=='exponential':delay=min(delay*2**min(max(0,self.failures-1),30),self.settings['max_delay_seconds'])
        self.state='RECONNECTING';self.scheduled_retries+=1;self.next_retry=time.time()+delay
        state.update(state='RECONNECTING',error=self.error,retries=self.total_retries)
        self.hub.event(self.owner,'RETRY_SCHEDULED',controller=self.id,delay_seconds=delay,attempt=self.total_retries+1)
        await asyncio.sleep(delay);self.total_retries+=1;self.next_retry=None;state['retries']=self.total_retries
        self.hub.event(self.owner,'RETRY_ATTEMPT',controller=self.id,attempt=self.total_retries);return True


DEFAULT_HUB=HealthHub()
HUB=ContextVar('communication_health',default=DEFAULT_HUB)
OWNER=ContextVar('communication_owner',default='manual/diagnostics')


def observed(operation):
    def decorate(func):
        @wraps(func)
        async def wrapped(self,*args,**kwargs):
            health=getattr(self,'health',None)
            if health is None:health=self.health=HUB.get().session(self.config)
            start=time.monotonic()
            try:
                result=await func(self,*args,**kwargs)
            except asyncio.CancelledError:raise
            except Exception as error:
                if operation!='close' and asyncio.current_task().cancelling():raise asyncio.CancelledError() from error
                health.failure(error,getattr(self,'last_write_result',None) if operation=='write' else None);raise
            else:
                if operation=='connect':health.connected()
                if operation!='close':health.success(time.monotonic()-start,operation)
                return result
            finally:
                if operation=='close':health.close()
        return wrapped
    return decorate
