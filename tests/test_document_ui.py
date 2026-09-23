from pathlib import Path
from streamlit.testing.v1 import AppTest
from shipping.services import Workbench
from shipping.agent.core import JobStore
from test_document_workflow import sample


def start(tmp_path,monkeypatch):
 from shipping.agent import credentials
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path));monkeypatch.setattr(credentials,'available',lambda:False)
 w=Workbench(tmp_path);store=JobStore(w.repo);j=store.create('范围验证',['invoice']);body=sample()
 for k in ('contracts','products','shipment'):j[k]=body[k]
 j['products'][0]['values']['customs_unit_price']='12';j=store.save(j,j['revision'])
 return store,j,AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=20).run()


def test_document_switch_reuses_facts_and_does_not_call_model(tmp_path,monkeypatch):
 from shipping.ui import agent
 calls=[];monkeypatch.setattr(agent,'extract_and_clean',lambda *a,**k:calls.append(True))
 store,j,app=start(tmp_path,monkeypatch);before=store.get(j['id']);app.run();assert store.get(j['id'])==before
 app.radio(key='agent_step_'+j['id']).set_value('核对').run()
 app.radio(key='review_group_'+j['id']+'_shipping_draft').set_value('invoice').run()
 assert not app.exception
 assert any(x.label.startswith('客户单价') for x in app.text_input)
 assert not any(x.label.startswith('报关单价') or x.label.startswith('航班') for x in app.text_input)
 app.selectbox(key='agent_document_'+j['id']).select('customs_set').run()
 app.radio(key='review_group_'+j['id']+'_customs_set').set_value('invoice').run()
 assert not app.exception and not calls
 assert any(x.label.startswith('报关单价（每报关单位）') for x in app.text_input)
 assert not any(x.label.startswith('客户单价') or x.label.startswith('生产日期') for x in app.text_input)
 after=store.get(j['id']);assert after['products']==j['products'] and after['active_document']=='customs_set'


def test_manual_fallback_is_available_without_api(tmp_path,monkeypatch):
 from shipping.agent import credentials
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path));monkeypatch.setattr(credentials,'available',lambda:False)
 app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=20).run()
 next(x for x in app.text_input if x.label.startswith('产品中文名称')).set_value('测试片').run()
 next(x for x in app.text_input if x.label.startswith('报关合同协议号')).set_value('C0002').run()
 app.button(key='review_save_new').click().run();assert not app.exception and not app.error
 assert any(r.value=='核对' for r in app.radio)
 job=JobStore(Workbench(tmp_path).repo).list()[0]
 assert job['targets']==['customs_set'] and job['products'][0]['values']['name_cn']=='测试片'
 assert job['contracts'][0]['values']['customs_contract_no']=='C0002'
