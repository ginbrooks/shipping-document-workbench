from pathlib import Path
from streamlit.testing.v1 import AppTest
from shipping.demo import synthetic
from shipping.services import Workbench


def test_overview_never_claims_unmeasured_unreviewed_complete(tmp_path):
    from shipping.ui.design import overview_state
    w=Workbench(tmp_path);s=synthetic();w.save_shipment(s,0);p=w.calculate_plan(s['id'])
    state=overview_state(w,s,p)
    assert not state['steps'][1]['done'] and not state['steps'][2]['done']
    assert not state['steps'][3]['done'] and not state['steps'][4]['done']
    assert state['pending'] and state['document_count']==0


def test_overview_actions_navigate_to_real_forms(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path));w=Workbench(tmp_path);s=synthetic();w.save_shipment(s,0)
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=30).run()
    assert app.sidebar.radio[0].value=='自动出单' and not app.error
    app.sidebar.radio[0].set_value('工作概览').run()
    app.button(key='overview_primary').click().run()
    assert app.sidebar.radio[0].value=='打托方案' and not app.error
    app.sidebar.radio[0].set_value('工作概览').run()
    app.button(key='overview_documents').click().run()
    assert app.sidebar.radio[0].value=='单证生成' and any(b.label=='生成可编辑草稿' for b in app.button)
