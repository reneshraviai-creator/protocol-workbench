'use strict';
// Contextual navigation only; protocol execution remains in the existing adapters.
const uxActive=s=>studioActive(s)||['QUEUED','STOPPING'].includes(s);
const uxCanEdit=()=>!window.workbenchRole||['Administrator','Engineer'].includes(window.workbenchRole);
function uxButton(label,code,kind='secondary',disabled=false){return `<button type="button" class="${kind}" onclick="${esc(code)}" ${disabled?'disabled':''}>${esc(label)}</button>`}
function uxTo(dest){page=dest;render()}
function uxDevice(key,tab='values'){studioOpen(key);studioTab=tab;render()}
function uxJob(key){const j=project.jobs.find(j=>j.id===key);if(!j)return toast('This workspace job no longer exists',true);uxDevice(j.connection,'jobs')}
function uxMonitor(key){const m=(snapshot.monitors||[]).find(m=>m.id===key);if(!m)return toast('This monitor is no longer available',true);inspectorId=key;selectedConnection=m.connection;studioId=m.connection;uxTo('inspector');refreshInspector()}
function uxEndpointList(connections){return `<ul class="ux-endpoints">${connections.map(c=>`<li><b>${esc(c.name)}</b><span>${esc(protocolLabel(c))}</span><code>${esc(connectionEndpoint(c))}</code></li>`).join('')||'<li>No configured endpoints</li>'}</ul>`}
function uxScenarioRuns(key){return runRows.filter(r=>r.scenario===key)}
function uxScenarioLink(key){return uxButton('Open saved test',`uxOpenScenario(${JSON.stringify(key)})`)}
function uxOpenScenario(key){uxTo('scenarios');const target=document.querySelector(`[data-scenario-id="${CSS.escape(key)}"]`);if(target){target.scrollIntoView({block:'center'});target.focus({preventScroll:true})}else toast('Saved test is removed or unavailable. Run evidence remains in Results.')}
function uxRunDevices(key){const devices=(snapshot.scenario_devices||[]).filter(d=>d.run===key);modal('Test devices & endpoints',`<p>These devices belong to this run's independent configuration, not the editable workspace.</p>${devices.map(d=>`<article class="ux-runtime"><b>${esc(d.name)}</b>${stateBadge(d.state)}<code>${esc(d.endpoint)}</code><span>${Number(d.points).toLocaleString()} points</span></article>`).join('')||'<p>No test-owned devices are currently active. Use View results for recorded evidence.</p>'}${uxButton('View results',`$('modal').close();showRun(${JSON.stringify(key)})`)}`,async()=>{},'Close')}
function uxRunningContents(){
 const jobs=project.jobs.filter(j=>uxActive(snapshot.jobs[j.id]?.state)||snapshot.jobs[j.id]?.state==='ERROR');
 const monitors=(snapshot.monitors||[]).filter(m=>uxActive(m.state)||m.state==='ERROR');
 const runs=runRows.filter(r=>uxActive(r.state));
 return `<p class="hint">Entire workspace · live jobs, receiving monitors and active test runs. A saved template never runs. Error rows need attention; RUNNING does not prove delivery.</p><h2>Workspace jobs <span class="muted">(${jobs.length})</span></h2>${jobs.map(j=>{const c=project.connections.find(c=>c.id===j.connection),src=project.connections.find(c=>c.id===j.source),s=snapshot.jobs[j.id];return `<article class="ux-runtime"><div><b>${esc(j.name)}</b><small>${esc(j.kind)} · ${esc(studioMode(j))} · every ${j.interval}s</small></div>${stateBadge(s.state)}<div class="ux-runtime-endpoint">${src?esc(src.name)+' → ':''}${esc(c?.name||'Missing connection')}<code>${esc(c?connectionEndpoint(c):'Endpoint unavailable')}</code>${src?`<code>Source: ${esc(connectionEndpoint(src))}</code>`:''}</div><div class="actions">${uxButton('Device & live values',`uxDevice(${JSON.stringify(j.connection)})`)}${uxButton('Job & controls',`uxJob(${JSON.stringify(j.id)})`)}${uxButton('Evidence',`viewJob(project.jobs.find(j=>j.id===${JSON.stringify(j.id)}))`)}${uxActive(s.state)?uxButton('Stop job',`studioTry(()=>studioJob(${JSON.stringify(j.id)},'stop'))`,'danger'):''}</div>${s.error?`<p class="job-error">${esc(s.error)}</p>`:''}</article>`}).join('')||'<p>No active workspace jobs. <button class="secondary small" onclick="studioCreate()">Create simulator</button> or <button class="secondary small" onclick="uxTo(\'jobs\')">View configured jobs</button>.</p>'}
 <h2>Live monitors <span class="muted">(${monitors.length})</span></h2>${monitors.map(m=>{const c=project.connections.find(c=>c.id===m.connection);return `<article class="ux-runtime"><b>${esc(m.name||c?.name||'Monitor')}</b>${stateBadge(m.state)}<code>${esc(c?connectionEndpoint(c):'Endpoint not in this workspace')}</code><div class="actions">${uxButton('Live values / trends',`uxMonitor(${JSON.stringify(m.id)})`)}${c?uxButton('Device',`uxDevice(${JSON.stringify(c.id)})`):''}${uxActive(m.state)?uxButton('Stop monitor',`studioTry(async()=>{await api(${JSON.stringify('monitors/'+m.id+'/stop')},'POST',{});await poll()})`,'danger'):''}</div></article>`}).join('')||'<p>No active monitors. Open a device and select Start read-only monitor.</p>'}
 <h2>Automated test runs <span class="muted">(${runs.length})</span></h2>${runs.map(r=>`<article class="ux-runtime"><b>${esc(r.name)}</b>${stateBadge(r.state)}<small>Run ${esc(r.id.slice(0,8))} · independent test snapshot</small><div class="actions">${uxScenarioLink(r.scenario)}${uxButton('Devices & endpoints',`uxRunDevices(${JSON.stringify(r.id)})`)}${uxButton('Progress / results',`showRun(${JSON.stringify(r.id)})`)}${uxButton('Stop this test',`stopScenario(${JSON.stringify(r.id)})`,'danger')}</div></article>`).join('')||'<p>No active automated tests. <button class="secondary small" onclick="uxTo(\'scenarios\')">Open Tests & templates</button>.</p>'}`;
}
function uxRunningPage(){return `<section class="panel ux-running"><div class="toolbar"><h2>What is running, and where?</h2>${uxButton('All configured jobs',"uxTo('jobs')")}</div><div id="ux-running-list">${uxRunningContents()}</div></section>`}
function uxTasks(){modal('Choose an engineering task',`<div class="ux-taskgrid">${[
 ['Create a continuous simulator','studioCreate()','OPC UA, Modbus TCP or serial RTU; choose a device symbol.'],
 ['Create a timed test','simulationWizard()','A guided simulation that stops automatically.'],
 ['Connect / scan a device','deviceWizard()','Configure an external device, browse or import its tags.'],
 ['Import an OPC UA server','opcuaImportWizard()','Browse and select tags, or import the available tag tree.'],
 ['Find running activity',"uxTo('running')",'Jobs, ports, monitors and active tests in one place.'],
 ['Bridge two devices',"startEngineeringTask('route')",'Configure source, destination and tag mappings.'],
 ['Automate / reuse a test',"uxTo('scenarios')",'Create a test, use a template, then inspect its run.'],
 ['Record / trend live values',"uxTo('inspector')",'Read-only acquisition, watchlists and CSV recording.']
 ].map(([label,code,description])=>`<article class="card">${uxButton(label,`$('modal').close();${code}`,'secondary',/studioCreate|simulationWizard|deviceWizard|opcuaImportWizard|startEngineeringTask/.test(code)&&!uxCanEdit())}<p>${description}</p></article>`).join('')}</div>`,async()=>{},'Close')}
function uxContext(){const c=project.connections.find(c=>c.id===selectedConnection),contextPages=['tags','inspector','bridges','verification','data-verification','reports','health','performance','logs','jobs'];return `<div class="ux-context"><div><b>${esc(titles[page]||'Workspace')}</b><span>${contextPages.includes(page)&&c?(page==='tags'?'Editing device: ':['inspector','jobs'].includes(page)?'Device shortcut: ':'Workspace evidence · device shortcut: ')+esc(c.name)+' · '+esc(connectionEndpoint(c)):'Local engineering workspace'}</span></div><div class="actions">${contextPages.includes(page)&&c?uxButton('Open selected device',`uxDevice(${JSON.stringify(c.id)})`):''}${uxButton('Running now',"uxTo('running')")}${uxButton('Choose a task','uxTasks()')}</div></div>`}
function uxScenarioCard(s){const rows=uxScenarioRuns(s.id),activeRows=rows.filter(r=>uxActive(r.state)),latest=rows[0];return `<article class="card ux-scenario" data-scenario-id="${esc(s.id)}" tabindex="-1"><span class="eyebrow">SAVED TEST · ${s.steps.length} STEPS · REV ${s.revision||1}</span><h2>${esc(s.name)}</h2><p>${esc(s.description||'Reusable test configuration')}</p>${uxEndpointList(s.project.connections)}<div class="ux-relationship">${activeRows.length?activeRows.map(r=>stateBadge(r.state)+uxButton('Open active run',`showRun(${JSON.stringify(r.id)})`)+uxButton('Find running devices',`uxRunDevices(${JSON.stringify(r.id)})`)+uxButton('Stop this test',`stopScenario(${JSON.stringify(r.id)})`,'danger')).join(''):latest?`Latest run: ${stateBadge(latest.state)} ${uxButton('View latest results',`showRun(${JSON.stringify(latest.id)})`)}`:'Not running · no retained runs'}<small>This definition owns its own configuration. Workspace jobs with similar names are separate.</small></div><div class="actions">${uxButton('Run test',`runScenario(${JSON.stringify(s.id)})`,'',!!window.workbenchRole&&window.workbenchRole==='Read-only')}${uxButton('Edit test',`scenarioForm(${JSON.stringify(s.id)})`,'secondary',!uxCanEdit())}${uxButton('Replace workspace with this test',`scenarioWorkspace(${JSON.stringify(s.id)})`,'secondary',!uxCanEdit())}</div><details class="ux-more"><summary>Reuse, history & remove</summary><div class="actions">${uxButton('Duplicate',`duplicateScenario(${JSON.stringify(s.id)})`,'secondary',!uxCanEdit())}${uxButton('Version history',`scenarioVersions(${JSON.stringify(s.id)})`)}${uxButton('Save as template',`saveScenarioTemplate(${JSON.stringify(s.id)})`,'secondary',!uxCanEdit())}${uxButton('Export template',`exportScenarioTemplate(${JSON.stringify(s.id)})`)}${uxButton('Delete saved test',`uxArchive('scenarios',${JSON.stringify(s.id)})`,'danger',!uxCanEdit()||!!activeRows.length)}</div>${activeRows.length?'<p>Stop the active run before removing this definition.</p>':''}</details></article>`}
scenarioPage=function(){return `<section class="hero-panel"><span class="eyebrow">TESTS & TEMPLATES</span><h2>Prepare a test. Run it. Review the results.</h2><p>Template → saved test with endpoints → run with progress and results.</p><div class="actions">${uxButton('Create timed simulation','simulationWizard()','',!uxCanEdit())}${uxButton('Build automated test','scenarioForm()','secondary',!uxCanEdit())}${uxButton('Running now',"uxTo('running')")}${uxButton('Trash / restore','uxTrash()')}</div><details class="ux-more"><summary>Signal-path tools, server import & examples</summary><div class="actions">${uxButton('Import OPC UA server','opcuaImportWizard()')}${uxButton('Verify a signal path','signalPathForm()')}${uxButton('Bulk signal-path test','bulkSignalPathForm()')}${uxButton('Local signal-path example','localSignalPath()')}${uxButton('Local Modbus example',"createLocalScenario().catch(e=>toast(e.message,true))")}${uxButton('20-point bulk example',"createLocalScenario(20).catch(e=>toast(e.message,true))")}</div></details></section><h2>Saved tests</h2><div class="scenario-grid">${scenarioRows.map(uxScenarioCard).join('')||'<p>No saved tests. Create one above or use a template below.</p>'}</div>${templateLibrary()}`};
templateLibrary=function(){return `<section class="panel"><div class="toolbar"><h2>Reusable templates</h2><div class="actions">${uxButton('Import template file','importTemplateFile()','',!uxCanEdit())}${uxButton('Trash / restore','uxTrash()')}</div></div><p>Templates are saved recipes: they never run. Create a test from a template, configure its endpoints, then run that test.</p><div class="grid">${templateRows.map(t=>{const derived=scenarioRows.filter(s=>s.source_template===t.id);return `<article class="card"><span class="pill">TEMPLATE · NOT EXECUTABLE</span><h3>${esc(t.name)}</h3><p>${t.template.parameters.length} devices · ${t.template.scenario.steps.length} steps</p><div class="actions">${uxButton('Create test from template',`useTemplate(${JSON.stringify(t.id)})`,'',!uxCanEdit())}${uxButton('Export',`downloadLibraryTemplate(${JSON.stringify(t.id)})`)}${uxButton('Delete template',`uxArchive('templates',${JSON.stringify(t.id)})`,'danger',!uxCanEdit())}</div>${t.source_scenario&&scenarioRows.some(s=>s.id===t.source_scenario)?`<p>Saved from: ${uxScenarioLink(t.source_scenario)}</p>`:''}${derived.length?`<p>Created tests:</p>${derived.map(s=>uxButton(s.name,`uxOpenScenario(${JSON.stringify(s.id)})`)).join('')}`:'<p class="hint">No linked tests recorded. Older imports may not have a recorded link.</p>'}</article>`}).join('')||'<p>No templates yet. Save a test as a template, or import a template file.</p>'}</div></section>`};
async function uxArchive(kind,key){const row=(kind==='templates'?templateRows:scenarioRows).find(r=>r.id===key);if(!row)return;modal('Delete '+(kind==='templates'?'template':'saved test'),`<p>Move <b>${esc(row.name)}</b> to Trash? Restore it later from Tests & templates → Trash / restore.</p><p>${kind==='templates'?'Created tests, their jobs and active runs are independent and remain unchanged.':'Run history and evidence remain. Active runs must be stopped before removing their saved definition.'}</p>`,async()=>{await api(`library-items/${kind}/${encodeURIComponent(key)}/archive`,'POST',{});await refreshScenarios();toast('Moved to Trash. Restore is available.')},'Move to Trash')}
async function uxTrash(){try{const rows=await api('library-trash');modal('Templates & tests · Trash',`<p>Restore an item to its library. Restoring does not start any communication.</p>${rows.map(r=>`<article class="ux-runtime"><b>${esc(r.name)}</b><span>${r.kind==='templates'?'Template':'Saved test'}</span>${uxButton('Restore',`uxRestore(${JSON.stringify(r.kind)},${JSON.stringify(r.id)})`,'secondary',!uxCanEdit())}</article>`).join('')||'<p>Trash is empty.</p>'}`,async()=>{},'Close')}catch(e){toast(e.message,true)}}
async function uxRestore(kind,key){try{await api(`library-items/${kind}/${encodeURIComponent(key)}/restore`,'POST',{});await refreshScenarios();await uxTrash();render();toast('Restored. No jobs started.')}catch(e){toast(e.message,true)}}
const uxOldRunPage=runPage;
runPage=function(){return `<div class="actions">${uxButton('Running now',"uxTo('running')")}${uxButton('Saved tests & templates',"uxTo('scenarios')")}</div>`+uxOldRunPage()};
const uxOldJobCard=jobCard;
jobCard=function(j){const c=project.connections.find(c=>c.id===j.connection);const card=uxOldJobCard(j).replace('<p>Run progress:', '<p data-ux-progress>Run progress:');return card.slice(0,-6)+`<div class="ux-job-context">${c?`<code>${esc(c.name)} · ${esc(connectionEndpoint(c))}</code>${uxButton('Device & live values',`uxDevice(${JSON.stringify(c.id)})`)}`:''}${uxButton('All running activity',"uxTo('running')")}</div></div>`};
const uxOldStudioCard=studioCard;
studioCard=function(c){return uxOldStudioCard(c).replace('</article>',`<div class="ux-card-links">${uxButton('Jobs & dependencies',`uxDevice(${JSON.stringify(c.id)},'links')`)}${uxButton('Present',`studioPresentDevice(${JSON.stringify(c.id)})`)}</div></article>`)};
const uxOriginalRefresh=studioRefresh;
studioRefresh=async function(){await uxOriginalRefresh();uxUpdateActivity();};
function uxUpdateActivity(){document.querySelectorAll('[data-job-card]').forEach(card=>{const s=snapshot.jobs[card.dataset.jobCard];if(!s)return;const values=card.querySelectorAll('.metrics b');if(values[1]&&s.points!==undefined)values[1].textContent=s.points;if(values[2])values[2].textContent=s.duration?Math.round(s.duration*1000)+'ms':'—';const progress=card.querySelector('[data-ux-progress]');if(progress&&s.progress_percent!==undefined)progress.textContent=`Run progress: ${Math.round(s.progress_percent)}% · ${Math.round(s.run_elapsed_seconds)} seconds`;});const count=project.jobs.filter(j=>uxActive(snapshot.jobs[j.id]?.state)).length+(snapshot.monitors||[]).filter(m=>uxActive(m.state)).length+runRows.filter(r=>uxActive(r.state)).length;const badge=$('ux-running-count');if(badge)badge.textContent=String(count);const list=$('ux-running-list');if(page==='running'&&list){const html=uxRunningContents();if(list.innerHTML!==html)list.innerHTML=html}}
const uxOriginalRender=render;
render=function(){uxOriginalRender();if(!$('ux-context')&&!studioPresentation){const node=document.createElement('section');node.id='ux-context';node.innerHTML=uxContext();$('content').prepend(node)}const running=$('running-state');if(running){running.setAttribute('role','button');running.setAttribute('tabindex','0');running.setAttribute('aria-label','Open running jobs, monitors and tests');running.onclick=()=>uxTo('running');running.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();uxTo('running')}}}uxUpdateActivity()};
titles.running='Running now';subtitles.running='Find every job, monitor and active test, with its device and endpoint.';titles.scenarios='Tests & templates';
// Stable controls: do not replace template cards or job controls on every poll.
let uxRenderedSignature='';
function uxPageSignature(){
 if(page==='scenarios')return JSON.stringify([scenarioRows,templateRows,runRows.map(r=>[r.id,r.scenario,r.state])]);
 if(page==='jobs')return JSON.stringify([project.jobs,Object.entries(snapshot.jobs).map(([id,s])=>[id,s.state,s.error])]);
 if(page==='runs')return JSON.stringify(runRows.map(r=>[r.id,r.state,r.steps.map(s=>s.state),r.error]));
 return null;
}
function uxPollRenderNeeded(){const signature=uxPageSignature();return signature===null||signature!==uxRenderedSignature}
const uxRenderWithContext=render;
render=function(){uxRenderWithContext();uxRenderedSignature=uxPageSignature();};
function uxOwnerLinks(owner){
 if(typeof owner!=='string')return '';
 const job=project.jobs.find(j=>'job:'+j.id===owner);if(job)return uxButton('Open job / device',`uxJob(${JSON.stringify(job.id)})`,'secondary small');
 const monitor=(snapshot.monitors||[]).find(m=>'monitor:'+m.id===owner);if(monitor)return uxButton('Open live monitor',`uxMonitor(${JSON.stringify(monitor.id)})`,'secondary small');
 const run=runRows.find(r=>'run:'+r.id===owner);if(run)return uxButton('Open run',`showRun(${JSON.stringify(run.id)})`,'secondary small');
 return '';
}

// URL contains navigation identifiers only; never credentials or project contents.
function uxRouteState(){return {page,device:project.connections.some(c=>c.id===selectedConnection)?selectedConnection:'',tab:typeof studioTab==='string'?studioTab:'values',monitor:typeof inspectorId==='string'?inspectorId:''}}
function uxRestoreRoute(){
 const query=new URLSearchParams(window.location.hash.slice(1));
 const destination=query.get('page');
 if(!destination||!Object.prototype.hasOwnProperty.call(titles,destination))return false;
 page=destination;
 const device=query.get('device');
 selectedConnection=project.connections.some(c=>c.id===device)?device:'';studioId=selectedConnection;
 const tab=query.get('tab');studioTab=['values','jobs','tools','links'].includes(tab)?tab:'values';
 inspectorId=query.get('monitor')||'';
 return true;
}
function uxRememberRoute(){
 const state=uxRouteState(),query=new URLSearchParams(state),hash='#'+query.toString();
 if(window.location.hash!==hash){
  const method=window.location.hash?'pushState':'replaceState';
  window.history[method](null,'',hash);
 }
}
function uxInspectorControls(){
 if(page!=='inspector')return;
 const monitor=(snapshot.monitors||[]).find(m=>m.id===inspectorId);
 const running=!!monitor&&uxActive(monitor.state);
 const recording=inspectorLatest?.id===inspectorId&&inspectorLatest?.recording?.state==='RECORDING';
 if(!monitor)for(const key of ['inspector-prev','inspector-next'])if($(key))$(key).disabled=true;
 const enabled={saveWatchlistForm:!!monitor&&!inspectorLatest?.temporary_demo,stopInspector:running,recordingForm:running&&!recording,stopRecording:recording,exportInspector:!!monitor,exportTrendCSV:!!monitor,freezeInspectorTrend:!!monitor};
 document.querySelectorAll('#content button[onclick]').forEach(button=>{
  const name=(button.getAttribute('onclick')||'').split('(')[0];
  if(Object.prototype.hasOwnProperty.call(enabled,name)){button.disabled=!enabled[name];button.title=enabled[name]?'':!monitor?'Open a live monitor first':'Unavailable in the current monitor state';}
 });
}
const uxNavigationRender=render;
render=function(){uxNavigationRender();uxRememberRoute();uxInspectorControls();};
window.addEventListener('popstate',()=>{if(!project)return;if($('modal')?.open)$('modal').close();if(uxRestoreRoute())render();});
const uxNavigationInspector=refreshInspector;
refreshInspector=async function(){await uxNavigationInspector();if(page!=='inspector')return;const monitor=(snapshot.monitors||[]).find(m=>m.id===inspectorId);if(monitor&&project.connections.some(c=>c.id===monitor.connection)){selectedConnection=monitor.connection;studioId=selectedConnection;const context=$('ux-context');if(context){const html=uxContext();if(context.innerHTML!==html)context.innerHTML=html;}uxRememberRoute();}uxInspectorControls();};
