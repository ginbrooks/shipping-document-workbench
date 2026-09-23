"""Small explicit actions; no I/O or model calls during screen rendering."""
from copy import deepcopy
from uuid import uuid4
from shipping.repository import now
from .catalog import CATALOG, input_hash


def fact_object(values,**extra):
 return dict(values=values,evidence={k:[{'source':'manual','quote':'本票手动建立','value':v}] for k,v in values.items()},conflicts={},**extra)


def add_product(job,contract_no,name):
 if not name.strip():raise ValueError('请填写产品名称')
 j=deepcopy(job)
 contract=next((c for c in j['contracts'] if c['values'].get('contract_no')==contract_no.strip() and contract_no.strip()),None)
 if not contract:
  contract=fact_object({'contract_no':contract_no.strip()} if contract_no.strip() else {},id='c'+uuid4().hex[:12],kind='other');j['contracts'].append(contract)
 j['products'].append(fact_object({'name_cn':name.strip()},id='p'+uuid4().hex[:12],contract_id=contract['id'],batches=[]))
 j['audit'].append({'at':now(),'event':'product_added','contract':contract['id']});j['manual_entry']=True
 return j


def select_document(job,kind):
 if kind not in CATALOG:raise ValueError('未知文件类型')
 j=deepcopy(job);j['active_document']=kind
 if kind not in j['targets']:j['targets'].append(kind)
 return j


def current_outputs(job,kind):
 return [o for o in job.get('outputs',[]) if o['type']==kind and o.get('input_hash')==input_hash(job,kind) and not o.get('superseded')]
