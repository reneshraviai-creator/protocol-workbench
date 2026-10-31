const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=process.argv[2]||require('node:path').resolve(__dirname,'../..');const read=n=>fs.readFileSync(root+'/app/static/'+n,'utf8');
function functionSource(file,name){const source=read(file),start=source.indexOf('function '+name+'(');assert(start>=0,name);const end=source.indexOf('\n',start);return source.slice(start,end<0?undefined:end);}
// Exercise the actual permission function for each supported role.
for(const [role,blocked] of [['Engineer',false],['Administrator',false],['Operator',true],['Read-only',true]]){
 const buttons=['new-connection','edit-job','mapping-job'].map(action=>({dataset:{action},disabled:false}));
 const c={window:{workbenchRole:role},document:{querySelectorAll:()=>buttons},$:()=>null};vm.createContext(c);vm.runInContext(functionSource('security.js','securityApplyRole'),c);c.securityApplyRole();assert(buttons.every(b=>b.disabled===blocked),role);
}
// Check real job markup across lifecycle states (not a separate mock implementation).
for(const [state,pause,stop] of [['STOPPED',true,true],['RUNNING',false,false],['PAUSED',false,false],['COMPLETED',true,true]]){
 const c={project:{connections:[{id:'c',name:'PLC'}]},snapshot:{jobs:{j:{state}}},esc:String,stateBadge:String,page:'jobs'};vm.createContext(c);vm.runInContext(functionSource('app.js','jobCard'),c);const html=c.jobCard({id:'j',connection:'c',kind:'server',name:'PLC',interval:2});for(const [action,disabled] of [['pause',pause],['resume',pause],['stop',stop]]){const match=html.match(new RegExp('<button data-action="'+action+'"[^>]*>'));if(match)assert.equal(match[0].includes('disabled'),disabled,state+' '+action);}
}
// Restore routes against current device inventory and validate stale identifiers.
const c={window:{location:{hash:'#page=tags&device=b&tab=jobs'}},project:{connections:[{id:'a'},{id:'b'}]},titles:{tags:'Tags',dashboard:'Home'},page:'dashboard',selectedConnection:'a',studioId:'a',studioTab:'values',inspectorId:'',URLSearchParams};vm.createContext(c);vm.runInContext(read('engineer-ux.js').split('function uxRestoreRoute(){')[1].split('function uxRememberRoute')[0].replace(/^/,'function uxRestoreRoute(){'),c);
assert(c.uxRestoreRoute());assert.equal(c.page,'tags');assert.equal(c.selectedConnection,'b');assert.equal(c.studioId,'b');assert.equal(c.studioTab,'jobs');c.window.location.hash='#page=tags&device=deleted&tab=bad';c.uxRestoreRoute();assert.equal(c.selectedConnection,'');assert.equal(c.studioTab,'values');
console.log('PASS: Engineer permissions, stopped/active job controls and refresh-safe validated device navigation.');
// A monitor must exist before actions are offered; recording controls follow recording state.
const controls=['saveWatchlistForm','stopInspector','recordingForm','stopRecording'].map(name=>({name,disabled:false,getAttribute:()=>name+'()'}));
const monitorContext={page:'inspector',snapshot:{monitors:[]},inspectorId:'',inspectorLatest:null,uxActive:s=>s==='RUNNING',document:{querySelectorAll:()=>controls},$:()=>null};vm.createContext(monitorContext);
const source=read('engineer-ux.js');vm.runInContext(source.slice(source.indexOf('function uxInspectorControls(){'),source.indexOf('const uxNavigationRender')),monitorContext);monitorContext.uxInspectorControls();assert(controls.every(b=>b.disabled));
monitorContext.inspectorId='m';monitorContext.snapshot.monitors=[{id:'m',state:'RUNNING'}];monitorContext.inspectorLatest={id:'m',recording:null};monitorContext.uxInspectorControls();assert(!controls.find(b=>b.name==='recordingForm').disabled);assert(controls.find(b=>b.name==='stopRecording').disabled);
monitorContext.inspectorLatest.recording={state:'RECORDING'};monitorContext.uxInspectorControls();assert(controls.find(b=>b.name==='recordingForm').disabled);assert(!controls.find(b=>b.name==='stopRecording').disabled);
console.log('PASS: empty-monitor and active-recording action states.');
