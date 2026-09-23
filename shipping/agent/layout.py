"""Deterministic layout contracts and local, rendered-page checks.
PDF checks use glyph ink boxes, not loose font ascent boxes. They do not OCR images.
"""
import hashlib,io,json,re,unicodedata
from lxml import etree as E
from .ooxml import members,S,W,sheet_paths

VERSION='fixed-layout-v1'


def fingerprint(data,extension):
 parts=members(data)
 if extension=='xlsx':
  keep={}
  for path in ['xl/styles.xml',*sheet_paths(parts).values()]:
   root=E.fromstring(parts[path])
   if path!='xl/styles.xml':
    for cell in root.findall('.//{'+S+'}c'):
     cell.attrib.pop('t',None)
     for child in list(cell):cell.remove(child)
   keep[path]=E.tostring(root,method='c14n').decode()
  wb=E.fromstring(parts['xl/workbook.xml'])
  keep['print']=E.tostring(wb.find('{'+S+'}definedNames'),method='c14n').decode() if wb.find('{'+S+'}definedNames') is not None else ''
 else:
  keep={k:hashlib.sha256(v).hexdigest() for k,v in parts.items() if k!='word/document.xml'}
  root=E.fromstring(parts['word/document.xml'])
  for node in root.findall('.//{'+W+'}t')+root.findall('.//{'+W+'}br'):node.getparent().remove(node)
  for run in root.findall('.//{'+W+'}r'):
   if len(run)==0:run.getparent().remove(run)
  keep['document']=E.tostring(root,method='c14n').decode()
 return hashlib.sha256(json.dumps(keep,sort_keys=True).encode()).hexdigest()


def assert_layout(before,after,extension):
 if fingerprint(before,extension)!=fingerprint(after,extension):raise ValueError('排版基线检查失败：字体、字号、对齐、行列尺寸或分页发生变化，未导出文件。')


def normalized(text):
 return ''.join(c for c in unicodedata.normalize('NFKC',str(text)) if not c.isspace() and c not in '\u200b\ufeff\u00ad')


def glyph_issues(chars,edges,width,height):
 errors=[]
 def add(char,issue):
  labels=char.get('labels',())
  errors.append((' / '.join(sorted(labels))+'：' if labels else '')+issue)
 for i,char in enumerate(chars):
  if not char.get('active',True):continue
  x0,y0,x1,y1=char['box']
  if x0<0 or y0<0 or x1>width+.5 or y1>height+.5:add(char,'文字超出页面')
  for ax,ay,bx,by in edges:
   # Require a line to cut into the visible ink by at least 0.65 pt.
   if abs(ay-by)<.15 and y0+.65<ay<y1-.65 and min(x1,bx)-max(x0,ax)>1.2:add(char,'文字压线');break
   if abs(ax-bx)<.15 and x0+.65<ax<x1-.65 and min(y1,by)-max(y0,ay)>1.2:add(char,'文字压线');break
  for oi,other in enumerate(chars):
   if oi==i:continue
   a,b,c,d=other['box']
   # Kerning/ligatures on one baseline are intentional, not collisions.
   if abs(y0-b)<1.2 and abs(y1-d)<1.2:continue
   ix=min(x1,c)-max(x0,a);iy=min(y1,d)-max(y0,b)
   if ix>1 and iy>1 and ix*iy>.3*min((x1-x0)*(y1-y0),(c-a)*(d-b)):
    add(char,'文字重叠');break
 return sorted(set(errors))


def check_pdf(data,expected,expected_pages):
 import pypdfium2 as pdfium
 import pdfplumber
 errors=[];pages=[]
 with pdfium.PdfDocument(data) as pdf, pdfplumber.open(io.BytesIO(data)) as vectors:
  if expected_pages is not None and len(pdf)!=expected_pages:errors.append(f'页数不符：应为 {expected_pages} 页，实际 {len(pdf)} 页')
  for index in range(len(pdf)):
   page=pdf[index];text=page.get_textpage();chars=[];flat='';mapping=[]
   for n in range(text.count_chars()):
    value=text.get_text_range(n,1)
    # PDFium represents a visible line-end discretionary hyphen as U+FFFE.
    # Keep the hyphen and its real ink box instead of treating it as missing text.
    if value=='\ufffe':value='-'
    if not normalized(value):continue
    x0,y0,x1,y1=text.get_charbox(n)
    char={'text':value,'box':(x0,page.get_height()-y1,x1,page.get_height()-y0),'active':False}
    ci=len(chars);chars.append(char);value=normalized(value);flat+=value;mapping.extend([ci]*len(value))
   # A PDF can retain clipped text in extraction; confirm each glyph has visible ink.
   bitmap=page.render(scale=2);picture=bitmap.to_pil().convert('RGB')
   for char in chars:
    x0,y0,x1,y1=char['box']
    crop=picture.crop((max(0,int(x0*2)),max(0,int(y0*2)),max(1,int(x1*2)+1),max(1,int(y1*2)+1)))
    char['invisible']=sum(min(rgb)<210 for rgb in crop.get_flattened_data())<2
    crop.close()
   picture.close();bitmap.close()
   edges=[]
   for e in vectors.pages[index].edges:
    if e.get('orientation')=='h' and e['x1']-e['x0']>=24:edges.append((e['x0'],e['top'],e['x1'],e['top']))
    elif e.get('orientation')=='v' and e['bottom']-e['top']>=24:edges.append((e['x0'],e['top'],e['x0'],e['bottom']))
   pages.append((flat,mapping,chars,edges,page.get_width(),page.get_height()))
   text.close();page.close()
  for label,value in expected.items():
   needle=normalized(value)
   if not needle:continue
   found=False
   for flat,mapping,chars,*_ in pages:
    start=flat.find(needle)
    if start>=0:
     found=True
     for ci in mapping[start:start+len(needle)]:chars[ci]['active']=True;chars[ci].setdefault('labels',set()).add(label)
   if not found:
    joined=''.join(p[0] for p in pages);start=joined.find(needle)
    if start>=0:
     found=True;offset=0
     for flat,mapping,chars,*_ in pages:
      for ci in mapping[max(0,start-offset):max(0,min(len(flat),start+len(needle)-offset))]:chars[ci]['active']=True;chars[ci].setdefault('labels',set()).add(label)
      offset+=len(flat)
   if not found:errors.append(label+'：未完整显示，内容可能超过固定区域，请检查长度或补充续页')
  for index,(_,_,chars,edges,width,height) in enumerate(pages,1):
   for issue in glyph_issues(chars,edges,width,height):errors.append(f'第 {index} 页 · '+issue)
   for char in chars:
    if char['active'] and char.get('invisible'):errors.append(f'第 {index} 页 · '+ ' / '.join(sorted(char.get('labels',())))+'：文字被裁切或不可见')
 if errors:raise ValueError('排版检查未通过：\n'+'\n'.join(dict.fromkeys(errors)))
 return {'version':VERSION,'status':'passed','pages':len(pages),'checked_fields':len(expected),'pdf_sha256':hashlib.sha256(data).hexdigest()}


def expected_text(data,extension,changes,paragraphs=None):
 from .document_ooxml import literal
 if extension=='xlsx':
  parts=members(data);paths=sheet_paths(parts);result={}
  for location,value in changes.items():
   sheet,cell=location.rsplit('!',1)
   if sheet in paths:result[location]=str(value.get('display',value.get('value','')) if isinstance(value,dict) else value or '')
  if '批次明细' in paths:
   for c in E.fromstring(parts[paths['批次明细']]).findall('.//{'+S+'}c'):
    result['批次明细!'+c.get('r')]=literal(parts,paths,'批次明细',c.get('r'))
  return result
 result={field_label(loc):value.get('value','') if isinstance(value,dict) else value for loc,value in changes.items()}
 for index,value in (paragraphs or {}).items():result['委托书第 '+str(index+1)+' 段']=value
 # Include fixed paragraphs so clipping cannot silently remove company/contact text.
 root=E.fromstring(members(data)['word/document.xml'])
 for index,p in enumerate(root.findall('.//{'+W+'}p')):
  text=''.join(n.text or '' for n in p.findall('.//{'+W+'}t'))
  if normalized(text) and set(text.strip())!={'-'}:result['固定文字第 '+str(index+1)+' 段']=text
 return result


def field_label(loc):
 table,row,col=map(int,loc.split(':'))
 if table==1:return f'报价 · 第 {row} 个产品 · '+['品名','数量及件数','客户单价','客户金额','报关单价','报关金额'][col]
 if table==2:return f'包装 · 第 {row} 个产品 · '+['品名','包装','箱托数','净重','每箱净重','每箱毛重'][col]
 return {'0:0:0':'托书日期','0:2:5':'合同号','0:9:3':'合同号（包装栏）','0:12:1':'入仓日期','0:15:3':'体积','0:16:3':'运费说明','0:17:3':'商品编码'}.get(loc,'表格 '+loc)


def booking_layout(data,count):
 """Reviewed two-page form: three fixed product slots; never grow with text."""
 from copy import deepcopy
 from .document_ooxml import fit_booking_tables,pack,xml
 from .profiles import expand_booking
 if count>3:raise ValueError('固定托书每份最多 3 个产品；本票超过固定位置容量，请分成多份托书。')
 parts=members(fit_booking_tables(expand_booking(data,3)));root=E.fromstring(parts['word/document.xml']);q=lambda tag:'{'+W+'}'+tag
 tables=root.findall('.//'+q('tbl'));outer=tables[0]
 for i,table in enumerate(tables):
  pr=table.find(q('tblPr'));layout=pr.find(q('tblLayout'))
  if layout is None:layout=E.SubElement(pr,q('tblLayout'))
  layout.set(q('type'),'fixed')
  for ri,row in enumerate(table.findall(q('tr'))):
   rp=row.find(q('trPr'))
   if rp is None:rp=E.Element(q('trPr'));row.insert(0,rp)
   ht=rp.find(q('trHeight'))
   if ht is None:ht=E.SubElement(rp,q('trHeight'))
   old=int(ht.get(q('val'),'360'))
   if i==1:height=1400
   elif i==2:height=1000 if ri==0 else 840
   elif ri==7:height=246 # nested price header + three fixed product rows + trailing paragraph
   elif ri==8:height=8300 # nested packing table + its caption and trailing paragraph
   else:height={0:1800,2:1440,3:2100,4:1000,5:1000,6:560}.get(ri,max(old,400) if ri>=9 else old)
   ht.set(q('val'),str(height));ht.set(q('hRule'),'exact')
   if rp.find(q('cantSplit')) is None:E.SubElement(rp,q('cantSplit'))
 # Normalize only repeated variable slots. All rows use the same fixed typography.
 for table in tables[1:]:
  for row in table.findall(q('tr'))[1:]:
   for cell in row.findall(q('tc')):
    cp=cell.find(q('tcPr'));va=cp.find(q('vAlign'))
    if va is None:va=E.SubElement(cp,q('vAlign'))
    va.set(q('val'),'center')
    for p in cell.findall(q('p')):
     pp=p.find(q('pPr'))
     if pp is None:pp=E.Element(q('pPr'));p.insert(0,pp)
     spacing=pp.find(q('spacing'))
     if spacing is None:spacing=E.SubElement(pp,q('spacing'))
     for key,value in {'before':'0','after':'0','line':'240','lineRule':'exact'}.items():spacing.set(q(key),value)
     jc=pp.find(q('jc'))
     if jc is None:jc=E.SubElement(pp,q('jc'))
     jc.set(q('val'),'left' if table is tables[1] and cell is row.findall(q('tc'))[0] else 'center')
     if not p.findall(q('r')):E.SubElement(p,q('r'))
     for run in p.findall(q('r')):
      rp=run.find(q('rPr'))
      if rp is None:rp=E.Element(q('rPr'));run.insert(0,rp)
      for tag in ('rFonts','sz','szCs','b','bCs','spacing','w'):
       for oldprop in list(rp.findall(q(tag))):rp.remove(oldprop)
      E.SubElement(rp,q('rFonts'),{q('ascii'):'Arial',q('hAnsi'):'Arial',q('eastAsia'):'Songti SC'})
      E.SubElement(rp,q('sz'),{q('val'):'18'});E.SubElement(rp,q('szCs'),{q('val'):'18'})
 # Write mappings use the original table order, so page splitting occurs after filling.
 parts['word/document.xml']=xml(root);return pack(parts)


def split_booking_pages(data):
 from copy import deepcopy
 from .document_ooxml import pack,xml
 parts=members(data);root=E.fromstring(parts['word/document.xml']);q=lambda tag:'{'+W+'}'+tag
 body=root.find(q('body'));table=body.find(q('tbl'));second=deepcopy(table)
 for row in list(table.findall(q('tr')))[9:]:table.remove(row)
 for row in list(second.findall(q('tr')))[:8]:second.remove(row)
 for target,keep,height in ((table,0,5840),(second,1,3880)):
  row=target.findall(q('tr'))[-1] if target is table else target.findall(q('tr'))[0]
  row.find(q('trPr')).find(q('trHeight')).set(q('val'),str(height))
  cell=row.find(q('tc'));nested=cell.findall(q('tbl'));remove=nested[1-keep];cell.remove(remove)
  # Both nested tables were in one cell. Keep its non-text spacer paragraphs.
 p=E.Element(q('p'));pr=E.SubElement(p,q('pPr'));E.SubElement(pr,q('spacing'),{q('before'):'0',q('after'):'0',q('line'):'20',q('lineRule'):'exact'})
 run=E.SubElement(p,q('r'));E.SubElement(run,q('br'),{q('type'):'page'})
 table.addnext(p);p.addnext(second)
 parts['word/document.xml']=xml(root);return pack(parts)


def layout_current(repo,output):
 """Old or modified exports never inherit a new version's delivery approval."""
 report=output.get('layout_check',{})
 if report.get('version')!=VERSION or report.get('status')!='passed':return False
 path=repo.safe_path(output['path']);pdf=path.with_suffix('.pdf')
 return (path.is_file() and pdf.is_file() and report.get('source_sha256')==output.get('sha256')==hashlib.sha256(path.read_bytes()).hexdigest()
         and report.get('pdf_sha256')==hashlib.sha256(pdf.read_bytes()).hexdigest())


def currency_format(data,currency):
 """The old total cell had EUR embedded in its style; currency is ticket data."""
 from copy import deepcopy
 from .document_ooxml import xml,pack
 if not re.fullmatch('[A-Z]{3}',currency):raise ValueError('客户币种未明确，无法设置发票合计格式')
 parts=members(data);paths=sheet_paths(parts);sheet=E.fromstring(parts[paths['发票']]);cell=sheet.find('.//{'+S+'}c[@r="L25"]')
 styles=E.fromstring(parts['xl/styles.xml']);xfs=styles.find('{'+S+'}cellXfs');formats=styles.find('{'+S+'}numFmts')
 if formats is None:formats=E.Element('{'+S+'}numFmts');styles.insert(0,formats)
 fid=max([163]+[int(n.get('numFmtId')) for n in formats])+1
 E.SubElement(formats,'{'+S+'}numFmt',numFmtId=str(fid),formatCode='"'+currency+' "#,##0.00');formats.set('count',str(len(formats)))
 xf=deepcopy(xfs[int(cell.get('s','0'))]);xf.set('numFmtId',str(fid));xf.set('applyNumberFormat','1');cell.set('s',str(len(xfs)));xfs.append(xf);xfs.set('count',str(len(xfs)))
 parts[paths['发票']]=xml(sheet);parts['xl/styles.xml']=xml(styles);return pack(parts)
