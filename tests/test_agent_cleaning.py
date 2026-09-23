from copy import deepcopy
import pytest
from shipping.agent.core import normalise_result,resolve,questions,derive


def raw_product(fields=None,batches=None):
 return {'contracts':[{'id':'c1','kind':'purchase','fields':[{'key':'contract_no','value':'C001','source':'contract:1','quote':'合同 C001'}]}],
 'products':[{'id':'p1','contract_id':'c1','fields':fields or [],'batches':batches or []}],'shipment':[]}

def fact(key,value,quote,source='a:1'):
 return {'key':key,'value':value,'quote':quote,'source':source}


def test_date_normalisation_preserves_month_precision_and_rejects_invented_day():
 fields=[fact('mfg_date','2026-08-07','生产日期 2026.08.07'),fact('exp_date','2029-07','有效期至 2029.07')]
 sources={'contract:1':'合同 C001','a:1':'生产日期 2026.08.07\n有效期至 2029.07'}
 j=normalise_result(raw_product(fields),sources)
 assert j['products'][0]['values']['mfg_date']=='2026-08-07'
 assert j['products'][0]['values']['exp_date']=='2029-07'
 bad=normalise_result(raw_product([fact('exp_date','2029-07-31','有效期至 2029.07')]),sources)
 assert 'exp_date' not in bad['products'][0]['values']
 with pytest.raises(ValueError):resolve(j,{'p1.mfg_date':'2026-02-30'},'manual')


def test_source_repair_stays_in_file_and_sizes_are_not_shipment_volume():
 fields=[fact('batch_no','B001','批号 B001','a:wrong'),fact('carton_length_mm','390','390×270×220 mm','a:2'),fact('carton_width_mm','270','390×270×220 mm','a:2'),fact('carton_height_mm','220','390×270×220 mm','a:2'),fact('carton_gross_kg','4.7','毛重 4700 g','a:2')]
 sources={'contract:1':'合同 C001','a:1':'批号 B001','a:2':'390×270×220 mm\n毛重 4700 g','b:1':'不存在的依据'}
 j=derive(normalise_result(raw_product(fields),sources));p=j['products'][0]
 assert p['values']['batch_no']=='B001' and p['evidence']['batch_no'][0]['source']=='a:1'
 assert p['values']['carton_volume_m3']=='0.023166'
 assert p['values']['carton_gross_kg']=='4.7' and 'volume_m3' not in p['values']
 rejected=normalise_result(raw_product([fact('name_cn','不存在','不存在的依据','a:1')]),sources)
 assert not rejected['products'][0]['values']


def test_batches_remain_separate_and_prefilled_values_not_missing():
 from shipping.agent.core import BATCH_FIELDS
 batches=[{'id':f'b{i}','fields':[fact('batch_no',f'B00{i}',f'批号 B00{i}',f'f{i}:1'),fact('mfg_date',f'2026-08-0{i}',f'2026.08.0{i}',f'f{i}:1'),fact('exp_date','2029-07','2029.07',f'f{i}:1')]} for i in range(1,5)]
 sources={'contract:1':'合同 C001',**{f'f{i}:1':f'批号 B00{i}\n2026.08.0{i}\n2029.07' for i in range(1,5)}}
 j=normalise_result(raw_product(batches=batches),sources);p=j['products'][0]
 assert len(p['batches'])==4 and 'mfg_date' not in p['values']
 assert not any(q['key'].endswith(('.batch_no','.mfg_date','.exp_date')) for q in questions(j,['customer']))
 j=resolve(j,{'b2.mfg_date':'2026-08-03'},'核实第二批')
 assert j['products'][0]['batches'][0]['values']['mfg_date']=='2026-08-01'
 assert j['products'][0]['batches'][1]['values']['mfg_date']=='2026-08-03'
 with pytest.raises(ValueError):resolve(j,{'b2.customer_currency':'USD'},'invalid owner')


def test_reclean_replaces_machine_conflicts_but_preserves_manual_answer(tmp_path):
 from shipping.agent.core import JobStore
 from shipping.agent.flow import apply_extraction
 from shipping.repository import Repository
 store=JobStore(Repository(tmp_path));job=store.create('clean',['customer'])
 sources={'contract:1':'合同 C001','a:1':'品名 测试片\n25片/瓶\n3000000片\n120000瓶\n英文 TEST'}
 first=raw_product([fact('name_cn','测试片','品名 测试片'),fact('base_unit','片','25片/瓶'),fact('ordered_quantity','3000000','3000000片')])
 job=apply_extraction(job,first,sources,'DeepSeek')
 job=resolve(job,{'p1.name_en':'USER VALUE'},'人工确定英文名')
 job['products'][0]['values'].pop('base_unit');job['products'][0]['conflicts']['base_unit']=['片','瓶']
 new=deepcopy(first);new['products'][0]['fields'].append(fact('name_en','TEST','英文 TEST'))
 cleaned=apply_extraction(job,new,sources,'DeepSeek 清洗',replace_machine=True)
 assert cleaned['products'][0]['values']['base_unit']=='片' and 'base_unit' not in cleaned['products'][0]['conflicts']
 assert cleaned['products'][0]['values']['name_en']=='USER VALUE'
 assert cleaned['outputs']==[]

def test_reclean_keeps_unassigned_product_level_manual_dates(tmp_path):
 from shipping.agent.core import JobStore
 from shipping.agent.flow import apply_extraction
 from shipping.repository import Repository
 job=JobStore(Repository(tmp_path)).create('manual dates',['customer'])
 sources={'contract:1':'合同 C001','a:1':'产品 测试片','b:1':'批号 B001\n2026.08.07'}
 raw=raw_product([fact('name_cn','测试片','产品 测试片')])
 job=apply_extraction(job,raw,sources,'local')
 job=resolve(job,{'p1.mfg_date':'2026-08-08'},'人工更正')
 raw['products'][0]['batches']=[{'id':'b1','fields':[fact('batch_no','B001','批号 B001','b:1'),fact('mfg_date','2026-08-07','2026.08.07','b:1')]}]
 new=apply_extraction(job,raw,sources,'DeepSeek',replace_machine=True)
 assert new['products'][0]['values']['mfg_date']=='2026-08-08'
 assert any('原产品级人工值' in issue['message'] for issue in new['issues'])


@pytest.mark.parametrize('legacy_null_id',[False,True])
def test_reclean_booking_keeps_shipment_questions_and_manual_values(tmp_path,legacy_null_id):
 from shipping.agent.core import JobStore
 from shipping.agent.flow import apply_extraction
 from shipping.repository import Repository
 job=JobStore(Repository(tmp_path)).create('booking re-clean',['customer','booking'])
 sources={'contract:1':'合同 C001','a:1':'产品 测试片'}
 raw=raw_product([fact('name_cn','测试片','产品 测试片')])
 job=apply_extraction(job,raw,sources,'local')
 job=resolve(job,{'shipment.freight_note':'人工确认运费'},'人工确认')
 if legacy_null_id:job['shipment']['id']=None
 before=deepcopy(job)
 new=apply_extraction(job,raw,sources,'DeepSeek（缓存）',replace_machine=True)
 assert job==before
 assert 'id' not in new['shipment']
 assert new['shipment']['values']['freight_note']=='人工确认运费'
 assert {q['key'] for q in questions(new,new['targets'])}>={'shipment.shipping_date','shipment.warehouse_date'}
 assert new['phase']=='needs_input'


@pytest.mark.parametrize('owner',['contract','product','batch'])
def test_model_null_ids_are_rejected_before_normalisation(owner):
 raw=raw_product(batches=[{'id':'b1','fields':[]}])
 obj={'contract':raw['contracts'][0],'product':raw['products'][0],'batch':raw['products'][0]['batches'][0]}[owner]
 obj['id']=None
 with pytest.raises(ValueError):normalise_result(raw,{'contract:1':'合同 C001'})


def test_questions_accept_legacy_shipment_null_id():
 job=normalise_result(raw_product(),{'contract:1':'合同 C001'})
 job['shipment']['id']=None
 assert 'shipment.shipping_date' in {q['key'] for q in questions(job,['booking'])}


def test_supplement_disagreement_keeps_manual_value_and_requires_review(tmp_path):
 from shipping.agent.core import JobStore
 from shipping.agent.flow import apply_extraction
 from shipping.repository import Repository
 job=JobStore(Repository(tmp_path)).create('supplement',['customer'])
 sources={'contract:1':'合同 C001','a:1':'英文 FIRST'}
 raw=raw_product([fact('name_en','FIRST','英文 FIRST')])
 job=apply_extraction(job,raw,sources,'test')
 job=resolve(job,{'p1.name_en':'USER CONFIRMED'},'人工核对原件')
 unchanged=apply_extraction(job,raw,sources,'test',replace_machine=True)
 assert 'name_en' not in unchanged['products'][0]['conflicts']
 sources['b:1']='英文 SECOND'
 raw['products'][0]['fields']=[fact('name_en','SECOND','英文 SECOND','b:1')]
 new=apply_extraction(job,raw,sources,'test',replace_machine=True)
 p=new['products'][0]
 assert p['values']['name_en']=='USER CONFIRMED'
 assert set(p['conflicts']['name_en'])=={'USER CONFIRMED','SECOND'}
 assert any(q['key']=='p1.name_en' for q in questions(new,['customer']))
 confirmed=resolve(new,{'p1.name_en':'USER CONFIRMED'},'核对后保留人工值')
 assert 'name_en' not in confirmed['products'][0]['conflicts']
