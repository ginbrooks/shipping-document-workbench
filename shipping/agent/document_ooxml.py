"""Explicit package transformations for single-document exports and continuations."""
import io,posixpath,re,zipfile
from copy import deepcopy
from lxml import etree as E
from .ooxml import S,W,R,members,sheet_paths,patch_xlsx,patch_docx
REL='http://schemas.openxmlformats.org/package/2006/relationships'
CT='http://schemas.openxmlformats.org/package/2006/content-types'


def xml(root):return E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
def pack(parts):
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
  for name,data in parts.items():
   if not name.endswith('/'):z.writestr(zipfile.ZipInfo(name,(2026,1,1,0,0,0)),data,compress_type=zipfile.ZIP_DEFLATED)
 return b.getvalue()


def literal(parts,paths,sheet,cell,seen=None):
 seen=set() if seen is None else seen
 if (sheet,cell) in seen:raise ValueError('模板存在循环引用')
 seen.add((sheet,cell));root=E.fromstring(parts[paths[sheet]])
 c=root.find('.//{'+S+'}c[@r="'+cell+'"]')
 if c is None:return ''
 formula=c.find('{'+S+'}f')
 if formula is not None:
  m=re.fullmatch(r"'?([^']+?)'?!(\$?[A-Z]+\$?\d+)",formula.text or '')
  if m:return literal(parts,paths,m[1],m[2].replace('$',''),seen)
  v=c.find('{'+S+'}v')
  if v is None or not v.text:raise ValueError('模板跨页公式没有可用值，请检查 '+sheet+'!'+cell)
  return v.text
 if c.get('t')=='s':
  strings=E.fromstring(parts['xl/sharedStrings.xml']);return ''.join(strings[int(c.find('{'+S+'}v').text)].itertext())
 if c.get('t')=='inlineStr':return ''.join(c.find('{'+S+'}is').itertext())
 v=c.find('{'+S+'}v');return v.text if v is not None else ''


def select_sheets(data,names):
 """Keep selected sheets, materialise references to removed sheets and prune orphan parts.
 Also remove obsolete shared strings, so old prices cannot remain inside the ZIP.
 """
 parts=members(data);paths=sheet_paths(parts)
 if not set(names)<=set(paths):raise ValueError('模板缺少工作表')
 changes={}
 for name in names:
  root=E.fromstring(parts[paths[name]])
  for c in root.findall('.//{'+S+'}c'):
   f=c.find('{'+S+'}f')
   if f is not None and '!' in (f.text or ''):
    m=re.fullmatch(r"'?([^']+?)'?!(\$?[A-Z]+\$?\d+)",f.text or '')
    if not m:raise ValueError('模板跨页公式尚未适配')
    if m[1] not in names:changes[name+'!'+c.get('r')]=literal(parts,paths,m[1],m[2].replace('$',''))
 if changes:parts=members(patch_xlsx(data,changes))
 wb=E.fromstring(parts['xl/workbook.xml']);sheets=wb.find('{'+S+'}sheets');all_names=list(paths);removed=[]
 for sheet in list(sheets):
  if sheet.get('name') not in names:removed.append(sheet.get('{'+R+'}id'));sheets.remove(sheet)
  else:sheet.attrib.pop('state',None)
 for view in wb.findall('.//{'+S+'}workbookView'):view.set('activeTab','0');view.set('firstSheet','0')
 for node in list(wb.findall('.//{'+S+'}definedName')):
  idx=node.get('localSheetId')
  if idx is not None:
   old=all_names[int(idx)]
   if old not in names:node.getparent().remove(node)
   else:node.set('localSheetId',str(names.index(old)))
 parts['xl/workbook.xml']=xml(wb)
 # Make each remaining string self-contained; old replaced values disappear.
 strings=E.fromstring(parts['xl/sharedStrings.xml']) if 'xl/sharedStrings.xml' in parts else None
 if strings is not None:
  for name in names:
   root=E.fromstring(parts[paths[name]])
   for c in root.findall('.//{'+S+'}c[@t="s"]'):
    v=c.find('{'+S+'}v');item=deepcopy(strings[int(v.text)]);item.tag='{'+S+'}is';c.remove(v);c.set('t','inlineStr');c.append(item)
   parts[paths[name]]=xml(root)
 rels=E.fromstring(parts['xl/_rels/workbook.xml.rels'])
 for r in list(rels):
  if r.get('Id') in removed or r.get('Type','').endswith(('/sharedStrings','/calcChain')):rels.remove(r)
 parts['xl/_rels/workbook.xml.rels']=xml(rels)
 # Follow package relationships; data belonging to removed sheets is not exported.
 reachable={'[Content_Types].xml'};queue=['']
 while queue:
  target=queue.pop()
  if target:reachable.add(target)
  relpath=(posixpath.dirname(target)+'/_rels/'+posixpath.basename(target)+'.rels').lstrip('/') if target else '_rels/.rels'
  if relpath not in parts:continue
  reachable.add(relpath)
  for r in E.fromstring(parts[relpath]):
   if r.get('TargetMode')=='External':continue
   ref=r.get('Target','');resolved=ref.lstrip('/') if ref.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(target),ref))
   if resolved in parts and resolved not in reachable:queue.append(resolved)
 types=E.fromstring(parts['[Content_Types].xml'])
 for t in list(types):
  if t.get('PartName') and t.get('PartName').lstrip('/') not in reachable:types.remove(t)
 parts['[Content_Types].xml']=xml(types)
 return pack({k:v for k,v in parts.items() if k in reachable})


def print_layout(data):
 """Only print settings change; no cell geometry or fonts change."""
 parts=members(data);paths=sheet_paths(parts);areas={'SHIPPING ADVICE':'$A$1:$L$34','发票':'$A$1:$N$37','箱单':'$B$1:$N$34','Page 1':'$A$1:$AV$43'}
 wb=E.fromstring(parts['xl/workbook.xml']);defs=wb.find('{'+S+'}definedNames')
 if defs is None:
  defs=E.Element('{'+S+'}definedNames');wb.insert(list(wb).index(wb.find('{'+S+'}sheets'))+1,defs)
 for i,(name,path) in enumerate(paths.items()):
  if name not in areas:continue
  # Retain an existing print area (e.g. the separate AWB template).
  old=next((d for d in defs if d.get('name')=='_xlnm.Print_Area' and d.get('localSheetId')==str(i)),None)
  if old is None:old=E.SubElement(defs,'{'+S+'}definedName',name='_xlnm.Print_Area',localSheetId=str(i));old.text="'"+name+"'!"+areas[name]
  root=E.fromstring(parts[path]);prop=root.find('{'+S+'}sheetPr')
  if prop is None:prop=E.Element('{'+S+'}sheetPr');root.insert(0,prop)
  setup=prop.find('{'+S+'}pageSetUpPr')
  if setup is None:setup=E.SubElement(prop,'{'+S+'}pageSetUpPr')
  setup.set('fitToPage','1');setup=root.find('{'+S+'}pageSetup')
  if setup is None:
   setup=E.Element('{'+S+'}pageSetup');margin=root.find('{'+S+'}pageMargins')
   if margin is not None:margin.addnext(setup)
   else:root.append(setup)
  setup.set('fitToWidth','1');setup.set('fitToHeight','1');setup.set('paperSize','9');setup.attrib.pop('scale',None)
  parts[path]=xml(root)
 parts['xl/workbook.xml']=xml(wb);return pack(parts)


def unsigned_customer(data):
 """The verified customer workbook's only picture is a blue company chop.
 Remove its anchors from copies; keep all cell text, fonts and geometry intact.
 select_sheets subsequently prunes the unreachable picture and drawing bytes.
 """
 parts=members(data)
 for path in sheet_paths(parts).values():
  root=E.fromstring(parts[path]);ids=set()
  for node in list(root.findall('{'+S+'}drawing')):
   ids.add(node.get('{'+R+'}id'));root.remove(node)
  if not ids:continue
  parts[path]=xml(root);relpath=posixpath.dirname(path)+'/_rels/'+posixpath.basename(path)+'.rels'
  if relpath in parts:
   rels=E.fromstring(parts[relpath])
   for node in list(rels):
    if node.get('Id') in ids:rels.remove(node)
   parts[relpath]=xml(rels)
 return pack(parts)


def append_batch_sheet(data,job,product):
 """Add a separately printable batch table without rewriting original worksheets."""
 parts=members(data);paths=sheet_paths(parts);wb=E.fromstring(parts['xl/workbook.xml']);rels=E.fromstring(parts['xl/_rels/workbook.xml.rels']);types=E.fromstring(parts['[Content_Types].xml'])
 styles=E.fromstring(parts['xl/styles.xml']);fonts=styles.find('{'+S+'}fonts');xfs=styles.find('{'+S+'}cellXfs');style_ids=[]
 for bold,size in [(False,11),(True,16),(True,11)]:
  fid=len(fonts);font=E.SubElement(fonts,'{'+S+'}font');E.SubElement(font,'{'+S+'}sz',val=str(size));E.SubElement(font,'{'+S+'}name',val='Arial')
  if bold:E.SubElement(font,'{'+S+'}b')
  style_ids.append(str(len(xfs)));xf=E.SubElement(xfs,'{'+S+'}xf',numFmtId='0',fontId=str(fid),fillId='0',borderId='0',xfId='0',applyFont='1',applyAlignment='1');E.SubElement(xf,'{'+S+'}alignment',vertical='center',wrapText='1')
 fonts.set('count',str(len(fonts)));xfs.set('count',str(len(xfs)));parts['xl/styles.xml']=xml(styles)
 root=E.Element('{'+S+'}worksheet',nsmap={None:S});E.SubElement(root,'{'+S+'}sheetPr');cols=E.SubElement(root,'{'+S+'}cols')
 for i,width in enumerate([24,22,22,20,18],1):E.SubElement(cols,'{'+S+'}col',min=str(i),max=str(i),width=str(width),customWidth='1')
 sd=E.SubElement(root,'{'+S+'}sheetData');contract=next(c for c in job['contracts'] if c['id']==product['contract_id'])
 rows=[['BATCH DETAILS'],['Invoice: '+str(contract['values'].get('invoice_no','待补'))],[product['values'].get('name_en') or product['values'].get('name_cn','待补')],['Batch No.','Mfg. date','Exp. date','Quantity ('+product['values'].get('base_unit','待补')+')','Cartons']]
 rows += [[b['values'].get(k,'待补' if k in ('batch_no','mfg_date','exp_date') else '') for k in ('batch_no','mfg_date','exp_date','quantity','cartons')] for b in product.get('batches',[])]
 for ri,values in enumerate(rows,1):
  row=E.SubElement(sd,'{'+S+'}row',r=str(ri),ht='32' if ri<=4 else '28',customHeight='1')
  for ci,value in enumerate(values):
   c=E.SubElement(row,'{'+S+'}c',r=chr(65+ci)+str(ri),t='inlineStr',s=style_ids[1 if ri==1 else 2 if ri==4 else 0]);is_=E.SubElement(c,'{'+S+'}is');E.SubElement(is_,'{'+S+'}t').text=str(value)
 merges=E.SubElement(root,'{'+S+'}mergeCells',count='3')
 for i in range(1,4):E.SubElement(merges,'{'+S+'}mergeCell',ref=f'A{i}:E{i}')
 E.SubElement(root,'{'+S+'}pageMargins',left='.4',right='.4',top='.6',bottom='.6',header='.2',footer='.2');E.SubElement(root,'{'+S+'}pageSetup',paperSize='9',orientation='landscape',fitToWidth='1',fitToHeight='0')
 E.SubElement(root.find('{'+S+'}sheetPr'),'{'+S+'}pageSetUpPr',fitToPage='1')
 breaks=E.SubElement(root,'{'+S+'}rowBreaks',count=str(max(0,(len(product.get('batches',[]))-1)//12)),manualBreakCount=str(max(0,(len(product.get('batches',[]))-1)//12)))
 for row in range(16,len(rows),12):E.SubElement(breaks,'{'+S+'}brk',id=str(row),min='0',max='16383',man='1')
 sid=max(int(s.get('sheetId')) for s in wb.find('{'+S+'}sheets'))+1;rid='rIdBatchDetails';path='xl/worksheets/batch-details.xml'
 E.SubElement(wb.find('{'+S+'}sheets'),'{'+S+'}sheet',name='批次明细',sheetId=str(sid),attrib={'{'+R+'}id':rid})
 E.SubElement(rels,'{'+REL+'}Relationship',Id=rid,Type=R+'/worksheet',Target='worksheets/batch-details.xml')
 E.SubElement(types,'{'+CT+'}Override',PartName='/'+path,ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
 defs=wb.find('{'+S+'}definedNames')
 if defs is None:defs=E.SubElement(wb,'{'+S+'}definedNames')
 E.SubElement(defs,'{'+S+'}definedName',name='_xlnm.Print_Titles',localSheetId=str(len(paths))).text="'批次明细'!$1:$4"
 parts.update({path:xml(root),'xl/workbook.xml':xml(wb),'xl/_rels/workbook.xml.rels':xml(rels),'[Content_Types].xml':xml(types)})
 return pack(parts)


def patch_pickup(data,changes,paragraphs):
 parts=members(patch_docx(data,changes));root=E.fromstring(parts['word/document.xml']);body=root.find('{'+W+'}body')
 for i,p in enumerate(body.findall('{'+W+'}p')):
  if i not in paragraphs:continue
  # Remove coloured placeholders and old shapes only inside the mapped input paragraphs.
  for child in list(p):
   if child.tag!='{'+W+'}pPr':p.remove(child)
  run=E.SubElement(p,'{'+W+'}r');E.SubElement(run,'{'+W+'}t').text=paragraphs[i]
 parts['word/document.xml']=xml(root);return pack(parts)


def audit_pickup(before,after,changes,paragraphs):
 from .ooxml import word_targets
 a,b=members(before),members(after);errors=[n for n in set(a)|set(b) if not n.endswith('/') and n!='word/document.xml' and a.get(n)!=b.get(n)]
 roots=[E.fromstring(p['word/document.xml']) for p in (a,b)]
 for root in roots:
  for cell in word_targets(root,changes).values():
   for child in list(cell):
    if child.tag!='{'+W+'}tcPr':cell.remove(child)
  for i,p in enumerate(root.find('{'+W+'}body').findall('{'+W+'}p')):
   if i in paragraphs:
    for child in list(p):
     if child.tag!='{'+W+'}pPr':p.remove(child)
 if E.tostring(roots[0],method='c14n')!=E.tostring(roots[1],method='c14n'):errors.append('委托书固定区')
 return errors


def unsigned_pickup(data):
 """This reviewed template contains one stamp image. Never reuse its signature."""
 parts=members(data);root=E.fromstring(parts['word/document.xml'])
 for node in root.findall('.//{'+W+'}drawing')+root.findall('.//{'+W+'}pict'):
  if node.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}blip') or node.findall('.//{urn:schemas-microsoft-com:vml}imagedata'):node.getparent().remove(node)
 rels=E.fromstring(parts['word/_rels/document.xml.rels'])
 for r in list(rels):
  if r.get('Type','').endswith('/image'):
   path=posixpath.normpath('word/'+r.get('Target'));parts.pop(path,None);rels.remove(r)
 types=E.fromstring(parts['[Content_Types].xml'])
 for t in list(types):
  if t.get('PartName','').startswith('/word/media/'):types.remove(t)
 parts.update({'word/document.xml':xml(root),'word/_rels/document.xml.rels':xml(rels),'[Content_Types].xml':xml(types)})
 return pack(parts)


def fit_booking_tables(data):
 """Print correction for the two audited booking templates: six useful columns.
 Remove only verified blank overflow cells; keep every text node and fixed label.
 """
 parts=members(data);root=E.fromstring(parts['word/document.xml']);tables=root.findall('.//{'+W+'}tbl')
 if len(tables)!=3:raise ValueError('托书表格结构已变化，未自动调整')
 q=lambda tag:'{'+W+'}'+tag
 for index,table in enumerate(tables):
  pr=table.find(q('tblPr'))
  for floating in pr.findall(q('tblpPr')):pr.remove(floating)
  widths=([4000,1320,1320,1400,1320,1380] if index==1 else [1350,1750,1350,1850,2200,2240]) if index else None
  if widths:
   grid=table.find(q('tblGrid'))
   for node in list(grid):grid.remove(node)
   for width in widths:E.SubElement(grid,q('gridCol'),{q('w'):str(width)})
   for row in table.findall(q('tr')):
    cells=row.findall(q('tc'))
    for extra in cells[6:]:
     if ''.join(extra.itertext()).strip() or extra.findall('.//'+q('drawing')):raise ValueError('托书额外列存在内容，未删除')
     row.remove(extra)
    for cell,width in zip(cells[:6],widths):
     cp=cell.find(q('tcPr'));cw=cp.find(q('tcW'))
     if cw is None:cw=E.SubElement(cp,q('tcW'))
     cw.set(q('w'),str(width));cw.set(q('type'),'dxa')
    rp=row.find(q('trPr'))
    if rp is None:rp=E.Element(q('trPr'));row.insert(0,rp)
    if rp.find(q('cantSplit')) is None:E.SubElement(rp,q('cantSplit'))
  tw=pr.find(q('tblW'))
  if tw is None:tw=E.SubElement(pr,q('tblW'))
  tw.set(q('w'),'10740');tw.set(q('type'),'dxa')
  indent=pr.find(q('tblInd'))
  if indent is not None:indent.set(q('w'),'0')
 parts['word/document.xml']=xml(root);return pack(parts)
