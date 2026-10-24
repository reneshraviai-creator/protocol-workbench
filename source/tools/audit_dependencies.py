"""Read-only OSV assessment of exact runtime packages; no private app data sent."""
import argparse,datetime,json,urllib.request
from importlib import metadata
from pathlib import Path
from packaging.requirements import Requirement


def installed_closure(requirements):
    seen={}
    def visit(name):
        d=metadata.distribution(name);key=d.metadata['Name'].lower().replace('_','-')
        if key in seen:return
        seen[key]={'name':d.metadata['Name'],'version':d.version}
        for raw in d.requires or []:
            req=Requirement(raw)
            if req.marker is None or req.marker.evaluate({'extra':''}):visit(req.name)
    for line in requirements.read_text().splitlines():
        if '==' in line:visit(line.split('==')[0])
    return sorted(seen.values(),key=lambda p:p['name'].lower())


def assess(targets):
    packages=list({(p['name'].lower().replace('_','-'),p['version']):p for rows in targets.values() for p in rows}.values())
    report={'assessed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source':'https://api.osv.dev/v1/querybatch','scope':'Public PyPI package names/versions only. No CPython, bundled system/OpenSSL, OS vulnerability or exploitability certification. A planned lock is not evidence of native execution.','targets':targets,'packages':packages,'state':'INCOMPLETE'}
    try:
        query={'queries':[{'package':{'name':p['name'],'ecosystem':'PyPI'},'version':p['version']} for p in packages]}
        req=urllib.request.Request(report['source'],data=json.dumps(query).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=45) as r:results=json.load(r)['results']
        if len(results)!=len(packages):raise ValueError('Incomplete advisory response')
        report.update(state='COMPLETED',results=[{'package':p,**r} for p,r in zip(packages,results)],advisory_matches=sum(len(r.get('vulns',[])) for r in results))
    except Exception as e:report['error']=f'{type(e).__name__}: {e}'
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--environment-roots',type=Path,required=True);parser.add_argument('--lock',action='append',default=[],help='LABEL=PATH');parser.add_argument('--output',type=Path,required=True);parser.add_argument('--freeze',type=Path);args=parser.parse_args()
    targets={'macos-staging-installed':installed_closure(args.environment_roots)}
    if args.freeze:args.freeze.write_text(''.join(p['name']+'=='+p['version']+'\n' for p in targets['macos-staging-installed']))
    for item in args.lock:
        label,path=item.split('=',1);targets[label]=[dict(zip(('name','version'),line.split('==',1))) for line in Path(path).read_text().splitlines() if '==' in line]
    report=assess(targets);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ('targets','packages','results')},indent=2))
    raise SystemExit(0 if report['state']=='COMPLETED' and report['advisory_matches']==0 else 1)
