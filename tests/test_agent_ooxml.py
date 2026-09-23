import io,zipfile
import pytest
from shipping.agent.ooxml import patch_xlsx, audit_xlsx, patch_docx, audit_docx

def archive(files):
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w') as z:
  for k,v in files.items():z.writestr(k,v)
 return out.getvalue()

def test_xlsx_only_target_cells_change_and_media_stays():
 src=archive({'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="发票" sheetId="1" r:id="r1"/></sheets></workbook>','xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>','xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" s="7" t="inlineStr"><is><t>公司保持</t></is></c><c r="B1" s="3" t="inlineStr"><is><t>旧产品</t></is></c><c r="C1" s="4"><f>3*4</f><v>12</v></c></row></sheetData></worksheet>','xl/media/image.png':b'original-image','xl/styles.xml':b'original-styles'})
 changes={'发票!B1':'NEW <product>','发票!C1':{'formula':'2*5','value':'10'}}
 out=patch_xlsx(src,changes)
 assert audit_xlsx(src,out,changes)==[]
 with zipfile.ZipFile(io.BytesIO(out)) as z:
  assert z.read('xl/media/image.png')==b'original-image'
  assert b'NEW &lt;product&gt;' in z.read('xl/worksheets/sheet1.xml')
 bad=patch_xlsx(out,{'发票!A1':'changed'})
 assert audit_xlsx(src,bad,changes)
 with pytest.raises(ValueError):patch_xlsx(src,{'不存在!A1':'x'})

def test_word_preserves_runs_and_header_while_changing_text_only():
 src=archive({'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>固定公司</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>旧</w:t></w:r><w:r><w:t>产品</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>','word/styles.xml':b'fixedstyles'})
 out=patch_docx(src,{'0:0:1':'新产品'})
 assert audit_docx(src,out,{'0:0:1':'新产品'})==[]
 bad=patch_docx(out,{'0:0:0':'changed'})
 assert audit_docx(src,bad,{'0:0:1':'新产品'})
