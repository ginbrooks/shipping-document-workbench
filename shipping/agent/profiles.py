"""Private original-file profiles. No company data or original document bytes in code."""
from pathlib import Path
import hashlib,json,re
from .ooxml import patch_xlsx,patch_docx,audit_xlsx,audit_docx,members,sheet_paths,word_targets
from .core import output_context, questions, checks
from .layout import VERSION,assert_layout,booking_layout,split_booking_pages,expected_text,check_pdf,fingerprint,currency_format
from decimal import Decimal
from datetime import date
from .catalog import CATALOG,NAMES,input_hash,document_checks
from .document_ooxml import select_sheets,print_layout,append_batch_sheet,patch_pickup,audit_pickup,unsigned_pickup,fit_booking_tables

FILES={'customer':'盐酸氯米帕明片_结汇资料.xlsx','booking':'空运托书.docx','awb':'提单待确认.xlsx'}
HASHES={'customer':'05f5656a4647f0bcef2556881607fb144c8b90bad9c9fbfbee0cf90d44d78902','booking':'4c2460aaef19b54f7347dac32af9639e7359c9fc885cb2f69ec337e06ba1be67','awb':'4653d7eb055f03ac52f4411ee8cc734cad44e2cbd059957c6677c7202109a31a'}

EXTRA_FILES={'road_booking':'陆运托书.docx','pickup':'上化  委托书 .docx'}
EXTRA_HASHES={'road_booking':'b545f0b7a42a78e24b0a94320abd3ff7ba5560233bd336c954fe36d429aa73cc','pickup':'c38e6558c5ec36135df6157f766f6a0b1aabb6ae7fdb94a60d1215ca54b6a7c2'}
ALL_FILES={**FILES,**EXTRA_FILES}
ALL_HASHES={**HASHES,**EXTRA_HASHES}


def install_profile(repo,source,kind):
 data=Path(source).read_bytes()
 if hashlib.sha256(data).hexdigest()!=ALL_HASHES[kind]:raise ValueError('原件与已审核模板不一致')
 root=repo.root/'agent_profiles'/'original-v1';root.mkdir(parents=True,exist_ok=True);path=root/ALL_FILES[kind]
 if path.exists() and path.read_bytes()!=data:raise ValueError('原模板已存在不同内容，未覆盖')
 if not path.exists():path.write_bytes(data);path.chmod(0o444)
 return path


def install_originals(repo,source):
 root=repo.root/'agent_profiles'/'original-v1';root.mkdir(parents=True,exist_ok=True)
 for kind,name in FILES.items():
  data=(Path(source)/name).read_bytes()
  if hashlib.sha256(data).hexdigest()!=HASHES[kind]:raise ValueError('ORIGINAL_HASH_MISMATCH')
  p=root/name
  if p.exists() and p.read_bytes()!=data:raise ValueError('ORIGINAL_PROFILE_CONFLICT')
  if not p.exists():p.write_bytes(data);p.chmod(0o444)
 return root

def original(repo,kind):
 kind=CATALOG[kind].template if kind in CATALOG else kind
 p=repo.root/'agent_profiles'/'original-v1'/ALL_FILES[kind]
 if not p.exists():raise ValueError('未安装原始模板：'+ALL_FILES[kind])
 data=p.read_bytes()
 if hashlib.sha256(data).hexdigest()!=ALL_HASHES[kind]:raise ValueError('原始模板已变化，请重新审核映射')
 return data

def get(c,k,default='待补'):
 v=c.get(k);return default if v is None or v=='' else str(v)

def number(c,k,unit=''):
 v=c.get(k);return '待补' if v is None or v=='' else str(v)+unit

def invoice_date(c):
 v=c.get('invoice_date')
 if not v:return '待补'
 return date.fromisoformat(v).strftime('%b.%d %Y')

def customer_changes(job,p,kind='customer'):
 multi=len(p.get('batches',[]))>1
 if p.get('batches') and not multi:
  from copy import deepcopy
  p=deepcopy(p);p['values'].update({k:v for k,v in p['batches'][0]['values'].items() if k in ('batch_no','mfg_date','exp_date')})
 c=output_context(job,p);v=get
 desc=v(c,'description');qty=v(c,'quantity_text');batch=v(c,'batch_no')
 if multi:
  batch='; '.join(b['values'].get('batch_no','待补') for b in p['batches']);c['batch_no']='SEE BATCH DETAILS';c['mfg_date']='SEE BATCH DETAILS';c['exp_date']='SEE BATCH DETAILS'
 dates=invoice_date(c);samples=c.get('samples_text','');sample_batches=[s.strip() for s in c.get('sample_batches','').split(';') if s.strip()]
 if kind in ('packing','customer') and len(sample_batches)>3:raise ValueError('样品批号超过原箱单的 3 个位置，需确认版式扩展')
 if len(desc)>130 or (kind!='advice' and len(samples)>220):raise ValueError('产品或样品说明超出已检查的原模板容量，请缩短到原件正式名称或确认扩展')
 changes={
 'SHIPPING ADVICE!B3':v(c,'invoice_no'),'SHIPPING ADVICE!A4':'DATE: '+dates,'SHIPPING ADVICE!A16':qty,'SHIPPING ADVICE!D16':desc,'SHIPPING ADVICE!A20':desc,'SHIPPING ADVICE!A21':v(c,'moc_insurance'),
 '发票!L7':v(c,'invoice_no'),'发票!L10':dates,'发票!D23':desc,'发票!H23':qty,'发票!I23':v(c,'price_text'),'发票!L23':v(c,'amount'),'发票!H25':qty,
 '发票!F27':'PACKAGE: '+v(c,'packing_text'),'发票!F32':samples,'发票!F33':v(c,'moc_insurance'),
 '箱单!I8':v(c,'invoice_no'),'箱单!I11':dates,'箱单!D15':desc,'箱单!F15':number(c,'cartons',' CTNS'),'箱单!G15':qty,'箱单!H15':number(c,'gross_kg',' KG'),'箱单!I15':number(c,'net_kg',' KG'),'箱单!J15':number(c,'volume_m3',' CBM'),
 '箱单!F19':number(c,'cartons',' CTNS'),'箱单!G19':qty,'箱单!H19':number(c,'gross_kg',' KG'),'箱单!I19':number(c,'net_kg',' KG'),'箱单!J19':number(c,'volume_m3',' CBM'),
 '箱单!B21':v(c,'storage'),'箱单!B22':v(c,'moc_insurance'),'箱单!B23':'BATCH NO.: '+batch,'箱单!B28':samples,
 '箱单!C29':sample_batches[0] if len(sample_batches)>0 else '', '箱单!C30':sample_batches[1] if len(sample_batches)>1 else '', '箱单!C31':sample_batches[2] if len(sample_batches)>2 else '',
 'Page 1!D22':v(c,'pallets'),'Page 1!F22':v(c,'gross_kg'),'Page 1!N22':v(c,'chargeable_kg'),'Page 1!AL22':desc+'\nHS CODE: '+v(c,'hs_code')+'\nVOL: '+v(c,'volume_m3')+' CBM','Page 1!E26':desc,'Page 1!E27':v(c,'moc_insurance'),'Page 1!S29':'','Page 1!AM39':'','Page 1!H42':''}
 # Keep a formula in the variable formula cell. Unknown price/quantity cannot retain old constants.
 changes['发票!L25']={'formula':c['quantity']+'*'+c['customer_unit_price'],'value':c['amount_number'],'display':get(c,'customer_currency')+' '+format(Decimal(c['amount_number']),',.2f')} if c.get('amount_number') and c.get('customer_currency') else '待补'
 # Retain every label, with wrapping governed by the original merged cell.
 changes['发票!A23']='PRODUCT NAME: '+desc+'\nBRAND:\nSTRENGTH: '+v(c,'strength')+'\nVOLUME: '+number(c,'volume_m3',' CBM')+'\nGROSS WEIGHT: '+number(c,'gross_kg',' KG')+'\nSTORAGE: '+v(c,'storage')+'\nBATCH NO.: '+('SEE BATCH DETAILS' if multi else batch)+'\nPACKAGE: '+v(c,'packing_text')+'\nEXP. DATE: '+v(c,'exp_date')+'\nMFG. DATE: '+v(c,'mfg_date')+'\nMANUFACTURER:'
 return changes

def sum_field(job,key):
 values=[p['values'].get(key) for p in job['products']]
 if not values or any(v is None for v in values):return '待补'
 return format(sum(Decimal(v) for v in values),'f')

def booking_changes(job):
 if len(job['products'])>30:raise ValueError('一份托书最多 30 个产品，请拆分本次货物')
 s=job['shipment']['values'];contract_nos=[c['values'].get('contract_no','待补') for c in job['contracts']]
 d=date.fromisoformat(s['shipping_date']) if s.get('shipping_date') else None
 header_date=f'{d.year}年 {d.month:02d}月{d.day:02d} 日' if d else '日期待补'
 changes={'0:0:0':{'find':'2026年 08月31 日','value':header_date},'0:2:5':' & '.join(contract_nos),'0:9:3':' & '.join(contract_nos),'0:12:1':get(s,'warehouse_date'),'0:15:3':sum_field(job,'volume_m3'),'0:16:3':get(s,'freight_note'),'0:17:3':'; '.join(sorted({get(p['values'],'hs_code') for p in job['products']}))}
 for index in range(max(2,len(job['products']))):
  row=index+1;p=job['products'][index] if index<len(job['products']) else None
  if p:
   c=output_context(job,p)
   price=[f"中文：{get(c,'name_cn')}\n英文：{get(c,'name_en')}",get(c,'quantity_text')+'\n'+number(c,'cartons','箱')+' / '+number(c,'pallets','托'),get(c,'price_text'),get(c,'customer_amount'),(c['customs_currency']+' '+c['customs_unit_price']+'/'+c['base_unit']) if all(c.get(k) for k in ('customs_currency','customs_unit_price','base_unit')) else '待补',get(c,'customs_amount')]
   compact_pack=get(c,'packing_text')
   for pattern,replacement in [(r'(?i)TABS?','片'),(r'(?i)BOTTLES?','瓶'),(r'(?i)CARTONS?','箱')]:compact_pack=re.sub(pattern,replacement,compact_pack)
   compact_pack=compact_pack.replace(' × ','，')
   packing=[get(c,'name_cn'),compact_pack,number(c,'pallets','托')+' ('+number(c,'cartons','箱')+')',number(c,'net_kg',' KG'),number(c,'carton_net_kg',' KG'),number(c,'carton_gross_kg',' KG')]
  else:price=['']*6;packing=['']*6
  for col,v in enumerate(price):changes[f'1:{row}:{col}']=v
  for col,v in enumerate(packing):changes[f'2:{row}:{col}']=v
 return changes

def awb_changes(job):
 s=job['shipment']['values'];desc='; '.join(p['values'].get('name_en','待补') for p in job['products']);hs='; '.join(sorted(set(p['values'].get('hs_code','待补') for p in job['products'])))
 return {'Page 1!B2':get(s,'airwaybill_no'),'Page 1!AE2':get(s,'airwaybill_no'),'Page 1!AF35':get(s,'airwaybill_no'),'Page 1!J2':' & '.join(c['values'].get('contract_no','待补') for c in job['contracts']),'Page 1!E14':get(s,'flight'),'Page 1!F14':get(s,'flight_date'),'Page 1!N31':get(s,'flight_date'),'Page 1!C23':sum_field(job,'pallets'),'Page 1!E23':sum_field(job,'gross_kg'),'Page 1!K23':get(s,'total_chargeable_kg'),'Page 1!AC23':desc+'\nHS CODE: '+hs+'\nVOL: '+sum_field(job,'volume_m3')+' CBM'}


def expand_booking(data,count):
 from copy import deepcopy
 from lxml import etree
 from .ooxml import W,package
 if count<=2:return data
 parts=members(data);root=etree.fromstring(parts['word/document.xml']);tables=root.findall('.//{'+W+'}tbl')
 for index in (1,2):
  rows=tables[index].findall('{'+W+'}tr');last=rows[-1]
  for _ in range(count-2):tables[index].append(deepcopy(last))
 return package(data,{'word/document.xml':etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)})


def pickup_changes(job):
 if len(job['products'])>6:raise ValueError('一份报告委托书最多 6 份报告，请分开办理')
 changes={}
 for i in range(6):
  p=job['products'][i]['values'] if i<len(job['products']) else {}
  values=[get(p,'report_no') if p else '',get(p,'name_cn') if p else '',number(p,'report_copies',' 份') if p else '', '√重出报告（¥100/份）' if p else '']
  for col,value in enumerate(values):changes[f'0:{i+1}:{col}']=value
 s=job['shipment']['values'];v=s.get('authorization_date');d=date.fromisoformat(v) if v else None
 paragraphs={7:f'{d.year}年 {d.month}月 {d.day}日' if d else '委托日期：待补',9:'委托取件单位：'+get(s,'collector_company'),10:'取件人：'+get(s,'collector_name'),11:'联系电话：'+get(s,'collector_phone')}
 return changes,paragraphs


def render_outputs(repo,job,targets=None):
 import uuid,shutil
 from shipping.ingestion import digest
 from .packages import attachment_package
 targets=targets or job['targets'];outputs=[];prepared=[]
 from .business_flows import FLOW_KEYS
 if any(k in FLOW_KEYS for k in targets):
  if len(targets)!=1 or targets[0] not in FLOW_KEYS:raise ValueError('请选择一类业务资料生成')
  from .business_packages import render_package
  return render_package(repo,job,targets[0])
 if not job.get('products') and any(t!='attachments' for t in targets):raise ValueError('请先识别资料或添加本票产品')
 if job.get('extracted_files') is not None and sorted(f['id'] for f in job['files'])!=job['extracted_files'] and not job.get('manual_entry'):raise ValueError('新增资料尚未提取，请先重新识别，再生成本票文件')
 for kind in targets:
  errors=document_checks(job,kind)
  if errors:raise ValueError('；'.join(errors))
  if kind=='attachments':
   prepared.append((kind,'ALL',attachment_package(repo,job),{},'zip',''));continue
  spec=CATALOG[kind];data=original(repo,kind);template=spec.template
  if template=='customer':
   for contract in job['contracts']:
    if sum(p['contract_id']==contract['id'] for p in job['products'])>1:raise ValueError('同一合同有多个产品，当前客户底稿需先适配多产品明细；可继续生成整票托书')
   items=[(p['id'],customer_changes(job,p,kind),p) for p in job['products']]
  else:items=[('ALL',booking_changes(job) if template in ('booking','road_booking') else pickup_changes(job)[0] if template=='pickup' else awb_changes(job),None)]
  for scope,changes,product in items:
   if template in ('booking','road_booking'):
    base=booking_layout(data,len(job['products']));
    # Always clear all three slots, including the fixed continuation slot.
    for table in (1,2):
     for row in range(len(job['products'])+1,4):
      for col in range(6):changes[f'{table}:{row}:{col}']=''
    rendered=patch_docx(base,changes);errors=audit_docx(base,rendered,changes);assert_layout(base,rendered,'docx')
   elif template=='pickup':
    base=unsigned_pickup(data);paragraphs=pickup_changes(job)[1];rendered=patch_pickup(base,changes,paragraphs);errors=audit_pickup(base,rendered,changes,paragraphs)
   else:
    base=currency_format(data,output_context(job,product).get('customer_currency')) if template=='customer' and isinstance(changes.get('发票!L25'),dict) else data
    rendered=patch_xlsx(base,changes);errors=audit_xlsx(base,rendered,changes);assert_layout(base,rendered,'xlsx')
   if errors:raise ValueError('固定区域检查失败：'+','.join(errors))
   if template in ('booking','road_booking'):rendered=split_booking_pages(rendered)
   if template=='customer':
    names=[spec.sheet] if spec.sheet else list(sheet_paths(members(rendered)))
    rendered=select_sheets(rendered,names)
    if len(product.get('batches',[]))>1 and kind in ('invoice','customer'):rendered=append_batch_sheet(rendered,job,product)
    rendered=print_layout(rendered)
   elif template=='awb':rendered=print_layout(select_sheets(rendered,['Page 1']))
   prepared.append((kind,scope,rendered,changes,Path(ALL_FILES[template]).suffix.lstrip('.'),ALL_HASHES[template]))
 run_id=uuid.uuid4().hex;folder=repo.root/'exports'/'agent'/job['id']/run_id
 try:
  for kind,scope,rendered,changes,extension,template_hash in prepared:
   product=next((p for p in job['products'] if p['id']==scope),None)
   contract=next((c for c in job['contracts'] if product and c['id']==product['contract_id']),None)
   scope_name=(contract['values'].get('invoice_no') or contract['values'].get('contract_no') or scope) if contract else '整票'
   safe=re.sub(r'[^\w\u4e00-\u9fff.-]+','_',scope_name)[:70]
   name=NAMES[kind]+'_'+safe+('_待核对' if kind!='attachments' else '')+'.'+extension
   path=folder/name
   if path.exists():raise ValueError('两份文件的发票号或合同号重复，请核实编号后再生成，未覆盖任何文件')
   path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(rendered)
   output={'type':kind,'scope':scope,'path':str(path.relative_to(repo.root)),'sha256':digest(rendered),'source_template_sha256':template_hash,'fixed_area_check':'passed','missing':questions(job,[kind]),'reviewed':False,'input_hash':input_hash(job,kind),'source_revision':job['revision'],'created_at':__import__('shipping.repository',fromlist=['now']).now(),'variable_cells':list(changes)}
   if extension in ('xlsx','docx'):
    from .preview import pdf_preview
    expected=expected_text(rendered,extension,changes,pickup_changes(job)[1] if kind=='pickup' else None)
    pages=2 if kind in ('booking','road_booking') else 2 if kind=='pickup' else len(sheet_paths(members(rendered)))
    if extension=='xlsx' and '批次明细' in sheet_paths(members(rendered)):
     pages+=(len(product.get('batches',[]))-1)//12
    output['layout_expected']=expected;output['layout_pages']=pages
    output['layout_check']=check_pdf(pdf_preview(repo,output),expected,pages)
    output['layout_check']['source_sha256']=output['sha256'];output['layout_check']['structure_sha256']=fingerprint(rendered,extension)
    path.with_suffix('.layout.json').write_text(json.dumps(output['layout_check'],ensure_ascii=False,indent=2))
   outputs.append(output)
 except Exception:shutil.rmtree(folder,ignore_errors=True);raise
 return outputs
