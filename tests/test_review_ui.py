"""Real Streamlit controls, blank shipment and explicit confirmation; no AI calls."""
from pathlib import Path
from streamlit.testing.v1 import AppTest
from shipping.services import Workbench
from shipping.models import leaves
from shipping.validation import get_path


def button(app,label):
    next(b for b in app.button if b.label==label).click().run()
    assert not app.exception and not app.error, [e.value for e in app.error]


def put(app,suffix,value):
    pool=list(app.text_input)+list(app.number_input)
    element=next(e for e in pool if str(e.key).endswith(':'+suffix) or str(e.key).endswith(suffix))
    element.set_value(value)


def choose(app,label,value):
    next(e for e in app.selectbox if e.label==label).set_value(value).run()
    assert not app.exception and not app.error,[e.value for e in app.error]


def test_blank_order_manual_confirmation_units_and_amendment(tmp_path,monkeypatch):
    import pytest
    from shipping.templating import load_spec
    if not load_spec('customer').get('source_path'):
        pytest.skip('This full export workflow requires a private customer template, which is not distributed')
    root=tmp_path/'data';monkeypatch.setenv('SHIPPING_DATA_DIR',str(root))
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=30).run()
    next(e for e in app.text_input if e.label=='业务号').set_value('UI-BLANK-SYNTHETIC')
    button(app,'新建空白批次');w=Workbench(root);sid=w.repo.list_shipments()[0]['id']
    assert not w.repo.load(sid)['facts']
    for prefix in ('shipper','consignee','manufacturer'):
        put(app,prefix+'name_en','SYNTHETIC '+prefix.upper());put(app,prefix+'address','100 TEST ROAD')
    for k,v in {'origin':'TEST ORIGIN','destination':'TEST DESTINATION','transport_mode':'air','trade_term':'CPT','payment_term':'TEST PAYMENT','planned_ship_date':'2026-09-11'}.items():put(app,k,v)
    button(app,'保存基础数据');button(app,'新增合同')
    s=w.repo.load(sid);cid=s['contracts'][0]['id']
    for k,v in {'contract_no':'UI-CONTRACT','invoice_no':'UI-INVOICE','invoice_date':'2026-09-11','customer_currency':'EUR','customs_currency':'USD'}.items():put(app,'contract'+cid+k,v)
    button(app,'保存合同');button(app,'新增货物行');lid=w.repo.load(sid)['lines'][0]['id']
    for k,v in {'name_en':'SYNTHETIC TABLETS','name_cn':'合成测试片','product_code':'UI-PRODUCT','strength_text':'25 mg','batch_no':'UI-BATCH','mfg_date':'2026-08-01','exp_date':'2028-07-31','base_unit':'TAB','inner_unit':'BOTTLE','hs_code':'TEST-HS','storage_text':'TEST STORAGE','base_quantity':360000,'units_per_inner':25,'inners_per_carton':240,'full_cartons':60}.items():put(app,'line'+lid+k,v)
    for group,currency in [('customer','EUR'),('customs','USD')]:
        put(app,lid+group+'unit_price','0.06');put(app,lid+group+'currency',currency);put(app,lid+group+'pricing_unit','TAB')
        next(e for e in app.selectbox if str(e.key).endswith(lid+group+'basis')).set_value('base')
    button(app,'保存货物与价格')
    assert not w.repo.load(sid)['facts']
    app.sidebar.radio[0].set_value('包装与运输').run();assert not app.error
    choose(app,'尺寸输入单位','cm');choose(app,'重量输入单位','kg')
    for k,v in {'outer_l_mm':'40','outer_w_mm':'30','outer_h_mm':'20','carton_net_g':'4','carton_gross_g':'5','max_layers':6,'max_goods_height_mm':'120','max_superimposed_g':'50'}.items():
        suffix='packing'+lid+k+('cm' if k.endswith('_mm') else 'kg' if k.endswith('_g') else '')
        put(app,suffix,v)
    button(app,'保存包装参数');button(app,'添加托盘快照')
    for k,v in {'length_mm':'120','width_mm':'100','height_mm':'15','tare_g':'20','max_payload_g':'1000'}.items():put(app,'0'+k+('cm' if k.endswith('_mm') else 'kg'),v)
    button(app,'保存此托盘')
    for k in ('extra_height_mm','extra_length_mm','extra_width_mm','auxiliary_g'):put(app,k+('cm' if k.endswith('_mm') else 'kg'),'0')
    button(app,'保存辅材')
    choose(app,'业务限高预设',1);button(app,'复制预设为新运输段')
    put(app,'leg0max_unit_gross_gkg','1000')
    next(e for e in app.checkbox if e.label=='本运输段参数已确认').check()
    button(app,'保存运输段')
    app.sidebar.radio[0].set_value('资料与基础数据').run()
    next(e for e in app.checkbox if e.label=='我已逐项核实，选择全部待确认字段').check().run()
    app.text_input(key='source_note').set_value('Synthetic UI test: all displayed fields checked manually')
    button(app,'确认所选字段')
    app.sidebar.radio[0].set_value('打托方案').run();button(app,'计算打托方案');button(app,'确认当前方案')
    assert w.current_plan(sid)['totals']['pallet_count']==1
    app.sidebar.radio[0].set_value('核对与版本').run();choose(app,'尺寸输入单位','cm');choose(app,'重量输入单位','kg')
    pid=w.current_plan(sid)['pallets'][0]['id']
    for k,v in {'length_mm':'120','width_mm':'100','height_mm':'135','gross_g':'320','net_g':'240'}.items():put(app,'actual'+pid+k+('cm' if k.endswith('_mm') else 'kg'),v)
    next(e for e in app.text_input if e.label=='工厂实测依据').set_value('Synthetic UI measured values')
    next(e for e in app.checkbox if e.label=='确认以上为含托外廓 / 含托总毛重').check();button(app,'保存实测并重检')
    app.sidebar.radio[0].set_value('打托方案').run();button(app,'确认当前方案')
    app.sidebar.radio[0].set_value('单证生成').run();choose(app,'出单范围',cid);button(app,'生成可编辑草稿');button(app,'批准这个版本')
    assert w.documents(sid)[0]['stage']=='approved'
    app.sidebar.radio[0].set_value('资料与基础数据').run();put(app,'line'+lid+'batch_no','UI-BATCH-REVISED');button(app,'保存货物与价格')
    assert w.documents(sid)[0]['stage']=='stale'
    assert not w.repo.load(sid)['facts']['lines.0.batch_no']['confirmed']
    assert get_path(w.repo.load(sid),'lines.0.packing_spec.outer_l_mm')==400
