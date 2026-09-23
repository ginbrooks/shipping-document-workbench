"""Fixed printable forms for the company's PDF-only references and continuations.

No company names, addresses, historical values or signatures live in this module.
Original customer worksheets are filled in copies; new forms use fixed font sizes
and bounded boxes, with explicit continuation sheets rather than shrinking text.
"""
from copy import deepcopy
from decimal import Decimal
import io,math,re,unicodedata
from openpyxl import Workbook
from openpyxl.styles import Font,Alignment,Border,Side
from openpyxl.worksheet.page import PageMargins
from openpyxl.utils import get_column_letter
from .business_packages import absent,amount,customs_lines,contract_groups,text


def quantity_totals(items,qkey='quantity',ukey='unit'):
 values={}
 for item in items:values[item[ukey]]=values.get(item[ukey],Decimal(0))+Decimal(item[qkey])
 return ' / '.join(format(v,'f')+' '+u for u,v in values.items())


def wrap(value,capacity):
 """Conservative font-width budget, then actual PDF ink validation after rendering."""
 lines=[]
 for line in str(value).split('\n'):
  current='';width=0
  tokens=re.findall(r'[A-Za-z0-9][A-Za-z0-9.,:/()_+\-]*|[^A-Za-z0-9]',line)
  for token in tokens:
   def measure(s):return sum(1 if unicodedata.east_asian_width(char) in ('W','F') else .65 if char in 'MW@%' else .53 for char in s)
   size=measure(token)
   if current and width+size>capacity:lines.append(current.rstrip());current='';width=0
   if size>capacity:
    for char in token:
     if current and width+measure(char)>capacity:lines.append(current);current='';width=0
     current+=char;width+=measure(char)
   else:current+=token;width+=size
  lines.append(current)
 return '\n'.join(lines)


class Form:
 def __init__(self,name,profile,landscape=False):
  self.name=name;self.profile=profile;self.landscape=landscape;self.w=Workbook();self.w.remove(self.w.active);self.expected={}
 def page(self,title,subtitle='',company_header=True):
  s=self.w.create_sheet('第'+str(len(self.w.worksheets)+1)+'页');s.sheet_view.showGridLines=False
  self.s=s;self.rows=28 if self.landscape else 41;self.column_pt=19.5 if self.landscape else 13.2
  # OOXML column width includes Excel's padding; do not add the padding twice
  # when budgeting text. These widths render to about 720 / 510 pt at 100%.
  for col in range(1,37):s.column_dimensions[get_column_letter(col)].width=3.6 if self.landscape else 2.55
  for row in range(1,self.rows+1):s.row_dimensions[row].height=18
  s.page_setup.orientation='landscape' if self.landscape else 'portrait';s.page_setup.paperSize=s.PAPERSIZE_A4
  s.page_setup.scale=100;s.page_setup.fitToWidth=1;s.page_setup.fitToHeight=1;s.sheet_properties.pageSetUpPr.fitToPage=True
  s.page_margins=PageMargins(left=.32,right=.32,top=.28,bottom=.28,header=0,footer=.12)
  s.print_area=f'A1:AJ{self.rows}';s.print_options.horizontalCentered=True
  s.oddFooter.center.text='Page &P';s.oddFooter.center.size=8
  if company_header:
   self.box(1,1,1,36,self.profile.get('company_cn',''),size=13,center=True,bold=True)
   self.box(2,1,2,36,self.profile.get('company_en',''),size=10,center=True)
   self.box(3,1,4,36,self.profile.get('address',''),size=9,center=True)
   self.box(5,1,6,36,title,size=14,bold=True,center=True)
  else:self.box(1,1,2,36,title,size=14,bold=True,center=True)
  if subtitle:self.box(7,1,8,36,subtitle,size=9)
  return self
 def box(self,r,c,r2,c2,value,*,size=10,border=False,center=False,bold=False):
  value='' if value is None else str(value)
  available=(c2-c+1)*self.column_pt-12
  wrapped=wrap(value,available/size)
  capacity=math.floor(((r2-r+1)*18-2)/(size*1.2))
  if value and len(wrapped.split('\n'))>capacity:raise ValueError(f'{self.name} · {self.s.title} 第 {r} 行：内容超过固定位置，请核对字段长度（保持 {size} 磅，不缩字）')
  if r2>r or c2>c:self.s.merge_cells(start_row=r,start_column=c,end_row=r2,end_column=c2)
  cell=self.s.cell(r,c,wrapped);cell.data_type='s';cell.font=Font(name='Arial',size=size,bold=bold,color='000000')
  cell.alignment=Alignment(horizontal='center' if center else 'left',vertical='center',wrap_text=True,shrink_to_fit=False,indent=0)
  if border:
   edge=Side(style='thin',color='444444')
   for row in self.s.iter_rows(min_row=r,max_row=r2,min_col=c,max_col=c2):
    for a in row:a.border=Border(left=edge if a.column==c else Side(),right=edge if a.column==c2 else Side(),top=edge if a.row==r else Side(),bottom=edge if a.row==r2 else Side())
  self.expected[self.s.title+'!'+cell.coordinate]=wrapped
 def table(self,r,spans,headers,rows,height=3,size=10):
  c=1
  for span,header in zip(spans,headers):self.box(r,c,r+1,c+span-1,header,size=9,border=True,center=True,bold=True);c+=span
  for ri,values in enumerate(rows):
   c=1;start=r+2+ri*height
   for span,value in zip(spans,values):self.box(start,c,start+height-1,c+span-1,value,size=size,border=True,center=True);c+=span
 def paragraphs(self,title,paragraphs,subtitle=''):
  self.page(title,subtitle);r=9
  for paragraph in paragraphs:
   # Fixed-size text flows onto explicit continuation pages; no crop/ellipsis.
   lines=wrap(paragraph,(36*self.column_pt-12)/9).split('\n')
   while lines:
    room=((self.rows-2-r+1)*18-4)//12
    if room<1:self.page(title+' / CONTINUED',subtitle);r=9;continue
    take=lines[:room];height=math.ceil((len(take)*12+4)/18)
    if r+height>self.rows-1:self.page(title+' / CONTINUED',subtitle);r=9;continue
    self.box(r,1,r+height-1,36,'\n'.join(take),size=9);r+=height+1;lines=lines[room:]
 def result(self):
  data=io.BytesIO();self.w.save(data)
  return dict(name=self.name,data=data.getvalue(),pages=len(self.w.worksheets),expected=self.expected)


def customs_forms(job,profile):
 s=job['shipment']['values'];parts=[];all_lines=customs_lines(job)
 for contract,products in contract_groups(job,customs=True):
  c=contract['values'];lines=customs_lines(job,products);cur=products[0]['values']['customs_currency'];suffix=text(c.get('customs_invoice_no'))
  subtitle=f"No. {suffix}     Date: {text(c.get('customs_invoice_date'))}\nTo: {text(c.get('customs_buyer'))}"
  for mode,title,headers,spans in [('invoice','报关发票 / INVOICE',['货物名称 / COMMODITY','数量 / QUANTITY','单价 / UNIT PRICE','总值 / AMOUNT'],[13,8,8,7]),('packing','报关箱单 / PACKING LIST',['货物名称 / COMMODITY','数量 / QUANTITY','净重 / N.W. (KG)','包装 / PACKAGES'],[14,9,7,6]),('sc','销货合约 / SALES CONTRACT',['货物名称 / COMMODITY','数量 / QUANTITY','单价 / UNIT PRICE','总值 / AMOUNT'],[13,8,8,7])]:
   f=Form({'invoice':'报关发票','packing':'报关箱单','sc':'销货合约'}[mode]+'_'+suffix,profile,landscape=True)
   for start in range(0,len(lines),3):
    f.page(title,subtitle);f.box(9,1,10,36,f"FROM: {s['loading_port']}    TO: {s['destination_port']}    {s['trade_term']} / {'AIR' if s['transport_mode']=='空运' else 'TRUCK'}\n"+('Contract: '+c['customs_contract_no'] if mode=='sc' else ''))
    rows=[]
    for line in lines[start:start+3]:
     name=line['name']+(' / 样品 SAMPLE' if line['sample'] else ' '+line['strength'])
     if mode=='packing':
      p=next(p['values'] for p in products if p['id']==line['product_id'])
      values=[name,line['quantity']+' '+line['unit'],format(line['net'],'f'),'随主货' if line['sample'] else text(p.get('customs_package_count'))+' '+s['customs_packaging']]
     else:values=[name,line['quantity']+' '+line['unit'],cur+' '+line['unit_price']+'/'+line['unit'],cur+' '+format(line['amount'],'.2f')]
     rows.append(values)
    f.table(11,spans,headers,rows,height=3,size=10)
    if start+3>=len(lines):
     total=quantity_totals(lines)
     if mode=='packing':
      net=sum(x['net'] for x in lines);gross=sum(Decimal(p['values']['gross_kg']) for p in products);pallets=sum(Decimal(p['values']['customs_package_count']) for p in products)
      volume=sum(Decimal(p['values']['volume_m3']) for p in products)
      f.box(23,1,25,36,f'TOTAL: {total}\nN.W.: {net} KG    G.W.: {gross} KG    {pallets} '+s['customs_packaging']+f'    VOL: {volume} CBM',border=True)
     else:f.box(23,1,25,36,'TOTAL: '+total+'     '+cur+' '+format(sum(x['amount'] for x in lines),'.2f'),border=True)
    f.box(26,1,27,36,'MARKS: '+s['customs_marks'],size=9)
   if mode=='sc':
    f.paragraphs('SALES CONTRACT / TERMS',[profile.get('seller_terms',''),'CONFIRMED BY BUYERS                         CONFIRMED BY SELLERS'],f"Contract: {c['customs_contract_no']}   Date: {c['customs_invoice_date']}")
   parts.append((mode,f.result()))
 # One shipment declaration: fees appear once even with multiple invoices.
 f=Form('出口报关单',profile,landscape=True)
 for start in range(0,len(all_lines),2):
  f.page('中华人民共和国海关出口货物报关单',company_header=False)
  f.box(3,1,3,36,'预录入编号：                         海关编号：                         核对稿',size=9)
  fixed=profile['company_cn']+'\n'+profile.get('registration','')
  buyers=' / '.join(dict.fromkeys(c['values']['customs_buyer'] for c,_ in contract_groups(job,customs=True)))
  contracts=' / '.join(c['values']['customs_contract_no'] for c,_ in contract_groups(job,customs=True))
  for r,label,value in [(4,'境内发货人',fixed),(6,'境外收货人',buyers),(8,'生产销售单位',fixed),(10,'合同协议号',contracts)]:f.box(r,1,r+1,11,label+'\n'+value,size=9,border=True)
  f.box(4,12,5,18,'出境关别\n'+s['departure_port'],size=9,border=True)
  f.box(4,19,5,27,'出口日期：',size=9,border=True);f.box(4,28,5,32,'申报日期：',size=9,border=True);f.box(4,33,5,36,'备案号：',size=9,border=True)
  f.box(6,12,7,18,'运输方式\n'+('AIR' if s['transport_mode']=='空运' else 'TRUCK'),size=9,border=True)
  f.box(6,19,7,27,'运输工具名称及航次号：',size=9,border=True);f.box(6,28,7,36,'提运单号：',size=9,border=True)
  f.box(8,12,9,18,'监管方式：',size=9,border=True);f.box(8,19,9,27,'征免性质：',size=9,border=True);f.box(8,28,9,36,'许可证号：',size=9,border=True)
  f.box(10,12,11,18,'贸易国（地区）\n'+s['trade_country'],size=9,border=True);f.box(10,19,11,27,'运抵国（地区）\n'+s['destination_country'],size=9,border=True)
  f.box(10,28,11,32,'指运港\n'+s['destination_port'],size=9,border=True);f.box(10,33,11,36,'离境口岸\n'+s['departure_port'],size=9,border=True)
  net=sum(x['net'] for x in all_lines);gross=sum(Decimal(p['values']['gross_kg']) for p in job['products']);pallets=sum(Decimal(p['values']['customs_package_count']) for p in job['products'])
  values=[('包装及件数',f"{s['customs_packaging']} / {pallets}"),('毛重 KG',str(gross)),('净重 KG',str(net)),('成交方式',s['customs_trade_term']),('运费 '+s['fees_currency'],s['customs_freight'] if start==0 else '见首页'),('保费 '+s['fees_currency'],s['customs_insurance'] if start==0 else '见首页'),('杂费 '+s['fees_currency'],s['customs_other_fee'] if start==0 else '见首页')]
  col=1
  for width,(label,value) in zip([6,5,5,5,5,5,5],values):f.box(12,col,13,col+width-1,label+'\n'+value,size=9,border=True);col+=width
  f.box(14,1,15,36,'标记唛码及备注：'+s['customs_marks']+'    实际成交方式：'+s['trade_term']+'\n'+s['customs_notes'],size=9,border=True)
  rows=[]
  for i,line in enumerate(all_lines[start:start+2],start+1):rows.append([str(i)+'\n'+line['hs_code'],line['name']+'\n'+line['strength'],line['quantity']+' '+line['unit'],line['unit_price']+'\n'+format(line['amount'],'.2f')+' '+line['currency'],line['origin']+'\n'+s['destination_country']+'\n'+line['source'],line['tax']])
  f.table(16,[5,10,5,7,6,3],['项号 / 商品编码','商品名称及规格型号','数量及单位','单价 / 总价 / 币制','原产国 / 目的国 / 货源地','征免'],rows,height=3,size=9)
  f.box(24,1,25,36,'特殊关系确认：              价格影响确认：              支付特许权使用费确认：              自报自缴：',size=9,border=True)
  f.box(26,1,28,27,'报关人员：              报关人员证号：              电话：\n申报单位（签章）：',size=9,border=True)
  f.box(26,28,28,36,'海关批注及签章：',size=9,border=True)
 parts.insert(2,('declaration',f.result()))
 paragraphs=[]
 for p in job['products']:
  v=p['values']
  if not absent(v['declaration_elements']):paragraphs.append(v['name_cn']+' / '+v['customs_hs_code']+'\n'+v['declaration_elements'])
  for row in job.get('customs_samples',{}).get(p['id'],[]):paragraphs.append('样品: '+row['name']+'\n用途及说明: '+row['purpose'])
 if paragraphs:
  f=Form('申报要素及样品说明',profile);f.paragraphs('申报要素 / DECLARATION DETAILS',paragraphs);parts.append(('elements',f.result()))
 order={'invoice':0,'packing':1,'declaration':2,'sc':3,'elements':4}
 return [p for k,p in sorted(parts,key=lambda x:order[x[0]])]


def co_form(job,profile,contract,products):
 s=job['shipment']['values'];c=contract['values'];f=Form('产地证草件_'+c['invoice_no'],profile)
 for p in products:
  v=p['values'];f.page('CERTIFICATE OF ORIGIN / DRAFT','FOR CHECKING ONLY - NOT AN ISSUED CERTIFICATE')
  f.box(9,1,14,18,'1. Exporter\n'+profile.get('co_exporter',profile['company_en']+'\n'+profile['address']),size=9,border=True)
  f.box(9,19,14,36,'Serial No.:\nCertificate No.:\n\nTo be completed by the issuing authority',size=9,border=True)
  f.box(15,1,21,18,'2. Consignee\n'+profile.get('co_consignee',profile['consignee']),size=9,border=True)
  f.box(15,19,18,36,'3. Means of transport and route\nFROM '+s['loading_port']+' TO '+s['destination_port']+' BY '+('AIR' if s['transport_mode']=='空运' else 'TRUCK'),size=9,border=True)
  f.box(19,19,21,36,'4. Country / region of destination\n'+text(s.get('destination_country')),size=9,border=True)
  f.table(22,[6,12,5,6,7],['6. Marks','7. Packages / description','8. HS Code','9. Quantity','10. Invoice / date'],[['AS PER INVOICE',v['pallets']+' PALLETS\n'+v['name_en']+'\n'+v['strength'],text(v.get('certificate_hs_code')),v['quantity']+' '+v['base_unit'],c['invoice_no']+'\n'+c['invoice_date']]],height=9,size=9)
  f.box(33,1,36,18,'11. Exporter declaration\nCountry of origin: '+text(v.get('origin_country'))+'\nPlace, date and signature:',size=9,border=True)
  f.box(33,19,36,36,'12. Certification\nFor certifying authority only\nSignature / stamp:',size=9,border=True)
  f.box(37,1,39,36,'DRAFT / 草件，仅用于核对；正式证书使用签发机构回件。',size=9)
 return f.result()


def road_form(job,profile,contract,products):
 s=job['shipment']['values'];c=contract['values'];f=Form('陆运运输单据草件_'+c['invoice_no'],profile)
 for p in products:
  v=p['values'];f.page('ROAD TRANSPORT INSTRUCTIONS / DRAFT','Invoice: '+c['invoice_no']+'    Date: '+c['invoice_date'])
  f.box(9,1,16,36,'CONSIGNEE\n'+profile['consignee'],size=10,border=True)
  f.box(17,1,19,36,'FROM: '+s['loading_port']+'    TO: '+s['destination_port']+'\nTERMS: '+s['trade_term']+' BY TRUCK',border=True)
  f.box(20,1,25,36,'DESCRIPTION OF GOODS\n'+v['name_en']+' / '+v['strength']+'\nHS CODE: '+v['hs_code']+'\nQUANTITY: '+v['quantity']+' '+v['base_unit'],border=True)
  f.box(26,1,29,36,f"PACKAGES: {v['pallets']} PALLETS / {v['cartons']} CTNS\nG.W.: {v['gross_kg']} KG    N.W.: {v['net_kg']} KG\nVOLUME: {v['volume_m3']} CBM",border=True)
  f.box(30,1,34,36,'TRANSPORT DOCUMENT NO.:\nCARRIER / TRUCK / DRIVER:\nTo be completed by the carrier.',border=True)
  f.box(36,1,39,36,'DRAFT / 草件，供货代核对，不代替承运人签发的运输单据。',size=9)
 return f.result()


def details_form(job,profile,contract,products):
 c=contract['values'];f=Form('商品及批次明细_'+c['invoice_no'],profile)
 for p in products:
  v=p['values'];paras=[v['name_en']+' / '+v['strength'],f"QUANTITY: {v['quantity']} {v['base_unit']}    UNIT PRICE: {v['customer_currency']} {v['customer_unit_price']}/{v['base_unit']}\nAMOUNT: {v['customer_currency']} {amount(v['quantity'],v['customer_unit_price'])}",f"PACKAGES: {v['cartons']} CTNS / {v['pallets']} PALLETS\nG.W.: {v['gross_kg']} KG   N.W.: {v['net_kg']} KG   VOLUME: {v['volume_m3']} CBM",'PACKAGE: '+v['packing_text'],'STORAGE: '+v['storage'],'MOC NO.: '+v['moc_no']+'    INSURANCE NO.: '+v['insurance_no'],'SAMPLES: '+v['samples_text']+'\nSAMPLE BATCHES: '+v.get('sample_batches','')]
  paras.append('PAYMENT: '+text(c.get('payment_terms')))
  for b in p.get('batches') or [{'values':v}]:
   bv=b['values'];paras.append('BATCH: '+text(bv.get('batch_no'))+'    MFG: '+text(bv.get('mfg_date'))+'    EXP: '+text(bv.get('exp_date'))+('\nQUANTITY: '+bv['quantity']+' '+v['base_unit'] if bv.get('quantity') else '')+('    CARTONS: '+bv['cartons'] if bv.get('cartons') else ''))
  f.paragraphs('ITEM & BATCH DETAILS',paras,'INVOICE: '+c['invoice_no']+'    DATE: '+c['invoice_date'])
 return f.result()


def original_customer_parts(job,profile,repo):
 from .profiles import original,customer_changes
 from .ooxml import patch_xlsx,audit_xlsx,members,sheet_paths
 from .document_ooxml import select_sheets,print_layout,append_batch_sheet,unsigned_customer
 from .layout import currency_format,assert_layout,expected_text
 from openpyxl import load_workbook
 parts=[]
 for contract,products in contract_groups(job):
  sub=deepcopy(job);sub['contracts']=[contract];sub['products']=products;p=deepcopy(products[0]);multi=len(products)>1
  long_fields={key for key,limit in [('name_en',85),('strength',25),('packing_text',65),('storage',50),('samples_text',150),('moc_no',30),('insurance_no',40),('sample_batches',65)] if len(p['values'].get(key,''))>limit}
  if len([x for x in p['values'].get('sample_batches','').split(';') if x.strip()])>3:long_fields.update({'samples_text','sample_batches'})
  for key in long_fields:p['values'][key]='' if key=='sample_batches' else 'SEE ITEM DETAILS'
  if multi:
   p['batches']=[];p['values'].update(name_en='SEE ITEM DETAILS',strength='',batch_no='SEE ITEM DETAILS',mfg_date='SEE ITEM DETAILS',exp_date='SEE ITEM DETAILS',packing_text='SEE ITEM DETAILS',storage='SEE ITEM DETAILS',samples_text='SEE ITEM DETAILS',sample_batches='',moc_no='SEE ITEM DETAILS',insurance_no='SEE ITEM DETAILS')
   for key in ('quantity','cartons','pallets','net_kg','gross_kg','volume_m3'):p['values'][key]=format(sum(Decimal(x['values'][key]) for x in products),'f')
   p['values']['customer_unit_price']='0';p['values']['base_unit']=''
  changes=customer_changes(sub,p,'customer');s=job['shipment']['values'];transport='AIR' if s['transport_mode']=='空运' else 'TRUCK'
  payment=text(contract['values'].get('payment_terms'))
  if len(payment)>70:long_fields.add('payment_terms');payment='SEE ITEM DETAILS'
  changes.update({'SHIPPING ADVICE!B27':payment,'发票!G36':payment})
  if not multi and len(p.get('batches',[]))>1:changes['箱单!B23']='BATCH NO.: SEE BATCH DETAILS'
  changes.update({'SHIPPING ADVICE!D18':s['loading_port'],'SHIPPING ADVICE!H18':s['destination_port'],'发票!C14':s['loading_port'],'发票!I14':s['destination_port'],'发票!K21':s['trade_term']+' BY '+transport,'发票!L21':s['destination_port'],'Page 1!D14':s['loading_port'],'Page 1!E17':s['destination_port'],'Page 1!E19':s['destination_port'],'Page 1!AB41':s['loading_port'],'Page 1!Y3':'DRAFT / 待承运人确认','Page 1!D22':p['values']['pallets'],'Page 1!N22':'','Page 1!E26':p['values']['name_en']})
  if multi:
   qty=quantity_totals([dict(quantity=x['values']['quantity'],unit=x['values']['base_unit']) for x in products]);currency=products[0]['values']['customer_currency'];total=sum(amount(x['values']['quantity'],x['values']['customer_unit_price']) for x in products)
   for k in ('SHIPPING ADVICE!A16','发票!H23','发票!H25','箱单!G15','箱单!G19'):changes[k]=qty if len(qty)<=20 else 'SEE DETAILS'
   changes.update({'发票!A23':'SEE ITEM & BATCH DETAILS','发票!I23':'SEE DETAILS','发票!L23':currency+' '+format(total,'.2f'),'发票!L25':currency+' '+format(total,'.2f'),'Page 1!AL22':'SEE ITEM DETAILS\nHS CODE: '+'; '.join(dict.fromkeys(x['values']['hs_code'] for x in products))+'\nVOL: '+p['values']['volume_m3']+' CBM'})
  data=unsigned_customer(original(repo,'customer'));base=currency_format(data,products[0]['values']['customer_currency']);rendered=patch_xlsx(base,changes)
  errors=audit_xlsx(base,rendered,changes);assert_layout(base,rendered,'xlsx')
  if errors:raise ValueError('客户原模板固定区检查失败：'+','.join(errors))
  for name,sheet in [('SHIPPING ADVICE','SHIPPING ADVICE'),('商业发票','发票'),('装箱单','箱单')]:
   output=print_layout(select_sheets(rendered,[sheet]));expected=expected_text(output,'xlsx',changes);pages=1
   if not multi and len(p.get('batches',[]))>1 and sheet in ('发票','箱单'):
    output=append_batch_sheet(output,sub,p);output=print_layout(output);expected=expected_text(output,'xlsx',changes);pages+=math.ceil(len(p['batches'])/12)
   parts.append(dict(name=name+'_'+contract['values']['invoice_no'],data=output,expected=expected,pages=pages))
  if multi or long_fields:parts.append(details_form(job,profile,contract,products))
  if s['transport_mode']=='空运':
   output=print_layout(select_sheets(rendered,['Page 1']));parts.append(dict(name='空运单据草件_'+contract['values']['invoice_no'],data=output,pages=1,expected=expected_text(output,'xlsx',changes)))
  else:parts.append(road_form(job,profile,contract,products))
  parts.append(co_form(job,profile,contract,products))
 return parts


def build_forms(job,kind,profile,repo=None):
 if kind=='customs_set':return customs_forms(job,profile)
 if repo is None:raise ValueError('客户资料需要已安装的原始工作簿')
 parts=original_customer_parts(job,profile,repo)
 if kind=='settlement':parts=[p for p in parts if not ('单据草件_' in p['name'] or p['name'].startswith('产地证草件_'))]
 return parts
