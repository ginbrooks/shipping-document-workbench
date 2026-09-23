from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json,re,uuid
from datetime import date
from shipping.repository import canonical, now
from .cleaning import normal_date,numeric_text,value_in_quote,repair_source

LABELS={'contract_no':'合同号','invoice_no':'本票发票号','invoice_date':'本票发票日期','name_cn':'产品中文名称','name_en':'产品英文名称','strength':'规格','ordered_quantity':'合同总数量（基础单位）','quantity':'本次出运数量（基础单位）','base_unit':'基础单位（如 TAB）','units_per_inner':'每内包装基础数','inners_per_carton':'每箱内包装数','batch_no':'批号','mfg_date':'生产日期','exp_date':'有效期','cartons':'本次箱数','pallets':'运输托数','net_kg':'本次总净重 kg','gross_kg':'本次含托总毛重 kg','volume_m3':'本次体积 m³','customer_unit_price':'客户单价（每基础单位）','customer_currency':'客户币种','customs_unit_price':'报关单价（每基础单位）','customs_currency':'报关币种','hs_code':'HS 编码','storage':'储存条件','packing_text':'包装说明','samples_text':'随货样品名称、数量与单位（无则填“无”）','sample_batches':'样品批号（多批用分号）','moc_no':'MOC 号（不适用填“无”）','insurance_no':'保险号（不适用填“无”）','shipping_date':'托书日期','warehouse_date':'入仓日期','airwaybill_no':'运单号','flight':'航班','flight_date':'航班日期','freight_note':'本票运费说明','carton_net_kg':'单箱净重 kg','carton_gross_kg':'单箱毛重 kg','purchase_unit_price':'采购单价','purchase_currency':'采购币种'}
LABELS.update({'chargeable_kg':'该产品计费重量 kg','total_chargeable_kg':'整票计费重量 kg'})
LABELS.update({'carton_length_mm':'外箱长 mm','carton_width_mm':'外箱宽 mm','carton_height_mm':'外箱高 mm','carton_volume_m3':'单箱体积 m³'})
BATCH_FIELDS={'batch_no','mfg_date','exp_date','quantity','cartons'}
CONTRACT_FIELDS={'contract_no','invoice_no','invoice_date'}
SHIPMENT_FIELDS={'shipping_date','warehouse_date','airwaybill_no','flight','flight_date','freight_note','total_chargeable_kg'}
PRODUCT_FIELDS=set(LABELS)-CONTRACT_FIELDS-SHIPMENT_FIELDS
NUMERIC={'ordered_quantity','quantity','units_per_inner','inners_per_carton','cartons','pallets','net_kg','gross_kg','volume_m3','customer_unit_price','customs_unit_price','carton_net_kg','carton_gross_kg','purchase_unit_price','chargeable_kg','total_chargeable_kg'}
NUMERIC.update({'carton_length_mm','carton_width_mm','carton_height_mm','carton_volume_m3'})
INTEGER={'ordered_quantity','quantity','units_per_inner','inners_per_carton','cartons','pallets'}
from .catalog import CATALOG, fields_for
LABELS.update({'report_no':'鉴定报告编号','report_copies':'需要份数','authorization_date':'委托日期','collector_company':'取件单位','collector_name':'取件人','collector_phone':'联系电话'})
SHIPMENT_FIELDS.update({'authorization_date','collector_company','collector_name','collector_phone'})
PRODUCT_FIELDS.update({'report_no','report_copies'})
NUMERIC.add('report_copies');INTEGER.add('report_copies')
from .business_flows import NEW_LABELS,NEW_CONTRACT_LABELS,NEW_PRODUCT_LABELS,NEW_SHIPMENT_LABELS
LABELS.update(NEW_LABELS);CONTRACT_FIELDS.update(NEW_CONTRACT_LABELS);PRODUCT_FIELDS.update(NEW_PRODUCT_LABELS);SHIPMENT_FIELDS.update(NEW_SHIPMENT_LABELS)
NUMERIC.update({'customs_quantity','declaration_unit_price','customs_freight','customs_insurance','customs_other_fee'})
NUMERIC.update({'customs_package_count','customs_main_net_kg'});INTEGER.add('customs_package_count')
REQUIRED={kind:set(spec.product) for kind,spec in CATALOG.items()}


def compact(s):return re.sub(r'\s+','',str(s)).replace(',','').replace('，','').casefold()

def checked(key,value):
 if key not in LABELS:raise ValueError('UNKNOWN_FIELD: '+key)
 if value is None or str(value).strip()=='':return None
 value=str(value).strip()
 if len(value)>1500:raise ValueError('VALUE_TOO_LONG')
 if key in NUMERIC:
  try:d=Decimal(numeric_text(key,value))
  except InvalidOperation:raise ValueError(LABELS[key]+'必须为数字')
  if not d.is_finite() or d<0 or (key in INTEGER and d!=d.to_integral_value()):raise ValueError(LABELS[key]+'必须为有效非负数'+('整数' if key in INTEGER else ''))
  if key in {'quantity','units_per_inner','inners_per_carton','report_copies','customs_quantity','customs_package_count'} and d==0:raise ValueError(LABELS[key]+'必须大于零')
  value=format(d,'f')
 if key=='transport_mode':
  value={'AIR':'空运','BY AIR':'空运','TRUCK':'陆运','ROAD':'陆运','BY ROAD':'陆运','BY TRUCK':'陆运'}.get(value.upper(),value)
  if value not in ('空运','陆运'):raise ValueError('运输方式请选择空运或陆运')
 if key.endswith('currency') and not re.fullmatch('[A-Za-z]{3}',value):raise ValueError('币种须为三位代码')
 if key.endswith('_date'):
  try:value=normal_date(value,key in ('mfg_date','exp_date'))
  except ValueError:raise ValueError(LABELS[key]+'请用有效日期 YYYY-MM-DD；批次日期可保留 YYYY-MM')
 return value.upper() if key.endswith('currency') else value


def normalise_result(raw,sources):
 if not isinstance(raw,dict) or set(raw)-{'contracts','products','shipment'}:raise ValueError('INVALID_RESULT_STRUCTURE')
 job={'contracts':[],'products':[],'shipment':{'values':{},'evidence':{},'conflicts':{}},'issues':[],'audit':[]}
 ids=set()
 def facts(target,items,allowed,kind='other',purchase_files=None):
  if not isinstance(items,list):raise ValueError('INVALID_FACTS')
  for f in items:
   key=f.get('key');value=f.get('value');source=f.get('source');quote=f.get('quote','')
   if key not in allowed:raise ValueError('UNKNOWN_FIELD: '+str(key))
   if value is None or str(value).strip()=='':continue
   original_source=source;source=repair_source(source,quote,sources)
   problem=None
   try:value=checked(key,value)
   except ValueError as e:problem=str(e)
   if not problem:
    if source not in sources or not quote or compact(quote) not in compact(sources[source]):problem='找不到原文依据'
    elif not value_in_quote(key,value,quote):problem='提取值与原文不一致'
    elif kind=='purchase' and (not purchase_files or source.split(':',1)[0] in purchase_files) and key in {'quantity','customer_unit_price','customer_currency','customs_unit_price','customs_currency','customs_quantity','declaration_unit_price'}:problem='采购合同不能作为本票出运量或客户/报关价格的依据'
   if problem:
    job['issues'].append({'key':(target.get('id') or 'shipment')+'.'+key,'message':problem,'source':source,'proposed_value':f.get('value'),'quote':quote});continue
   old=target['values'].get(key)
   if key in target['conflicts'] or (old is not None and compact(old)!=compact(value)):
    target['conflicts'].setdefault(key,[old] if old is not None else [])
    if value not in target['conflicts'][key]:target['conflicts'][key].append(value)
    target['values'].pop(key,None)
   else:target['values'][key]=value
   target['evidence'].setdefault(key,[]).append({'source':source,'quote':quote,'value':value})
 for c in raw.get('contracts',[]):
  if not isinstance(c.get('id'),str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,39}',c['id']) or c['id'] in ids:raise ValueError('模型返回的合同编号格式无效，未填写字段')
  if c.get('kind') not in ('purchase','sales','other'):raise ValueError('INVALID_CONTRACT_KIND')
  ids.add(c['id']);item={'id':c['id'],'kind':c['kind'],'values':{},'evidence':{},'conflicts':{}}
  facts(item,c.get('fields',[]),CONTRACT_FIELDS,c['kind']);job['contracts'].append(item)
 contract_ids=set(ids)
 for p in raw.get('products',[]):
  if not isinstance(p.get('id'),str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,39}',p['id']) or p['id'] in ids or p.get('contract_id') not in contract_ids:raise ValueError('模型返回的产品编号或合同归属无效，未填写字段')
  ids.add(p['id']);item={'id':p['id'],'contract_id':p['contract_id'],'values':{},'evidence':{},'conflicts':{}}
  kind=next(c['kind'] for c in job['contracts'] if c['id']==p['contract_id'])
  purchase_files={e['source'].split(':',1)[0] for c in job['contracts'] if c['id']==p['contract_id'] for e in c['evidence'].get('contract_no',[]) if ':' in e['source']}
  facts(item,p.get('fields',[]),PRODUCT_FIELDS,kind,purchase_files)
  item['batches']=[]
  for b in p.get('batches',[]):
   bid=b.get('id')
   if not isinstance(bid,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,39}',bid) or bid in ids:raise ValueError('模型返回的批次编号格式无效，未填写字段')
   ids.add(bid);batch={'id':bid,'values':{},'evidence':{},'conflicts':{}}
   facts(batch,b.get('fields',[]),BATCH_FIELDS,kind,purchase_files)
   batch_files={e['source'].split(':',1)[0] for e in batch['evidence'].get('batch_no',[])}
   for field in list(batch['values']):
    if field=='batch_no':continue
    evidence=batch['evidence'].get(field,[])
    if not batch_files or any(e['source'].split(':',1)[0] not in batch_files for e in evidence):
     job['issues'].append({'key':bid+'.'+field,'message':'该日期或数量的文件缺少可核验的同批号依据','proposed_value':batch['values'].pop(field),'source':evidence[0]['source'] if evidence else ''})
   item['batches'].append(batch)
  if len(item['batches'])>30:raise ValueError('TOO_MANY_BATCHES')
  # A product with batch records must not retain a misleading single batch/date slot.
  if item['batches']:
   for field in ('batch_no','mfg_date','exp_date'):
    item['values'].pop(field,None);item['evidence'].pop(field,None);item['conflicts'].pop(field,None)
  job['products'].append(item)
 facts(job['shipment'],raw.get('shipment',[]),SHIPMENT_FIELDS)
 if not job['products']:raise ValueError('NO_PRODUCTS_RECOGNIZED')
 if len(job['products'])>30:raise ValueError('TOO_MANY_PRODUCTS')
 return job


def questions(job,targets):
 out={}
 for kind in targets:
  from .catalog import owners
  for obj,owner in owners(job,kind):
   required=fields_for(kind,owner,optional=False,job=job);visible=fields_for(kind,owner,job=job)
   if owner=='product':
    if obj.get('batches'):required-={'batch_no','mfg_date','exp_date'};visible-={'batch_no','mfg_date','exp_date'}
    if 'sample_batches' in visible and obj['values'].get('samples_text') not in (None,'无'):required.add('sample_batches')
   for key in sorted(required|((set(obj.get('conflicts',{}))|set(obj.get('review_pending',[])))&visible)):
    if obj['values'].get(key) in (None,'') or key in obj.get('conflicts',{}) or key in obj.get('review_pending',[]):
     path=(obj.get('id') or 'shipment')+'.'+key
     out[path]={'key':path,'label':LABELS[key],'owner':obj['values'].get('batch_no') or obj['values'].get('name_cn') or obj['values'].get('contract_no') or obj.get('id') or '整票','reason':'资料冲突' if key in obj.get('conflicts',{}) else '资料缺失' if obj['values'].get(key) in (None,'') else '待核对','choices':obj.get('conflicts',{}).get(key,[])}
 return list(out.values())


def resolve(job,answers,reason):
 if not reason.strip():raise ValueError('ANSWER_REASON_REQUIRED')
 j=deepcopy(job);owners={x['id']:x for x in j['contracts']+j['products']};owners['shipment']=j['shipment'];changes=[]
 batch_ids=set()
 for p in j['products']:
  for b in p.get('batches',[]):owners[b['id']]=b;batch_ids.add(b['id'])
 for path,raw in answers.items():
  oid,key=path.split('.',1)
  if oid not in owners:raise ValueError('UNKNOWN_OWNER')
  allowed=BATCH_FIELDS if oid in batch_ids else SHIPMENT_FIELDS if oid=='shipment' else CONTRACT_FIELDS if oid in {c['id'] for c in j['contracts']} else PRODUCT_FIELDS
  if key not in allowed:raise ValueError('UNKNOWN_FIELD')
  value=checked(key,raw);o=owners[oid];before=o['values'].get(key)
  was_pending=key in o.get('review_pending',[])
  if before==value and key not in o['conflicts'] and not was_pending:continue
  if value is None:o['values'].pop(key,None)
  else:o['values'][key]=value
  o['conflicts'].pop(key,None)
  if before!=value or not o['evidence'].get(key):o['evidence'][key]=[{'source':'manual','quote':reason,'value':value}]
  o['review_pending']=[k for k in o.get('review_pending',[]) if k!=key]
  o.setdefault('confirmed',{})[key]=value
  j['issues']=[i for i in j['issues'] if i['key']!=path]
  changes.append({'key':path,'before':before,'after':value})
 if changes:derive(j)
 if changes:j['audit'].append({'at':now(),'reason':reason,'changes':changes});j['phase']='needs_input' if questions(j,j.get('targets',['customer'])) else 'ready'
 return j


def derive(job):
 from .defaults import apply_customs_defaults
 apply_customs_defaults(job)
 # Recompute only derived values; manual/quoted facts always take precedence.
 for p in job['products']:
  v=p['values'];e=p['evidence']
  for key in list(v):
   if (e.get(key) or [{}])[0].get('source')=='calculated':v.pop(key);e.pop(key,None)
  if all(v.get(k) for k in ('carton_length_mm','carton_width_mm','carton_height_mm')) and 'carton_volume_m3' not in v and 'carton_volume_m3' not in p['conflicts']:
   size=Decimal(v['carton_length_mm'])*Decimal(v['carton_width_mm'])*Decimal(v['carton_height_mm'])/Decimal(1_000_000_000)
   v['carton_volume_m3']=format(size,'f');e['carton_volume_m3']=[{'source':'calculated','quote':'外箱长 × 宽 × 高（mm）÷ 10⁹；不等于含托体积','value':v['carton_volume_m3']}]
  if all(v.get(k) for k in ('quantity','units_per_inner','inners_per_carton')) and 'cartons' not in v and 'cartons' not in p['conflicts']:
   n=Decimal(v['quantity'])/(Decimal(v['units_per_inner'])*Decimal(v['inners_per_carton']))
   if n==n.to_integral_value():
    v['cartons']=format(n,'f');e['cartons']=[{'source':'calculated','quote':'本票数量 ÷ 每内包装基础数 ÷ 每箱内包装数（整箱）','value':v['cartons']}]
  if all(v.get(k) for k in ('cartons','carton_net_kg')) and 'net_kg' not in v and 'net_kg' not in p['conflicts']:
   v['net_kg']=format(Decimal(v['cartons'])*Decimal(v['carton_net_kg']),'f');e['net_kg']=[{'source':'calculated','quote':'箱数 × 单箱净重','value':v['net_kg']}]
  packaging=str(job.get('shipment',{}).get('values',{}).get('customs_packaging','')).strip().upper()
  source={'托盘':'pallets','木托':'pallets','PALLET':'pallets','PALLETS':'pallets','纸箱':'cartons','CARTON':'cartons','CARTONS':'cartons','CTNS':'cartons'}.get(packaging)
  if source and v.get(source) is not None and source not in p['conflicts'] and 'customs_package_count' not in v and 'customs_package_count' not in p['conflicts']:
   v['customs_package_count']=v[source];e['customs_package_count']=[{'source':'calculated','quote':'报关包装种类为 '+packaging+'，沿用已确认的'+LABELS[source],'value':v[source]}]
  from .business_packages import absent
  if absent(v.get('samples_declaration')) and v.get('net_kg') is not None and 'net_kg' not in p['conflicts'] and 'customs_main_net_kg' not in v and 'customs_main_net_kg' not in p['conflicts']:
   v['customs_main_net_kg']=v['net_kg'];e['customs_main_net_kg']=[{'source':'calculated','quote':'已确认无另列报关样品，沿用本次总净重','value':v['net_kg']}]
 return job


def output_context(job,product):
 p=product['values'];c=next(c['values'] for c in job['contracts'] if c['id']==product['contract_id']);s=job['shipment']['values']
 ctx={**p,**c,**s}
 q=Decimal(p['quantity']) if p.get('quantity') else None
 for group in ('customer','customs'):
  val=None
  if q is not None and p.get(group+'_unit_price') is not None:val=(q*Decimal(p[group+'_unit_price'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
  ctx[group+'_amount_number']=format(val,'.2f') if val is not None else ''
  ctx[group+'_amount']=(p.get(group+'_currency','')+' '+format(val,'.2f')).strip() if val is not None else ''
 ctx['amount']=ctx['customer_amount'];ctx['amount_number']=ctx['customer_amount_number']
 ctx['description']=' '.join(x for x in [p.get('name_en') or '待补品名',p.get('strength')] if x)
 ctx['quantity_text']=((format(q,',f').rstrip('0').rstrip('.') if '.' in format(q,',f') else format(q,',f'))+' '+p.get('base_unit','')).strip() if q is not None else ''
 ctx['price_text']=(p['customer_currency']+' '+p['customer_unit_price']+'/'+p['base_unit']) if all(p.get(k) for k in ('customer_currency','customer_unit_price','base_unit')) else ''
 ctx['moc_insurance']='MOC NO.: '+('' if p.get('moc_no')=='无' else p.get('moc_no',''))+'      INSURANCE NO.: '+('' if p.get('insurance_no')=='无' else p.get('insurance_no',''))
 ctx['samples_text']='' if p.get('samples_text')=='无' else p.get('samples_text','')
 return ctx


def checks(job):
 issues=[]
 for p in job['products']:
  v=p['values'];pref=p['id']
  if all(v.get(k) is not None for k in ('net_kg','gross_kg')) and Decimal(v['gross_kg'])<Decimal(v['net_kg']):issues.append(pref+'：毛重小于净重')
  if all(v.get(k) is not None for k in ('quantity','ordered_quantity')) and Decimal(v['quantity'])>Decimal(v['ordered_quantity']):issues.append(pref+'：本次出运量超过合同总量，请核实归属')
  if all(v.get(k) is not None for k in ('quantity','units_per_inner','inners_per_carton','cartons')):
   q=Decimal(v['quantity']);cap=Decimal(v['units_per_inner'])*Decimal(v['inners_per_carton']);n=Decimal(v['cartons'])
   if q>cap*n or q<=cap*(n-1):issues.append(pref+'：箱数与包装换算不一致')
  if v.get('samples_text') not in (None,'无') and not v.get('sample_batches'):issues.append(pref+'：随货样品需填写对应批号')
 return issues


class JobStore:
 def __init__(self,repo):self.repo=repo
 def create(self,name,targets):
  if not name.strip() or set(targets)-set(REQUIRED) or not targets:raise ValueError('INVALID_JOB')
  j={'id':uuid.uuid4().hex,'name':name.strip(),'targets':targets,'revision':0,'phase':'uploaded','files':[],'contracts':[],'products':[],'shipment':{'values':{},'evidence':{},'conflicts':{}},'issues':[],'audit':[],'outputs':[],'created_at':now()}
  return self.save(j,0)
 def save(self,job,expected):
  j=deepcopy(job)
  with self.repo.connect() as c:
   c.execute('BEGIN IMMEDIATE');row=c.execute("SELECT payload FROM records WHERE kind='agent_jobs' AND id=?",(j['id'],)).fetchone();old=json.loads(row[0]) if row else None
   if (old['revision'] if old else 0)!=expected:raise ValueError('AGENT_VERSION_CONFLICT')
   if old:
    c.execute("INSERT OR IGNORE INTO records VALUES('agent_revisions',?,'',?,?)",(j['id']+':'+str(expected),canonical(old),now()))
   j['revision']=expected+1;j['updated_at']=now()
   c.execute("INSERT INTO records VALUES('agent_revisions',?,'',?,?)",(j['id']+':'+str(j['revision']),canonical(j),now()))
   c.execute("INSERT INTO records VALUES('agent_jobs',?,'',?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",(j['id'],canonical(j),now()))
  return j
 def get(self,id):
  with self.repo.connect() as c:r=c.execute("SELECT payload FROM records WHERE kind='agent_jobs' AND id=?",(id,)).fetchone()
  if not r:raise KeyError(id)
  return json.loads(r[0])
 def revision(self,id,revision):
  with self.repo.connect() as c:r=c.execute("SELECT payload FROM records WHERE kind='agent_revisions' AND id=?",(id+':'+str(revision),)).fetchone()
  if not r:raise KeyError((id,revision))
  return json.loads(r[0])
 def list(self):return self.repo.records('agent_jobs')
