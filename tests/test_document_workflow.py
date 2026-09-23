from copy import deepcopy
from shipping.agent.core import JobStore, questions, resolve
from shipping.repository import Repository


def sample():
 def obj(oid, values, **kw):return dict(id=oid,values=values,evidence={},conflicts={},**kw)
 return dict(id='test',targets=['customer','booking'],revision=1,files=[],issues=[],audit=[],outputs=[],
 contracts=[obj('c1',{'contract_no':'C1','invoice_no':'I1','invoice_date':'2026-09-20'},kind='sales')],
 products=[obj('p1',{'name_cn':'测试片','name_en':'TEST TABLETS','strength':'25mg','quantity':'12000','base_unit':'TAB','pallets':'1','gross_kg':'15','volume_m3':'0.2','hs_code':'1234'},contract_id='c1',batches=[obj('b1',{'batch_no':'B1'})])],shipment=obj('shipment',{}))


def test_scoped_questions_do_not_leak_other_document_conflicts():
 j=sample();j['products'][0]['conflicts']={'customs_unit_price':['1','2'],'mfg_date':['2026-01','2026-02']};j['shipment']['conflicts']={'flight':['A1','A2']}
 assert {q['key'] for q in questions(j,['advice'])}=={'p1.moc_no','p1.insurance_no'}
 assert not any(q['key'].startswith('b1.') for q in questions(j,['booking']))
 assert not any('price' in q['key'] for q in questions(j,['awb']))


def test_fields_match_output_and_do_not_show_purchase_values():
 from shipping.agent.catalog import fields_for
 assert 'customer_unit_price' in fields_for('invoice','product')
 assert 'customs_unit_price' not in fields_for('invoice','product')
 assert 'purchase_unit_price' not in fields_for('invoice','product')
 assert not fields_for('invoice','shipment')
 assert fields_for('awb','shipment')=={'airwaybill_no','flight','flight_date','total_chargeable_kg'}
 assert not fields_for('booking','batch')


def test_hash_only_changes_for_dependencies_and_conflicts():
 from shipping.agent.catalog import input_hash
 j=sample();a=input_hash(j,'advice');i=input_hash(j,'invoice');j['shipment']['values']['flight']='A1'
 assert input_hash(j,'advice')==a
 j['products'][0]['values']['customer_unit_price']='0.1'
 assert input_hash(j,'advice')==a and input_hash(j,'invoice')!=i
 j['products'][0]['conflicts']['name_en']=['TEST TABLETS','OTHER']
 assert input_hash(j,'advice')!=a


def test_edit_keeps_export_history_and_can_clear_a_mistake():
 j=sample();j['outputs']=[{'path':'old.xlsx','type':'advice'}];out=resolve(j,{'p1.name_en':''},'清除误识别')
 assert 'name_en' not in out['products'][0]['values']
 assert out['outputs']==j['outputs']
 assert any(q['key']=='p1.name_en' for q in questions(out,['advice']))


def test_saved_revisions_are_recoverable(tmp_path):
 store=JobStore(Repository(tmp_path));j=store.create('A',['invoice']);old=deepcopy(j);j['name']='B';new=store.save(j,j['revision'])
 assert store.revision(j['id'],old['revision'])==old
 assert store.get(j['id'])==new


def test_document_checks_ignore_unrelated_prices_but_detect_batch_totals():
 from shipping.agent.catalog import document_checks
 j=sample();j['products'][0]['values'].update(net_kg='20',samples_text='样品')
 assert not document_checks(j,'advice')
 assert any('毛重' in x for x in document_checks(j,'packing'))
 j['products'][0]['batches'][0]['values']['quantity']='10000'
 assert any('批次' in x for x in document_checks(j,'packing'))


def test_new_manual_product_has_no_template_business_values(tmp_path):
 from shipping.agent.workflow import add_product
 store=JobStore(Repository(tmp_path));j=store.create('新票',['invoice']);updated=add_product(j,'合同A','新产品')
 assert updated['contracts'][0]['values']=={'contract_no':'合同A'}
 assert updated['products'][0]['values']=={'name_cn':'新产品'}
 assert not questions(j,['invoice'])
 assert questions(updated,['invoice'])


def test_english_invoice_dates_keep_real_source_evidence():
 from shipping.agent.core import checked
 from shipping.agent.cleaning import value_in_quote
 assert checked('invoice_date','Sep.4 2026')=='2026-09-04'
 assert value_in_quote('invoice_date','2026-09-04','Date: Sep.4 2026')
 assert not value_in_quote('invoice_date','2026-09-05','Date: Sep.4 2026')


def test_multi_product_reclean_matches_names_not_return_order():
 from shipping.agent.flow import merge_cleaned
 j=sample();j['products'][0]['values']={'name_cn':'甲片','name_en':'ALPHA','customer_unit_price':'1'};j['products'][0]['evidence']={'customer_unit_price':[{'source':'manual','quote':'确认','value':'1'}]}
 second=deepcopy(j['products'][0]);second['id']='p2';second['values']={'name_cn':'乙片','name_en':'BETA'};second['evidence']={};j['products'].append(second)
 raw=deepcopy(j);raw['products']=list(reversed(raw['products']));raw['products'][0]['id']='np1';raw['products'][1]['id']='np2';raw['products'][1]['values'].pop('customer_unit_price');raw['products'][1]['evidence']={}
 out=merge_cleaned(j,raw)
 alpha=next(p for p in out['products'] if p['values']['name_en']=='ALPHA')
 assert alpha['id']=='p1' and alpha['values']['customer_unit_price']=='1'


def test_report_copy_proof_does_not_match_partial_number():
 from shipping.agent.cleaning import value_in_quote
 assert value_in_quote('report_copies','1','壹份')
 assert value_in_quote('report_copies','1','1 份')
 assert not value_in_quote('report_copies','1','10份')
 assert not value_in_quote('report_copies','1','十一份')
 assert value_in_quote('report_no','123456','NO.123456')


def test_reclean_matches_exact_invoice_after_user_adds_missing_contract():
 from shipping.agent.flow import merge_cleaned
 j=sample();j['contracts'][0]['evidence']={'contract_no':[{'source':'manual','value':'C1','quote':'核实'}]}
 raw=deepcopy(j);raw['contracts'][0]['values'].pop('contract_no');raw['contracts'][0]['evidence']={}
 out=merge_cleaned(j,raw)
 assert out['contracts'][0]['values']['contract_no']=='C1'
 raw['contracts'][0]['values']['invoice_no']='UNKNOWN'
 import pytest
 with pytest.raises(ValueError):merge_cleaned(j,raw)
