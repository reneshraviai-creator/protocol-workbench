"""Independent workers, common-clock simulation and protocol gateway lifecycle."""
import asyncio
import copy
import logging
import time
import uuid
from collections import deque
from pathlib import Path
from asyncua import Server, ua
from pymodbus.server import ModbusTcpServer,ModbusSerialServer
from serial_config import is_rtu,options,lock
from pymodbus.datastore import ModbusDeviceContext,ModbusSequentialDataBlock,ModbusServerContext
from drivers import adapter
from communication_health import RecoveryController,OWNER
from verification import Session as VerificationSession,selected as verification_selected
from resources import ResourceRegistry,job_plan
from model import initial,generated,transform,encode,view_value
from simulation_control import schedule_time
from bridge import Session as BridgeSession

class Engine:
    def __init__(self,project,registry=None):
        self.instance_id=uuid.uuid4().hex[:12];self.registry=registry if registry is not None else ResourceRegistry();self.project=project;self.tasks={};self.states={};self.recovery_controllers={};self.run_gates={};self.run_history=deque(maxlen=200);self.logs=deque(maxlen=500);self.lock=asyncio.Lock();self.logger=logging.getLogger('workbench')
    def log(self,job,level,message):
        row={'time':time.time(),'job':job,'level':level,'message':str(message)}
        from event_log import emit
        job_type=next((j['kind'] for j in self.project['jobs'] if j['id']==job),None)
        emit('simulation' if job_type in ('simulation','server') else 'bridge' if job_type=='gateway' else 'application',level,'worker_event',job=job,message=str(message))
        self.logs.append(row);getattr(self.logger,level.lower(),self.logger.info)(f'{job}: {message}')
    def snapshot(self):
        states={}
        for j in self.project['jobs']:
            s=copy.deepcopy(self.states.get(j['id'],{'state':'STOPPED','cycles':0,'values':[]}))
            if s.get('updated'):s['age']=round(time.time()-s['updated'],2)
            if s.get('started') and j.get('duration_seconds'):
                s['run_elapsed_seconds']=max(0,(s.get('finished',time.time())-s['started']));s['progress_percent']=min(100,100*s['run_elapsed_seconds']/float(j['duration_seconds']))
            states[j['id']]=s
        return {'jobs':states,'logs':list(self.logs)[-100:],'simulation_history':list(self.run_history)}
    async def start(self,ident):
        async with self.lock:
            if ident in self.tasks and not self.tasks[ident].done():return
            job=next((j for j in self.project['jobs'] if j['id']==ident),None)
            if not job:raise ValueError('Job not found')
            self.registry.acquire('job:'+ident,job.get('name',ident),job_plan(self.project,job),'workspace job')
            self.states[ident]={'state':'CONNECTING','cycles':0,'values':[],'error':'','writes':0}
            self.run_gates[ident]=asyncio.Event();self.run_gates[ident].set()
            self.tasks[ident]=asyncio.create_task(self.owned_worker(copy.deepcopy(job)))
    async def owned_worker(self,job):
        token=OWNER.set('job:'+job['id']+'@'+self.instance_id)
        try:await self.worker(job)
        finally:
            self.states[job['id']].setdefault('finished',time.time())
            if job['kind'] in ('simulation','server'):self.run_history.append({'job':job['id'],'name':job.get('name',job['id']),'finished':time.time(),'state':self.states[job['id']]['state'],'cycles':self.states[job['id']].get('cycles',0),'started':self.states[job['id']].get('started')})
            self.run_gates.pop(job['id'],None);self.registry.release('job:'+job['id']);OWNER.reset(token)
    async def stop(self,ident):
        async with self.lock:
            task=self.tasks.get(ident)
            if task and not task.done():
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
            self.registry.release('job:'+ident)
            if ident in self.states:self.states[ident].update(state='STOPPED',error='')
    async def stop_all(self):
        servers={j['id'] for j in self.project['jobs'] if j['kind']=='server'}
        for ident in sorted(self.tasks,key=lambda i:i in servers):await self.stop(ident)
    def active(self):return any(not t.done() for t in self.tasks.values())
    async def pause(self,ident):
        job=next((j for j in self.project['jobs'] if j['id']==ident),None)
        if not job or job['kind'] not in ('simulation','server') or ident not in self.run_gates or self.states[ident]['state'] not in ('RUNNING','OVERRUN','PAUSING','PAUSED'):raise ValueError('Only running simulation/server jobs can pause')
        self.run_gates[ident].clear();self.states[ident]['state']='PAUSING'
    async def resume(self,ident):
        if ident not in self.run_gates or self.states[ident]['state'] not in ('PAUSING','PAUSED'):raise ValueError('Job is not paused')
        self.run_gates[ident].set();self.states[ident]['state']='RUNNING'
    async def boundary(self,ident):
        gate=self.run_gates.get(ident)
        if gate and not gate.is_set():
            self.states[ident]['state']='PAUSED';await gate.wait()
    async def worker(self,job):
        state=self.states[job['id']]
        try:
            scheduled=schedule_time(job.get('schedule_at'))
            if scheduled is not None and scheduled>time.time():
                state.update(state='SCHEDULED',scheduled=scheduled);await asyncio.sleep(scheduled-time.time())
            state['started']=time.time()
            duration=job.get('duration_seconds')
            if duration is None:await self.run_worker(job)
            else:
                try:await asyncio.wait_for(self.run_worker(job),float(duration))
                except TimeoutError:state.update(state='COMPLETED',finished=time.time(),error='Configured duration completed')
        except asyncio.CancelledError:
            state['state']='STOPPED';raise
        finally:
            controller=self.recovery_controllers.pop(job['id'],None)
            if controller:controller.close()
    async def run_worker(self,job):
        ident=job['id'];state=self.states[ident];configs={c['id']:c for c in self.project['connections']};config=configs[job['connection']]
        tags=[t for t in config.get('tags',[]) if t.get('enabled',True)];chosen=set(job.get('tags',[]));tags=[t for t in tags if not chosen or t['id'] in chosen]
        if not tags:state.update(state='ERROR',error='No tags configured');return
        if job['kind']=='gateway':tags=[t for t in config['tags'] if t['id'] in {m['destination'] for m in job['mappings']}]
        from credential_vault import CONTEXT as VAULT
        from write_access import CONTEXT as WRITE
        if VAULT.get() and (job['kind'] in ('simulation','gateway') or job['kind']=='server' and job.get('dynamic',True)):
            if not WRITE.get():raise ValueError('Explicit write authorization required')
            WRITE.get().check()
            if len(tags)>WRITE.get().maximum:raise ValueError('Write exceeds the 10,000 point operation limit')
        verifier=VerificationSession(job,tags,config['protocol'])
        bridge=BridgeSession(job,configs[job['source']],config,state) if job['kind']=='gateway' else None
        recovery=RecoveryController(config,OWNER.get(),job.get('name',ident),read_only=job['kind']=='scan') if job['kind']!='server' else None
        if recovery:self.recovery_controllers[ident]=recovery
        interval=float(job.get('interval',2));initial_attempted=False;pending_retry=None
        while True:
            dest=source=None;stage='connect';write_tags=tags;write_values={};write_metadata={}
            try:
                state.update(state='CONNECTING',error='')
                if job['kind']=='server':
                    await self.serve(job,config,tags,state)
                    return
                dest=adapter(config);await dest.connect()
                if job['kind']=='gateway':
                    mappings=job['mappings'];needed={m['destination'] for m in mappings if m.get('enabled',True)};tags=[t for t in config['tags'] if t['id'] in needed]
                    srcconfig=configs[job['source']];needed={m['source'] for m in mappings if m.get('enabled',True)};srctags=[t for t in srcconfig['tags'] if t['id'] in needed]
                    source=adapter(srcconfig);await source.connect();await source.prepare(srctags)
                stage='prepare';await dest.prepare(tags,write=job['kind']!='scan')
                self.log(ident,'INFO',f"Connected; {len(tags)} tags validated")
                if bridge:bridge.reconnected()
                if pending_retry:
                    write_tags,write_values,write_metadata=pending_retry;stage='transform'
                    if bridge:
                        bridge.retry(write_tags,write_values,write_metadata)
                        if job.get('max_age_seconds') is not None:
                            stamps=[p.get('source_timestamp') for p in bridge.current['points']]
                            if any(t is None or time.time()-t>float(job['max_age_seconds']) for t in stamps):raise ValueError('Captured source timestamps are missing or stale; retry not attempted')
                    stage='write'
                    if write_metadata:await dest.write(write_tags,write_values,metadata=write_metadata)
                    else:await dest.write(write_tags,write_values)
                    if bridge:bridge.finish(getattr(dest,'last_write_result',None))
                    pending_retry=None
                    recovery.success()
                # Align separate simulation jobs with matching tag indices to a common wall clock.
                if job['kind']=='simulation':await asyncio.sleep((int(time.time()/interval)+1)*interval-time.time())
                while True:
                    await self.boundary(ident)
                    start=time.monotonic();slot=int(time.time()/interval);read_verification_recorded=False
                    if job['kind']=='simulation':
                        values={t['id']:(initial(t,job.get('simulation',{})) if not initial_attempted and 'initial_value' in t else generated(t,job.get('simulation',{}),slot)) for t in tags}
                        stage='write';write_tags=tags;write_values=values;initial_attempted=True;writes=await dest.write(tags,values)
                    elif job['kind']=='gateway':
                        bridge.begin();stage='read'
                        raw=await source.read(srctags,allow_bad_quality=True) if srcconfig['protocol']=='opcua' and bridge.settings['bad_quality']!='stop' else await source.read(srctags)
                        if srctags and job.get('max_age_seconds') is not None:
                            if srcconfig['protocol']!='opcua':raise ValueError('Timestamp freshness checks require an OPC UA source')
                            stamp=source.oldest_source_timestamp
                            if stamp is None or time.time()-stamp>float(job['max_age_seconds']):raise ValueError('Source timestamps are missing or stale; destination retains last values')
                        byid={t['id']:t for t in tags}
                        stage='transform';write_tags,values,write_metadata=bridge.plan(raw,getattr(source,'last_observations',{}),getattr(source,'last_read_transaction',None))
                        stage='write';write_values=values;initial_attempted=True
                        if write_tags:
                            writes=await dest.write(write_tags,values,metadata=write_metadata) if write_metadata else await dest.write(write_tags,values)
                            bridge.finish(getattr(dest,'last_write_result',None))
                        else:writes=0;bridge.finish()
                    else:
                        stage='read'
                        try:values=await dest.read(tags)
                        except BaseException as error:
                            row=None
                            if verifier.config['mode']!='off':
                                row=verifier.evaluate(verification_selected(tags,verifier.config),{},observations=getattr(dest,'last_observations',{}),read_error=error,transaction=getattr(dest,'last_read_transaction',None))
                                state['verification']={k:row[k] for k in ('id','outcome','counts','threshold_exceeded')}
                            may_continue=row is not None and not verifier.config['legacy'] and isinstance(error,ValueError) and (verifier.config['on_failure']=='continue' or not row['threshold_exceeded'] and not row['inconclusive'])
                            if not may_continue:raise
                            values={key:ob['value'] for key,ob in getattr(dest,'last_observations',{}).items() if ob.get('good') is True}
                            read_verification_recorded=True
                        writes=0
                    if verifier.due() and not read_verification_recorded:
                        stage='verify';await self.verify_cycle(verifier,dest,values,state,scan=job['kind']=='scan')
                    recovery.success()
                    elapsed=time.monotonic()-start
                    preview=[{'tag':t['tag'],'value':view_value(values[t['id']],t['dtype']) if t['id'] in values else None,'dtype':t['dtype'],'units':t.get('units',''),'raw':getattr(dest,'last_raw',{}).get(t['id']) if job['kind']=='scan' else None} for t in tags[:100]]
                    state.update(state='RUNNING' if elapsed<interval else 'OVERRUN',cycles=state['cycles']+1,updated=time.time(),duration=round(elapsed,4),writes=writes,points=len(tags),slot=slot,values=preview,error='' if elapsed<interval else 'Cycle exceeded the configured interval; increase interval or reduce load')
                    if job['kind']=='simulation':delay=max(0,(slot+1)*interval-time.time())
                    else:delay=max(0,interval-elapsed)
                    await asyncio.sleep(max(0.01,delay))
            except asyncio.CancelledError:
                if bridge:bridge.finish(getattr(dest,'last_write_result',None) if stage=='write' and dest else None,asyncio.CancelledError())
                state['state']='STOPPED';self.log(ident,'INFO','Stopped; destinations retain last values');raise
            except Exception as error:
                if recovery is None:
                    state.update(state='ERROR',error=str(error));self.log(ident,'ERROR',str(error));return
                row=getattr(dest,'last_write_result',None) if stage=='write' and dest else None
                if bridge:
                    if stage=='read' and bridge.current:bridge.current['source_transaction']=getattr(source,'last_read_transaction',None)
                    bridge.finish(row,error)
                # Close clients before waiting/holding so serial handles and sockets are released.
                for client in (source,dest):
                    if client:
                        try:await asyncio.wait_for(client.close(),3)
                        except Exception:pass
                source=dest=None
                pending_retry=None
                if stage=='write' and row and recovery.settings['policy']=='retry_failed' and not row['counts']['UNKNOWN']:
                    failed={p['id'] for p in row['points'] if p['outcome']=='FAILED'}
                    if failed:pending_retry=([t for t in write_tags if t['id'] in failed],{k:v for k,v in write_values.items() if k in failed},{k:v for k,v in write_metadata.items() if k in failed})
                self.log(ident,'WARNING',f'{type(error).__name__}; recovery policy {recovery.settings["policy"]}')
                if not await recovery.wait(error,state,row,write=stage=='write'):return
                if recovery.settings['policy']!='retry_failed' or row and row['counts']['UNKNOWN']:pending_retry=None
            finally:
                for client in (source,dest):
                    if client:
                        try:await asyncio.wait_for(client.close(),3)
                        except Exception:pass
    async def verify_cycle(self,verifier,client,values,state,scan=False):
        chosen=verification_selected(verifier.tags,verifier.config)
        if not scan:chosen=[t for t in chosen if t['id'] in values]
        if not chosen:return
        error=None
        actual=values if scan else {}
        if not scan:
            try:actual=await client.read(chosen)
            except BaseException as failure:error=failure
        row=verifier.evaluate(chosen,actual,None if scan else values,getattr(client,'last_observations',{}),error,getattr(client,'last_read_transaction',None))
        state['verification']={k:row[k] for k in ('id','outcome','counts','threshold_exceeded')}
        if error and (verifier.config['legacy'] or not isinstance(error,ValueError)):raise error
        failed=row['threshold_exceeded'] or row['inconclusive']
        if failed and verifier.config['on_failure']=='stop':
            point=next((p for p in row['points'] if p['outcome']!='PASS' and p['issues']!=['NOT_SELECTED']),None)
            if verifier.config['legacy'] and point:
                raise ValueError(f"Readback mismatch on {point['tag']}: wrote {point['expected']}, read {point['actual']}")
            raise ValueError('Data verification '+row['outcome']+': '+(', '.join(point['issues']) if point else 'No usable evidence'))

    async def serve(self,job,config,tags,state):
        interval=float(job.get('interval',2));server=None;serial_lock=None
        try:
            if config['protocol']=='opcua':
                server=Server();await server.init();server.set_endpoint(config['url']);server.set_server_name(config['name'])
                server.set_security_policy([ua.SecurityPolicyType.NoSecurity]);idx=await server.register_namespace('urn:protocol-workbench:simulator')
                folder=await server.nodes.objects.add_folder(idx,'Simulation');nodes={}
                for t in tags:
                    node=await folder.add_variable(ua.NodeId(t['tag'],idx),t['tag'],ua.Variant(initial(t,job.get('simulation',{})),getattr(ua.VariantType,t['dtype'])))
                    await node.set_writable();nodes[t['id']]=node
                await server.start()
            else:
                # DataBlock begins at one because ModbusDeviceContext translates protocol offsets by +1.
                device=ModbusDeviceContext(**{k:ModbusSequentialDataBlock(1,[0]*65536) for k in ('co','di','hr','ir')})
                for t in tags:
                    if 'initial_value' in t:
                        area=t.get('area','holding');v=initial(t,job.get('simulation',{}));raw=[bool(v)] if area in ('coil','discrete') else encode(v,t['dtype'],t.get('order','ABCD'))
                        device.setValues({'holding':3,'input':4,'coil':1,'discrete':2}[area],int(t['address']),raw)
                context=ModbusServerContext(devices={int(config.get('device_id',1)):device},single=False)
                if is_rtu(config):
                    serial_lock=lock(config);server=ModbusSerialServer(context,port=config['serial_port'],**options(config))
                else:server=ModbusTcpServer(context,address=(config['host'],int(config.get('port',1502))))
                await server.serve_forever(background=True)
            self.log(job['id'],'INFO','Simulator server listening')
            while True:
                await self.boundary(job['id'])
                start=time.monotonic();slot=int(time.time()/interval);values={t['id']:(initial(t,job.get('simulation',{})) if state['cycles']==0 and 'initial_value' in t else generated(t,job.get('simulation',{}),slot)) for t in tags}
                if job.get('dynamic',True):
                    from write_access import CONTEXT as WRITE
                    if WRITE.get():WRITE.get().check()
                    for t in tags:
                        if config['protocol']=='opcua':await nodes[t['id']].write_value(ua.Variant(values[t['id']],getattr(ua.VariantType,t['dtype'])))
                        else:
                            area=t.get('area','holding');fc={'holding':3,'input':4,'coil':1,'discrete':2}[area]
                            raw=[bool(values[t['id']])] if area in ('coil','discrete') else encode(values[t['id']],t['dtype'],t.get('order','ABCD'))
                            result=device.setValues(fc,int(t['address']),raw)
                            if result is not None:raise ValueError(f'Modbus datastore update failed: {result}')
                else:
                    values={}
                    for t in tags[:100]:
                        if config['protocol']=='opcua':
                            observed=await nodes[t['id']].read_data_value(raise_on_bad_status=False);values[t['id']]=observed.Value.Value
                        else:
                            from model import decode,width
                            area=t.get('area','holding');raw=device.getValues({'holding':3,'input':4,'coil':1,'discrete':2}[area],int(t['address']),1 if area in ('coil','discrete') else width(t['dtype']))
                            values[t['id']]=bool(raw[0]) if area in ('coil','discrete') else decode(raw,t['dtype'],t.get('order','ABCD'))
                elapsed=time.monotonic()-start
                state.update(state='RUNNING',cycles=state['cycles']+1,updated=time.time(),duration=round(elapsed,4),points=len(tags),values=[{'tag':t['tag'],'value':view_value(values.get(t['id']),t['dtype']),'dtype':t['dtype'],'units':t.get('units','')} for t in tags[:100]])
                await asyncio.sleep(max(0.01,interval-elapsed))
        finally:
            try:
                if server:
                    if config['protocol']=='opcua':await server.stop()
                    else:await server.shutdown()
            finally:
                if serial_lock:serial_lock.close()
