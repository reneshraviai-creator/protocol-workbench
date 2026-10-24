"""Bounded measured client-data performance and opt-in admission control.

Counters exclude discovery/metadata/subscriptions and inbound server traffic.
Throughput is point outcomes, never inferred network-byte throughput.
"""
import asyncio
import csv
import ctypes
import io
import math
import os
import statistics
import sys
import time
from collections import Counter, deque
from contextlib import asynccontextmanager

DEFAULTS={'enabled':False,'concurrency':8,'queue_limit':128,'operations_per_second':0.,
          'cpu_percent':90.,'memory_mb':1024.,'loop_lag_ms':100.,'p95_ms':1000.,'queue_percent':80.}


def settings(value=None):
    if value is None:value={}
    if not isinstance(value,dict) or set(value)-set(DEFAULTS):raise ValueError('Invalid performance settings')
    p={**DEFAULTS,**value}
    if type(p['enabled']) is not bool:raise ValueError('Performance control enabled must be Boolean')
    for k,lo,hi in [('concurrency',1,64),('queue_limit',0,4096)]:
        if type(p[k]) is not int or not lo<=p[k]<=hi:raise ValueError(f'{k} must be an integer {lo}..{hi}')
    for k,lo,hi in [('operations_per_second',0,10000),('cpu_percent',0,6400),('memory_mb',0,1048576),('loop_lag_ms',0,60000),('p95_ms',0,3600000),('queue_percent',0,100)]:
        if isinstance(p[k],bool):raise ValueError(f'{k} must be a finite number')
        try:n=float(p[k])
        except (ValueError,TypeError,OverflowError):raise ValueError(f'{k} must be a finite number')
        if not math.isfinite(n) or not lo<=n<=hi:raise ValueError(f'{k} must be {lo}..{hi}')
        p[k]=n
    return p


def batch_settings(config):
    p=config.get('performance');p={} if p is None else p
    if not isinstance(p,dict) or set(p)-{'opcua_batch_size','modbus_batch_units'}:raise ValueError('Invalid connection performance settings')
    result={'opcua_batch_size':50,'modbus_batch_units':120,**p}
    for k,lo,hi in [('opcua_batch_size',1,1000),('modbus_batch_units',4,120)]:
        if type(result[k]) is not int or not lo<=result[k]<=hi:raise ValueError(f'{k} must be an integer {lo}..{hi}')
    return result


def latency(values):
    a=sorted(values)
    if not a:return {k:None for k in ('average','median','minimum','maximum','p95','p99')}|{'samples':0}
    return {'average':statistics.fmean(a),'median':statistics.median(a),'minimum':a[0],'maximum':a[-1],
            'p95':a[math.ceil(.95*len(a))-1],'p99':a[math.ceil(.99*len(a))-1],'samples':len(a)}


def resident_memory():
    """Current resident bytes using native process APIs; unavailable is never zero."""
    try:
        if sys.platform.startswith('linux'):
            with open('/proc/self/statm') as f:count=int(f.read().split()[1])
            return count*os.sysconf('SC_PAGE_SIZE'),'current RSS / procfs'
        if sys.platform=='darwin':
            # mach_task_basic_info uses natural_t-sized words (MACH_TASK_BASIC_INFO=20).
            class Info(ctypes.Structure):
                _fields_=[('virtual_size',ctypes.c_uint64),('resident_size',ctypes.c_uint64),('resident_size_max',ctypes.c_uint64),('user_time',ctypes.c_int*2),('system_time',ctypes.c_int*2),('policy',ctypes.c_int),('suspend_count',ctypes.c_int)]
            lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib');lib.mach_task_self.restype=ctypes.c_uint
            info=Info();count=ctypes.c_uint(ctypes.sizeof(info)//ctypes.sizeof(ctypes.c_uint))
            if lib.task_info(lib.mach_task_self(),20,ctypes.byref(info),ctypes.byref(count))==0:return info.resident_size,'current RSS / mach_task_info'
        if os.name=='nt':
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(x,ctypes.c_size_t) for x in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
            kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.GetCurrentProcess.restype=wintypes.HANDLE
            psapi=ctypes.WinDLL('psapi',use_last_error=True);psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
            info=Counters();info.cb=ctypes.sizeof(info)
            if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(info),info.cb):return info.WorkingSetSize,'current working set / Windows API'
    except (OSError,ValueError,IndexError,AttributeError):pass
    return None,'unavailable'


class BackpressureError(ValueError):pass


class Performance:
    def __init__(self,config=None,clock=time.monotonic):
        self.config=settings(config);self.clock=clock;self.started=clock();self.totals=Counter();self.timeline=deque(maxlen=2048);self.durations=deque(maxlen=10000);self.history=deque(maxlen=3600);self.alerts=deque(maxlen=500);self.alerting=set();self.active=0;self.waiting=0;self.rejected=0;self.waited=0;self.wait_seconds=0.;self.next_start=0.;self.condition=asyncio.Condition();self.cpu_time=time.process_time();self.cpu_wall=clock();self.sample_task=None;self.sample_errors=0
    def configure(self,config):
        p=settings(config)
        if self.active or self.waiting:raise ValueError('Stop active client operations before changing performance settings')
        self.config=p;self.next_start=0.
    def count(self,**counts):
        now=self.clock();self.totals.update(counts)
        second=int(now)
        if not self.timeline or self.timeline[-1][0]!=second:self.timeline.append((second,Counter()))
        self.timeline[-1][1].update(counts)
    def request(self):self.count(requests=1)
    def response(self):self.count(responses=1)
    def finished(self,row):
        counts=row['counts'];self.count(success=counts['SUCCEEDED'],failed=counts['FAILED'],unknown=counts['UNKNOWN'],attempted=counts['attempted'],transactions=1,operation_success=int(row['state']=='SUCCEEDED' and not row.get('error')),operation_failed=int(bool(counts['FAILED']) or row['state']=='RESPONSE_ERROR'),operation_unknown=int(bool(counts['UNKNOWN'])))
        self.durations.extend((self.clock(),b['duration_ms']) for b in row['batches'])
    def rates(self):
        now=self.clock();elapsed=max(.001,min(10.,now-self.started));c=Counter()
        # Per-second buckets: current plus nine prior seconds, bounded rates include idle time.
        for stamp,counts in reversed(self.timeline):
            if stamp<int(now)-9:break
            c.update(counts)
        return {k:c[k]/elapsed for k in ('requests','responses','success','failed','unknown','attempted','operation_success','operation_failed','operation_unknown')}
    def latencies(self):return latency([v for stamp,v in self.durations if self.clock()-stamp<=60])
    @asynccontextmanager
    async def slot(self):
        started=self.clock();queued=False;entered=False
        try:
            async with self.condition:
                limited=self.config['enabled'];rate=self.config['operations_per_second']
                while limited and (self.active>=self.config['concurrency'] or rate and self.clock()<self.next_start):
                    if not queued:
                        if self.waiting>=self.config['queue_limit']:
                            self.rejected+=1;self.count(rejected=1);raise BackpressureError('Client admission queue full; operation was not dispatched')
                        queued=True;self.waiting+=1;self.waited+=1
                    delay=max(.001,self.next_start-self.clock()) if rate and self.active<self.config['concurrency'] else None
                    try:await asyncio.wait_for(self.condition.wait(),delay) if delay is not None else await self.condition.wait()
                    except TimeoutError:pass
                if queued:self.waiting-=1;queued=False;self.wait_seconds+=self.clock()-started
                self.active+=1;entered=True
                if limited and rate:self.next_start=self.clock()+1/rate
            yield
        finally:
            async with self.condition:
                if queued:self.waiting-=1;self.wait_seconds+=self.clock()-started
                if entered:self.active-=1
                self.condition.notify_all()
    def sample(self,lag_ms=0.):
        now=self.clock();cpu=time.process_time();elapsed=now-self.cpu_wall
        cpu_percent=100*(cpu-self.cpu_time)/elapsed if elapsed>=.1 else None
        if elapsed>=.1:self.cpu_time=cpu;self.cpu_wall=now
        memory,source=resident_memory()
        try:tasks=sum(not t.done() for t in asyncio.all_tasks())
        except RuntimeError:tasks=None
        row={'time':time.time(),'rates':self.rates(),'latency_ms':self.latencies(),'cpu_percent':cpu_percent,'memory_mb':memory/(1024**2) if memory is not None else None,'memory_source':source,'active_tasks':tasks,'active_operations':self.active,'waiting_operations':self.waiting,'queue_percent':100*self.waiting/self.config['queue_limit'] if self.config['enabled'] and self.config['queue_limit'] else 0.,'loop_lag_ms':max(0.,lag_ms),'rejected_operations':self.rejected}
        self.history.append(row)
        values={'cpu_percent':cpu_percent,'memory_mb':row['memory_mb'],'loop_lag_ms':row['loop_lag_ms'],'p95_ms':row['latency_ms']['p95'],'queue_percent':row['queue_percent']}
        for k,v in values.items():
            if v is None:
                if k in self.alerting:self.alerts.append({'time':row['time'],'metric':k,'state':'UNAVAILABLE','value':None,'threshold':self.config[k]});self.alerting.remove(k)
                continue
            breached=v is not None and self.config[k]>0 and v>self.config[k]
            if breached and k not in self.alerting:self.alerts.append({'time':row['time'],'metric':k,'state':'HIGH','value':v,'threshold':self.config[k]});self.alerting.add(k)
            if not breached and k in self.alerting:self.alerts.append({'time':row['time'],'metric':k,'state':'CLEARED','value':v,'threshold':self.config[k]});self.alerting.remove(k)
        return row
    async def run(self):
        loop=asyncio.get_running_loop();due=loop.time()+1
        while True:
            await asyncio.sleep(max(0.,due-loop.time()))
            lag=max(0.,loop.time()-due)*1000
            try:self.sample(lag)
            except Exception:self.sample_errors+=1
            due=loop.time()+1
    async def start(self):
        self.sample();self.sample_task=asyncio.create_task(self.run(),name='workbench-performance-sampler')
    async def stop(self):
        if self.sample_task:
            self.sample_task.cancel()
            try:await self.sample_task
            except asyncio.CancelledError:pass
            self.sample_task=None
    def snapshot(self,history_limit=None):
        return {'settings':self.config,'totals':dict(self.totals),'rates':self.rates(),'latency_ms':self.latencies(),'latest':self.history[-1] if self.history else None,'history':list(self.history)[-history_limit:] if history_limit is not None else list(self.history),'alerts':list(reversed(self.alerts)),'active_alerts':sorted(self.alerting),'admission':{'active':self.active,'waiting':self.waiting,'rejected':self.rejected,'waited':self.waited,'wait_seconds':self.wait_seconds},'sample_errors':self.sample_errors,'limits':{'history':3600,'latencies':10000,'alerts':500},'scope':'Client data read/write service batches only; point throughput, not wire bytes. Rates use ten second buckets; latency is last 60 seconds / 10000 completed batches, including no-response attempts. Process CPU is percent of one core. History is process-local.'}
    def csv(self):
        f=io.StringIO();w=csv.writer(f);w.writerow(['time','requests_s','responses_s','success_points_s','failed_points_s','unknown_points_s','attempted_points_s','successful_operations_s','failed_operations_s','unknown_operations_s','average_batch_ms','p50_batch_ms','p95_batch_ms','p99_batch_ms','cpu_one_core_percent','memory_mb','memory_source','active_tasks','active_operations','waiting_operations','queue_percent','loop_lag_ms'])
        for r in self.history:
            w.writerow([r['time'],*[r['rates'][k] for k in ('requests','responses','success','failed','unknown','attempted','operation_success','operation_failed','operation_unknown')],*[r['latency_ms'][k] for k in ('average','median','p95','p99')],*[r[k] for k in ('cpu_percent','memory_mb','memory_source','active_tasks','active_operations','waiting_operations','queue_percent','loop_lag_ms')]])
        return f.getvalue()
