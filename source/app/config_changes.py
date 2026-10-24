"""Conservative configuration updates that do not disturb running workers."""
def require_connection_additions_only(old, new):
    for key in set(old)|set(new):
        if key not in ('connections',) and old.get(key)!=new.get(key):
            raise ValueError('While jobs or tests run, only new connections can be saved. Stop them before changing existing configuration.')
    current={c['id']:c for c in old['connections']}
    incoming={c['id']:c for c in new['connections']}
    for key,c in current.items():
        if incoming.get(key)!=c:
            raise ValueError('While jobs or tests run, existing connections cannot be changed or removed. Add a new connection or stop the jobs first.')

def require_active_unchanged(old,new,active_ids):
    old_jobs={j['id']:j for j in old['jobs']};new_jobs={j['id']:j for j in new['jobs']}
    old_connections={c['id']:c for c in old['connections']};new_connections={c['id']:c for c in new['connections']}
    used=set()
    for key in active_ids:
        job=old_jobs[key]
        if new_jobs.get(key)!=job:raise ValueError(f"Running job {job.get('name',key)} cannot be changed or removed; stop that job first")
        used.add(job['connection'])
        if job.get('source'):used.add(job['source'])
    for key in used:
        if new_connections.get(key)!=old_connections[key]:raise ValueError(f"Connection {old_connections[key]['name']} is used by a running workspace job; stop that job before editing it")
