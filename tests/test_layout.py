import io
from copy import deepcopy,copy
import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment
from shipping.agent.ooxml import patch_xlsx, members


def book():
 w=Workbook();s=w.active;s.title='发票';s['A1']='FIXED';s['A2']='old';s['A2'].font=Font(name='Arial',size=10);s['A2'].alignment=Alignment(wrap_text=True)
 s.column_dimensions['A'].width=12;s.row_dimensions[2].height=24
 b=io.BytesIO();w.save(b);return b.getvalue()


def test_layout_fingerprint_ignores_values_but_protects_target_style_and_geometry():
 from shipping.agent.layout import assert_layout
 base=book();out=patch_xlsx(base,{'发票!A2':'new'})
 assert_layout(base,out,'xlsx')
 w=load_workbook(io.BytesIO(out));w.active['A2'].font=Font(name='Arial',size=8);b=io.BytesIO();w.save(b)
 with pytest.raises(ValueError,match='排版'):assert_layout(base,b.getvalue(),'xlsx')


def test_layout_detects_cell_width_and_hidden_row_changes():
 from shipping.agent.layout import assert_layout
 base=book();w=load_workbook(io.BytesIO(base));w.active.row_dimensions[2].hidden=True;b=io.BytesIO();w.save(b)
 with pytest.raises(ValueError,match='排版'):assert_layout(base,b.getvalue(),'xlsx')


def test_review_text_has_fixed_font_and_long_evidence_continues_without_loss():
 from shipping.agent.review_export import workbook
 from test_document_workflow import sample
 j=sample();j['name']='核对测试';j['products'][0]['evidence']['name_cn']=[{'source':'file','quote':'原文依据'*600,'value':'测试片'}]
 w=load_workbook(io.BytesIO(workbook(j,'shipping_draft')));s=w.active
 assert all(c.font.sz==10 for row in s.iter_rows(min_row=5) for c in row if c.value)
 assert all(s.row_dimensions[r].height and s.row_dimensions[r].height<=120 for r in range(5,s.max_row+1))
 assert '原文依据'*600 in ''.join(str(s.cell(r,4).value or '').replace('\n','') for r in range(5,s.max_row+1))


def test_pdf_guard_rejects_missing_text_and_wrong_page_count():
 from shipping.agent.layout import check_pdf
 from pypdf import PdfWriter
 writer=PdfWriter();writer.add_blank_page(width=595,height=842);b=io.BytesIO();writer.write(b)
 with pytest.raises(ValueError,match='未完整显示'):check_pdf(b.getvalue(),{'发票 · 品名':'MISSING'},1)
 with pytest.raises(ValueError,match='页数'):check_pdf(b.getvalue(),{},2)


def test_pdf_guard_detects_border_crossing_and_text_collision():
 from shipping.agent.layout import glyph_issues
 chars=[dict(text='A',box=(10,10,20,20)),dict(text='B',box=(12,14,22,24))]
 assert any('压线' in x for x in glyph_issues(chars,[(0,15,30,15)],100,100))
 assert any('重叠' in x for x in glyph_issues(chars,[],100,100))
 assert not glyph_issues([chars[0]],[(0,9,30,9)],100,100)


def test_preview_cache_is_tied_to_file_hash(tmp_path):
 from shipping.agent.preview import preview_key
 p=tmp_path/'a.docx';p.write_bytes(b'first');a=preview_key(p);p.write_bytes(b'second')
 assert preview_key(p)!=a


def test_changed_pdf_or_old_rule_cannot_be_confirmed(tmp_path):
 from shipping.agent.layout import VERSION,layout_current
 from shipping.repository import Repository
 import hashlib
 r=Repository(tmp_path);p=r.root/'a.docx';p.write_bytes(b'word');p.with_suffix('.pdf').write_bytes(b'pdf')
 digest=lambda b:hashlib.sha256(b).hexdigest()
 o={'path':'a.docx','sha256':digest(b'word'),'layout_check':{'version':VERSION,'status':'passed','source_sha256':digest(b'word'),'pdf_sha256':digest(b'pdf')}}
 assert layout_current(r,o)
 p.with_suffix('.pdf').write_bytes(b'changed');assert not layout_current(r,o)
 o.pop('layout_check');assert not layout_current(r,o)


def test_three_fixed_slots_clear_unfilled_products_and_fonts_are_stable():
 from docx import Document
 from shipping.agent.layout import booking_layout
 # Capacity is rejected before opening or changing any document.
 with pytest.raises(ValueError,match='3 个产品'):booking_layout(b'',4)


def test_actual_renderer_detects_clipped_text(tmp_path):
 from shipping.agent.preview import converter,pdf_preview
 from shipping.agent.layout import check_pdf
 from shipping.repository import Repository
 if not converter():pytest.skip('本机未安装 LibreOffice，渲染集成测试未运行')
 w=Workbook();s=w.active;s['A1']='TEXT THAT CANNOT FIT IN THE FIXED BOX';s['A1'].alignment=Alignment(wrap_text=True)
 s['A1'].font=Font(name='Arial',size=12);s.column_dimensions['A'].width=10;s.row_dimensions[1].height=6
 r=Repository(tmp_path);p=r.root/'clipped.xlsx';w.save(p)
 pdf=pdf_preview(r,{'path':'clipped.xlsx'})
 with pytest.raises(ValueError,match='未完整显示|裁切'):check_pdf(pdf,{'产品说明':s['A1'].value},1)


def test_total_currency_format_tracks_ticket_without_changing_fonts():
 from shipping.agent.layout import currency_format
 from shipping.agent.ooxml import patch_xlsx
 w=Workbook();s=w.active;s.title='发票';s['L25']=0;s['L25'].number_format='"EUR "#,##0.00';s['L25'].font=Font(name='Arial',size=9)
 b=io.BytesIO();w.save(b)
 out=currency_format(b.getvalue(),'USD');sheet=load_workbook(io.BytesIO(out)).active
 assert sheet['L25'].number_format=='"USD "#,##0.00'
 assert copy(sheet['L25'].font)==copy(s['L25'].font)
