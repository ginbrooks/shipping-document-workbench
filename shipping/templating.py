"""Template rendering receives only a per-document allowlisted context."""
import io
import json
import re
from pathlib import Path
from decimal import Decimal
from copy import copy
from pydantic import Field
from .models import Model
from .validation import get_path, amount, quantity, rounded_adjustments
from .planning import totals, effective, layout_svg
from .ingestion import digest

TEMPLATES=Path(__file__).parents[1]/'templates'
TYPES={'customer':'客户草件','customs':'报关资料草稿','booking':'空运托书','awb':'提单确认草稿','delivery':'交货信息表','plan':'打托方案'}


class TemplateSpec(Model):
    id:str
    version:str
    type:str
    scope_mode:str
    source_path:str
    source_sha256:str
    original_source_sha256:str | None = None
    context_allowlist:list[str]
    field_map:dict[str,str]
    repeat_regions:list[dict]
    required_fields:list[str]
    dependency_paths:list[str]
    document_stage:str='draft'
    output_format:str
    sensitive_assets_reviewed:bool=False
    visual_checked:bool=False


def load_spec(kind,template_root=None):
    directory=Path(template_root) if template_root else TEMPLATES
    path=directory/(kind+'.json')
    if path.exists():return json.loads(path.read_text())
    return {'id':kind,'version':'1.0','type':kind,'scope_mode':'contract','source_path':'','source_sha256':'generated-v1',
            'context_allowlist':[],'field_map':{},'repeat_regions':[],'required_fields':['shipper.name_en','consignee.name_en','lines.*.batch_no','lines.*.base_quantity'],
            'dependency_paths':['lines','plan'],'document_stage':'draft','output_format':'xlsx','sensitive_assets_reviewed':True,'visual_checked':False}


def validate_spec(spec,template_root=None):
    directory=Path(template_root) if template_root else TEMPLATES
    s=TemplateSpec.model_validate(spec)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}',s.version) or '..' in s.version:raise ValueError('TEMPLATE_VERSION_INVALID')
    if s.type not in TYPES or s.output_format not in ('xlsx','docx'):raise ValueError('TEMPLATE_TYPE_INVALID')
    if set(s.field_map.values())-set(s.context_allowlist):raise ValueError('CONTEXT_NOT_ALLOWED')
    banned={'customer':'customs','customs':'customer','awb':'price','delivery':'price','plan':'price'}.get(s.type)
    if banned and any(banned in p for p in s.context_allowlist):raise ValueError('PRICE_ISOLATION')
    if s.source_path:
        p=(directory/s.source_path).resolve()
        if not p.is_relative_to(directory.resolve()) or p.suffix!='.'+s.output_format:raise ValueError('TEMPLATE_PATH_INVALID')
        if digest(p.read_bytes())!=s.source_sha256:raise ValueError('TEMPLATE_HASH_MISMATCH')
    return s.model_dump()


def text(value):return '' if value is None else str(value)
def kg(v):return None if v is None else Decimal(v)/1000
def party(p):return '\n'.join(text(p.get(k)) for k in ('name_en','address','registration_no','contact','phone','email') if p.get(k))
def money_text(a):return None if a['amount'] is None else a['currency']+' '+a['amount']


def build_context(s,plan,kind,scope):
    if kind not in TYPES:raise ValueError('DOCUMENT_TYPE_INVALID')
    selected=[l for l in s['lines'] if scope=='ALL' or l['contract_id']==scope or l['id']==scope]
    if not selected:raise ValueError('SCOPE_EMPTY')
    contracts=[c for c in s['contracts'] if c['id'] in {l['contract_id'] for l in selected}]
    if kind in ('customer','customs') and len(contracts)!=1:raise ValueError('CONTRACT_SCOPE_REQUIRED')
    c=contracts[0];common=c['common_fields'];transport=s['transport_details']
    pallets=[p for p in plan['pallets'] if p['line_id'] in {l['id'] for l in selected}]
    total=totals(pallets)
    ctx={'blank':'','business_no':s['business_no'],'revision':s['revision'],'scope':scope,
         'shipper':party(s['shipper']),'shipper_name':s['shipper']['name_en'],'shipper_cn':s['shipper']['name_cn'],
         'shipper_address':s['shipper']['address'],'consignee':party(s['consignee']),'notify':party(s['notify_party']),
         'manufacturer':party(s['manufacturer']),'origin':s['origin'],'destination':s['destination'],
         'transport_mode':s['transport_mode'],'trade_term':s['trade_term'],'payment_term':s['payment_term'],
         'planned_ship_date':s['planned_ship_date'],'remarks':s['remarks'],'contract_nos':' & '.join(text(x['contract_no']) for x in contracts),
         'invoice_no':c['invoice_no'],'invoice_date':c['invoice_date'],'invoice_date_label':'DATE: '+text(c['invoice_date']),
         'pallet_count':total['pallet_count'],'carton_count':total['carton_count'],'gross_kg':kg(total['gross_g']),'net_kg':kg(total['net_g']),
         'volume':total['volume_m3'],'packages':f'{len(pallets)} PALLET'+('S' if len(pallets)!=1 else '')+f' ({total["carton_count"]} CARTONS)',
         'quantity_text':'; '.join(f'{sum(l["base_quantity"] for l in selected if l["base_unit"]==u)} {u}' for u in sorted({text(l['base_unit']) for l in selected}) if all(l['base_quantity'] is not None for l in selected if l['base_unit']==u)),
         'moc_insurance':'MOC NO.: '+text(common.get('moc_no'))+'   INSURANCE NO.: '+text(common.get('insurance_no')),
         'marks':common.get('marks'),'draft_notice':'DRAFT / '+('ACTUAL MEASUREMENTS' if pallets and all(p.get('actual') for p in pallets) else 'ESTIMATED MEASUREMENTS'),
         'header_text':'出口货物托运单 DRAFT\n'+text(s['planned_ship_date'])+'\n托运单位：'+text(s['shipper']['name_cn'])+'\n地址：'+text(s['shipper']['address'])+'\n联系人：'+text(s['shipper']['contact'])+'  电话：'+text(s['shipper']['phone']),
         'country':common.get('origin_country'),'purchase_contract_nos':' & '.join(text(x['common_fields'].get('purchase_contract_no')) for x in contracts if x['common_fields'].get('purchase_contract_no'))}
    for key in ('split_transshipment','own_marks','warehouse_date','dangerous_class','insurance_scope','freight_note','salesperson','customs_registration'):

        if kind=='booking':ctx[key]=common.get(key)
    for key in ('awb_no','carrier','flight','flight_date','awb_date','freight_payment','destination_code','awb_currency','freight_code','other_charge_code','declared_carriage','declared_import_value','insurance','rate_note','chargeable_kg'):
        ctx[key]=transport.get(key)
    if kind=='customer':
        ctx['shipping_advice_header']=text(s['shipper']['name_en'])+'\nSHIPPING ADVICE - DRAFT'
        ctx['bank_account']=common.get('bank_account')
        ctx['bank_info']='\n'.join(text(common.get(k)) for k in ('bank_name','bank_account','bank_swift','bank_address') if common.get(k))
    lines=[]
    for l in selected:
        lp=[p for p in pallets if p['line_id']==l['id']];lt=totals(lp);pk=l['packing_spec']
        product='\n'.join(text(l.get(k)) for k in ('name_en','strength_text') if l.get(k))
        description=product+'\nBatch: '+text(l['batch_no'])+'\nMFG: '+text(l['mfg_date'])+'  EXP: '+text(l['exp_date'])
        sample_text='\n'.join(f'{r["name"]} | Batch: {text(r["batch_no"])} | {text(r["quantity"])} {text(r["unit"])}'+(' | FREE SAMPLE' if r['free_confirmed'] else '') for r in l['reference_samples'])
        row={'id':l['id'],'contract_id':l['contract_id'],'description':description,'description_cn':text(l['name_cn'])+'\n'+text(l['batch_no']),
             'quantity_text':str(l['base_quantity'])+' '+text(l['base_unit']) if l['base_quantity'] is not None else '',
             'quantity':l['base_quantity'],'base_unit':l['base_unit'],'carton_count':lt['carton_count'],
             'quantity_with_cartons':str(l['base_quantity'])+' '+text(l['base_unit'])+'\n'+str(lt['carton_count'])+' CARTONS',
             'packing_text':f'{text(l["units_per_inner"])} {text(l["base_unit"])}/INNER × {text(l["inners_per_carton"])} INNER/CARTON',
             'packages':f'{len(lp)} PALLET'+('S' if len(lp)!=1 else '')+f' ({lt["carton_count"]} CARTONS)',
             'gross_kg':kg(lt['gross_g']),'net_kg':kg(lt['net_g']),'volume':lt['volume_m3'],
             'batch_text':'BATCH NO.: '+text(l['batch_no']),'storage':l['storage_text'],'sample_text':sample_text,
             'net_text':'' if lt['net_g'] is None else str(kg(lt['net_g']))+' KG',
             'carton_net_text':'' if pk['carton_net_g'] is None else str(kg(pk['carton_net_g']))+' KG',
             'carton_gross_text':'' if pk['carton_gross_g'] is None else str(kg(pk['carton_gross_g']))+' KG',
             'hs_code':l['hs_code'],'blank':''}
        for group in (('customer','customs') if kind=='booking' else (kind,) if kind in ('customer','customs') else ()):
            contract=next(c for c in contracts if c['id']==l['contract_id'])
            price=l[group+'_price'];a=amount(l,group,contract[group+'_currency'],s.get('currency_decimals'))
            prefix=group+'_' if kind=='booking' else ''
            row[prefix+'price_text']=None if price['unit_price'] is None or price['currency'] is None else text(price['currency'])+' '+text(price['unit_price'])+'/'+text(price['pricing_unit'])
            row[prefix+'amount_text']=money_text(a)
            row[prefix+'amount']=a['amount']
        if kind=='booking':
            row['description']=text(l['name_cn'])+'\n'+row['description']
            row['packages']=f'{len(lp)} 托\n({lt["carton_count"]} 箱)'
            row['packing_text']=f'{text(l["units_per_inner"])} {text(l["base_unit"])} / 内包装\n{text(l["inners_per_carton"])} 内包装 / 箱'
        lines.append(row)
    ctx['lines']=lines;ctx['pallets']=pallets
    if kind=='plan':
        constraints=[]
        def add(label,value,path):
            if value is None:
                parent=get_path(s,path.rsplit('.',1)[0])
                if isinstance(parent,dict) and parent.get('availability',{}).get(path.rsplit('.',1)[1])=='not_applicable':
                    path=path.rsplit('.',1)[0]+'.availability.'+path.rsplit('.',1)[1];value='not_applicable'
            fact=s['facts'].get(path,{})
            ref=fact.get('source_ref',{})
            source=ref.get('manual_note') or ' / '.join(str(ref[k]) for k in ('file_id','locator') if ref.get(k))
            constraints.append([label,'待确认' if value is None else str(value),source or path,'已确认' if fact.get('confirmed') and fact.get('value')==value else '待确认'])
        labels={'outer_l_mm':'箱长 mm','outer_w_mm':'箱宽 mm','outer_h_mm':'箱高 mm','carton_gross_g':'箱毛重 g','max_layers':'最大层数','max_goods_height_mm':'货高上限 mm','max_superimposed_g':'底箱叠压 g','temp_min_c':'温度下限 ℃','temp_max_c':'温度上限 ℃'}
        for index,line in enumerate(s['lines']):
            if line not in selected:continue
            for key,label in labels.items():add(line['id']+' '+label,line['packing_spec'][key],f'lines.{index}.packing_spec.{key}')
        for index,leg in enumerate(s['route_legs']):
            for key,label in {'max_unit_height_mm':'含托限高 mm','max_unit_gross_g':'单件限重 g','clearance_mm':'净空 mm'}.items():add(leg['id']+' '+label,leg[key],f'route_legs.{index}.{key}')
            for key,value in (leg.get('vehicle_snapshot') or {}).items():add(leg['id']+' 车辆 '+key,value,f'route_legs.{index}.vehicle_snapshot.{key}')
        for index,pallet in enumerate(s['pallet_choices']):
            for key in ('length_mm','width_mm','height_mm','tare_g','max_payload_g'):add(pallet['name']+' '+key,pallet[key],f'pallet_choices.{index}.{key}')
        for key,value in s['extras'].items():add('辅材 '+key,value,'extras.'+key)
        ctx['constraints']=constraints
    ctx['awb_description']='; '.join(text(l['name_en'])+' '+text(l['strength_text'])+' / Batch '+text(l['batch_no']) for l in selected)+'\nHS CODE: '+'; '.join(text(l['hs_code']) for l in selected)+'\n'+ctx['packages']+'\nVOL: '+(format(total['volume_m3'],'.3f') if total['volume_m3'] is not None else 'PENDING')+' CBM\n'+ctx['draft_notice']
    if kind in ('customer','customs'):
        amounts=[r['amount'] for r in lines]
        precision=s.get('currency_decimals',{'EUR':2,'USD':2,'CNY':2}).get(c[kind+'_currency'])
        ctx['total_amount_text']=None if any(a is None for a in amounts) else c[kind+'_currency']+' '+format(sum(Decimal(a) for a in amounts)+sum((Decimal(a['amount']) for a in rounded_adjustments(c,kind,s['currency_decimals'])),Decimal(0)),f'.{precision}f')
        ctx['adjustments']=rounded_adjustments(c,kind,s['currency_decimals'])
        ctx['invoice_adjustments_text']='\n'.join(a['label']+': '+c[kind+'_currency']+' '+a['amount'] for a in ctx['adjustments'])
    return ctx


def write_cell(ws,cell,value):
    c=ws[cell]
    if c.__class__.__name__=='MergedCell':raise ValueError('WRITE_MERGED_NON_ANCHOR')
    if isinstance(value,Decimal):c.value=float(value) # money is precomputed; Excel numeric transport units only
    else:c.value=value
    if isinstance(value,str):c.data_type='s' # formula injection from user business text is inert


def fit_rows(ws):
    """Estimate wrapped text lines using the complete merged width, never shrink fonts."""
    import math
    for row in ws:
        for c in row:
            if not isinstance(c.value,str) or not c.value:continue
            merge=next((r for r in ws.merged_cells.ranges if r.min_row==c.row and r.min_col==c.column),None)
            first,last=(merge.min_col,merge.max_col) if merge else (c.column,c.column)
            from openpyxl.utils import get_column_letter
            def col_width(index):
                exact=ws.column_dimensions.get(get_column_letter(index))
                if exact is not None:return exact.width
                matching=[d for d in ws.column_dimensions.values() if (d.min or 0)<=index<=(d.max or d.min or 0)]
                return matching[-1].width if matching else (ws.sheet_format.defaultColWidth or 8.43)
            width=sum(col_width(i) for i in range(first,last+1))
            size=c.font.sz or 10
            usable=max(3,(width-2)*10/size)
            lines=sum(max(1,math.ceil(sum(2 if ord(ch)>255 else 1 for ch in line)/usable)) for line in c.value.split('\n'))
            needed=lines*size*1.28+6
            end=merge.max_row if merge else c.row
            current=sum(ws.row_dimensions[i].height or 15 for i in range(c.row,end+1))
            if needed>current:
                # Put additional height on final row of a vertical merge, preserving surrounding row anchors.
                ws.row_dimensions[end].height=(ws.row_dimensions[end].height or 15)+needed-current


def render(kind,ctx,out,spec=None,template_root=None):
    directory=Path(template_root) if template_root else TEMPLATES
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment
    spec=validate_spec(spec or load_spec(kind,template_root),template_root);out=Path(out)
    if not spec['sensitive_assets_reviewed']:raise ValueError('TEMPLATE_ASSETS_REVIEW_REQUIRED')
    if kind not in ('customer','awb','booking'):
        return render_generated(kind,ctx,out)
    if spec['output_format']=='docx':
        from docxtpl import DocxTemplate
        from jinja2.sandbox import SandboxedEnvironment
        from jinja2 import StrictUndefined
        env=SandboxedEnvironment(undefined=StrictUndefined,autoescape=True)
        env.globals.clear()
        doc=DocxTemplate(directory/spec['source_path'])
        allowed={k:ctx[k] for k in spec['context_allowlist'] if k in ctx}
        def clean(v):
            if v is None:return ''
            if isinstance(v,list):return [clean(x) for x in v]
            if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
            return v
        doc.render(clean(allowed),jinja_env=env,autoescape=True);doc.save(out)
        return
    wb=load_workbook(directory/spec['source_path'],keep_links=False)
    originals=list(wb.worksheets)
    sets=[originals]
    if kind=='customer':
        for i in range(1,len(ctx['lines'])):
            copies=[]
            for original in originals:
                ws=wb.copy_worksheet(original);ws.title=original.title+'_'+str(i+1);copies.append(ws)
            sets.append(copies)
    for i,sheets in enumerate(sets):
        local=dict(ctx,line=ctx['lines'][min(i,len(ctx['lines'])-1)])
        if kind=='customer' and i<len(sets)-1:
            local['invoice_adjustments_text']=''
            local['total_amount_text']=local['line']['amount_text']
            local['quantity_text']=local['line']['quantity_text']
            for key in ('packages','gross_kg','net_kg','volume'):local[key]=local['line'][key]
        lookup={original.title:ws for original,ws in zip(originals,sheets)}
        for address,path in spec['field_map'].items():
            name,cell=address.split('!');write_cell(lookup[name],cell,get_path(ctx if name=='Page 1' else local,path))
        for ws in sheets:
            ws.oddHeader.center.text=f'DRAFT | {i+1}/{len(sets)}'
            for row in ws:
                for c in row:
                    if c.value is not None:
                        a=copy(c.alignment);a.wrap_text=True;c.alignment=a
                        if isinstance(c.value,(int,float,Decimal)) and c.coordinate not in ('D22','C23'):c.number_format='0.000'
            if kind=='customer':
                if ws.title.startswith('发票'):
                    ws.row_dimensions[23].height=84;ws.row_dimensions[24].height=22
                    ws.row_dimensions[32].height=max(24,18*(local['line']['sample_text'].count('\n')+1))
                    write_cell(ws,'G25','TOTAL:' if i==len(sets)-1 else 'SUBTOTAL:')
                    ws.row_dimensions[14].height=max(32,ws.row_dimensions[14].height or 0)
                    ws.row_dimensions[16].height=max(32,ws.row_dimensions[16].height or 0)
                    ws.row_dimensions[21].height=max(40,ws.row_dimensions[21].height or 0)
                if ws.title.startswith('箱单'):
                    write_cell(ws,'D19','TOTAL:' if i==len(sets)-1 else 'SUBTOTAL:')
                    ws.row_dimensions[15].height=84
                    ws.row_dimensions[8].height=42
                    ws.row_dimensions[19].height=62
                    ws.row_dimensions[28].height=max(30,18*(local['line']['sample_text'].count('\n')+1))
                if ws.title.startswith('SHIPPING ADVICE'):
                    ws.row_dimensions[16].height=90;ws.row_dimensions[20].height=76
    for ws in wb:fit_rows(ws)
    wb.save(out)


def render_generated(kind,ctx,out):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.drawing.image import Image
    from PIL import Image as PILImage, ImageDraw, ImageFont
    wb=Workbook();ws=wb.active;ws.title=TYPES[kind]
    def table(sheet,headers,rows):
        sheet.append(headers)
        for row in rows:sheet.append([float(x) if isinstance(x,Decimal) else x for x in row])
        for c in sheet[1]:c.font=Font(name='Arial',size=11,bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='215D55');c.alignment=Alignment(wrap_text=True,vertical='center')
        sheet.row_dimensions[1].height=34
        for row in sheet.iter_rows(min_row=2):
            for c in row:
                c.font=Font(name='Arial',size=10);c.alignment=Alignment(vertical='top',wrap_text=True)
                if isinstance(c.value,str):c.data_type='s'
            sheet.row_dimensions[row[0].row].height=26
        for i,col in enumerate(sheet.columns):sheet.column_dimensions[col[0].column_letter].width=24 if i==0 else 20
        sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
        sheet.sheet_properties.pageSetUpPr.fitToPage=True;sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=sheet.PAPERSIZE_A3
        sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0;sheet.print_title_rows='1:1'
        sheet.oddHeader.center.text=TYPES[kind]+' / DRAFT';sheet.oddFooter.center.text='&P / &N'
    if kind=='plan':
        table(ws,['业务号','运输包装','净重 kg','毛重 kg','体积 m³','状态'],[[ctx['business_no'],ctx['packages'],ctx['net_kg'],ctx['gross_kg'],ctx['volume'],ctx['draft_notice']]])
        detail=wb.create_sheet('逐托明细')
        rows=[]
        for p in ctx['pallets']:
            e=effective(p);line=next(l for l in ctx['lines'] if l['id']==p['line_id']);rows.append([p['id'],line['description'],p['carton_count'],p['per_layer'],p['layers'],p['boxes_last_layer'],e['length_mm'],e['width_mm'],e['height_mm'],kg(e['net_g']),kg(e['gross_g']),totals([p])['volume_m3'],'ACTUAL' if p['actual'] else 'ESTIMATED'])
        table(detail,['托号','产品/批号/日期','箱数','每层箱数','层数','顶层箱数','长 mm','宽 mm','含托高 mm','净重 kg','毛重 kg','体积 m³','重量依据'],rows)
        diagrams=wb.create_sheet('摆放与高度')
        diagrams.column_dimensions['A'].width=110
        for i,p in enumerate(ctx['pallets']):
            image=PILImage.new('RGB',(920,520),'white');draw=ImageDraw.Draw(image)
            font=ImageFont.load_default(size=21)
            L=p['pallet_spec_snapshot']['length_mm'];W=p['pallet_spec_snapshot']['width_mm'];scale=min(350/L,280/W)
            draw.text((20,10),f'{p["id"]} | {p["per_layer"]}/layer | {p["layers"]} layers | top {p["boxes_last_layer"]}',fill='black',font=font)
            for origin,boxes,title in ((20,p['layout'],'FULL LAYER'),(460,p['top_layout'],'TOP LAYER')):
                draw.text((origin,52),title,fill='#215d55',font=font)
                draw.rectangle((origin,85,origin+L*scale,85+W*scale),outline='#777777',width=2)
                for b in boxes:
                    x,y=origin+b['x_mm']*scale,85+b['y_mm']*scale
                    draw.rectangle((x,y,x+b['w_mm']*scale-3,y+b['d_mm']*scale-3),fill='#b9d6ce',outline='#215d55',width=2)
            h=effective(p)['height_mm'];draw.text((20,390),f'L x W: {L} x {W} mm | HEIGHT INCLUDING PALLET: {h} mm',fill='black',font=font)
            base=485;sh=min(80/(h or 1),.1);top=base-(h or 0)*sh
            draw.rectangle((730,top,880,base),fill='#b9d6ce',outline='#215d55',width=2)
            for layer in range(p['layers']+1):
                y=base-(p['pallet_spec_snapshot']['height_mm']+layer*p['box_height_mm'])*sh
                draw.line((730,y,880,y),fill='#215d55',width=1)
            draw.text((20,440),'SCHEMATIC - dimensions govern; no mixing or overhang',fill='#555555',font=font)
            stream=io.BytesIO();image.save(stream,format='PNG');stream.seek(0)
            img=Image(stream);img.width=920;img.height=520;diagrams.add_image(img,f'A{i*29+1}')
            for row in range(i*29+1,(i+1)*29+1):diagrams.row_dimensions[row].height=15
        diagrams.print_area=f'A1:M{max(29,29*len(ctx["pallets"]))}'
        from openpyxl.worksheet.pagebreak import Break
        for i in range(1,len(ctx['pallets'])):diagrams.row_breaks.append(Break(id=i*29))
        diagrams.sheet_properties.pageSetUpPr.fitToPage=True;diagrams.page_setup.orientation='landscape';diagrams.page_setup.fitToWidth=1;diagrams.page_setup.fitToHeight=0
        sources=wb.create_sheet('约束来源');table(sources,['约束项目','本单值','来源/字段定位','确认'],ctx['constraints']);sources.column_dimensions['A'].width=44;sources.column_dimensions['C'].width=65
    else:
        headers=['合同','产品/批号/日期','本次数量','单位','包装','净重 kg','毛重 kg','体积 m³','HS CODE']
        if kind=='customs':headers+=['报关单价','报关金额']
        rows=[]
        for l in ctx['lines']:
            row=[l['contract_id'],l['description'],l['quantity'],l['base_unit'],l['packages'],l['net_kg'],l['gross_kg'],l['volume'],l['hs_code']]
            if kind=='customs':row+=[l['price_text'],l['amount_text']]
            rows.append(row)
        table(ws,headers,rows)
        info=wb.create_sheet('交货信息' if kind=='delivery' else '申报草稿信息')
        table(info,['字段','本单数据'],[['业务号',ctx['business_no']],['合同',ctx['contract_nos']],['发货人',ctx['shipper']],['收货人',ctx['consignee']],['工厂',ctx['manufacturer']],['起运地',ctx['origin']],['目的地',ctx['destination']],['运输包装',ctx['packages']],['毛重 kg',ctx['gross_kg']],['体积 m³',ctx['volume']],['贸易条款',ctx['trade_term']],['状态',ctx['draft_notice']]])
        info.column_dimensions['B'].width=80
        info.page_setup.orientation='portrait';info.page_setup.paperSize=info.PAPERSIZE_A4
        for row in range(2,info.max_row+1):info.row_dimensions[row].height=26
        if kind=='customs':
            for a in ctx['adjustments']:info.append(['调整：'+a['label'],ctx['total_amount_text'].split()[0]+' '+a['amount']])
            info.append(['报关总额',ctx['total_amount_text']])
            for row in info.iter_rows(min_row=15):
                for cell in row:
                    cell.font=Font(name='Arial',size=10);cell.alignment=Alignment(vertical='top',wrap_text=True)
                    if isinstance(cell.value,str):cell.data_type='s'
                info.row_dimensions[row[0].row].height=26
            info.auto_filter.ref=info.dimensions
    for sheet in wb:
        if sheet.title!='摆放与高度':fit_rows(sheet)
    wb.save(out)
