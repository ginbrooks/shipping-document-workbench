"""Targeted OOXML edits. Preserve every non-target package member byte-for-byte.
No workbook import/export roundtrip: that can rewrite drawings, formulas or layout.
"""
import io,posixpath,re,zipfile
from html import escape
from lxml import etree
S='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'

def members(data):
 with zipfile.ZipFile(io.BytesIO(data)) as z:
  if z.testzip():raise ValueError('CORRUPT_TEMPLATE')
  return {n:z.read(n) for n in z.namelist()}

def package(data,updates):
 out=io.BytesIO()
 with zipfile.ZipFile(io.BytesIO(data)) as src,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as dst:
  for info in src.infolist():dst.writestr(info,updates.get(info.filename,src.read(info.filename)))
 return out.getvalue()

def sheet_paths(parts):
 wb=etree.fromstring(parts['xl/workbook.xml']);rels=etree.fromstring(parts['xl/_rels/workbook.xml.rels'])
 targets={r.get('Id'):r.get('Target') for r in rels}
 return {s.get('name'):posixpath.normpath('xl/'+targets[s.get('{'+R+'}id')]) if not targets[s.get('{'+R+'}id')].startswith('/') else targets[s.get('{'+R+'}id')].lstrip('/') for s in wb.findall('.//{'+S+'}sheet')}

def cell_pattern(cell):return rb'<c\b(?=[^>]*\br="'+cell.encode()+rb'")(?:[^>]*/>|[^>]*>.*?</c>)'

def patch_xlsx(data,changes):
 parts=members(data);paths=sheet_paths(parts);updates={}
 for location,value in changes.items():
  sheet,cell=location.rsplit('!',1)
  if sheet not in paths or not re.fullmatch('[A-Z]{1,3}[1-9][0-9]*',cell):raise ValueError('INVALID_TEMPLATE_CELL: '+location)
  path=paths[sheet];xml=updates.get(path,parts[path]);m=re.search(cell_pattern(cell),xml,re.S)
  if not m:raise ValueError('TEMPLATE_CELL_NOT_FOUND: '+location)
  old=m.group();opening=re.match(rb'<c\b[^>]*',old).group().rstrip(b'/')
  opening=re.sub(rb'\s+t="[^"]*"',b'',opening)
  # Only payload changes; cell style/reference and worksheet bytes outside cell are untouched.
  if isinstance(value,dict):
   formula=escape(str(value['formula']));cached=escape(str(value.get('value','')))
   replacement=opening+b'><f>'+formula.encode()+b'</f><v>'+cached.encode()+b'</v></c>'
  else:
   text='' if value is None else str(value)
   replacement=opening+b' t="inlineStr"><is><t xml:space="preserve">'+escape(text).encode()+b'</t></is></c>'
  xml=xml[:m.start()]+replacement+xml[m.end():];updates[path]=xml
 return package(data,updates)

def masked_xlsx(parts,changes):
 result=dict(parts);paths=sheet_paths(parts)
 for loc in changes:
  sheet,cell=loc.rsplit('!',1);path=paths[sheet]
  def hide(m):
   opening=re.match(rb'<c\b[^>]*',m.group()).group().rstrip(b'/');opening=re.sub(rb'\s+t="[^"]*"',b'',opening)
   return opening+b'>VARIABLE</c>'
  result[path]=re.sub(cell_pattern(cell),hide,result[path],flags=re.S)
 return result

def audit_xlsx(before,after,changes):
 a=masked_xlsx(members(before),changes);b=masked_xlsx(members(after),changes)
 return [n for n in set(a)|set(b) if a.get(n)!=b.get(n)]

def word_targets(root,changes):
 tables=root.findall('.//{'+W+'}tbl');result={}
 for location in changes:
  ti,ri,ci=map(int,location.split(':'))
  try:cell=tables[ti].findall('{'+W+'}tr')[ri].findall('{'+W+'}tc')[ci]
  except IndexError:raise ValueError('TEMPLATE_WORD_CELL_NOT_FOUND: '+location)
  result[location]=cell
 return result

def patch_docx(data,changes):
 parts=members(data);parser=etree.XMLParser(remove_blank_text=False);root=etree.fromstring(parts['word/document.xml'],parser)
 for location,cell in word_targets(root,changes).items():
  value=changes[location];nodes=cell.findall('.//{'+W+'}t')
  if isinstance(value,dict):
   # Replace an exact text fragment within a fixed paragraph, e.g. date in masthead.
   original=''.join(n.text or '' for n in nodes);find=value['find']
   if original.count(find)!=1:raise ValueError('TEMPLATE_TEXT_ANCHOR_MISMATCH')
   a=original.index(find);b=a+len(find);offset=0;inserted=False
   for n in nodes:
    text=n.text or '';end=offset+len(text)
    if end>a and offset<b:
     n.text=text[:max(0,a-offset)]+(str(value['value']) if not inserted else '')+text[max(0,b-offset):];inserted=True
    offset=end
  else:
   paragraphs=cell.findall('{'+W+'}p')
   if not paragraphs:raise ValueError('TEMPLATE_PARAGRAPH_MISSING')
   lines=('' if value is None else str(value)).split('\n')
   for i,p in enumerate(paragraphs):
    ns=p.findall('.//{'+W+'}t')
    if not ns:
     r=etree.SubElement(p,'{'+W+'}r');ns=[etree.SubElement(r,'{'+W+'}t')]
    chunks=lines[i:] if i==len(paragraphs)-1 else lines[i:i+1]
    ns[0].text=chunks[0] if chunks else '';ns[0].set('{http://www.w3.org/XML/1998/namespace}space','preserve')
    for n in ns[1:]:n.text=''
    anchor=ns[0]
    for line in chunks[1:]:
     br=etree.Element('{'+W+'}br');anchor.addnext(br)
     t=etree.Element('{'+W+'}t');t.text=line;br.addnext(t);anchor=t
 # No style, geometry, media, relationship or header parts are regenerated.
 return package(data,{'word/document.xml':etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)})

def audit_docx(before,after,changes):
 a=members(before);b=members(after);issues=[n for n in set(a)|set(b) if n!='word/document.xml' and a.get(n)!=b.get(n)]
 roots=[etree.fromstring(x['word/document.xml']) for x in (a,b)]
 for root in roots:
  for loc,cell in word_targets(root,changes).items():
   value=changes[loc]
   # Retain all existing properties; strip only text content and empty text runs in mapped cells.
   if isinstance(value,dict):
    text=''.join(n.text or '' for n in cell.findall('.//{'+W+'}t'))
    anchor=value['find'] if root is roots[0] else str(value['value'])
    if anchor not in text:issues.append('masthead:'+loc)
    else:text=text.replace(anchor,'VARIABLE',1)
    for n in cell.findall('.//{'+W+'}t'):n.text='';n.attrib.pop('{http://www.w3.org/XML/1998/namespace}space',None)
    cell.set('fixed-text-check',text)
   else:
    # Compare the complete original cell properties/paragraph/run properties independently.
    for n in cell.findall('.//{'+W+'}t')+cell.findall('.//{'+W+'}br'):n.getparent().remove(n)
    # An originally empty paragraph needs a text run; ignore only property-free added runs.
    for r in cell.findall('.//{'+W+'}r'):
     if len(r)==0:r.getparent().remove(r)
 if etree.tostring(roots[0],method='c14n')!=etree.tostring(roots[1],method='c14n'):issues.append('word/document.xml fixed structure')
 return issues
