from copy import deepcopy
from .repository import canonical
from .ingestion import digest
from .planning import geometry_hash
from .templating import build_context,load_spec


def document_hash(s,p,kind,scope,spec=None):
    context=build_context(s,p,kind,scope)
    context.pop('revision',None)
    return digest(canonical({'context':context,'geometry':geometry_hash(s),'template':spec or load_spec(kind)}).encode())


def diff(before,after,prefix=''):
    rows=[]
    if isinstance(before,dict) and isinstance(after,dict):
        for key in sorted(before.keys()|after.keys()):rows+=diff(before.get(key),after.get(key),prefix+'.'+key if prefix else key)
    elif isinstance(before,list) and isinstance(after,list):
        for i in range(max(len(before),len(after))):rows+=diff(before[i] if i<len(before) else None,after[i] if i<len(after) else None,f'{prefix}.{i}')
    elif before!=after:rows.append({'field_path':prefix,'before':before,'after':after})
    return rows
