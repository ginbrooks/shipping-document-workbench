"""Scope-aware expected values shared by observation UI, checking and approval."""
from decimal import Decimal
from .validation import get_path,amount,quantity
from .models import leaves


def scoped_lines(s,scope):
    return [(i,l) for i,l in enumerate(s['lines']) if scope in ('ALL',s['id'],l['contract_id'],l['id'])]


def required_paths(s,kind,scope,spec):
    required=list(spec['required_fields'])
    if kind in ('customer','customs'):
        required+=['contracts.*.contract_no','contracts.*.invoice_no','contracts.*.invoice_date','shipper.address','consignee.address','lines.*.strength_text']
    groups=('customer','customs') if kind=='booking' else (kind,) if kind in ('customer','customs') else ()
    required += [f'lines.*.{g}_price.{k}' for g in groups for k in ('unit_price','currency','pricing_unit','base_units_per_pricing_unit')]
    selected=scoped_lines(s,scope);contracts={l['contract_id'] for _,l in selected};out=[]
    for path in required:
        if path.startswith('lines.*.'):
            out.extend(path.replace('*',str(i),1) for i,_ in selected)
        elif path.startswith('contracts.*.'):
            out.extend(path.replace('*',str(i),1) for i,c in enumerate(s['contracts']) if c['id'] in contracts)
        else:out.append(path)
    return sorted(set(out))


def applies(s,path,record_scope,kind,scope):
    selected=scoped_lines(s,scope);ids={l['id'] for _,l in selected};contracts={l['contract_id'] for _,l in selected}
    if ('customer_price' in path or path.startswith('prices.customer')) and kind not in ('customer','booking'):return False
    if ('customs_price' in path or path.startswith('prices.customs')) and kind not in ('customs','booking'):return False
    owner_ids=set();field_selected=True
    try:
        if path.startswith('lines.'):
            line=s['lines'][int(path.split('.')[1])];owner_ids={line['id'],line['contract_id']};field_selected=line['id'] in ids
        elif path.startswith('contracts.'):
            contract=s['contracts'][int(path.split('.')[1])];owner_ids={contract['id']};field_selected=contract['id'] in contracts
    except (ValueError,IndexError):return True
    whole=record_scope in ('ALL',s['id'],None,'')
    source_selected=whole or record_scope==scope or record_scope in ids|contracts
    # A record linked to B but pointing at A must be resolved for either affected scope.
    if owner_ids and not whole and record_scope not in owner_ids:return source_selected or field_selected
    return source_selected and field_selected


def resolve_expected(s,plan,path,scope):
    from .planning import totals,effective
    selected=scoped_lines(s,scope);ids={l['id'] for _,l in selected}
    valid=bool(selected)
    if path.startswith('lines.') or path.startswith('contracts.'):
        try:
            index=int(path.split('.')[1])
            valid=valid and (s['lines'][index]['id'] in ids if path.startswith('lines.') else s['contracts'][index]['id'] in {l['contract_id'] for _,l in selected})
        except (ValueError,IndexError):valid=False
    if path.startswith('plan.'):
        if not plan:return None,False,False
        pallets=[p for p in plan['pallets'] if p['line_id'] in ids]
        t=totals(pallets)
        t['base_quantity']=sum(l['base_quantity'] for _,l in selected) if len({l['base_unit'] for _,l in selected})==1 and all(l['base_quantity'] is not None for _,l in selected) else None
        t['quantities']={u:sum(l['base_quantity'] for _,l in selected if l['base_unit']==u) for u in {l['base_unit'] for _,l in selected} if all(l['base_quantity'] is not None for _,l in selected if l['base_unit']==u)}
        if path.startswith('plan.totals.'):value=get_path(t,path.removeprefix('plan.totals.'))
        elif path.startswith('plan.pallets.'):
            parts=path.split('.');key=parts[2]
            p=next((p for p in pallets if p['id']==key),None)
            if p is None and key.isdigit() and int(key)<len(pallets):p=pallets[int(key)]
            value=get_path({**p,**effective(p),'volume_m3':totals([p])['volume_m3']},'.'.join(parts[3:])) if p else None
        else:value=None
        return value,plan['status']=='confirmed',valid
    if path.startswith('prices.'):
        parts=path.split('.')
        if len(parts)!=3:return None,False,False
        group=parts[1]
        if group not in ('customer','customs'):return None,False,False
        cs={l['contract_id'] for _,l in selected}
        currencies={c[group+'_currency'] for c in s['contracts'] if c['id'] in cs}
        if len(currencies)!=1:return None,False,valid
        currency=next(iter(currencies));amounts=[]
        from .validation import rounded_adjustments
        try:
            for _,l in selected:
                result=amount(l,group,currency,s['currency_decimals'])
                if result['issue']:return None,False,valid
                amounts.append(result['amount'])
            value=sum((Decimal(a) for a in amounts),Decimal(0)) if all(a is not None for a in amounts) else None
            if value is not None:
                value+=sum((Decimal(a['amount']) for c in s['contracts'] if c['id'] in cs for a in rounded_adjustments(c,group,s['currency_decimals'])),Decimal(0))
        except ValueError:return None,False,valid
        confirmed=all(s['facts'].get(f'lines.{i}.{group}_price.{k}',{}).get('confirmed') and s['facts'][f'lines.{i}.{group}_price.{k}']['value']==l[group+'_price'][k] for i,l in selected for k in ('unit_price','currency','base_units_per_pricing_unit','pricing_unit'))
        confirmed=confirmed and all(s['facts'].get(f'lines.{i}.base_quantity',{}).get('confirmed') and s['facts'][f'lines.{i}.base_quantity']['value']==l['base_quantity'] for i,l in selected)
        return (value if parts[2]=='total_amount' else currency if parts[2]=='currency' else None),confirmed,valid
    value=get_path(s,path);fact=s.get('facts',{}).get(path,{})
    return value,fact.get('confirmed',False) and fact.get('value')==value,valid


def observation_fields(s,plan,scope):
    fields=[p for p,_ in leaves(s) if not p.startswith(('facts','audit','revision','schema_version'))]
    fields+=['plan.totals.'+k for k in ('pallet_count','carton_count','base_quantity','gross_g','net_g','volume_m3')]
    for p in (plan or {}).get('pallets',[]):
        if p['line_id'] in {l['id'] for _,l in scoped_lines(s,scope)}:
            fields += [f'plan.pallets.{p["id"]}.{k}' for k in ('carton_count','length_mm','width_mm','height_mm','gross_g','net_g','volume_m3')]
    fields += [f'prices.{g}.{k}' for g in ('customer','customs') for k in ('total_amount','currency')]
    return fields
