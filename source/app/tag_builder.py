"""Build typed tag groups without modifying existing project configuration."""
import copy
import re
from model import dtype, uid, width, TYPES, validate_project, default_project


def integer(spec, name, default, minimum, maximum):
    value = spec.get(name, default)
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)):
        raise ValueError(f'{name.replace("_", " ")} must be a whole number')
    number = int(value)
    if not minimum <= number <= maximum: raise ValueError(f'{name.replace("_", " ")} must be {minimum}..{maximum}')
    return number


def generate_tags(connection, spec):
    c = copy.deepcopy(connection)
    existing = c.get('tags', [])
    count = integer(spec, 'count', 1000, 1, 20000)
    if len(existing) + count > 20000: raise ValueError('Maximum 20,000 tags per connection; reduce the new tag count')
    start = integer(spec, 'start_number', 1, 0, 999999999)
    digits = integer(spec, 'digits', 4, 0, 12)
    kind = dtype(spec.get('dtype', 'Float'))
    if kind not in TYPES: raise ValueError('Unsupported datatype')
    prefix = spec.get('prefix', 'Point_'); suffix = spec.get('suffix', '')
    if not isinstance(prefix, str) or not isinstance(suffix, str) or len(prefix)+len(suffix)>240:
        raise ValueError('Prefix and suffix must be text totalling at most 240 characters')
    if any(ord(ch)<32 for ch in prefix+suffix): raise ValueError('Names cannot contain control characters')
    first_index = max((int(t.get('index', i+1)) for i,t in enumerate(existing)), default=0)+1
    tags=[]
    if c['protocol']=='modbus':
        area=spec.get('area','holding');order=spec.get('order','ABCD')
        if area in ('coil','discrete') and kind!='Boolean': raise ValueError('Coils and discrete inputs require Boolean datatype')
        size=1 if area in ('coil','discrete') else width(kind)
        address=integer(spec,'address',0,0,65535)
        stride=integer(spec,'stride',size,size,65536)
        if address+(count-1)*stride+size>65536: raise ValueError('Generated addresses exceed the Modbus address space')
    elif c['protocol']=='opcua':
        namespace=integer(spec,'namespace',2,0,65535)
        node_prefix=spec.get('node_prefix','')
        if not isinstance(node_prefix,str): raise ValueError('NodeId prefix must be text')
    else: raise ValueError('Unsupported protocol')
    for offset in range(count):
        name=prefix+str(start+offset).zfill(digits)+suffix
        tag={'id':uid(),'tag':name,'dtype':kind,'index':first_index+offset}
        if c['protocol']=='modbus':tag.update(address=address+offset*stride,area=area,order=order)
        else:
            from asyncua import ua
            tag['nodeid']=ua.NodeId(node_prefix+name,namespace).to_string()
        tags.append(tag)
    c['tags']=existing+tags
    if c['protocol']=='opcua':
        ids=[t['nodeid'] for t in c['tags'] if t.get('nodeid')]
        if len(ids)!=len(set(ids)): raise ValueError('Generated NodeIds conflict with existing tags; change the namespace, prefix or numbering')
    p=default_project();p['connections']=[c];validate_project(p)
    return tags
