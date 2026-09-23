"""Four deterministic local packages. Facts and signed returns stay separate."""
from copy import deepcopy
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from pathlib import Path
import io,json,re,shutil,uuid,zipfile
from pypdf import PdfReader,PdfWriter
from pypdf.errors import PyPdfError
from shipping.ingestion import digest
from shipping.repository import now,canonical
from .catalog import input_hash,NAMES,document_checks

VERSION='four-packages-v2'
NONE={'无','NONE','N/A','NA','NO','不适用'}


def absent(value):return str(value or '').strip().upper() in NONE
def amount(q,p):return (Decimal(str(q))*Decimal(str(p))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
def text(v):return '待补' if v is None or v=='' else str(v)
def num(v):return format(Decimal(str(v)),'f')


def contract_groups(job,customs=False):
 """A single invoice can cover multiple contracts, but is emitted exactly once."""
 invoice='customs_invoice_no' if customs else 'invoice_no';contract_key='customs_contract_no' if customs else 'contract_no';groups={}
 for c in job['contracts']:
  products=[p for p in job['products'] if p['contract_id']==c['id']]
  if not products:continue
  key=c['values'].get(invoice) or c['id']
  if key not in groups:groups[key]=(deepcopy(c),list(products))
  else:
   combined,items=groups[key];items.extend(products)
   combined['values'][contract_key]=' & '.join(dict.fromkeys([text(combined['values'].get(contract_key)),text(c['values'].get(contract_key))]))
 return list(groups.values())


def save_samples(job,pid,rows):
 if pid not in {p['id'] for p in job['products']}:raise ValueError('样品所属产品不存在')
 clean=[]
 for row in rows:
  if not any(v not in ('',None) for v in row.values()):continue
  item={k:str(row.get(k) or '').strip() for k in ('name','quantity','unit','unit_price','net_kg','hs_code','origin','source','tax','purpose')}
  if any(not v for v in item.values()):raise ValueError('请补齐样品品名、数量、单位、单价、净重、编码、原产国、货源地和用途')
  for k in ('quantity','unit_price','net_kg'):
   try:d=Decimal(item[k])
   except InvalidOperation:raise ValueError('样品数量、单价和净重必须是数字') from None
   if not d.is_finite() or d<0 or (k=='quantity' and d==0):raise ValueError('样品数值无效')
   item[k]=format(d,'f')
  if any(len(v)>300 for v in item.values()):raise ValueError('样品单项过长，请核对正式名称与用途')
  clean.append(item)
 if len(clean)>30:raise ValueError('每产品最多 30 条报关样品明细')
 j=deepcopy(job);j.setdefault('customs_samples',{})[pid]=clean
 j['audit'].append({'at':now(),'event':'customs_samples_reviewed','product_id':pid,'count':len(clean)})
 return j


def customs_lines(job,products=None):
 lines=[]
 for p in products or job['products']:
  v=p['values'];lines.append(dict(product_id=p['id'],name=text(v.get('name_cn')),strength=text(v.get('strength')),quantity=text(v.get('customs_quantity')),unit=text(v.get('customs_unit')),unit_price=text(v.get('declaration_unit_price')),currency=text(v.get('customs_currency')),amount=amount(v['customs_quantity'],v['declaration_unit_price']),net=Decimal(v['customs_main_net_kg']),hs_code=text(v.get('customs_hs_code')),origin=text(v.get('origin_country')),source=text(v.get('domestic_source')),tax=text(v.get('customs_tax_method')),sample=False,purpose=''))
  for s in job.get('customs_samples',{}).get(p['id'],[]):
   lines.append(dict(s,product_id=p['id'],strength='',currency=v['customs_currency'],amount=amount(s['quantity'],s['unit_price']),net=Decimal(s['net_kg']),sample=True))
 return lines


def package_issues(job,kind):
 issues=list(document_checks(job,kind))
 if not job.get('products'):issues.append('请先添加本票产品')
 if job['shipment']['values'].get('transport_mode') not in ('空运','陆运'):issues.append('请确认空运或陆运')
 if kind=='customs_set':
  for p in job['products']:
   rows=job.get('customs_samples',{}).get(p['id'],[]);v=p['values'];label=v.get('name_cn',p['id'])
   if not absent(v.get('samples_declaration')) and not rows:issues.append(label+'：请核对报关样品明细表，程序不会从说明猜数量和价格')
   if absent(v.get('samples_declaration')) and rows:issues.append(label+'：样品说明为无，但明细表仍有样品，请核对')
   if rows:
    try:save_samples(job,p['id'],rows)
    except ValueError as e:issues.append(label+'：'+str(e))
   if v.get('gross_kg') and v.get('customs_main_net_kg'):
    try:
     if Decimal(v['customs_main_net_kg'])+sum(Decimal(s['net_kg']) for s in rows)>Decimal(v['gross_kg']):issues.append(label+'：主货加样品净重超过含托总毛重')
    except (KeyError,InvalidOperation):pass
 invoice_key='customs_invoice_no' if kind=='customs_set' else 'invoice_no';date_key='customs_invoice_date' if kind=='customs_set' else 'invoice_date'
 invoice_dates={}
 for c in job['contracts']:
  value=c['values'].get(invoice_key);day=c['values'].get(date_key)
  if kind!='consignment' and value and value in invoice_dates and invoice_dates[value]!=day:issues.append(text(value)+'：同一发票号的日期不一致')
  invoice_dates[value]=day
 if kind=='customs_set' and len({c['values'].get('customs_buyer') for c in job['contracts'] if any(p['contract_id']==c['id'] for p in job['products'])})>1:issues.append('报关境外收货人不同，请分票办理')
 for c,products in contract_groups(job,customs=kind=='customs_set'):
  price_key='customs_currency' if kind=='customs_set' else 'customer_currency'
  if kind in ('customs_set','shipping_draft','settlement') and len({p['values'].get(price_key) for p in products})>1:issues.append(text(c['values'].get('invoice_no') or c['values'].get('customs_invoice_no'))+'：同一张发票出现不同币种，请拆分发票归属')
 if kind=='settlement':
  for p in job['products']:
   for role,label,key in [('transport','运输回件','transport_document_no'),('certificate','产地证回件','certificate_no')]:
    rows=[r for r in job.get('returns',[]) if r.get('product_id')==p['id'] and r.get('role')==role]
    if len(rows)!=1 or not rows[0].get('confirmed') or rows[0].get('reference')!=p['values'].get(key) or rows[0].get('facts_hash')!=return_facts_hash(job):issues.append(p['values'].get('name_cn',p['id'])+'：请关联并核对'+label+'（数据变化后需重新确认）')
 return list(dict.fromkeys(issues))


def parse_pages(value,total):
 if not str(value or '').strip():return list(range(1,total+1))
 out=[]
 try:
  for part in str(value).replace('，',',').split(','):
   bounds=[int(v.strip()) for v in part.split('-')]
   if len(bounds)==1:out.extend(bounds)
   elif len(bounds)==2 and bounds[0]<=bounds[1]:out.extend(range(bounds[0],bounds[1]+1))
   else:raise ValueError()
 except ValueError:raise ValueError('页码请填写 1,3 或 2-4；留空表示整份') from None
 if not out or len(out)!=len(set(out)) or min(out)<1 or max(out)>total:raise ValueError('回件页码超出范围或重复')
 return out


def validate_returns(repo,job):
 issues=[];files={f['id']:f for f in job['files']+job.get('attachments',[])};used={}
 products={p['id']:p for p in job['products']}
 seen=set()
 for r in job.get('returns',[]):
  try:
   pid=r['product_id'];role=r['role'];fid=r['file_id'];pages=r['pages']
   if pid not in products or role not in ('transport','certificate') or fid not in files:raise ValueError('回件归属或文件不存在')
   if (pid,role) in seen:raise ValueError('同产品同类回件重复')
   seen.add((pid,role))
   if Path(files[fid]['name']).suffix.lower()!='.pdf':raise ValueError('请使用正式回件 PDF 原件')
   pdf=PdfReader(io.BytesIO(repo.file_bytes(fid)))
   if pdf.is_encrypted:raise ValueError('回件 PDF 有密码，请先取得可读取的正式文件')
   if not pages or any(type(p) is not int or p<1 or p>len(pdf.pages) for p in pages) or len(set(pages))!=len(pages):raise ValueError('回件页码无效')
   page_text=''.join(pdf.pages[p-1].extract_text() or '' for p in pages)
   compact=lambda value:re.sub(r'[^A-Z0-9]','',str(value).upper())
   reference=compact(r.get('reference',''))
   if len(page_text.strip())>50 and reference and reference not in compact(page_text):raise ValueError('所选回件页中未找到编号 '+r['reference']+'，请核对文件和页码')
   for page in pages:
    old=used.get((fid,page));key='transport_document_no' if role=='transport' else 'certificate_no'
    ref=products[pid]['values'].get(key)
    if old and old!=(role,ref):raise ValueError('同一页回件被关联为不同类型或不同编号')
    used[(fid,page)]=(role,ref)
  except PyPdfError:issues.append('回件 PDF 无法读取，请重新上传完整的原件')
  except (ValueError,KeyError,OSError) as e:issues.append(str(e))
 return issues


def return_page_count(repo,fid):
 try:
  pdf=PdfReader(io.BytesIO(repo.file_bytes(fid)))
  if pdf.is_encrypted:raise ValueError('回件 PDF 有密码，请使用可读取的原件')
  return len(pdf.pages)
 except PyPdfError:raise ValueError('回件 PDF 无法读取，请重新上传完整的原件') from None


def save_returns(repo,job,rows):
 j=deepcopy(job);j['returns']=deepcopy(rows)
 errors=validate_returns(repo,j)
 if errors:raise ValueError('；'.join(errors))
 for r in j['returns']:r['facts_hash']=return_facts_hash(j)
 j['audit'].append({'at':now(),'event':'returns_reviewed','count':len(rows)})
 return j


def return_facts_hash(job):
 """A signed return approval cannot survive any change in facts it should match."""
 from .catalog import owners,fields_for
 from .review import prune_empty
 job=prune_empty(job)
 facts=[{k:o['values'].get(k) for k in sorted(fields_for('settlement',owner,job=job))} for o,owner in owners(job,'settlement')]
 return digest(canonical(facts).encode())


def profile(repo):
 from openpyxl import load_workbook
 from .profiles import original
 wb=load_workbook(io.BytesIO(original(repo,'customer')),data_only=True);s=wb['发票']
 result=dict(company_cn=s['A1'].value,company_en=s['A2'].value,address=s['A3'].value,consignee=s['B6'].value)
 p=repo.root/'agent_profiles'/'business-v1.json'
 if p.exists():
  extra=json.loads(p.read_text());result.update(extra)
  result['seller_terms']=re.sub(r'\s*\n\s*',' ',result['seller_terms'])
 co=repo.root/'agent_profiles'/'co-v1.json'
 if co.exists():result.update(json.loads(co.read_text()))
 return result


def install_co_profile(repo,data):
 approved='bf42e2f1e09b8896fd705b44e2c491eeaf8dae1a96b5b68e3a80dce4621c9448'
 if digest(data)!=approved:raise ValueError('这份 PDF 与已适配草件样例不一致，请使用原 OLD-DEMO-CONTRACT-1 出运草件 PDF')
 value=PdfReader(io.BytesIO(data)).pages[-1].extract_text()
 result=dict(co_exporter=value.split('1.Exporter\n',1)[1].split('***',1)[0].strip(),co_consignee=value.split('2.Consignee\n',1)[1].split('3.Means of transport',1)[0].strip(),co_source_sha256=approved)
 p=repo.root/'agent_profiles'/'co-v1.json';p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists() and json.loads(p.read_text())!=result:raise ValueError('产地证固定资料已有不同版本，未覆盖')
 if not p.exists():p.write_text(json.dumps(result,ensure_ascii=False,indent=2));p.chmod(0o444)
 return result


def install_business_profile(repo,data):
 """Import only verified fixed company identifier/SC clauses, never historical facts."""
 approved='6fbd2875aecfd3fa546b6043c9b6ab7bf13ca716dacccf0140887cc89659cc3a'
 if digest(data)!=approved:raise ValueError('这份 PDF 与已适配报关样例不一致，请使用原 OLD-DEMO-CONTRACT-1 报关资料 PDF')
 pages=PdfReader(io.BytesIO(data)).pages;declaration=pages[2].extract_text();sc=pages[3].extract_text()
 codes=re.findall(r'91[A-Z0-9]{16}',declaration)
 if not codes or '(7)' not in sc or 'CONFIRMED BY BUYERS' not in sc:raise ValueError('报关固定资料提取失败')
 terms=sc[sc.index('(7)'):sc.index('CONFIRMED BY BUYERS')].strip()
 result=dict(registration=codes[0],seller_terms=terms,source_sha256=approved)
 p=repo.root/'agent_profiles'/'business-v1.json';p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists() and json.loads(p.read_text())!=result:raise ValueError('固定资料已有不同版本，未覆盖')
 if not p.exists():p.write_text(json.dumps(result,ensure_ascii=False,indent=2));p.chmod(0o444)
 return result


def package_current(repo,out):
 if out.get('package_version')!=VERSION:return False
 from .layout import VERSION as LV
 if out.get('layout_check',{}).get('version')!=LV:return False
 try:
  if out.get('profile_hash') and out['profile_hash']!=digest(canonical(profile(repo)).encode()):return False
  for f in out['members']:
   if digest(repo.safe_path(f['path']).read_bytes())!=f['sha256']:return False
  return digest(repo.safe_path(out['path']).read_bytes())==out['sha256']
 except (KeyError,OSError,ValueError):return False


def render_package(repo,job,kind,on_progress=None):
 from .review import prune_empty
 source_job=job
 job=prune_empty(job)
 from .core import questions
 from .preview import pdf_preview
 from .layout import check_pdf,VERSION as LV
 from .business_forms import build_forms
 from .profiles import render_outputs
 from .flow import extraction_current
 if job.get('files') and not extraction_current(repo,job) and job.get('manual_files')!=sorted(f['id'] for f in job['files']):raise ValueError('资料尚未完成识别，请先处理资料或选择手动核对')
 missing=questions(job,[kind]);issues=package_issues(job,kind)
 if job.get('unmatched'):issues.append('还有识别内容未确认合同或产品归属，请先在核对页处理')
 if missing:issues.insert(0,'请先补齐 '+str(len(missing))+' 项核对信息：'+'、'.join(x['label'] for x in missing[:5]))
 if kind=='settlement':issues.extend(validate_returns(repo,job))
 if issues:raise ValueError('\n'.join(issues))
 run=repo.root/'exports'/'agent'/job['id']/uuid.uuid4().hex;run.mkdir(parents=True)
 members=[];components=[];extra_dirs=[];writer=PdfWriter();page_start=1
 def add(name,data,role,**meta):
  p=run/name;p.write_bytes(data);item=dict(path=str(p.relative_to(repo.root)),name=name,sha256=digest(data),role=role,**meta);members.append(item);return item
 def progress(label):
  if on_progress:on_progress(label)
 try:
  if kind=='consignment':
   mode='road_booking' if job['shipment']['values']['transport_mode']=='陆运' else 'booking'
   # One numbered group per three fixed slots; original company/route text stays intact.
   for index in range(0,len(job['products']),3):
    sub=deepcopy(job);sub['products']=job['products'][index:index+3];ids={p['contract_id'] for p in sub['products']};sub['contracts']=[c for c in job['contracts'] if c['id'] in ids]
    progress(f'填写托书第 {index//3+1} 组并检查两页排版')
    generated=render_outputs(repo,sub,[mode]);extra_dirs.extend(repo.safe_path(o['path']).parent for o in generated)
    for o in generated:
     src=repo.safe_path(o['path']);base=f'{index//3+1:02d}_{NAMES[mode]}'
     add(base+'.docx',src.read_bytes(),'editable');pdf=src.with_suffix('.pdf').read_bytes();add(base+'.pdf',pdf,'generated_pdf')
     n=len(PdfReader(io.BytesIO(pdf)).pages);writer.append(io.BytesIO(pdf));components.append(dict(name=base,pages=n,start=page_start,products=[p['id'] for p in sub['products']],layout=o['layout_check']));page_start+=n
  else:
   data_profile=profile(repo)
   if kind=='customs_set' and (not data_profile.get('registration') or not data_profile.get('seller_terms')):raise ValueError('报关固定公司资料尚未安装，请在设置导入已适配的报关样例 PDF')
   if kind=='shipping_draft' and not data_profile.get('co_exporter'):raise ValueError('产地证固定资料尚未安装，请在设置导入已适配的出运草件 PDF')
   for index,part in enumerate(build_forms(job,kind,data_profile,repo=repo),1):
    progress('填写并检查：'+part['name'])
    name=f'{index:02d}_'+re.sub(r'[^\w\u4e00-\u9fff.-]+','_',part['name'])[:65]
    item=add(name+'.xlsx',part['data'],'editable');out={'path':item['path']}
    pdf=pdf_preview(repo,out);report=check_pdf(pdf,part['expected'],part['pages']);report['source_sha256']=item['sha256']
    # Converter already wrote the PDF; register it in the package manifest.
    add(name+'.pdf',pdf,'generated_pdf');writer.append(io.BytesIO(pdf));components.append(dict(name=part['name'],pages=part['pages'],start=page_start,layout=report));page_start+=part['pages']
  if kind=='settlement':
   included=set();originals=set()
   for role,label in [('transport','已确认运输单据'),('certificate','正式产地证')]:
    for r in [r for r in job['returns'] if r['role']==role]:
     fid=r['file_id'];data=repo.file_bytes(fid)
     if fid not in originals:
      source=next(f for f in job['files']+job.get('attachments',[]) if f['id']==fid);safe=re.sub(r'[^\w\u4e00-\u9fff.-]+','_',Path(source['name']).stem)[:60]+'.pdf'
      add('原件_'+fid[:10]+'_'+safe,data,'original_return',source_sha256=fid);originals.add(fid)
     pages=[p for p in r['pages'] if (role,fid,p) not in included]
     if not pages:continue
     writer.append(io.BytesIO(data),pages=[p-1 for p in pages]);included.update((role,fid,p) for p in pages)
     components.append(dict(name=label,reference=r['reference'],source_sha256=fid,source_pages=pages,start=page_start,pages=len(pages),original=True));page_start+=len(pages)
  merged=io.BytesIO();writer.write(merged);base=NAMES[kind]+'_待核对';pdf_item=add(base+'.pdf',merged.getvalue(),'merged_pdf')
  profile_hash=digest(canonical(profile(repo)).encode()) if kind!='consignment' else None
  manifest=dict(version=VERSION,document=kind,job_id=job['id'],source_revision=job['revision'],input_hash=input_hash(source_job,kind),profile_hash=profile_hash,created_at=now(),components=components,return_links=job.get('returns',[]) if kind=='settlement' else [],files=deepcopy(members))
  add('文件清单.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode(),'manifest')
  zdata=io.BytesIO()
  with zipfile.ZipFile(zdata,'w',zipfile.ZIP_DEFLATED) as z:
   for item in members:z.writestr(item['name'],repo.safe_path(item['path']).read_bytes())
  target=run/(base+'.zip');target.write_bytes(zdata.getvalue())
  return [dict(type=kind,scope='ALL',path=str(target.relative_to(repo.root)),sha256=digest(zdata.getvalue()),input_hash=input_hash(source_job,kind),source_revision=job['revision'],created_at=now(),reviewed=False,package_version=VERSION,profile_hash=profile_hash,members=members,components=components,pdf_path=pdf_item['path'],layout_check=dict(version=LV,status='passed',pages=page_start-1))]
 except Exception:shutil.rmtree(run,ignore_errors=True);raise
 finally:
  for p in set(extra_dirs):shutil.rmtree(p,ignore_errors=True)
