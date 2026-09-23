import json
from copy import deepcopy
from pathlib import Path
from .models import Shipment, leaves


def synthetic(two_contracts=False):
    base = json.loads((Path(__file__).parents[1]/'fixtures/acceptance.json').read_text())['base']
    line = {k:v for k,v in base.items() if k not in ('pallet_spec','extras','route_legs','line_id')}
    line.update(id=base['line_id'], name_cn='合成测试片剂甲', name_en='SYNTHETIC TABLETS A',
                strength_text='25 mg', mfg_date='2026-08-01',exp_date='2028-07-31',hs_code='TEST-HS', storage_text='15-25 C')
    party = {'name_cn':'合成测试公司','name_en':'SYNTHETIC TRADING LTD.', 'address':'100 Test Road, Test City',
             'contact':'TEST CONTACT','phone':'TEST-PHONE','email':'example@example.invalid'}
    contract = {'id':base['contract_id'],'contract_no':'TEST-CONTRACT-A','invoice_no':'TEST-INVOICE-A',
                'invoice_date':'2026-09-11','customer_currency':'EUR','customs_currency':'USD',
                'ordered_quantities':{'SYNTHETIC-TABLET':600000}, 'common_fields':{'bank_account':'000123456789','moc_no':'TEST-MOC-A'}}
    data = {'id':'SYNTHETIC-DEMO','business_no':'DEMO-20260911','shipper':party,'consignee':dict(party,name_en='SYNTHETIC BUYER LTD.'),
            'notify_party':dict(party,name_en='SYNTHETIC BUYER LTD.'),'manufacturer':dict(party,name_en='SYNTHETIC FACTORY LTD.'),
            'origin':'TEST ORIGIN','destination':'TEST DESTINATION','transport_mode':'air','trade_term':'CPT',
            'payment_term':'TEST PAYMENT','planned_ship_date':'2026-09-12','remarks':'合成测试数据 / SYNTHETIC TEST',
            'contracts':[contract],'lines':[line],'route_legs':base['route_legs'], 'pallet_choices':[base['pallet_spec']], 'extras':base['extras']}
    for leg in data['route_legs']:
        leg['availability']={k:'not_applicable' for k in ('max_unit_gross_g',) if leg.get(k) is None}
    if two_contracts:
        b=deepcopy(line); b.update(id='TEST-LINE-B',contract_id='TEST-CONTRACT-B',name_cn='合成测试片剂乙',name_en='SYNTHETIC TABLETS B',batch_no='TEST-BATCH-002')
        c=deepcopy(contract);c.update(id='TEST-CONTRACT-B',contract_no='TEST-CONTRACT-B',invoice_no='TEST-INVOICE-B')
        data['lines'].append(b);data['contracts'].append(c)
    value=Shipment.model_validate(data).model_dump(mode='json')
    value['facts']={p:{'value':v,'confirmed':True,'source_ref':{'manual_note':'SYNTHETIC 软件验收数据'}} for p,v in leaves(value) if not p.startswith(('facts','audit','revision')) and v is not None}
    return Shipment.model_validate(value).model_dump(mode='json')
