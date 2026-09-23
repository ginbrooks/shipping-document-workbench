"""Deterministic strip packing and conservative transport checks. No I/O or AI."""
import itertools
import math
from copy import deepcopy
from decimal import Decimal
from .models import Shipment
from .validation import quantity, issue
from .repository import canonical
from .ingestion import digest

ALGORITHM='grid_plus_two_orientation_strips_v1.1'


def no_overlap(boxes, length, width):
    for i,a in enumerate(boxes):
        if min(a['x_mm'],a['y_mm'])<0 or a['x_mm']+a['w_mm']>length or a['y_mm']+a['d_mm']>width:return False
        for b in boxes[:i]:
            if a['x_mm']<b['x_mm']+b['w_mm'] and b['x_mm']<a['x_mm']+a['w_mm'] and a['y_mm']<b['y_mm']+b['d_mm'] and b['y_mm']<a['y_mm']+a['d_mm']:return False
    return True


def layouts(length,width,box_l,box_w,rotate=True):
    if any(v is None or v<=0 for v in (length,width,box_l,box_w)):
        raise ValueError('DIMENSIONS_REQUIRED')
    if max(length//min(box_l,box_w),width//min(box_l,box_w))>1000:
        raise ValueError('GEOMETRY_TOO_LARGE')
    result={}
    for transpose in (False,True):
        L,W=(width,length) if transpose else (length,width)
        l,w=(box_w,box_l) if transpose else (box_l,box_w)
        for r0 in range(W//w+1):
            for r90 in range((W-r0*w)//l+1 if rotate else 1):
                coords=[];y=0
                for rows,bl,bw,angle in ((r0,l,w,0),(r90,w,l,90)):
                    for _ in range(rows):
                        for col in range(L//bl):
                            b={'x_mm':col*bl,'y_mm':y,'w_mm':bl,'d_mm':bw,'rotation':angle}
                            if transpose:b={'x_mm':b['y_mm'],'y_mm':b['x_mm'],'w_mm':b['d_mm'],'d_mm':b['w_mm'],'rotation':angle}
                            coords.append(b)
                        y+=bw
                coords.sort(key=lambda x:(x['x_mm'],x['y_mm'],x['rotation']))
                if coords and no_overlap(coords,length,width):result[canonical(coords)]=coords
    def rank(c):
        return (-len(c),max(x['x_mm']+x['w_mm'] for x in c)*max(x['y_mm']+x['d_mm'] for x in c),canonical(c))
    return sorted(result.values(),key=rank)


def geometry_hash(s):
    keys=('id','contract_id','batch_no','full_cartons','partial_cartons','base_quantity','units_per_inner','inners_per_carton','packing_spec','reference_samples','allowed_pallet_ids','packing_extras')
    value={'lines':[{k:l.get(k) for k in keys} for l in s['lines']],
           'route_legs':s['route_legs'],'pallet_choices':s['pallet_choices'],'extras':s['extras'],'algorithm':ALGORITHM}
    return digest(canonical(value).encode())


def effective(p):
    return {**p['estimated'],**{k:v for k,v in (p.get('actual') or {}).items() if k in p['estimated'] and v is not None}}


def totals(pallets):
    e=[effective(p) for p in pallets]
    def sum_known(key):return sum(x[key] for x in e) if all(x.get(key) is not None for x in e) else None
    volume=sum((Decimal(x['length_mm'])*x['width_mm']*x['height_mm']/Decimal(10**9) for x in e),Decimal(0)) if all(all(x.get(k) is not None for k in ('length_mm','width_mm','height_mm')) for x in e) else None
    return {'pallet_count':len(pallets),'carton_count':sum(p['carton_count'] for p in pallets),'net_g':sum_known('net_g'),
            'gross_g':sum_known('gross_g'),'volume_m3':volume}


def pack_group(line, spec, count, pallet, extras, legs, suffix='', manual=None, layout_index=0):
    L,W,H=spec['outer_l_mm'],spec['outer_w_mm'],spec['outer_h_mm']
    candidates=layouts(pallet['length_mm'],pallet['width_mm'],L,W,spec.get('allow_rotate_90',True))
    if not candidates:raise ValueError('BOX_EXCEEDS_PALLET')
    layout=candidates[layout_index]
    issues=[];height_limits=[]
    if any(extras.get(k) is None for k in ('extra_height_mm','extra_length_mm','extra_width_mm','auxiliary_g')):
        issues.append(issue('EXTRAS_UNCONFIRMED',message='辅材重量及外廓附加量须确认，零值也须明确'))
    if H is None or pallet['height_mm'] is None:raise ValueError('HEIGHT_REQUIRED')
    for leg in legs:
        if leg.get('repalletize'):raise ValueError('REPALLETIZING_UNSUPPORTED')
        v=leg.get('vehicle_snapshot') or {}
        limits=[n for n in (leg.get('max_unit_height_mm'),v.get('usable_h_mm'),v.get('door_h_mm')) if n is not None]
        if limits:height_limits.append(min(limits)-leg['clearance_mm'])
        if not leg['constraints_confirmed'] or leg['height_includes_pallet'] is not True:
            issues.append(issue('ROUTE_UNCONFIRMED',scope=leg['id']))
        for key in ('max_unit_height_mm','max_unit_gross_g'):
            if leg.get(key) is None and leg.get('availability',{}).get(key)!='not_applicable':
                issues.append(issue('ROUTE_LIMIT_UNKNOWN',path=key,scope=leg['id']))
    if not height_limits:raise ValueError('ROUTE_HEIGHT_REQUIRED')
    limits=[(min(height_limits)-pallet['height_mm']-(extras.get('extra_height_mm') or 0))//H]
    for k in ('max_layers','max_goods_height_mm','max_superimposed_g'):
        n=spec.get(k)
        if n is not None:
            if k=='max_layers':limits.append(n)
            elif k=='max_goods_height_mm':limits.append(n//H)
            elif spec['carton_gross_g']:limits.append(n//spec['carton_gross_g']+1)
    if not any(spec.get(k) is not None for k in ('max_layers','max_goods_height_mm','max_superimposed_g')):
        issues.append(issue('STACK_RULE_UNCONFIRMED'))
    allowed=min(limits);capacity=len(layout)*allowed
    gross=spec['carton_gross_g'];tare=pallet['tare_g'];aux=extras.get('auxiliary_g')
    if gross is None or tare is None or aux is None or pallet['max_payload_g'] is None:
        issues.append(issue('WEIGHT_CHECK_PENDING'))
    if gross:
        weights=[]
        if pallet['max_payload_g'] is not None and aux is not None:weights.append(pallet['max_payload_g']-aux)
        if tare is not None and aux is not None:
            weights.extend(leg['max_unit_gross_g']-tare-aux for leg in legs if leg.get('max_unit_gross_g') is not None)
        if weights:capacity=min(capacity,min(weights)//gross)
    if capacity<=0:raise ValueError('ZERO_CAPACITY')
    counts=manual if manual is not None else [min(capacity,count-i) for i in range(0,count,capacity)]
    if sum(counts)!=count:raise ValueError('CARTON_CONSERVATION')
    if any(not isinstance(n,int) or n<=0 or n>capacity for n in counts):raise ValueError('MANUAL_CAPACITY_EXCEEDED')
    pallets=[]
    for i,n in enumerate(counts):
        layers=math.ceil(n/len(layout));last=(n-1)%len(layout)+1
        top=sorted(layout,key=lambda b:((b['x_mm']+b['w_mm']/2-pallet['length_mm']/2)**2+(b['y_mm']+b['d_mm']/2-pallet['width_mm']/2)**2,b['x_mm'],b['y_mm']))[:last]
        estimate={'length_mm':pallet['length_mm']+(extras.get('extra_length_mm') or 0),
                  'width_mm':pallet['width_mm']+(extras.get('extra_width_mm') or 0),
                  'height_mm':pallet['height_mm']+layers*H+(extras.get('extra_height_mm') or 0),
                  'net_g':n*spec['carton_net_g'] if spec['carton_net_g'] is not None else None,
                  'gross_g':n*gross+tare+aux if all(v is not None for v in (gross,tare,aux)) else None}
        # Unknown extras may permit a geometric candidate but never a fabricated final external size.
        for k,extra in (('length_mm','extra_length_mm'),('width_mm','extra_width_mm'),('height_mm','extra_height_mm')):
            if extras.get(extra) is None:estimate[k]=None
        pallets.append({'id':f'{line["id"]}{suffix}-P{i+1:03}', 'line_id':line['id'],'contract_id':line['contract_id'],
                        'carton_count':n,'carton_ids':[f'{line["id"]}{suffix}-C{j+1}' for j in range(sum(counts[:i]),sum(counts[:i])+n)],
                        'pallet_spec_snapshot':deepcopy(pallet),'layout':layout,'top_layout':top,'per_layer':len(layout),
                        'layers':layers,'boxes_last_layer':last,'estimated':estimate,'actual':None,'extras':deepcopy(extras),
                        'approval':None,'capacity':capacity,'box_height_mm':H,'group':suffix or 'full'})
    return pallets,issues


def vehicle_layout(pallets,v):
    """Two orderings and four orientation priorities, shelf/row scan."""
    L,W=v['usable_l_mm'],v['usable_w_mm']
    for order in ('long','area'):
        items=sorted(pallets,key=lambda p:(-(max(effective(p)['length_mm'],effective(p)['width_mm']) if order=='long' else effective(p)['length_mm']*effective(p)['width_mm']),p['id']))
        for rotate_mode in range(4):
            coords=[];x=y=row_depth=0;ok=True
            for p in items:
                e=effective(p);a,b=e['length_mm'],e['width_mm']
                orientations=[(a,b,0),(b,a,90)]
                if rotate_mode==1:orientations.reverse()
                if rotate_mode==2:orientations.sort(key=lambda x:x[1])
                if rotate_mode==3:orientations.sort(key=lambda x:x[0])
                fit=next((q for q in orientations if x+q[0]<=L and y+q[1]<=W),None)
                if fit is None:
                    x=0;y+=row_depth;row_depth=0
                    fit=next((q for q in orientations if q[0]<=L and y+q[1]<=W),None)
                if fit is None:ok=False;break
                a,b,angle=fit
                coords.append({'id':p['id'],'x_mm':x,'y_mm':y,'w_mm':a,'d_mm':b,'rotation':angle});x+=a;row_depth=max(row_depth,b)
            if ok and len(coords)==len(pallets) and no_overlap(coords,L,W):return coords
    return None


def check_route(pallets,s):
    issues=[]
    for leg in s['route_legs']:
        e=[effective(p) for p in pallets]
        clearance=leg['clearance_mm'];v=leg.get('vehicle_snapshot')
        for p,d in zip(pallets,e):
            for key,cap,code in (('height_mm',leg.get('max_unit_height_mm'),'EXCEEDS_LEG_HEIGHT'),('gross_g',leg.get('max_unit_gross_g'),'EXCEEDS_UNIT_WEIGHT')):
                if cap is not None and d.get(key) is not None and d[key]>(cap-clearance if key=='height_mm' else cap):
                    issues.append(issue(code,'FAIL',scope=p['id']))
        if v is None:
            if leg['mode']=='road':issues.append(issue('VEHICLE_CHECK_PENDING',scope=leg['id']))
            continue
        required=('usable_l_mm','usable_w_mm','usable_h_mm','door_w_mm','door_h_mm','payload_g')
        pending=any(v.get(k) is None for k in required) or any(any(d.get(k) is None for k in ('length_mm','width_mm','height_mm','gross_g')) for d in e)
        if pending:issues.append(issue('VEHICLE_CHECK_PENDING',scope=leg['id']))
        for d in e:
            if d.get('height_mm') is not None:
                for k,code in (('door_h_mm','EXCEEDS_DOOR_HEIGHT'),('usable_h_mm','EXCEEDS_VEHICLE_HEIGHT')):
                    if v.get(k) is not None and d['height_mm']>v[k]-clearance:issues.append(issue(code,'FAIL',scope=leg['id']))
            if v.get('door_w_mm') and d.get('length_mm') and d.get('width_mm') and min(d['length_mm'],d['width_mm'])>v['door_w_mm']:
                issues.append(issue('EXCEEDS_DOOR_WIDTH','FAIL',scope=leg['id']))
        if not pending:
            if sum(d['gross_g'] for d in e)>v['payload_g']:issues.append(issue('EXCEEDS_VEHICLE_PAYLOAD','FAIL',scope=leg['id']))
            layout=vehicle_layout(pallets,v)
            issues.append(issue('VEHICLE_GEOMETRY' if layout is not None else 'LAYOUT_NOT_FOUND','PASS' if layout is not None else 'NEEDS_CONFIRMATION',observed=layout,
                                message='按已填参数，地板几何校验通过' if layout is not None else '启发式未找到布局，不代表数学上装不下',scope=leg['id']))
        for line in s['lines']:
            spec=line['packing_spec'];lo,hi=spec.get('temp_min_c'),spec.get('temp_max_c')
            if lo is None or hi is None or v.get('temp_min_c') is None or v.get('temp_max_c') is None:
                issues.append(issue('TEMPERATURE_CHECK_PENDING',scope=leg['id']))
            elif Decimal(str(v['temp_min_c']))>Decimal(str(lo)) or Decimal(str(v['temp_max_c']))<Decimal(str(hi)):
                issues.append(issue('TEMPERATURE_RANGE_NOT_COVERED','FAIL',scope=leg['id']))
    return issues


def calculate_plan(shipment,manual_counts=None,layout_index=0):
    s=Shipment.model_validate(shipment).model_dump(mode='json')
    if not s['pallet_choices']:raise ValueError('PALLET_REQUIRED')
    if not s['lines']:raise ValueError('LINES_REQUIRED')
    alternatives=[]
    for line in s['lines']:
        quantity(line);options=[];errors=[]
        for pallet in s['pallet_choices']:
            if line.get('allowed_pallet_ids') and pallet['id'] not in line['allowed_pallet_ids']:continue
            extras=line.get('packing_extras') or s['extras']
            try:
                pp=[];ii=[]
                if line['full_cartons']:
                    a,b=pack_group(line,line['packing_spec'],line['full_cartons'],pallet,extras,s['route_legs'],manual=(manual_counts or {}).get(line['id']),layout_index=layout_index)
                    pp+=a;ii+=b
                for part in line['partial_cartons']:
                    spec={**line['packing_spec'],**{k:part[k] for k in ('outer_l_mm','outer_w_mm','outer_h_mm')},
                          'carton_net_g':part['net_g'],'carton_gross_g':part['gross_g'],'max_layers':None,'max_goods_height_mm':None,'max_superimposed_g':None,**part['stack_rule']}
                    a,b=pack_group(line,spec,1,pallet,extras,s['route_legs'],suffix='-'+part['id']);pp+=a;ii+=b
                options.append((pp,ii))
            except (ValueError,IndexError) as e:errors.append(str(e))
        if not options:raise ValueError('; '.join(errors))
        alternatives.append(options)
    if math.prod(len(a) for a in alternatives)>4096:raise ValueError('TOO_MANY_PALLET_COMBINATIONS')
    ranked=[]
    for combo in itertools.product(*alternatives):
        pallets=[p for pp,_ in combo for p in pp];issues=[i for _,ii in combo for i in ii];checks=check_route(pallets,s)
        issues+=checks;t=totals(pallets)
        blocked=any(i['status']!='PASS' for i in checks)
        ranked.append(((blocked,len(pallets),t['volume_m3'] if t['volume_m3'] is not None else Decimal('Infinity'),canonical([p['pallet_spec_snapshot']['id'] for p in pallets])),pallets,issues,t))
    _,pallets,issues,t=min(ranked,key=lambda x:x[0])
    return {'input_hash':geometry_hash(s),'algorithm_version':ALGORITHM,'pallets':pallets,'totals':t,
            'route_checks':check_route(pallets,s), 'issues':issues,'status':'provisional',
            'manual_counts':manual_counts or {},'layout_index':layout_index}


def layout_svg(pallet):
    p=pallet['pallet_spec_snapshot'];L,W=p['length_mm'],p['width_mm']
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-40 -40 {L+80} {W+220}"><rect width="{L}" height="{W}" fill="#eef4f3" stroke="#183e3c" stroke-width="8"/>']
    for i,b in enumerate(pallet['layout']):
        parts.append(f'<rect x="{b["x_mm"]+5}" y="{b["y_mm"]+5}" width="{b["w_mm"]-10}" height="{b["d_mm"]-10}" fill="#b9d6ce" stroke="#286256" stroke-width="3"/>')
        parts.append(f'<text x="{b["x_mm"]+b["w_mm"]//2}" y="{b["y_mm"]+b["d_mm"]//2}" text-anchor="middle" font-size="42">{i+1}</text>')
    parts.append(f'<text x="0" y="{W+100}" font-size="36">{L} × {W} mm · {pallet["per_layer"]}/layer · {pallet["layers"]} layers</text></svg>')
    return ''.join(parts)


def actual_checks(p):
    """Reject impossible measurements; explicitly review lower-than-plan geometry."""
    if not p.get('actual'):return []
    e=effective(p);body=p['pallet_spec_snapshot'];issues=[]
    if not ((e['length_mm']>=body['length_mm'] and e['width_mm']>=body['width_mm']) or (e['length_mm']>=body['width_mm'] and e['width_mm']>=body['length_mm'])):
        issues.append(issue('ACTUAL_SMALLER_THAN_PALLET','FAIL',scope=p['id']))
    if e['height_mm']<body['height_mm']:issues.append(issue('ACTUAL_BELOW_PALLET_HEIGHT','FAIL',scope=p['id']))
    # A newly typed low net weight cannot erase the existing verified goods floor.
    net=max(v for v in (p['estimated'].get('net_g'),e.get('net_g'),0) if v is not None)
    floor=(body.get('tare_g') or 0)+(p['extras'].get('auxiliary_g') or 0)+net
    if e.get('gross_g') is not None and e['gross_g']<floor:issues.append(issue('ACTUAL_BELOW_WEIGHT_FLOOR','FAIL',expected=floor,observed=e['gross_g'],scope=p['id']))
    if e.get('gross_g') is not None and body.get('max_payload_g') is not None and e['gross_g']-(body.get('tare_g') or 0)>body['max_payload_g']:issues.append(issue('ACTUAL_EXCEEDS_PAYLOAD','FAIL',scope=p['id']))
    estimated=p['estimated']
    lower=e['height_mm']<estimated['height_mm'] or any(a<b for a,b in zip(sorted([e['length_mm'],e['width_mm']]),sorted([estimated['length_mm'],estimated['width_mm']])))
    if lower and not p['actual'].get('difference_review'):
        issues.append(issue('ACTUAL_PLAN_DIFFERENCE','NEEDS_CONFIRMATION',message='实测小于箱规/层数预计外廓，须记录针对本托的复核依据',scope=p['id']))
    return issues
