from copy import deepcopy
import pytest
from shipping.agent.core import JobStore,questions,resolve
from shipping.repository import Repository
from shipping.agent.business_flows import FLOW_KEYS


def test_blank_forms_have_every_required_field_without_template_values():
 from shipping.agent.review import blank_job,review_items
 for kind in FLOW_KEYS:
  j=blank_job(kind);rows=review_items(j,kind)
  assert rows and not j['files'] and not any(x['value'] for x in rows)
  assert 'shipment.transport_mode' in {r['path'] for r in rows}
  assert any(r['status']=='missing' for r in rows)
  assert not any(r['key']=='purchase_unit_price' for r in rows)
  if kind=='consignment':assert not any(r['key']=='exp_date' for r in rows)
  if kind=='customs_set':assert any(r['key']=='declaration_unit_price' for r in rows)


def test_global_priority_optional_zero_and_material_suggestions():
 from shipping.agent.review import blank_job,review_items,material_gaps
 j=blank_job('customs_set');p=j['products'][0];c=j['contracts'][0]
 c['values']['customs_contract_no']='C1';p['values']['customs_quantity']='8';p['conflicts']['customs_quantity']=['8','9']
 j['shipment']['values']['customs_freight']='0'
 rows=review_items(j,'customs_set');ranks=[r['rank'] for r in rows]
 assert ranks==sorted(ranks)
 assert next(r for r in rows if r['key']=='customs_freight')['status']!='missing'
 assert next(r for r in rows if r['key']=='cartons')['status']=='optional'
 hints=material_gaps(j,'customs_set');assert len([x for x in hints if x['category']=='包装与实测'])==1
 assert any('毛重' in '、'.join(x['fields']) for x in hints)


def test_partial_save_and_invalid_input_never_pollute_facts(tmp_path):
 from shipping.agent.review import blank_job,save_review,DraftStore
 store=JobStore(Repository(tmp_path));j=blank_job('shipping_draft');pid=j['products'][0]['id']
 drafts=DraftStore(store.repo);drafts.put(j,{pid+'.name_cn':'测试片',pid+'.quantity':'oops'})
 with pytest.raises(ValueError):save_review(store,j,drafts.get(j)['answers'],'shipping_draft')
 assert not store.list() and drafts.get(j)['answers'][pid+'.quantity']=='oops'
 out=save_review(store,j,{pid+'.name_cn':'测试片'},'shipping_draft')
 assert out['id']!='new' and store.get(out['id'])['products'][0]['values']=={'name_cn':'测试片'}
 assert out['manual_files']==[] and questions(out,['shipping_draft'])


def test_transfer_is_snapshot_pending_and_protects_existing_values():
 from shipping.agent.review import blank_job,transfer_fields,transfer_options
 source=blank_job('shipping_draft');source['id']='source';source['revision']=4
 sp=source['products'][0];sp['values'].update(name_cn='甲片',quantity='100',base_unit='TAB',customer_unit_price='2',customer_currency='USD')
 target=blank_job('shipping_draft');tp=target['products'][0];tp['values']['name_cn']='乙片'
 options=transfer_options(source,sp['id'],'shipping_draft')
 assert next(x for x in options if x['key']=='name_cn')['default']
 assert not next(x for x in options if x['key']=='quantity')['default']
 with pytest.raises(ValueError):transfer_fields(target,source,sp['id'],tp['id'],['name_cn'],'shipping_draft')
 copied=transfer_fields(target,source,sp['id'],tp['id'],['quantity','base_unit'],'shipping_draft')
 p=copied['products'][0];assert p['values']['name_cn']=='乙片' and p['values']['quantity']=='100'
 assert any(q['key']==tp['id']+'.quantity' and q['reason']=='待核对' for q in questions(copied,['shipping_draft']))
 sp['values']['quantity']='999';assert p['values']['quantity']=='100'
 e=p['evidence']['quantity'][0];assert e['source_job']=='source' and e['source_revision']==4
 confirmed=resolve(copied,{tp['id']+'.quantity':'100'},'本票确认')
 assert 'quantity' not in confirmed['products'][0].get('review_pending',[])
 assert confirmed['products'][0]['evidence']['quantity'][0]['source_job']=='source'


def test_transfer_price_requires_its_unit_and_currency():
 from shipping.agent.review import blank_job,transfer_fields
 a=blank_job('consignment');b=blank_job('consignment');a['id']='old'
 p=a['products'][0];p['values'].update(customer_unit_price='2',customer_currency='USD',base_unit='TAB')
 with pytest.raises(ValueError):transfer_fields(b,a,p['id'],b['products'][0]['id'],['customer_unit_price'],'consignment')
 out=transfer_fields(b,a,p['id'],b['products'][0]['id'],['customer_unit_price','customer_currency','base_unit'],'consignment')
 assert out['products'][0]['values']['customer_unit_price']=='2'


def test_manual_first_extraction_matches_partial_contract_and_preserves_conflicts():
 from shipping.agent.review import blank_job,merge_reviewed
 j=blank_job('shipping_draft');p=j['products'][0];pid=p['id'];cid=p['contract_id']
 j=resolve(j,{pid+'.name_cn':'测试片',pid+'.quantity':'80'},'已核实')
 new=blank_job('shipping_draft');new['contracts'][0]['id']='source_c';new['contracts'][0]['kind']='sales';new['contracts'][0]['values']={'contract_no':'C1'}
 new['products'][0].update(id='source_p',contract_id='source_c',values={'name_cn':'测试片','quantity':'100','strength':'25mg'})
 out=merge_reviewed(j,new)
 assert len(out['products'])==1 and out['products'][0]['id']==pid and out['contracts'][0]['id']==cid
 assert out['products'][0]['values']['quantity']=='80'
 assert out['products'][0]['conflicts']['quantity']==['80','100']
 assert out['products'][0]['values']['strength']=='25mg'
 assert not out.get('unmatched')


def test_unmatched_product_is_queued_not_overwritten_and_can_be_resolved():
 from shipping.agent.review import blank_job,merge_reviewed,accept_candidate
 j=blank_job('shipping_draft');pid=j['products'][0]['id'];j=resolve(j,{pid+'.name_cn':'甲片'},'核实')
 raw=blank_job('shipping_draft');raw['products'][0]['values']={'name_cn':'乙片','quantity':'100'}
 out=merge_reviewed(j,raw)
 assert out['products'][0]['values']['name_cn']=='甲片' and len(out['products'])==1
 assert len(out['unmatched'])==1
 out=accept_candidate(out,out['unmatched'][0]['id'],j['contracts'][0]['id'],None)
 assert len(out['products'])==2 and not out['unmatched']
 assert len({p['id'] for p in out['products']})==2


def test_reversed_same_name_different_strength_never_cross_fills():
 from shipping.agent.review import blank_job,merge_reviewed
 j=blank_job('shipping_draft');p=j['products'][0];p['values']={'name_cn':'测试片','strength':'25mg','quantity':'1'}
 p['evidence']={'quantity':[{'source':'manual','value':'1'}]}
 p2=deepcopy(p);p2['id']='p2';p2['values'].update(strength='50mg',quantity='2');p2['evidence']['quantity'][0]['value']='2';j['products'].append(p2)
 raw=deepcopy(j);raw['products'].reverse()
 for p in raw['products']:p['id']='src_'+p['id'];p['values'].pop('quantity');p['evidence']={}
 out=merge_reviewed(j,raw)
 assert [(p['values']['strength'],p['values']['quantity']) for p in out['products']]==[('25mg','1'),('50mg','2')]


def test_pending_review_invalidates_outputs_without_modifying_original_bytes():
 from shipping.agent.catalog import input_hash
 from shipping.agent.review import blank_job
 j=blank_job('shipping_draft');j['products'][0]['values']['name_en']='TEST';before=input_hash(j,'shipping_draft');j['products'][0]['review_pending']=['name_en']
 assert input_hash(j,'shipping_draft')!=before


def test_ui_without_uploads_edits_saves_and_keeps_drafts_across_flows(tmp_path,monkeypatch):
 from streamlit.testing.v1 import AppTest
 from pathlib import Path
 from shipping.agent import credentials
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path));monkeypatch.setattr(credentials,'available',lambda:False)
 app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=30).run()
 store=JobStore(Repository(tmp_path))
 assert app.radio(key='agent_step_new').value=='核对' and not store.list()
 field=next(x for x in app.text_input if x.label.startswith('产品中文名称'))
 field.set_value('不上传也能填写').run()
 app.selectbox(key='agent_document_new').select('consignment').run()
 assert next(x for x in app.text_input if x.label.startswith('产品中文名称')).value=='不上传也能填写'
 assert not store.list()
 app.button(key='review_save_new').click().run();assert not app.exception and not app.error
 j=store.list()[0];assert j['products'][0]['values']['name_cn']=='不上传也能填写'
 fresh=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=30).run()
 assert next(x for x in fresh.text_input if x.label.startswith('产品中文名称')).value=='不上传也能填写'
 fresh.text_input(key='review_name_'+j['id']).set_value('更改后的票名').run()
 fresh.button(key='review_name_save_'+j['id']).click().run()
 assert not fresh.exception and not fresh.error
 assert store.get(j['id'])['name']=='更改后的票名'


def test_ui_draft_raw_validation_and_reopen(tmp_path,monkeypatch):
 from streamlit.testing.v1 import AppTest
 from pathlib import Path
 from shipping.agent import credentials
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path));monkeypatch.setattr(credentials,'available',lambda:False)
 path=str(Path(__file__).parents[1]/'app.py');app=AppTest.from_file(path,default_timeout=30).run()
 app.radio(key='review_group_new_customs_set').set_value('invoice').run()
 next(x for x in app.text_input if x.label.startswith('报关数量')).set_value('非数字').run()
 app.button(key='review_save_new').click().run()
 assert any('必须为数字' in x.value for x in app.error)
 assert not JobStore(Repository(tmp_path)).list()
 fresh=AppTest.from_file(path,default_timeout=30).run()
 fresh.radio(key='review_group_new_customs_set').set_value('invoice').run()
 assert next(x for x in fresh.text_input if x.label.startswith('报关数量')).value=='非数字'


def test_empty_form_adopts_multiple_recognised_contracts_without_false_matching():
 from shipping.agent.review import blank_job,merge_reviewed
 raw=blank_job('shipping_draft');raw['contracts'][0]['values']={'contract_no':'A'};raw['products'][0]['values']={'name_cn':'甲片'}
 c=deepcopy(raw['contracts'][0]);c['id']='c2';c['values']={'contract_no':'B'};raw['contracts'].append(c)
 p=deepcopy(raw['products'][0]);p['id']='p2';p['contract_id']='c2';p['values']={'name_cn':'乙片'};raw['products'].append(p)
 out=merge_reviewed(blank_job('shipping_draft'),raw)
 assert len(out['contracts'])==2 and len(out['products'])==2 and not out['unmatched']


def test_manual_first_batch_is_preserved_when_upload_adds_batches():
 from shipping.agent.review import blank_job,merge_reviewed
 j=blank_job('shipping_draft');pid=j['products'][0]['id']
 j=resolve(j,{pid+'.name_cn':'甲片',pid+'.batch_no':'B1',pid+'.mfg_date':'2026-01'},'核实')
 raw=deepcopy(j);p=raw['products'][0];p['values']={'name_cn':'甲片'};p['evidence']={};p['batches']=[dict(id='incoming',values={'batch_no':'B1','mfg_date':'2026-02'},evidence={},conflicts={})]
 out=merge_reviewed(j,raw);p=out['products'][0]
 assert len(p['batches'])==1 and p['batches'][0]['values']['mfg_date']=='2026-01'
 assert p['batches'][0]['conflicts']['mfg_date']==['2026-01','2026-02']
 assert 'mfg_date' not in p['values']


def test_confirmed_extraction_same_value_is_not_asked_again_after_supplement():
 from shipping.agent.review import blank_job,merge_reviewed
 j=blank_job('shipping_draft');p=j['products'][0];pid=p['id'];p['values']={'name_cn':'甲片'};p['evidence']={'name_cn':[{'source':'file:1','quote':'甲片','value':'甲片'}]};p['review_pending']=['name_cn']
 j=resolve(j,{pid+'.name_cn':'甲片'},'核对')
 out=merge_reviewed(j,deepcopy(j))
 assert 'name_cn' not in out['products'][0]['review_pending']


def test_generation_ignores_unused_blank_placeholders_but_not_partial_products():
 from shipping.agent.review import prune_empty,empty_object
 from test_business_packages import complete_job
 j=complete_job();p=empty_object('p',contract_id=j['contracts'][0]['id'],batches=[]);j['products'].append(p)
 clean=prune_empty(j)
 assert len(clean['products'])==1 and len(j['products'])==2
 j['products'][-1]['values']['quantity']='100'
 assert len(prune_empty(j)['products'])==2


def test_draft_external_change_needs_explicit_rebase(tmp_path):
 from shipping.agent.review import blank_job,save_review,DraftStore
 store=JobStore(Repository(tmp_path));j=blank_job('shipping_draft');pid=j['products'][0]['id']
 j=save_review(store,j,{pid+'.name_cn':'甲片'},'shipping_draft');drafts=DraftStore(store.repo)
 drafts.put(j,{pid+'.name_cn':'乙片'})
 latest=store.save(resolve(j,{pid+'.name_cn':'丙片'},'另一窗口'),j['revision'])
 with pytest.raises(ValueError,match='新版本'):save_review(store,latest,drafts.get(latest)['answers'],'shipping_draft')
 assert drafts.get(latest)['answers'][pid+'.name_cn']=='乙片'


def test_ui_history_transfer_differences_and_confirmation(tmp_path,monkeypatch):
 from pathlib import Path
 from streamlit.testing.v1 import AppTest
 from shipping.agent import credentials
 from shipping.agent.review import blank_job,save_review
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path));monkeypatch.setattr(credentials,'available',lambda:False)
 store=JobStore(Repository(tmp_path));a=blank_job('shipping_draft');ap=a['products'][0]['id']
 a=save_review(store,a,{ap+'.name_cn':'历史片',ap+'.strength':'25mg',ap+'.quantity':'100',ap+'.base_unit':'TAB'},'shipping_draft');source_before=deepcopy(a)
 b=blank_job('shipping_draft');bp=b['products'][0]['id'];b=save_review(store,b,{bp+'.name_cn':'目标片'},'shipping_draft')
 app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=30).run()
 app.selectbox(key='history_owner_'+b['id']+'_'+a['id']).select(ap).run()
 assert app.button(key='history_apply_'+b['id']).disabled
 app.checkbox(key='history_overwrite_'+b['id']).check().run()
 app.button(key='history_apply_'+b['id']).click().run()
 assert not app.error and not app.exception
 saved=store.get(b['id']);assert saved['products'][0]['values']['name_cn']=='历史片'
 assert 'quantity' not in saved['products'][0]['values']
 assert 'name_cn' in saved['products'][0]['review_pending']
 app.button(key='review_save_'+b['id']).click().run()
 assert not store.get(b['id'])['products'][0]['review_pending']
 assert store.get(a['id'])==source_before


def test_manual_attachment_approval_invalidates_after_new_upload(tmp_path):
 from shipping.agent.review import blank_job,save_review
 from shipping.agent.flow import attach_uploads
 from test_agent_intake import contract_upload,Upload
 store=JobStore(Repository(tmp_path));j=blank_job('shipping_draft');pid=j['products'][0]['id'];j=save_review(store,j,{pid+'.name_cn':'测试片'},'shipping_draft')
 j=attach_uploads(store.repo,j,[contract_upload()]);j['manual_files']=[f['id'] for f in j['files']];j=store.save(j,j['revision'])
 j=attach_uploads(store.repo,j,[Upload('新资料.pdf',b'new')])
 assert j['manual_files']!=sorted(f['id'] for f in j['files'])


def test_review_exports_pending_status_not_false_confirmation():
 from shipping.agent.review import blank_job
 from shipping.agent.business_flows import review_rows
 j=blank_job('consignment');p=j['products'][0];p['values']['name_cn']='测试片';p['review_pending']=['name_cn']
 rows=review_rows(j,'consignment');assert next(x for x in rows if x['字段']=='产品中文名称')['状态']=='待核对'


def test_template_registry_covers_every_public_field_and_conditional_branch():
 from shipping.agent.review import field_metadata,blank_job
 from shipping.agent.catalog import fields_for
 from shipping.agent.core import CONTRACT_FIELDS,PRODUCT_FIELDS,SHIPMENT_FIELDS,BATCH_FIELDS
 for kind in FLOW_KEYS:
  for mode in ['','空运','陆运']:
   j=blank_job(kind);j['shipment']['values']['transport_mode']=mode
   for owner,allowed in [('contract',CONTRACT_FIELDS),('product',PRODUCT_FIELDS),('shipment',SHIPMENT_FIELDS),('batch',BATCH_FIELDS)]:
    for key in fields_for(kind,owner,job=j):
     meta=field_metadata(kind,owner,key,j)
     assert key in allowed and meta['template_location'] and meta['suggested_material']
     assert meta['required']==(key in fields_for(kind,owner,optional=False,job=j))
