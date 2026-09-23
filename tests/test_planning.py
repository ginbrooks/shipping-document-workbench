import json
from pathlib import Path
from copy import deepcopy
import pytest
from shipping.demo import synthetic
from shipping.models import Shipment, convert_unit
from shipping.planning import calculate_plan, check_route, layouts
from shipping.validation import quantity, amount

FIX = json.loads((Path(__file__).parents[1]/'fixtures/acceptance.json').read_text())


def case_data(case):
    base=deepcopy(FIX['base'])
    for path,value in case['set'].items():
        obj=base; parts=path.split('.')
        for p in parts[:-1]:obj=obj[int(p)] if isinstance(obj,list) else obj[p]
        if isinstance(obj,list):obj[int(parts[-1])]=value
        else:obj[parts[-1]]=value
    s=synthetic()
    for key in ('full_cartons','base_quantity','packing_spec','partial_cartons','customer_price','customs_price'):
        s['lines'][0][key]=base[key]
    s['pallet_choices']=[base['pallet_spec']];s['route_legs']=base['route_legs'];s['extras']=base['extras']
    return s


@pytest.mark.parametrize('case',[c for c in FIX['cases'] if c['operation']=='calculate_plan'],ids=lambda c:c['id'])
def test_acceptance_calculations(case):
    s=case_data(case);p=calculate_plan(s);e=case['expected']
    assert len(p['pallets'])==e['pallet_count']
    for key, field in [('carton_counts','carton_count'),('layers','layers')]:
        if key in e:assert [x[field] for x in p['pallets']]==e[key]
    if 'heights_mm' in e:assert [x['estimated']['height_mm'] for x in p['pallets']]==e['heights_mm']
    if 'gross_g' in e:assert p['totals']['gross_g']==e['gross_g']
    if 'volume_m3' in e:assert f"{p['totals']['volume_m3']:.3f}"==e['volume_m3']
    assert p['totals']['carton_count']==s['lines'][0]['full_cartons']


def test_A07_A08_and_pending():
    s=synthetic();p=calculate_plan(s)
    s['route_legs'][0]['vehicle_snapshot']['door_h_mm']=1300
    assert 'EXCEEDS_DOOR_HEIGHT' in [x['code'] for x in check_route(p['pallets'],s)]
    s=synthetic();s['lines'][0]['packing_spec'].update(temp_min_c=2,temp_max_c=8)
    assert 'TEMPERATURE_RANGE_NOT_COVERED' in [x['code'] for x in check_route(p['pallets'],s)]
    s['route_legs'][0]['vehicle_snapshot']['usable_l_mm']=None
    assert 'VEHICLE_CHECK_PENDING' in [x['code'] for x in check_route(p['pallets'],s)]


def test_A09_prices_and_rounding():
    s=synthetic();l=s['lines'][0]
    assert amount(l,'customer','EUR')['amount']=='21600.00'
    assert amount(l,'customs','USD')['amount']=='14400.00'
    l['customer_price'].update(unit_price='1.50',pricing_unit='BOTTLE',base_units_per_pricing_unit=25)
    assert amount(l,'customer','EUR')['quantity']==14400
    assert amount(l,'customer','EUR')['amount']=='21600.00'
    l['base_quantity']=1;l['customer_price'].update(unit_price='0.005',base_units_per_pricing_unit=1)
    assert amount(l,'customer','EUR')['amount']=='0.01'


def test_A23_partial():
    c=next(c for c in FIX['cases'] if c['id']=='A23_QUANTITY');s=case_data(c)
    assert quantity(s['lines'][0])=={'carton_count':60,'base_quantity':357000,'separate_packing_groups':2}
    p=calculate_plan(s)
    assert p['totals']['carton_count']==60
    assert len(p['pallets'])==2
    assert p['totals']['net_g']==238000


def test_A24_invalid_dimensions_capacity_and_units():
    s=synthetic();s['lines'][0]['packing_spec']['outer_l_mm']=0
    with pytest.raises(ValueError):Shipment.model_validate(s)
    s=synthetic();s['lines'][0]['packing_spec']['outer_l_mm']=3000
    with pytest.raises(ValueError,match='BOX_EXCEEDS_PALLET'):calculate_plan(s)
    s=synthetic();s['pallet_choices'][0]['max_payload_g']=4000
    with pytest.raises(ValueError,match='ZERO_CAPACITY'):calculate_plan(s)
    assert convert_unit('1.25','kg','weight')==1250
    with pytest.raises(ValueError):convert_unit('0.0001','kg','weight')


def test_A04_smaller_pallet_and_unknown_weights():
    s=synthetic();s['pallet_choices'][0]['width_mm']=800
    assert calculate_plan(s)['pallets'][0]['per_layer'] < 10
    s['lines'][0]['packing_spec']['carton_gross_g']=None
    p=calculate_plan(s)
    assert p['totals']['gross_g'] is None and p['status']=='provisional'


def test_layout_coordinates_and_manual_counts():
    candidates=layouts(1200,1000,400,300,True)
    assert len(candidates[0])==10
    s=synthetic();p=calculate_plan(s,manual_counts={'TEST-LINE-A':[30,30]})
    assert [x['carton_count'] for x in p['pallets']]==[30,30]
    with pytest.raises(ValueError,match='CARTON_CONSERVATION'):calculate_plan(s,manual_counts={'TEST-LINE-A':[30]})
