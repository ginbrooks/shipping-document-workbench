"""Lossless formatting and evidence matching; no guessing OCR characters or dates."""
import re
from datetime import date
from decimal import Decimal

DATE_PATTERN=r'(?<!\d)(\d{4})\s*[-./年]\s*(\d{1,2})(?:\s*[-./月]\s*(\d{1,2}))?\s*日?(?!\d)'


MONTHS={m.lower():i+1 for i,m in enumerate(['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'])}
ENGLISH_DATE=r'(?i)\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[. ]*([0-9]{1,2})[ ,]+([0-9]{4})\b'

def normal_date(value,allow_month=False):
 english=re.fullmatch(ENGLISH_DATE,str(value).strip())
 if english:return date(int(english[3]),MONTHS[english[1].lower()],int(english[2])).isoformat()
 m=re.fullmatch(DATE_PATTERN,str(value).strip())
 if not m:raise ValueError('日期格式应为 YYYY-MM-DD，批次日期也可保留 YYYY-MM')
 y,mo,d=int(m[1]),int(m[2]),int(m[3]) if m[3] else None
 date(y,mo,d or 1)
 if d is None and not allow_month:raise ValueError('此日期需要具体到日')
 return f'{y:04d}-{mo:02d}'+(f'-{d:02d}' if d is not None else '')


def numeric_text(key,value):
 text=str(value).strip().replace(',','')
 if key.endswith('_kg'):
  m=re.fullmatch(r'([\d.]+)\s*(kg|千克|公斤|g|克)',text,re.I)
  if m:return format(Decimal(m[1])/(1000 if m[2].lower() in ('g','克') else 1),'f')
 if key.endswith('_mm'):
  m=re.fullmatch(r'([\d.]+)\s*(mm|毫米|cm|厘米|m|米)',text,re.I)
  if m:return format(Decimal(m[1])*{'mm':1,'毫米':1,'cm':10,'厘米':10,'m':1000,'米':1000}[m[2].lower()],'f')
 return text


def value_in_quote(key,value,quote):
 compact=lambda s:re.sub(r'\s+','',str(s)).replace(',','').replace('，','').casefold()
 if key=='transport_mode':
  words={'空运':r'空运|\bAIR\b','陆运':r'陆运|公路|\b(?:TRUCK|ROAD)\b'}
  return bool(value in words and re.search(words[value],quote,re.I))
 if key in {'contract_no','invoice_no','batch_no','report_no','hs_code','customs_hs_code','customs_invoice_no','customs_contract_no','certificate_no','transport_document_no'}:
  return bool(re.search(r'(?<![A-Za-z0-9])'+re.escape(str(value))+r'(?![A-Za-z0-9])',quote,re.I))
 if key=='report_copies' and value in ('1','2','3'):
  choices='|'.join({'1':('1','壹','一'),'2':('2','贰','二'),'3':('3','叁','三')}[value])
  return bool(re.search(r'(?<![\d一二三四五六七八九十百壹贰叁拾])(?:'+choices+r')\s*份',quote))
 if key.endswith('_date'):
  for m in list(re.finditer(DATE_PATTERN,quote))+list(re.finditer(ENGLISH_DATE,quote)):
   try:
    if normal_date(m[0],key in ('mfg_date','exp_date'))==value:return True
   except ValueError:pass
  return False
 if key.endswith('_mm'):
  m=re.search(r'([\d.]+)\s*[x×X*]\s*([\d.]+)\s*[x×X*]\s*([\d.]+)\s*(mm|毫米|cm|厘米|m|米)',quote)
  if m:
   axis={'carton_length_mm':1,'carton_width_mm':2,'carton_height_mm':3}.get(key)
   return bool(axis and Decimal(value)==Decimal(numeric_text(key,m[axis]+m[4])))
 if key.endswith('_kg'):
  matches=list(re.finditer(r'(?<![\d.])([\d.]+)\s*(kg|千克|公斤|g|克)(?![a-z])',quote,re.I))
  if matches:
   return any(Decimal(value)==Decimal(numeric_text(key,m[0])) for m in matches)
 # Decimal tokens must match as numbers, not as substrings of larger values.
 try:
  number=Decimal(value)
 except Exception:return compact(value) in compact(quote)
 for match in re.finditer(r'(?<![\d.])\d+(?:,\d{3})*(?:\.\d+)?(?![\d.])',quote):
  if Decimal(match[0].replace(',',''))==number:return True
 return False


def repair_source(source,quote,sources):
 compact=lambda s:re.sub(r'\s+','',str(s)).replace(',','').replace('，','').casefold()
 if source in sources and quote and compact(quote) in compact(sources[source]):return source
 if not isinstance(source,str) or ':' not in source or not quote:return source
 # Never borrow a matching value from another batch/document.
 prefix=source.split(':',1)[0]+':'
 matches=[s for s,text in sources.items() if s.startswith(prefix) and compact(quote) in compact(text)]
 return matches[0] if len(matches)==1 else source
