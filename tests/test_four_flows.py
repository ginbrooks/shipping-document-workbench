from copy import deepcopy
import pytest
from test_document_workflow import sample


def test_four_business_choices_and_legacy_entry_mapping():
 from shipping.agent.business_flows import FLOW_KEYS,flow_for
 from shipping.agent.catalog import NAMES
 assert [NAMES[k] for k in FLOW_KEYS]==['整套报关资料','出运草件','结汇资料','托书']
 assert flow_for('invoice')=='shipping_draft'
 assert flow_for('road_booking')=='consignment'


def test_customs_fields_use_their_own_invoice_price_and_unit():
 from shipping.agent.catalog import fields_for
 p=fields_for('customs_set','product')
 assert {'customs_quantity','customs_unit','declaration_unit_price','customs_hs_code','declaration_elements','samples_declaration'}<=p
 assert not {'customer_unit_price','moc_no','insurance_no','report_copies'}&p
 assert 'customs_invoice_no' in fields_for('customs_set','contract')
 assert 'invoice_no' not in fields_for('customs_set','contract')


def test_settlement_reuses_draft_and_only_adds_transport_and_certificate():
 from shipping.agent.business_flows import select_flow,settlement_changes
 from shipping.agent.core import questions,resolve
 from shipping.agent.catalog import fields_for
 j=sample();old=deepcopy(j)
 j=select_flow(j,'settlement')
 assert j['products']==old['products'] and j['files']==old['files']
 assert not settlement_changes(j)
 j=resolve(j,{'p1.gross_kg':'18'},'回件实测')
 assert any(x['field']=='gross_kg' and x['before']=='15' and x['after']=='18' for x in settlement_changes(j))
 assert {'transport_document_no','certificate_no','certificate_date'}<=fields_for('settlement','product')
 assert not any(q['key'].endswith(('flight','flight_date','transport_document_no','certificate_no')) for q in questions(j,['shipping_draft']))
 assert 'p1.certificate_no' in {q['key'] for q in questions(j,['settlement'])}


def test_road_settlement_does_not_ask_air_fields():
 from shipping.agent.catalog import fields_for
 from shipping.agent.core import questions
 j=sample();j['shipment']['values']['transport_mode']='陆运'
 assert not {'flight','flight_date','total_chargeable_kg'}&fields_for('settlement','shipment',job=j)
 assert not any(q['key'].endswith(('flight','flight_date','chargeable_kg')) for q in questions(j,['settlement']))
 j['shipment']['values']['transport_mode']='空运'
 assert 'flight' in fields_for('settlement','shipment',job=j)


def test_separate_customs_and_customer_hashes_and_computed_amount():
 from shipping.agent.catalog import input_hash
 from shipping.agent.business_flows import customs_amount
 j=sample();before=input_hash(j,'shipping_draft');j['products'][0]['values'].update(customs_quantity='14400',customs_unit='BOTTLE',declaration_unit_price='1.125',customs_currency='USD')
 assert input_hash(j,'shipping_draft')==before
 assert customs_amount(j['products'][0]['values'])=='16200.00'
 assert customs_amount({'quantity':'360000','declaration_unit_price':'1.125'}) is None


def test_public_ui_has_four_choices_and_customs_review_fields(tmp_path,monkeypatch):
 from streamlit.testing.v1 import AppTest
 from pathlib import Path
 from shipping.services import Workbench
 from shipping.agent.core import JobStore
 from shipping.agent import credentials
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path));monkeypatch.setattr(credentials,'available',lambda:False)
 store=JobStore(Workbench(tmp_path).repo);j=store.create('四类核对',['customs_set']);s=sample()
 for k in ('contracts','products','shipment'):j[k]=s[k]
 j=store.save(j,j['revision'])
 app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=20).run()
 assert app.selectbox(key='agent_document_'+j['id']).options==['整套报关资料','出运草件','结汇资料','托书']
 app.radio(key='agent_step_'+j['id']).set_value('核对').run()
 app.radio(key='review_group_'+j['id']+'_customs_set').set_value('invoice').run()
 labels=[x.label for x in app.text_input]+[x.label for x in app.text_area]
 assert any(x.startswith('报关单价（每报关单位）') for x in labels)
 assert not any(x.startswith('客户单价') for x in labels)
 app.selectbox(key='agent_document_'+j['id']).select('settlement').run()
 assert not app.exception
 assert store.get(j['id'])['products']==j['products']
 assert store.get(j['id']).get('draft_baseline')


def test_transport_reading_and_review_export_do_not_invent_prices():
 import io
 from openpyxl import load_workbook
 from shipping.agent.cleaning import value_in_quote
 from shipping.agent.core import checked
 from shipping.agent.review_export import workbook
 assert checked('transport_mode','AIR')=='空运'
 assert value_in_quote('transport_mode','陆运','TRUCK')
 assert not value_in_quote('transport_mode','陆运','AIR')
 j=sample();j['name']='核对表';j['products'][0]['values']['name_cn']='=1+1'
 w=load_workbook(io.BytesIO(workbook(j,'customs_set')))
 cells=[c for row in w.active for c in row]
 assert all(c.data_type!='f' for c in cells)
 assert any(c.value=='待补' for c in cells)
 assert not any(c.value=='客户单价（每基础单位）' for c in cells)
