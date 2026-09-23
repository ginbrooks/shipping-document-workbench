"""Presentation groups over the shared facts; uploads persist before recognition."""
from copy import deepcopy
from pathlib import Path
from .review import review_items,DraftStore,save_review
from .core import JobStore


def section(ident,title,hint,keys):
 return dict(id=ident,title=title,hint=hint,keys=set(keys.split()))

COMMON=[
 section('shared','共用信息','合同、商品和路线填写一次，通知、发票、箱单共同使用。','contract_no invoice_no invoice_date payment_terms name_cn name_en strength quantity base_unit transport_mode loading_port destination_port trade_term'),
 section('invoice','发票与通知','外销金额使用客户价格；MOC、保险信息同步到相关单据。','customer_unit_price customer_currency moc_no insurance_no'),
 section('packing','箱单与批次','核对包装、重量、体积、批次及样品，相关内容同步到发票。','cartons pallets net_kg gross_kg volume_m3 packing_text storage batch_no mfg_date exp_date samples_text sample_batches'),
]
SECTIONS={
 'customs_set':[
  section('shared','共用信息','抬头、品名和路线填写一次，四份报关单据共同使用。','transport_mode customs_invoice_no customs_invoice_date customs_contract_no customs_buyer name_cn strength loading_port destination_port trade_term customs_marks'),
  section('invoice','发票与合约','报关发票、销货合约和报关单共用本组量价；货值自动计算。','customs_quantity customs_unit declaration_unit_price customs_currency'),
  section('packing','箱单与包装','箱单与报关单共用包装件数和重量；主货净重不含另列样品。','customs_package_count customs_packaging customs_main_net_kg gross_kg volume_m3 cartons packing_text'),
  section('declaration','报关单','核对报关专用口岸、编码、产地、成交方式及运保杂费。','departure_port destination_country trade_country customs_trade_term customs_hs_code origin_country domestic_source customs_tax_method customs_freight customs_insurance customs_other_fee fees_currency customs_notes'),
  section('samples','样品与要素','无样品或不适用时按实际填写“无”，有样品时再补逐项明细。','samples_declaration declaration_elements'),
 ],
 'shipping_draft':COMMON+[section('transport','运输与产地证','核对草件使用的编码与目的国，正式编号留待结汇时填写。','hs_code certificate_hs_code origin_country destination_country chargeable_kg')],
 'settlement':COMMON+[section('transport','运输与正式回件','补实际运输和证书信息，再选择已上传的 PDF 确认对应关系。','hs_code certificate_hs_code origin_country destination_country chargeable_kg departure_date flight flight_date transport_document_no certificate_no certificate_date')],
 'consignment':[
  section('shared','合同与货物','先核对合同、运输方式、品名及本次出运数量。','contract_no transport_mode name_cn name_en quantity base_unit'),
  section('invoice','客户价与报关价','两组价格分别填写，程序分别计算金额。','customer_unit_price customer_currency customs_unit_price customs_currency'),
  section('packing','包装与重量','包装说明、箱托数、净重和体积对应托书明细。','packing_text cartons pallets net_kg carton_net_kg carton_gross_kg volume_m3'),
  section('transport','运输安排','核对托书日期、进仓日期、运费说明及运输用商品编码。','shipping_date warehouse_date freight_note hs_code'),
 ],
}


def review_sections(job,kind,rows=None):
 result=[dict(s,rows=[]) for s in SECTIONS[kind]]
 for row in review_items(job,kind) if rows is None else rows:
  matches=[s for s in result if s['id']=='packing'] if row['owner']=='batch' else [s for s in result if row['key'] in s['keys']]
  if len(matches)!=1:raise ValueError('核对分组未正确配置：'+kind+'/'+row['key'])
  matches[0]['rows'].append(row)
 for s in result:
  s['missing']=sum(r['status']=='missing' for r in s['rows'])
  s['pending']=sum(r['status'] in ('pending','conflict') for r in s['rows'])
 return result


def uploaded_materials(job):
 unique={}
 for f in job.get('files',[])+job.get('attachments',[]):unique.setdefault(f['id'],f)
 return [{'序号':i,'文件名':f['name']} for i,f in enumerate(unique.values(),1)]


def save_materials(repo,job,uploads,kind):
 from .flow import validate_uploads,attach_uploads
 from shipping.ingestion import digest
 items=validate_uploads(uploads)
 known={f['id'] for f in job['files']}
 if not any(digest(data) not in known for _,data in items):return job
 if job['id']=='new':
  draft=DraftStore(repo).get(job);view=deepcopy(job)
  if view['name']=='未命名新票':view['name']=Path(items[0][0]).stem
  job=save_review(JobStore(repo),view,{},kind,confirm=False)
  if draft['answers']:DraftStore(repo).put(job,draft['answers'])
 return attach_uploads(repo,job,uploads)
