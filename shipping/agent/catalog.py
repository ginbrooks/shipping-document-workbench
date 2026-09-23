"""The document contract shared by intake, review, validation and export."""
from dataclasses import dataclass, field
from decimal import Decimal
from shipping.repository import canonical
from shipping.ingestion import digest


def fs(words=''):return frozenset(words.split())

@dataclass(frozen=True)
class Document:
 name: str
 category: str
 description: str
 inputs: str
 product: frozenset = field(default_factory=frozenset)
 contract: frozenset = field(default_factory=lambda:fs('contract_no'))
 shipment: frozenset = field(default_factory=frozenset)
 batch: frozenset = field(default_factory=frozenset)
 optional_product: frozenset = field(default_factory=frozenset)
 optional_batch: frozenset = field(default_factory=frozenset)
 template: str = 'customer'
 sheet: str = ''

ADVICE=fs('name_en strength quantity base_unit moc_no insurance_no')
INVOICE=fs('name_en strength quantity base_unit customer_unit_price customer_currency packing_text gross_kg volume_m3 storage samples_text moc_no insurance_no batch_no mfg_date exp_date')
PACKING=fs('name_en strength quantity base_unit cartons gross_kg net_kg volume_m3 storage samples_text moc_no insurance_no batch_no')
BOOKING=fs('name_cn name_en quantity base_unit cartons pallets net_kg volume_m3 packing_text customer_unit_price customer_currency customs_unit_price customs_currency carton_net_kg carton_gross_kg hs_code')
AWB=fs('name_en pallets gross_kg volume_m3 hs_code')
BATCH=fs('batch_no mfg_date exp_date')
INV_CONTRACT=fs('contract_no invoice_no invoice_date')
CATALOG={
 'invoice':Document('商业发票','客户单证','按合同生成，金额自动计算。','销售合同、已确认的本次发货数量；批次和包装资料。采购合同可补品名规格，不能代替外销价格。',INVOICE,INV_CONTRACT,batch=BATCH,optional_product=fs('sample_batches name_cn'),optional_batch=fs('quantity cartons'),sheet='发票'),
 'packing':Document('装箱单','客户单证','只核对数量、包装和实测数据。','装箱清单、箱标、工厂实测净毛重及体积、对应发票号。',PACKING,INV_CONTRACT,batch=fs('batch_no'),optional_product=fs('sample_batches name_cn'),optional_batch=fs('quantity cartons'),sheet='箱单'),
 'advice':Document('SHIPPING ADVICE','客户单证','出运通知，沿用固定收发货信息。','本票发票号与日期、英文品名规格、发货数量、MOC 及保险号（不适用填无）。',ADVICE,INV_CONTRACT,sheet='SHIPPING ADVICE'),
 'awb':Document('提单待确认','客户单证','填写货运信息，供货代确认。','货代回件或订舱资料、件数、毛重、体积、HS 编码和航班信息。',AWB,shipment=fs('airwaybill_no flight flight_date total_chargeable_kg'),template='awb'),
 'booking':Document('空运托书','运输交货','按空运原件填写产品及两组价格。','本次发货清单、客户价和报关价依据、包装、入仓日期及运费说明。',BOOKING,shipment=fs('shipping_date warehouse_date freight_note'),template='booking'),
 'road_booking':Document('陆运托书','运输交货','使用陆运底稿，保留陆运路线。','陆运发货清单、客户价和报关价依据、箱数及包装、进仓日期和运费。',BOOKING,shipment=fs('shipping_date warehouse_date freight_note'),template='road_booking'),
 'pickup':Document('报告取件委托书','运输交货','按报告编号填写，留待本次签署。','鉴定报告编号、对应品名、份数；取件单位、人员、联系电话及委托日期。',fs('name_cn report_no report_copies'),contract=frozenset(),shipment=fs('authorization_date collector_company collector_name collector_phone'),template='pickup'),
 'attachments':Document('交货附件包','运输交货','选好原件，一次打包并附文件清单。','上传或复用本票交货资料、COA、鉴定、授权或资质原件；只打包你选择的文件。',contract=frozenset(),template=''),
 'customer':Document('客户结汇资料','整份工作簿','一次生成通知、发票、箱单和运单草件。','本票客户合同及出运资料、批次、实测重量体积和计费重量。选择此项会核对整份工作簿所需数据。',INVOICE|PACKING|AWB|fs('packing_text chargeable_kg'),INV_CONTRACT,batch=BATCH,optional_product=fs('sample_batches name_cn'),optional_batch=fs('quantity cartons')),
}
# Public workflows are packages; the entries above are internal document renderers.
DRAFT=INVOICE|PACKING|AWB|fs('packing_text')
ROUTE=fs('transport_mode loading_port destination_port trade_term')
CATALOG.update({
 'customs_set':Document('整套报关资料','报关办理','核对整套报关单证使用的数据，报关量价单独保存。','本票报关价格、包装与实测数据、报关抬头、运保费、样品及申报要素。',fs('name_cn strength customs_quantity customs_unit declaration_unit_price customs_currency customs_hs_code pallets gross_kg net_kg volume_m3 origin_country domestic_source samples_declaration declaration_elements'),fs('customs_contract_no customs_invoice_no customs_invoice_date customs_buyer'),shipment=ROUTE|fs('departure_port destination_country trade_country customs_trade_term customs_freight customs_insurance customs_other_fee fees_currency customs_packaging customs_marks customs_notes'),optional_product=fs('cartons packing_text'),template=''),
 'shipping_draft':Document('出运草件','出运前核对','一次生成通知、发票、箱单、运输单据和产地证草件。','客户合同、出运数量、外销价格、批次和包装、计划箱托及重量体积、MOC 和保险号。最终单号、航班和证书编号留待结汇时补充。',DRAFT|fs('origin_country'),INV_CONTRACT,shipment=ROUTE|fs('destination_country'),batch=BATCH,optional_product=fs('name_cn sample_batches chargeable_kg'),optional_batch=fs('quantity cartons'),template='customer'),
 'settlement':Document('结汇资料','出运后确认','沿用草件，加入已确认运输单据和正式产地证。','同票已确认的出运草件、实测装箱数据、运输回件及正式产地证；已有品名、数量和客户价格会直接沿用。',DRAFT|fs('origin_country transport_document_no certificate_no certificate_date'),INV_CONTRACT,shipment=ROUTE|fs('destination_country departure_date'),batch=BATCH,optional_product=fs('name_cn sample_batches'),optional_batch=fs('quantity cartons'),template='customer'),
 'consignment':Document('托书','委托运输','同一入口，根据运输方式使用空运或陆运底稿。','本票合同、产品中英文名、出运数量、箱托数、两组价格、包装重量、入仓日期和运费说明。',BOOKING,shipment=fs('transport_mode shipping_date warehouse_date freight_note'),template='booking'),
})
NAMES={k:d.name for k,d in CATALOG.items()}


def fields_for(kind,owner,optional=True,job=None):
 d=CATALOG[kind];values=set(getattr(d,owner))
 if kind=='customs_set' and owner=='product':
  values.update(fs('customs_tax_method customs_package_count customs_main_net_kg'));values.difference_update(fs('pallets net_kg'))
 if kind in ('shipping_draft','settlement') and owner=='product':values.add('certificate_hs_code')
 if kind in ('shipping_draft','settlement') and owner=='contract':values.add('payment_terms')
 if optional:values.update(getattr(d,'optional_'+owner,frozenset()))
 if kind=='settlement' and (job or {}).get('shipment',{}).get('values',{}).get('transport_mode')=='空运':
  if owner=='shipment':values.update(fs('flight flight_date'))
  if owner=='product':values.add('chargeable_kg')
 return values


def owners(job,kind):
 """Yield just the scopes this document uses, in reading order."""
 if fields_for(kind,'contract',job=job):
  for c in job['contracts']:yield c,'contract'
 for p in job['products']:
  if fields_for(kind,'product',job=job):yield p,'product'
  if fields_for(kind,'batch',job=job):
   for b in p.get('batches',[]):yield b,'batch'
 if fields_for(kind,'shipment',job=job):yield job['shipment'],'shipment'


def input_hash(job,kind):
 if kind in ('customs_set','shipping_draft','settlement','consignment'):
  from .review import prune_empty
  job=prune_empty(job)
 parts=[]
 for obj,owner in owners(job,kind):
  keys=fields_for(kind,owner,job=job)
  if owner=='product' and obj.get('batches'):keys-=set(BATCH)
  part={'id':obj.get('id') or 'shipment','contract_id':obj.get('contract_id'), 'values':{k:obj['values'].get(k) for k in sorted(keys)},'conflicts':{k:v for k,v in obj.get('conflicts',{}).items() if k in keys}}
  if set(obj.get('review_pending',[]))&keys:part['pending']=sorted(set(obj['review_pending'])&keys)
  parts.append(part)
 if kind=='attachments':parts=sorted(job.get('attachment_selection',[]))
 extra={}
 if job.get('unmatched'):extra['unmatched']=job['unmatched']
 if kind=='customs_set':extra['samples']=job.get('customs_samples',{})
 if kind=='settlement':extra['returns']=job.get('returns',[])
 if kind in ('customs_set','shipping_draft','settlement','consignment'):
  from .business_packages import VERSION
  extra['package_rules']=VERSION
 return digest(canonical({'version':1,'document':kind,'facts':parts,**extra}).encode())


def document_checks(job,kind):
 from .core import checks
 d=CATALOG[kind];subset={'products':[]}
 for p in job['products']:
  subset['products'].append(dict(p,values={k:v for k,v in p['values'].items() if k in fields_for(kind,'product',job=job)}))
 issues=checks(subset)
 if d.batch:
  for p in job['products']:
   for b in p.get('batches',[]):
    v=b['values']
    if {'mfg_date','exp_date'}<=d.batch and v.get('mfg_date') and v.get('exp_date') and v['mfg_date'][:7]>v['exp_date'][:7]:issues.append(p['id']+'：批次有效期早于生产日期')
   for key in ('quantity','cartons'):
    values=[b['values'].get(key) for b in p.get('batches',[])]
    if values and all(x is not None for x in values) and p['values'].get(key) is not None:
     if sum(Decimal(v) for v in values)!=Decimal(p['values'][key]):issues.append(p['id']+'：各批次'+('数量' if key=='quantity' else '箱数')+'合计与本次出运不一致')
 return issues
