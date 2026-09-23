from decimal import Decimal, ROUND_HALF_UP
from .models import Issue


def issue(code, status='NOT_CHECKED', path=None, expected=None, observed=None, message='', refs=None, scope=None):
    return Issue(code=code, status=status, severity='error' if status=='FAIL' else 'warning' if status!='PASS' else 'info',
                 field_path=path, expected=expected, observed=observed, message=message or code,
                 source_refs=refs or [], scope=scope).model_dump(mode='json')


def get_path(data, path):
    try:
        for part in path.split('.'):
            data = data[int(part)] if isinstance(data,list) else data[part]
        return data
    except (KeyError,IndexError,TypeError,ValueError):
        return None


def set_path(data, path, value):
    parts=path.split('.');obj=data
    for part in parts[:-1]:obj=obj[int(part)] if isinstance(obj,list) else obj[part]
    obj[int(parts[-1]) if isinstance(obj,list) else parts[-1]]=value


def quantity(line):
    required = ('units_per_inner','inners_per_carton','full_cartons','base_quantity')
    if any(line.get(k) is None for k in required):
        raise ValueError('QUANTITY_MISSING')
    unit=line['units_per_inner']*line['inners_per_carton']
    partial=line.get('partial_cartons',[])
    if any(p['base_quantity']>=unit for p in partial):
        raise ValueError('PARTIAL_NOT_PARTIAL')
    total=line['full_cartons']*unit+sum(p['base_quantity'] for p in partial)
    if total!=line['base_quantity']:
        raise ValueError('QUANTITY_MISMATCH')
    return {'carton_count':line['full_cartons']+len(partial),'base_quantity':total,
            'separate_packing_groups':int(line['full_cartons']>0)+len(partial)}


def amount(line, group, currency, decimals=None):
    p=line[group+'_price']; d=decimals or {'EUR':2,'USD':2,'CNY':2}
    if line.get('base_quantity') is None or any(p.get(k) is None for k in ('unit_price','currency','pricing_unit','base_units_per_pricing_unit')):
        return {'amount':None,'quantity':None,'currency':p.get('currency'),'issue':'PRICE_MISSING'}
    if not currency or currency!=p['currency']:
        raise ValueError('CURRENCY_MISMATCH')
    if currency not in d:
        raise ValueError('CURRENCY_DECIMALS_REQUIRED')
    factor,semantic_issue=pricing_factor(line,group)
    q=Decimal(line['base_quantity'])/Decimal(factor)
    if p.get('integer_units',True) and q!=q.to_integral_value():
        raise ValueError('FRACTIONAL_PRICING_UNIT')
    money=(q*Decimal(p['unit_price'])).quantize(Decimal(1).scaleb(-d[currency]), rounding=ROUND_HALF_UP)
    return {'amount':str(money),'quantity':int(q) if q==q.to_integral_value() else str(q),'currency':currency,'issue':semantic_issue}


def validate_shipment(s):
    issues=[];contracts={c['id']:c for c in s['contracts']}
    for i,l in enumerate(s['lines']):
        try:
            q=quantity(l)
            issues.append(issue('Q01','PASS',f'lines.{i}.base_quantity',q['base_quantity'],l['base_quantity']))
        except ValueError as e:
            issues.append(issue('Q01','FAIL' if str(e)!='QUANTITY_MISSING' else 'NOT_CHECKED',f'lines.{i}',message=str(e)))
        for group in ('customer','customs'):
            try:
                a=amount(l,group,contracts[l['contract_id']][group+'_currency'],s.get('currency_decimals'))
                issues.append(issue('P01','NOT_CHECKED' if a['amount'] is None or a['issue'] else 'PASS',f'lines.{i}.{group}_price',message=a['issue'] or '价格组独立计算'))
            except ValueError as e:issues.append(issue('P01','FAIL',f'lines.{i}.{group}_price',message=str(e)))
        for sample in l['reference_samples']:
            if not sample['included_in_carton_gross'] and not sample['separate_line_id']:
                issues.append(issue('W01','NOT_CHECKED',f'lines.{i}.reference_samples',message='对照品包装与重量未确认'))
            if sample['separate_line_id'] and sample['separate_line_id'] not in {x['id'] for x in s['lines']}:
                issues.append(issue('W01','FAIL',f'lines.{i}.reference_samples',message='独立对照品货物行不存在'))
    for c in s['contracts']:
        for product,ordered in c['ordered_quantities'].items():
            matched=[l for l in s['lines'] if l['contract_id']==c['id'] and l['product_code']==product]
            if all(l['base_quantity'] is not None for l in matched):
                shipped=sum(l['base_quantity'] for l in matched)
                issues.append(issue('Q01','FAIL' if shipped>ordered else 'PASS','contracts',ordered,shipped,scope=c['id']))
    return issues


def compare_observations(s, observations, plan=None):
    from .expectations import resolve_expected
    results=[]
    for o in observations:
        path=o['field_path'];expected,confirmed,valid_scope=resolve_expected(s,plan,path,o.get('scope'))
        observed=o.get('value');ref=o['source_ref']
        code='Q02' if path in ('plan.totals.pallet_count','plan.totals.carton_count','plan.totals.base_quantity') else 'W01' if path.startswith('plan.') else 'P01' if path.startswith('prices.') or 'price' in path else 'S01'
        def result(status,message):
            item=issue(code,status,path,expected,observed,message,[ref],o.get('scope'));item['observation_id']=o.get('id');item['doc_role']=o['doc_role'];results.append(item)
        if o['doc_role']=='reference_coa' and 'reference_samples' not in path:
            code='S03';result('FAIL','对照品证书不能替代成品 COA');continue
        if ('price' in path) and o['doc_role'] in ('purchase_invoice','finished_coa','reference_coa'):
            result('NOT_CHECKED','来源角色不能用于销售价格核对');continue
        provenance=o.get('_provenance_valid',bool(ref.get('locator') and ref.get('file_id')))
        if expected is None or observed is None or not valid_scope or not provenance or not confirmed:
            result('NOT_CHECKED','缺值、来源定位、文件角色/范围不一致或期望数据未确认');continue
        try:
            if o.get('unit') in ('kg','g') and path.endswith('_g'):
                from .models import convert_unit
                observed=convert_unit(observed,o['unit'],'weight')
            if o.get('unit') in ('mm','cm','m') and path.endswith('_mm'):
                from .models import convert_unit
                observed=convert_unit(observed,o['unit'],'dimension')
            if o.get('decimals') is not None:
                q=Decimal(1).scaleb(-o['decimals']);equal=Decimal(str(expected)).quantize(q,rounding=ROUND_HALF_UP)==Decimal(str(observed)).quantize(q,rounding=ROUND_HALF_UP)
            elif isinstance(expected,(int,float,Decimal)) and not isinstance(expected,bool):equal=Decimal(str(expected))==Decimal(str(observed))
            else:equal=' '.join(str(expected).split())==' '.join(str(observed).split())
        except (ValueError,ArithmeticError):equal=False
        if not equal:result('FAIL','原件候选/记录值与当前期望值不同')
        elif o.get('extraction_status') not in ('MANUAL','VERIFIED'):result('NEEDS_CONFIRMATION','值相符，候选来源待人工核验')
        else:result('PASS','已人工核验的来源字段相符')
    if not observations:results.append(issue('S01',message='尚未录入原件或回件 Observation'))
    return results


def packaging_check(plan, pallets, cartons, base_quantity, line):
    good=pallets==len(plan['pallets']) and cartons==plan['totals']['carton_count'] and base_quantity==line['base_quantity']
    return issue('Q02','PASS' if good else 'FAIL',expected={'pallets':len(plan['pallets']),'cartons':plan['totals']['carton_count']},observed={'pallets':pallets,'cartons':cartons})


def pricing_factor(line,group):
    p=line[group+'_price'];basis=p.get('pricing_basis','auto');unit=str(p.get('pricing_unit') or '').upper()
    if basis=='auto':
        if unit==str(line.get('base_unit') or '').upper():basis='base'
        elif line.get('inner_unit') and unit==line['inner_unit'].upper():basis='inner'
        elif unit in ('CARTON','CTN','CTNS','CARTONS'):basis='carton'
        else:return p['base_units_per_pricing_unit'],'PRICING_UNIT_UNRESOLVED'
    expected={'base':1,'inner':line.get('units_per_inner'),'carton':(line.get('units_per_inner') or 0)*(line.get('inners_per_carton') or 0)}
    if basis=='custom':return p['base_units_per_pricing_unit'],None if (p.get('conversion_note') or '').strip() else 'PRICING_CONVERSION_NOTE_REQUIRED'
    if not expected[basis]:raise ValueError('PRICING_CONVERSION_MISSING')
    if p['base_units_per_pricing_unit']!=expected[basis]:raise ValueError('PRICING_FACTOR_MISMATCH')
    return expected[basis],None


def rounded_adjustments(contract,group,decimals):
    values=contract[group+'_adjustments'];currency=contract[group+'_currency']
    if not values:return []
    if currency not in decimals:raise ValueError('CURRENCY_DECIMALS_REQUIRED')
    quantum=Decimal(1).scaleb(-decimals[currency])
    return [{'label':a['label'],'amount':format(Decimal(a['amount']).quantize(quantum,rounding=ROUND_HALF_UP),f'.{decimals[currency]}f'),'evidence':a['evidence']} for a in values]
