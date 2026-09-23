from copy import deepcopy
from shipping.agent.review import blank_job,save_review
from shipping.agent.core import JobStore,resolve
from shipping.repository import Repository


def test_defaults_use_each_contract_freeze_date_and_never_change_other_flows():
 from shipping.agent.defaults import apply_customs_defaults
 j=blank_job('customs_set');c=j['contracts'][0];c['values']['contract_no']='C-ONE'
 c2=deepcopy(c);c2['id']='c2';c2['values']['contract_no']='C-TWO';j['contracts'].append(c2)
 apply_customs_defaults(j,today='2026-09-22')
 for c,no in zip(j['contracts'],['C-ONE','C-TWO']):
  assert c['values']['customs_invoice_no']==c['values']['customs_contract_no']==no
  assert c['values']['customs_invoice_date']=='2026-09-22'
 assert j['shipment']['values']['customs_marks']=='N/M'
 apply_customs_defaults(j,today='2026-09-23')
 assert j['contracts'][0]['values']['customs_invoice_date']=='2026-09-22'
 other=blank_job('shipping_draft');before=deepcopy(other);apply_customs_defaults(other)
 assert other==before


def test_defaults_preserve_existing_conflicting_manual_and_cleared_values():
 from shipping.agent.defaults import apply_customs_defaults
 j=blank_job('customs_set');c=j['contracts'][0];c['values'].update(contract_no='C1',customs_invoice_no='INV-EXPLICIT')
 c['conflicts']['customs_contract_no']=['A','B']
 j['shipment']['confirmed']={'customs_marks':None}
 apply_customs_defaults(j)
 assert c['values']['customs_invoice_no']=='INV-EXPLICIT'
 assert 'customs_contract_no' not in c['values']
 assert 'customs_marks' not in j['shipment']['values']
 c['conflicts']={'contract_no':['C1','C2']};apply_customs_defaults(j)
 assert 'customs_contract_no' not in c['values']


def test_save_defaults_and_manual_override_survive_reopen(tmp_path):
 from shipping.agent.defaults import apply_customs_defaults
 j=blank_job('customs_set');c=j['contracts'][0];cid=c['id'];c['values']['contract_no']='C1'
 apply_customs_defaults(j,today='2026-09-22')
 c['values']['contract_no']='C2';apply_customs_defaults(j)
 assert c['values']['customs_invoice_no']=='C2'
 store=JobStore(Repository(tmp_path))
 saved=save_review(store,j,{cid+'.customs_invoice_no':'MY-INV','shipment.customs_marks':'BOX-A'},'customs_set')
 again=store.get(saved['id']);apply_customs_defaults(again,today='2026-09-25')
 assert again['contracts'][0]['values']['customs_invoice_no']=='MY-INV'
 assert again['shipment']['values']['customs_marks']=='BOX-A'
 assert again['contracts'][0]['values']['customs_invoice_date']=='2026-09-22'


def test_display_evidence_deduplicates_without_mutating_audit():
 from shipping.agent.review import display_evidence
 e={'source':'file:page:1','quote':'产品\n25mg','value':'25mg'}
 evidence=[e,{**e,'method':'api'},{**e,'quote':'产品 25mg'},{**e,'source':'other:page:1'}]
 before=deepcopy(evidence)
 assert len(display_evidence(evidence))==2
 assert evidence==before


def test_defaults_reach_all_customs_forms():
 from test_business_packages import complete_job
 from shipping.agent.defaults import apply_customs_defaults
 from shipping.agent.business_forms import build_forms
 from openpyxl import load_workbook
 import io
 j=complete_job();j['targets']=['customs_set'];c=j['contracts'][0]
 c['values']['contract_no']='DEFAULT-QA-2026'
 for k in ('customs_invoice_no','customs_contract_no','customs_invoice_date'):c['values'].pop(k,None)
 j['shipment']['values'].pop('customs_marks',None)
 apply_customs_defaults(j,today='2026-09-22')
 parts=build_forms(j,'customs_set',dict(company_cn='测试公司',company_en='QA',address='TEST'))
 assert len(parts)==4
 for part in parts:
  w=load_workbook(io.BytesIO(part['data']))
  text='\n'.join(str(c.value) for s in w for row in s for c in row if c.value is not None)
  assert 'DEFAULT-QA-2026' in text and 'N/M' in text
  if '报关单' not in part['name']:assert '2026-09-22' in text


def test_existing_ticket_ui_autofills_and_deduplicates_source_popover(tmp_path,monkeypatch):
 from pathlib import Path
 from streamlit.testing.v1 import AppTest
 from shipping.agent import credentials
 from shipping.agent.defaults import datetime,ZoneInfo
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path));monkeypatch.setattr(credentials,'available',lambda:False)
 store=JobStore(Repository(tmp_path));j=blank_job('customs_set');j['id']='qa-defaults'
 c=j['contracts'][0];c['values']['contract_no']='SOURCE-C1'
 p=j['products'][0];p['values']['name_cn']='合成测试品';p['review_pending']=['name_cn']
 e=dict(source='file:page:1',quote='合成测试品25mg',value='合成测试品')
 p['evidence']['name_cn']=[e,deepcopy(e),{**e,'method':'api'}]
 j=store.save(j,0)
 app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run()
 assert not app.exception
 for label,value in [('报关发票号','SOURCE-C1'),('报关合同协议号','SOURCE-C1'),('报关发票日期',datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()),('报关唛头','N/M')]:
  assert next(x for x in app.text_input if x.label.startswith(label)).value==value
 assert all('合成测试品25mg' not in x.value for x in app.caption)
 # Provenance remains available in a popover and the complete audit table.
 assert any('查看依据 · 1 条'==x.proto.popover.label for x in app.get('popover'))
 app.button(key='review_save_'+j['id']).click().run()
 assert not app.error and not app.exception
 saved=store.get(j['id'])
 assert saved['contracts'][0]['values']['customs_invoice_no']=='SOURCE-C1'
 assert saved['shipment']['values']['customs_marks']=='N/M'
 assert len(saved['products'][0]['evidence']['name_cn'])==3
