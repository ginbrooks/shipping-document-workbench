"""Four business workflows. Legacy document kinds remain internal/history only."""
from copy import deepcopy
from decimal import Decimal,ROUND_HALF_UP

FLOW_KEYS=('customs_set','shipping_draft','settlement','consignment')
COMPONENTS={
 'customs_set':('报关发票','报关箱单','出口报关单','销货合约','申报要素（适用时）'),
 'shipping_draft':('SHIPPING ADVICE','商业发票','装箱单','运输单据草件','产地证草件'),
 'settlement':('SHIPPING ADVICE','商业发票','装箱单','已确认运输单据','正式产地证'),
 'consignment':('空运或陆运托书',),
}
NEW_CONTRACT_LABELS={'customs_invoice_no':'报关发票号','customs_invoice_date':'报关发票日期','customs_contract_no':'报关合同协议号','customs_buyer':'报关境外收货人 / 购货人','payment_terms':'客户付款条款（按本票合同）'}
NEW_PRODUCT_LABELS={
 'customs_quantity':'报关数量（按报关单位）','customs_unit':'报关单位（如 BOTTLE）','declaration_unit_price':'报关单价（每报关单位）','customs_hs_code':'报关商品编码','origin_country':'原产国','domestic_source':'境内货源地',
 'declaration_elements':'申报要素（按产品对应清单，不适用填无）','samples_declaration':'报关样品明细（品名、数量单位、单价、净重、用途；无则填无）',
 'customs_tax_method':'报关征免方式（如照章征税）',
 'certificate_hs_code':'产地证商品编码（按证书要求的位数）',
 'customs_package_count':'报关包装件数（按本票包装种类）','customs_main_net_kg':'报关主货净重 kg（不含另列样品）',
 'transport_document_no':'本产品运输单号','certificate_no':'本产品正式产地证编号','certificate_date':'产地证签发日期',
}
NEW_SHIPMENT_LABELS={
 'transport_mode':'运输方式','loading_port':'起运地 / 装运口岸','destination_port':'目的地 / 指运港','departure_port':'离境口岸','destination_country':'最终目的国','trade_country':'贸易国 / 地区',
 'trade_term':'实际成交方式','customs_trade_term':'报关成交方式','customs_freight':'报关运费','customs_insurance':'报关保费','customs_other_fee':'报关杂费','fees_currency':'费用币种','customs_packaging':'报关包装种类','customs_marks':'报关唛头','customs_notes':'报关备注（样品不收汇等）','departure_date':'实际发运日期',
}
NEW_LABELS={**NEW_CONTRACT_LABELS,**NEW_PRODUCT_LABELS,**NEW_SHIPMENT_LABELS}
LONG_FIELDS={'declaration_elements','samples_declaration','customs_notes','samples_text','packing_text','freight_note'}
FIELD_HELP={
 'customs_quantity':'例如客户发票是 360,000 片、报关是 14,400 瓶，这里填写有依据的瓶数。不会直接借用客户数量。',
 'declaration_unit_price':'使用报关单位对应的价格；不会拿客户价、采购价或每片报关价直接代入。',
 'customs_hs_code':'报关样例为 10 位编码，运输草件可能使用不同位数；按本票报关依据核对。',
 'declaration_elements':'核对品牌类型、出口享惠、用途、成分、配定剂量、零售包装、化学通用名、注册上市、品牌、包装规格。只保留本产品有依据的内容。',
 'transport_document_no':'以本产品对应的运输回件为准；同票不同产品有不同单号时分别填写。',
}


def flow_for(kind):
 if kind in FLOW_KEYS:return kind
 if kind in ('booking','road_booking'):return 'consignment'
 return 'shipping_draft'


def fact_snapshot(job,kind):
 from .catalog import owners,fields_for
 return {(o.get('id') or 'shipment')+'.'+k:o['values'].get(k) for o,owner in owners(job,kind) for k in fields_for(kind,owner,job=job)}


def select_flow(job,kind):
 from .workflow import select_document
 j=select_document(job,kind)
 if kind=='customs_set':
  from .core import derive
  derive(j)
 if kind=='settlement' and 'draft_baseline' not in j and j['products']:
  j['draft_baseline']={'revision':job['revision'],'facts':fact_snapshot(job,'shipping_draft')}
 return j


def settlement_changes(job):
 old=job.get('draft_baseline',{}).get('facts',{});current=fact_snapshot(job,'shipping_draft')
 return [{'key':k,'field':k.split('.',1)[1],'before':v,'after':current.get(k)} for k,v in old.items() if v!=current.get(k)]


def customs_amount(values):
 if any(values.get(k) in (None,'') for k in ('customs_quantity','declaration_unit_price')):return None
 return format((Decimal(values['customs_quantity'])*Decimal(values['declaration_unit_price'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP),'.2f')


def review_rows(job,kind):
 from .catalog import owners,fields_for
 from .core import LABELS
 rows=[]
 for obj,owner in owners(job,kind):
  label=obj['values'].get('name_cn') or obj['values'].get('batch_no') or obj['values'].get('contract_no') or obj.get('id') or '本票'
  keys=fields_for(kind,owner,job=job)
  if owner=='product' and obj.get('batches'):keys-={'batch_no','mfg_date','exp_date'}
  for key in LABELS:
   if key not in keys:continue
   evidence=obj['evidence'].get(key,[])
   rows.append({'归属':label,'字段':LABELS[key],'核对值':obj['values'].get(key) or '待补','原文依据':'；'.join(str(e.get('quote','')) for e in evidence),'状态':'冲突' if key in obj['conflicts'] else '待核对' if key in obj.get('review_pending',[]) else '已填' if obj['values'].get(key) not in (None,'') else '待补'})
  if kind=='customs_set' and owner=='product':rows.append({'归属':label,'字段':'报关货值（不含样品和运保费）','核对值':customs_amount(obj['values']) or '待补','原文依据':'报关数量 × 每报关单位单价','状态':'计算'})
  if kind=='customs_set' and owner=='product':
   for index,s in enumerate(job.get('customs_samples',{}).get(obj['id'],[]),1):
    from .business_packages import amount
    rows.append({'归属':label,'字段':f'报关样品 {index}','核对值':f"{s.get('name','')} / {s.get('quantity','')} {s.get('unit','')} / 单价 {s.get('unit_price','')} / 净重 {s.get('net_kg','')} kg / 编码 {s.get('hs_code','')} / 原产国 {s.get('origin','')} / 货源 {s.get('source','')} / {s.get('tax','')} / {s.get('purpose','')}",'原文依据':'样品明细人工核对','状态':'已填'})
  if kind=='settlement' and owner=='product':
   from .business_packages import return_facts_hash
   files={f['id']:f['name'] for f in job['files']+job.get('attachments',[])}
   for r in job.get('returns',[]):
    if r.get('product_id')==obj['id']:rows.append({'归属':label,'字段':'运输回件' if r['role']=='transport' else '产地证回件','核对值':files.get(r['file_id'],'原件缺失')+' / '+r.get('reference','')+' / 页码 '+','.join(map(str,r.get('pages',[]))),'原文依据':'PDF 原件 '+r['file_id'][:12],'状态':'已核对' if r.get('confirmed') and r.get('facts_hash')==return_facts_hash(job) else '待重核'})
 return sorted(rows,key=lambda r:{'待补':0,'冲突':1,'待核对':2,'待重核':2,'已填':3,'已核对':3,'计算':4}.get(r['状态'],4))
