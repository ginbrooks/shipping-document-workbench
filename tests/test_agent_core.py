import pytest
from shipping.agent.core import JobStore, normalise_result, questions, resolve, output_context
from shipping.repository import Repository


def result():
 return {'contracts':[{'id':'c1','kind':'purchase','fields':[{'key':'contract_no','value':'C001','source':'s1','quote':'采购合同 C001'}]}], 'products':[{'id':'p1','contract_id':'c1','fields':[{'key':'name_cn','value':'测试片','source':'s2','quote':'测试片 25mg 3000000片'},{'key':'ordered_quantity','value':'3000000','source':'s2','quote':'测试片 25mg 3000000片'}]}], 'shipment':[]}


def test_purchase_is_not_shipment_or_customer_price():
 r=result();r['products'][0]['fields'].append({'key':'customer_unit_price','value':'0.2','source':'s3','quote':'采购单价0.2'})
 j=normalise_result(r,{'s1':'采购合同 C001','s2':'测试片 25mg 3000000片','s3':'采购单价0.2'})
 assert j['products'][0]['values'].get('quantity') is None
 assert j['products'][0]['values'].get('customer_unit_price') is None
 assert any(q['key']=='p1.quantity' for q in questions(j,['customer']))


def test_unknown_sources_and_invented_values_are_not_accepted():
 r=result();r['products'][0]['fields'] += [{'key':'batch_no','value':'FAKE','source':'s2','quote':'测试片 25mg 3000000片'},{'key':'name_en','value':'TEST','source':'unknown','quote':'TEST'}]
 j=normalise_result(r,{'s1':'采购合同 C001','s2':'测试片 25mg 3000000片'})
 assert 'batch_no' not in j['products'][0]['values'] and 'name_en' not in j['products'][0]['values']
 assert len(j['issues'])==2


def test_conflict_questions_persist_until_explicit_resolution():
 r=result();r['products'][0]['fields'] += [{'key':'batch_no','value':'B1','source':'a','quote':'批号B1'},{'key':'batch_no','value':'B2','source':'b','quote':'批号B2'}]
 j=normalise_result(r,{'s1':'采购合同 C001','s2':'测试片 25mg 3000000片','a':'批号B1','b':'批号B2'})
 assert any(q['key']=='p1.batch_no' and q['reason']=='资料冲突' for q in questions(j,['customer']))
 j=resolve(j,{'p1.batch_no':'B2'},'依据工厂更正')
 assert j['products'][0]['values']['batch_no']=='B2'
 assert not any(q['key']=='p1.batch_no' for q in questions(j,['customer']))
 assert j['audit'][-1]['reason']=='依据工厂更正'


def test_persist_resume_and_revision_conflict(tmp_path):
 store=JobStore(Repository(tmp_path));j=store.create('票A',['customer'])
 saved=store.save(j,j['revision']);assert JobStore(Repository(tmp_path)).get(j['id'])['revision']==saved['revision']
 with pytest.raises(ValueError,match='VERSION'):store.save(j,j['revision'])


def test_bad_answer_rejected_and_calculations_are_decimal():
 j=normalise_result(result(),{'s1':'采购合同 C001','s2':'测试片 25mg 3000000片'})
 with pytest.raises(ValueError):resolve(j,{'p1.quantity':'-1'},'test')
 with pytest.raises(ValueError):resolve(j,{'p1.shipper':'other'},'test')
 j=resolve(j,{'p1.quantity':'3','p1.customer_unit_price':'0.1','p1.customer_currency':'EUR','p1.base_unit':'TAB'},'本票确认')
 c=output_context(j,j['products'][0]);assert c['amount']=='EUR 0.30' and c['amount_number']=='0.30'

def test_attachment_can_supply_shipment_quantity_and_derived_cartons_refresh():
 r=result();r['contracts'][0]['fields'][0]['source']='contract:page1'
 r['products'][0]['fields'] += [{'key':'quantity','value':'6000','source':'plan:page1','quote':'本票6000片'}]
 j=normalise_result(r,{'contract:page1':'采购合同 C001','s2':'测试片 25mg 3000000片','plan:page1':'本票6000片'})
 assert j['products'][0]['values']['quantity']=='6000'
 j=resolve(j,{'p1.units_per_inner':'25','p1.inners_per_carton':'240'},'包装确认')
 assert j['products'][0]['values']['cartons']=='1'
 j=resolve(j,{'p1.quantity':'12000'},'改单')
 assert j['products'][0]['values']['cartons']=='2'
