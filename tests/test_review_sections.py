from copy import deepcopy
from pathlib import Path
import pytest
from shipping.repository import Repository
from shipping.agent.core import JobStore
from shipping.agent.review import blank_job,review_items,DraftStore,save_review
from shipping.agent.business_flows import FLOW_KEYS
from test_agent_intake import Upload


def test_sections_cover_all_fields_once_in_all_modes_and_batch_scopes():
 from shipping.agent.review_sections import review_sections
 from shipping.agent.review import empty_object
 for kind in FLOW_KEYS:
  for mode in ('','空运','陆运'):
   j=blank_job(kind);j['shipment']['values']['transport_mode']=mode
   for batches in (False,True):
    if batches:j['products'][0]['batches']=[empty_object('b')]
    expected=review_items(j,kind);sections=review_sections(j,kind)
    rows=[r for s in sections for r in s['rows']]
    assert sorted(r['path'] for r in rows)==sorted(r['path'] for r in expected)
    assert len({r['path'] for r in rows})==len(rows)
    for s in sections:assert [r['rank'] for r in s['rows']]==sorted(r['rank'] for r in s['rows'])
    assert sum(s['missing'] for s in sections)==sum(r['status']=='missing' for r in expected)


def test_customs_shares_values_between_invoice_and_contract_without_duplicate_editing():
 from shipping.agent.review_sections import review_sections
 j=blank_job('customs_set');sections={s['id']:s for s in review_sections(j,'customs_set')}
 assert list(sections)==['shared','invoice','packing','declaration','samples']
 assert '发票' in sections['invoice']['title'] and '合约' in sections['invoice']['title']
 assert 'declaration_unit_price' in {r['key'] for r in sections['invoice']['rows']}
 assert 'gross_kg' in {r['key'] for r in sections['packing']['rows']}
 assert 'customs_hs_code' in {r['key'] for r in sections['declaration']['rows']}


def test_save_current_section_does_not_confirm_unseen_pending_fields(tmp_path):
 from shipping.agent.review_sections import review_sections
 store=JobStore(Repository(tmp_path));j=blank_job('customs_set');p=j['products'][0];pid=p['id']
 p['values']={'name_cn':'测试片','gross_kg':'10'};p['review_pending']=['name_cn','gross_kg']
 section=review_sections(j,'customs_set')[0]
 saved=save_review(store,j,{},'customs_set',confirm_paths={r['path'] for r in section['rows']})
 assert saved['products'][0]['review_pending']==['gross_kg']
 assert saved['products'][0]['confirmed']['name_cn']=='测试片'
 assert 'gross_kg' not in saved['products'][0].get('confirmed',{})


def test_upload_saves_locally_without_extraction_and_preserves_invalid_edit_draft(tmp_path,monkeypatch):
 from shipping.agent.review_sections import save_materials
 from shipping.agent import flow
 monkeypatch.setattr(flow,'prepare_sources',lambda *a,**k:pytest.fail('Upload must not read or recognise'))
 repo=Repository(tmp_path);j=blank_job('customs_set');pid=j['products'][0]['id'];drafts=DraftStore(repo)
 drafts.put(j,{pid+'.customs_quantity':'非数字',pid+'.name_cn':'先手填'})
 saved=save_materials(repo,j,[Upload('资料.pdf',b'%PDF-QA-local-only')],'customs_set')
 assert saved['id']!='new' and saved['files'][0]['name']=='资料.pdf'
 assert saved['products'][0]['values']=={}
 assert drafts.get(saved)['answers']=={pid+'.customs_quantity':'非数字',pid+'.name_cn':'先手填'}
 assert repo.cache_get('review-draft:new') is None
 assert repo.file_bytes(saved['files'][0]['id'])==b'%PDF-QA-local-only'
 assert save_materials(repo,saved,[Upload('重复.pdf',b'%PDF-QA-local-only')],'customs_set')==saved


def test_invalid_upload_does_not_create_empty_ticket(tmp_path):
 from shipping.agent.review_sections import save_materials
 repo=Repository(tmp_path)
 with pytest.raises(ValueError):save_materials(repo,blank_job('customs_set'),[Upload('bad.pdf',b'')],'customs_set')
 assert not JobStore(repo).list()


def test_review_page_contains_no_uploads_and_only_numbered_file_list(tmp_path,monkeypatch):
 from streamlit.testing.v1 import AppTest
 from shipping.agent import credentials
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path));monkeypatch.setattr(credentials,'available',lambda:False)
 store=JobStore(Repository(tmp_path));j=blank_job('settlement');j['files']=[store.repo.put_file('合同<一>.pdf',b'one')]
 j['attachments']=[store.repo.put_file('正式回件.pdf',b'two')];j=save_review(store,j,{},'settlement',confirm=False)
 app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run()
 assert app.radio(key='agent_step_'+j['id']).options==['资料','核对','文件']
 assert not app.get('file_uploader')
 assert not any(e.label in ('上传资料自动填写 / 本票资料','缺少什么，可以补什么资料') for e in app.expander)
 files=next(e for e in app.expander if e.label=='已上传资料 · 2 份');assert not files.proto.expanded
 html=''.join(m.value for m in files.markdown)
 assert '合同&lt;一&gt;.pdf' in html and '正式回件.pdf' in html and '序号' in html
 app.radio(key='review_group_'+j['id']+'_settlement').set_value('transport').run()
 assert not app.get('file_uploader')
 app.radio(key='agent_step_'+j['id']).set_value('资料').run()
 assert len(app.get('file_uploader'))==1
 assert not app.exception


def test_ui_group_switch_preserves_draft_and_saves_only_viewed_pending(tmp_path,monkeypatch):
 from streamlit.testing.v1 import AppTest
 from shipping.agent import credentials
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path));monkeypatch.setattr(credentials,'available',lambda:False)
 repo=Repository(tmp_path);store=JobStore(repo);j=blank_job('customs_set');p=j['products'][0];pid=p['id']
 p['values']={'name_cn':'待核对品名','gross_kg':'10'};p['review_pending']=['name_cn','gross_kg'];j=save_review(store,j,{},'customs_set',confirm=False)
 app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run()
 group='review_group_'+j['id']+'_customs_set'
 assert app.radio(key=group).value=='shared'
 assert not any(x.label.startswith('本次含托总毛重') for x in app.text_input)
 next(x for x in app.text_input if x.label.startswith('报关发票号')).set_value('NEW-1').run()
 app.radio(key=group).set_value('packing').run()
 app.radio(key=group).set_value('shared').run()
 assert next(x for x in app.text_input if x.label.startswith('报关发票号')).value=='NEW-1'
 app.button(key='review_save_'+j['id']).click().run()
 saved=store.get(j['id']);assert saved['products'][0]['review_pending']==['gross_kg']
 assert saved['contracts'][0]['values']['customs_invoice_no']=='NEW-1'
 assert not app.error and not app.exception
