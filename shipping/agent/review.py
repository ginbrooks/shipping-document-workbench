"""Template-driven review, persisted edit drafts and explicit fact reuse. No model calls."""
from copy import deepcopy
from uuid import uuid4
from .catalog import fields_for,owners
from .core import LABELS,checked,resolve,derive,questions
from shipping.repository import now,canonical
from shipping.ingestion import digest

STABLE={'name_cn','name_en','strength','base_unit','packing_text','storage','origin_country','domestic_source'}
RANK={'missing':0,'conflict':1,'pending':2,'filled':3,'optional':4}
STATUS={'missing':'待补充','conflict':'有冲突','pending':'待核对','filled':'已填写','optional':'选填'}
MATERIALS=[
 ('包装与实测','工厂装箱单、实测重量和体积记录',set('cartons pallets net_kg gross_kg volume_m3 packing_text carton_net_kg carton_gross_kg customs_package_count customs_main_net_kg customs_packaging'.split())),
 ('批次','COA、批次清单或清晰箱标',set('batch_no mfg_date exp_date sample_batches'.split())),
 ('报关依据','本票报关确认资料、费用明细及样品说明',set('customs_contract_no customs_invoice_no customs_invoice_date customs_buyer customs_quantity customs_unit declaration_unit_price customs_unit_price customs_currency customs_hs_code customs_freight customs_insurance customs_other_fee fees_currency customs_trade_term customs_marks customs_notes customs_tax_method samples_declaration declaration_elements'.split())),
 ('运输与证书','货代订舱确认、运输回件及正式产地证',set('transport_mode loading_port destination_port departure_port destination_country trade_country trade_term departure_date shipping_date warehouse_date freight_note flight flight_date chargeable_kg transport_document_no certificate_no certificate_date hs_code certificate_hs_code'.split())),
 ('合同与商品','销售合同、本次发货确认及产品资料',set(LABELS)),
]

def empty_object(prefix,**extra):
 return dict(id=prefix+uuid4().hex[:12],values={},evidence={},conflicts={},placeholder=True,**extra)


def blank_job(kind):
 c=empty_object('c',kind='other');p=empty_object('p',contract_id=c['id'],batches=[])
 return dict(id='new',name='未命名新票',revision=0,phase='needs_input',targets=[kind],active_document=kind,files=[],contracts=[c],products=[p],shipment=dict(values={},evidence={},conflicts={}),issues=[],audit=[],outputs=[],review_first=True,manual_entry=True,manual_files=[],created_at=now())


def review_view(job):
 j=deepcopy(job)
 if not j['contracts']:j['contracts']=[empty_object('c',kind='other')]
 if not j['products']:j['products']=[empty_object('p',contract_id=j['contracts'][0]['id'],batches=[])]
 from .defaults import apply_customs_defaults
 return apply_customs_defaults(j)


def display_evidence(evidence):
 """Collapse repeated display lines while retaining distinct origins and values."""
 seen=set();result=[]
 for e in evidence:
  key=(e.get('source'),e.get('source_job'),e.get('source_revision'),str(e.get('value','')),''.join(str(e.get('quote') or '').split()))
  if key in seen:continue
  seen.add(key);result.append(e)
 return result


def prune_empty(job):
 """Empty editing slots are UI affordances, never extra commercial items."""
 j=deepcopy(job)
 for p in j['products']:p['batches']=[b for b in p.get('batches',[]) if b['values'] or b.get('conflicts')]
 j['products']=[p for p in j['products'] if p['values'] or p.get('batches') or p.get('conflicts')]
 used={p['contract_id'] for p in j['products']}
 j['contracts']=[c for c in j['contracts'] if c['id'] in used]
 return j


def objects(job):
 return {**{o['id']:o for o in job['contracts']+job['products']},**{b['id']:b for p in job['products'] for b in p.get('batches',[])},'shipment':job['shipment']}


def material_for(key):
 return next((a,b) for a,b,keys in MATERIALS if key in keys)


def title_for(job,obj,owner):
 v=obj['values'];oid=obj.get('id')
 if owner=='shipment':return '本票运输与费用'
 if owner=='contract':return '合同 '+str(next(i for i,c in enumerate(job['contracts'],1) if c['id']==oid))+' · '+str(v.get('contract_no') or v.get('customs_contract_no') or '编号待填')
 if owner=='product':return '产品 '+str(next(i for i,p in enumerate(job['products'],1) if p['id']==oid))+' · '+str(v.get('name_cn') or v.get('name_en') or '名称待填')
 p=next(p for p in job['products'] if obj in p.get('batches',[]))
 return title_for(job,p,'product')+' / 批次 '+str(v.get('batch_no') or next(i for i,b in enumerate(p['batches'],1) if b['id']==oid))


def review_items(job,kind):
 pending={q['key']:q for q in questions(job,[kind])};rows=[]
 for obj,owner in owners(job,kind):
  keys=fields_for(kind,owner,job=job)
  if owner=='product' and obj.get('batches'):keys-={'batch_no','mfg_date','exp_date'}
  required=fields_for(kind,owner,optional=False,job=job)
  for key in LABELS:
   if key not in keys:continue
   path=(obj.get('id') or 'shipment')+'.'+key;value=obj['values'].get(key)
   status='conflict' if key in obj.get('conflicts',{}) else 'missing' if path in pending and value in (None,'') else 'pending' if key in obj.get('review_pending',[]) else 'filled' if value not in (None,'') else 'optional'
   category,material=material_for(key)
   rows.append(dict(**field_metadata(kind,owner,key,job),path=path,title=title_for(job,obj,owner),value='' if value is None else str(value),status=status,rank=RANK[status],material=material,evidence=obj['evidence'].get(key,[]),choices=obj.get('conflicts',{}).get(key,[])))
 return sorted(rows,key=lambda r:(r['rank'],0 if r['key']=='transport_mode' else 1))


def material_gaps(job,kind):
 groups={}
 for row in review_items(job,kind):
  if row['status']!='missing':continue
  entry=groups.setdefault(row['category'],dict(category=row['category'],material=row['material'],fields=[]))
  if LABELS[row['key']] not in entry['fields']:entry['fields'].append(LABELS[row['key']])
 return list(groups.values())


class DraftStore:
 """Raw text remains separate from checked facts; edit callbacks survive page changes."""
 def __init__(self,repo):self.repo=repo
 def get(self,job):return self.repo.cache_get('review-draft:'+job['id']) or dict(answers={},base={},view=review_view(job),revision=job['revision'])
 def put(self,job,answers):
  draft=self.get(job);objs=objects(review_view(job))
  for path,value in answers.items():
   oid,key=path.split('.',1)
   if oid not in objs:raise ValueError('字段归属已变化，请刷新后再填写')
   draft['base'].setdefault(path,objs[oid]['values'].get(key))
   draft['answers'][path]=value
  draft['view']=review_view(job);draft['saved_at']=now();self.repo.cache_set('review-draft:'+job['id'],draft)
  return draft
 def clear(self,job):self.repo.cache_set('review-draft:'+job['id'],None)
 def validate_base(self,job):
  d=self.get(job);objs=objects(job)
  for path,value in d['answers'].items():
   oid,key=path.split('.',1);current=objs.get(oid,{}).get('values',{}).get(key)
   if oid not in objs or (current!=d['base'].get(path) and str(current or '')!=str(value or '')):raise ValueError('已有新版本修改了 '+LABELS.get(key,key)+'，编辑草稿仍保留，请核对新旧值后再保存')
 def rebase(self,job,keep=False):
  d=self.get(job);objs=objects(job)
  for path in list(d['answers']):
   oid,key=path.split('.',1);current=objs.get(oid,{}).get('values',{}).get(key)
   if oid not in objs:d['answers'].pop(path);d['base'].pop(path,None)
   elif current!=d['base'].get(path):
    if not keep:d['answers'].pop(path)
    d['base'][path]=current
  d['revision']=job['revision'];d['saved_at']=now();self.repo.cache_set('review-draft:'+job['id'],d)


def save_review(store,job,answers,kind,reason='本票人工核实',confirm=True,confirm_paths=None):
 view=review_view(job);view['review_first']=True
 valid={};errors=[]
 for path,value in answers.items():
  try:valid[path]=checked(path.split('.',1)[1],value)
  except ValueError as e:errors.append(str(e))
 if errors:raise ValueError('；'.join(errors))
 if job['id']!='new':DraftStore(store.repo).validate_base(view)
 if confirm:
  for obj,owner in owners(view,kind):
   for key in obj.get('review_pending',[]):
    path=(obj.get('id') or 'shipment')+'.'+key
    if key in fields_for(kind,owner,job=view) and (confirm_paths is None or path in confirm_paths):valid.setdefault(path,obj['values'].get(key))
 out=resolve(view,valid,reason)
 for obj in objects(out).values():
  if obj['values']:obj.pop('placeholder',None)
 out['manual_entry']=True
 if not out['files']:out['manual_files']=[]
 out['review_saved_at']=now()
 if out['id']=='new':
  out['id']=uuid4().hex
  if out['name']=='未命名新票':out['name']=next((p['values'].get('name_cn') or p['values'].get('name_en') for p in out['products'] if p['values'].get('name_cn') or p['values'].get('name_en')),'新票 '+now()[:16].replace('T',' '))
 out=store.save(out,job['revision']);DraftStore(store.repo).clear(job)
 return out


def transfer_options(source,oid,kind):
 obj=objects(source)[oid];owner='shipment' if oid=='shipment' else 'contract' if oid in {c['id'] for c in source['contracts']} else 'product' if oid in {p['id'] for p in source['products']} else 'batch'
 keys=fields_for(kind,owner,job=source)
 if owner=='product' and obj.get('batches'):keys-={'batch_no','mfg_date','exp_date'}
 return [dict(key=k,label=LABELS[k],value=v,default=k in STABLE,owner=owner) for k,v in obj['values'].items() if k in keys and v not in (None,'')]


def transfer_fields(target,source,source_oid,target_oid,keys,kind,overwrite=False):
 j=deepcopy(target);available={x['key'] for x in transfer_options(source,source_oid,kind)}
 target_options=next((owner for obj,owner in owners(j,kind) if (obj.get('id') or 'shipment')==target_oid),None)
 if not target_options or set(keys)-available or set(keys)-fields_for(kind,target_options,job=j):raise ValueError('字段不属于当前资料或目标归属')
 source_options=transfer_options(source,source_oid,kind)
 if keys and any(x['owner']!=target_options for x in source_options if x['key'] in keys):raise ValueError('来源与目标层级不一致')
 old=objects(source)[source_oid];new=objects(j)[target_oid]
 def context(job,oid,obj):
  return next((p['values'] for p in job['products'] if any(b['id']==oid for b in p.get('batches',[]))),obj['values'])
 src_context=context(source,source_oid,old);target_context=context(j,target_oid,new)
 for quantity,unit in [('quantity','base_unit'),('customs_quantity','customs_unit')]:
  if quantity in keys:
   if not src_context.get(unit):raise ValueError('来源数量缺少对应单位，请先核实来源票')
   if unit not in keys and src_context[unit]!=target_context.get(unit):raise ValueError('转移数量时请同时选择对应单位；批次数量需先核对所属产品单位')
 for price,unit,currency in [('customer_unit_price','base_unit','customer_currency'),('customs_unit_price','base_unit','customs_currency'),('declaration_unit_price','customs_unit','customs_currency')]:
  if price in keys and not {unit,currency}<=set(keys):raise ValueError('转移价格时请同时选择对应单位和币种')
 for key in keys:
  if new['values'].get(key) not in (None,'',old['values'][key]) and not overwrite:raise ValueError('目标已有不同的 '+LABELS[key]+'，请先核对覆盖差异')
 for key in keys:
  value=checked(key,old['values'][key]);new['values'][key]=value;new['conflicts'].pop(key,None)
  new.setdefault('confirmed',{}).pop(key,None)
  new.setdefault('review_pending',[])
  if key not in new['review_pending']:new['review_pending'].append(key)
  new['evidence'][key]=[dict(source='history',value=value,quote='从历史票带入，待本票核对',source_job=source['id'],source_name=source['name'],source_revision=source['revision'],source_owner=source_oid,source_evidence=deepcopy(old['evidence'].get(key,[])),at=now())]
 new.pop('placeholder',None);j['review_first']=True
 j['audit'].append(dict(at=now(),event='history_transferred',source_job=source['id'],source_revision=source['revision'],target_owner=target_oid,fields=list(keys)))
 return derive(j)


def _same(a,b):return ''.join(str(a or '').split()).casefold()==''.join(str(b or '').split()).casefold()


def _merge_facts(old,new):
 pending=set(old.get('review_pending',[]))
 for key in new['values'].keys()|new['conflicts'].keys():
  prior=old['values'].get(key);incoming=new['values'].get(key);ev=new['evidence'].get(key,[])
  vals=list(dict.fromkeys(([incoming] if incoming is not None else [])+new['conflicts'].get(key,[])))
  protected=old.get('confirmed',{}).get(key)==prior and prior is not None or any(e.get('source') in ('manual','history') for e in old['evidence'].get(key,[]))
  if prior is not None and protected:
   different=[v for v in vals if v!=prior]
   if different:old['conflicts'][key]=list(dict.fromkeys([prior]+different));pending.add(key)
  else:
   if incoming is not None:old['values'][key]=incoming
   if key in new['conflicts']:old['conflicts'][key]=list(new['conflicts'][key]);old['values'].pop(key,None)
   else:old['conflicts'].pop(key,None)
   pending.add(key)
  old['evidence'].setdefault(key,[]).extend(deepcopy(e) for e in ev if e not in old['evidence'].get(key,[]))
 old['review_pending']=sorted(pending)
 if old['values']:old.pop('placeholder',None)
 return old


def _append_product(j,c,p):
 new=deepcopy(p);new['id']='p'+uuid4().hex[:12];new['contract_id']=c['id'];new.pop('placeholder',None)
 new['review_pending']=list(new['values']);new.pop('confirmed',None)
 for b in new.get('batches',[]):b['id']='b'+uuid4().hex[:12];b['review_pending']=list(b['values']);b.pop('confirmed',None)
 j['products'].append(new);return new


def _merge_product(old,new):
 if new.get('batches') and not old.get('batches') and any(old['values'].get(k) for k in ('batch_no','mfg_date','exp_date')):
  batch=empty_object('b');batch.pop('placeholder',None)
  for key in ('batch_no','mfg_date','exp_date'):
   if key in old['values']:
    batch['values'][key]=old['values'].pop(key);batch['evidence'][key]=old['evidence'].pop(key,[])
    for name in ('confirmed','conflicts'):
     if key in old.get(name,{}):batch.setdefault(name,{})[key]=old[name].pop(key)
    if key in old.get('review_pending',[]):batch.setdefault('review_pending',[]).append(key);old['review_pending'].remove(key)
  old.setdefault('batches',[]).append(batch)
 _merge_facts(old,new)
 for b in new.get('batches',[]):
  matches=[x for x in old.get('batches',[]) if b['values'].get('batch_no') and _same(x['values'].get('batch_no'),b['values']['batch_no'])]
  if len(matches)==1:_merge_facts(matches[0],b)
  elif not matches:
   nb=deepcopy(b);nb['id']='b'+uuid4().hex[:12];nb['review_pending']=list(nb['values']);old.setdefault('batches',[]).append(nb)
  else:raise ValueError('本票有重复批号，请先核实')
 return old


def merge_reviewed(job,result):
 j=deepcopy(job);j['unmatched']=[];idmap={};used=set()
 fresh=not any(c['values'] for c in j['contracts']) and not any(p['values'] or p.get('batches') for p in j['products'])
 if fresh:j['contracts']=[];j['products']=[]
 for c in result['contracts']:
  def identity(old):return any(c['values'].get(k) and _same(c['values'][k],old['values'].get(k)) for k in ('contract_no','invoice_no','customs_contract_no','customs_invoice_no'))
  matches=[x for x in j['contracts'] if x['id'] not in used and identity(x)]
  identity_keys={'contract_no','invoice_no','customs_contract_no','customs_invoice_no'}
  if not matches and not any(c['values'].get(k) for k in identity_keys):
   candidates=set()
   for np in result['products']:
    if np['contract_id']!=c['id']:continue
    ps=[op for op in j['products'] if any(op['values'].get(k) and np['values'].get(k) and _same(op['values'][k],np['values'][k]) for k in ('name_cn','name_en')) and (not op['values'].get('strength') or not np['values'].get('strength') or _same(op['values']['strength'],np['values']['strength']))]
    candidates.update(p['contract_id'] for p in ps)
   if len(candidates)==1:matches=[x for x in j['contracts'] if x['id'] in candidates and x['id'] not in used]
  if not matches and len(j['contracts'])==1 and len(result['contracts'])==1:
   x=j['contracts'][0]
   if not any(x['values'].get(k) for k in identity_keys):matches=[x]
  matches=[x for x in matches if x.get('kind','other')=='other' or c['kind']=='other' or x['kind']==c['kind']]
  if len(matches)==1:
   target=matches[0];used.add(target['id']);_merge_facts(target,c)
   if target.get('kind')=='other':target['kind']=c['kind']
   idmap[c['id']]=target['id']
  elif fresh or not j['contracts']:
   target=deepcopy(c);target['id']='c'+uuid4().hex[:12];target['review_pending']=list(target['values']);j['contracts'].append(target);idmap[c['id']]=target['id']
  else:target=None
  incoming=[p for p in result['products'] if p['contract_id']==c['id']]
  old_products=[p for p in j['products'] if target and p['contract_id']==target['id']];taken=set()
  for p in incoming:
   matches=[x for x in old_products if x['id'] not in taken and any(x['values'].get(k) and p['values'].get(k) and _same(x['values'][k],p['values'][k]) for k in ('name_cn','name_en')) and (not x['values'].get('strength') or not p['values'].get('strength') or _same(x['values']['strength'],p['values']['strength']))]
   if not matches and len(old_products)==1 and len(incoming)==1 and not any(old_products[0]['values'].get(k) for k in ('name_cn','name_en','strength')):matches=[old_products[0]]
   if target and len(matches)==1:
    old=matches[0];taken.add(old['id']);_merge_product(old,p);idmap[p['id']]=old['id']
   elif target and not any(x['values'] or x.get('batches') for x in old_products):
    j['products']=[x for x in j['products'] if x not in old_products];new=_append_product(j,target,p);idmap[p['id']]=new['id']
   else:j['unmatched'].append(dict(id=uuid4().hex,contract=deepcopy(c),product=deepcopy(p),suggested_contract=target['id'] if target else None))
 _merge_facts(j['shipment'],result['shipment'])
 j['issues']=[i for i in j.get('issues',[]) if i.get('key','').split('.',1)[0] not in idmap.values()]
 for issue in result.get('issues',[]):
  x=deepcopy(issue);oid,_,key=x['key'].partition('.')
  if oid in idmap:x['key']=idmap[oid]+'.'+key
  j['issues'].append(x)
 return derive(j)


def accept_candidate(job,candidate_id,contract_id=None,product_id=None):
 j=deepcopy(job);item=next(x for x in j.get('unmatched',[]) if x['id']==candidate_id)
 if contract_id:
  c=next(x for x in j['contracts'] if x['id']==contract_id);_merge_facts(c,item['contract'])
 else:
  c=deepcopy(item['contract']);c['id']='c'+uuid4().hex[:12];c['review_pending']=list(c['values']);j['contracts'].append(c)
 if product_id:
  p=next(x for x in j['products'] if x['id']==product_id)
  if p['contract_id']!=c['id']:raise ValueError('产品与合同归属不一致')
  _merge_product(p,item['product'])
 else:_append_product(j,c,item['product'])
 j['unmatched']=[x for x in j['unmatched'] if x['id']!=candidate_id]
 j['audit'].append(dict(at=now(),event='candidate_matched',candidate=candidate_id,contract=c['id'],product=product_id or j['products'][-1]['id']))
 return derive(j)


# Positions come from the checked fixed-template adapters, never from uploaded ticket values.
CUSTOMER_POSITIONS={
 'invoice_no':'SHIPPING ADVICE!B3；发票!L7；箱单!I8', 'invoice_date':'SHIPPING ADVICE!A4；发票!L10；箱单!I11',
 'payment_terms':'SHIPPING ADVICE!B27；发票!G36', 'name_en':'SHIPPING ADVICE!D16；发票!D23；箱单!D15',
 'strength':'发票!A23/D23；箱单!D15', 'quantity':'SHIPPING ADVICE!A16；发票!H23/H25；箱单!G15/G19',
 'base_unit':'数量及单价相邻单位', 'customer_unit_price':'发票!I23/L23/L25', 'customer_currency':'发票单价、货值与合计币种',
 'packing_text':'发票!F27/A23', 'cartons':'箱单!F15/F19', 'pallets':'运输草件件数；产地证第7栏',
 'gross_kg':'箱单!H15/H19；运输草件毛重', 'net_kg':'箱单!I15/I19', 'volume_m3':'箱单!J15/J19；运输草件体积',
 'storage':'箱单!B21；发票!A23', 'samples_text':'发票!F32；箱单!B28', 'sample_batches':'箱单样品区或续页',
 'moc_no':'SHIPPING ADVICE!A21；发票!F33；箱单!B22', 'insurance_no':'SHIPPING ADVICE!A21；发票!F33；箱单!B22',
 'batch_no':'箱单!B23；发票!A23；批次续页', 'mfg_date':'发票!A23；批次续页', 'exp_date':'发票!A23；批次续页',
 'certificate_hs_code':'产地证草件第8栏', 'origin_country':'产地证草件第11栏',
}
BOOKING_POSITIONS={
 'contract_no':'Word表0：合同编号与合同号', 'shipping_date':'Word表0：托书标题日期', 'warehouse_date':'Word表0：进仓日期',
 'freight_note':'Word表0：运费', 'volume_m3':'Word表0：总体积', 'hs_code':'Word表0：海关编码',
 'name_cn':'Word表1：中文品名；表2：品名', 'name_en':'Word表1：英文品名', 'quantity':'Word表1：数量',
 'base_unit':'Word表1：数量和单价单位', 'customer_unit_price':'Word表1：外销实际单价/总金额', 'customer_currency':'Word表1：外销实际币种',
 'customs_unit_price':'Word表1：报关单价/总金额', 'customs_currency':'Word表1：报关币种', 'cartons':'Word表1/2：箱数',
 'pallets':'Word表1/2：托数', 'packing_text':'Word表2：包装规格', 'net_kg':'Word表2：货物净重',
 'carton_net_kg':'Word表2：单箱净重', 'carton_gross_kg':'Word表2：单箱毛重', 'transport_mode':'选择空运/陆运固定底稿',
}


def field_metadata(kind,owner,key,job=None):
 from .core import NUMERIC,INTEGER
 if key not in fields_for(kind,owner,job=job):raise ValueError('字段不属于所选模板范围')
 category,material=material_for(key)
 if kind=='consignment':location=BOOKING_POSITIONS[key]
 elif kind=='customs_set':location=('报关主件：'+LABELS[key]) if key not in {'cartons','packing_text'} else '包装核对依据（选填）'
 else:location=CUSTOMER_POSITIONS.get(key, '正式回件核对：'+LABELS[key] if key in {'transport_document_no','certificate_no','certificate_date','flight','flight_date','departure_date','chargeable_kg'} else '运输/产地证草件或商品明细：'+LABELS[key])
 return dict(key=key,label=LABELS[key],owner=owner,required=key in fields_for(kind,owner,optional=False,job=job),data_type='integer' if key in INTEGER else 'decimal' if key in NUMERIC else 'date' if key.endswith('_date') else 'text',category=category,suggested_material=material,template_location=location,stable_reuse=key in STABLE)
