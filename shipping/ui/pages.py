import json,uuid,os,base64
from copy import deepcopy
from pathlib import Path
import streamlit as st
import pandas as pd
from decimal import Decimal
from shipping.models import convert_unit
from shipping.expectations import observation_fields
from shipping.models import Shipment,ShipmentLine,Contract,PalletSpec,RouteLeg,Vehicle,PackingSpec,leaves
from shipping.demo import synthetic
from shipping.validation import validate_shipment,get_path
from shipping.planning import layout_svg,effective,layouts,totals
from shipping.templating import TYPES,load_spec,validate_spec,TEMPLATES
from shipping.versioning import diff
from shipping.extraction import CompatibleChatProvider,extract_candidates,pdf_page_images

from shipping.ui.design import PAGES,NAV,theme,brand,header,overview,compact_summary,section
LABELS={'name_cn':'中文名称','name_en':'英文名称','address':'地址','contact':'联系人','phone':'电话','email':'邮箱','registration_no':'登记号',
        'origin':'起运地','destination':'目的地','transport_mode':'运输方式','trade_term':'贸易条款','payment_term':'付款条款','planned_ship_date':'预计出运日期','remarks':'备注',
        'product_code':'产品代码','strength_text':'药品规格（文字）','batch_no':'批号','mfg_date':'生产日期','exp_date':'有效日期','base_unit':'基础单位','base_quantity':'基础数量',
        'units_per_inner':'每内包装基础数量','inners_per_carton':'每箱内包装数','full_cartons':'满箱箱数','hs_code':'HS 编码','storage_text':'储存条件',
        'outer_l_mm':'外箱长 mm','outer_w_mm':'外箱宽 mm','outer_h_mm':'外箱高 mm','carton_net_g':'单箱净重 g','carton_gross_g':'单箱毛重 g',
        'max_layers':'最大堆叠层数','max_goods_height_mm':'最大货物高度 mm','max_superimposed_g':'单箱最大上压重量 g','temp_min_c':'最低温度 ℃','temp_max_c':'最高温度 ℃',
        'length_mm':'托盘长 mm','width_mm':'托盘宽 mm','height_mm':'托盘高 mm','tare_g':'托盘自重 g','max_payload_g':'托盘有效载荷 g',
        'extra_height_mm':'附加总高度 mm','extra_length_mm':'附加总长度 mm','extra_width_mm':'附加总宽度 mm','auxiliary_g':'辅材重量 g',
        'max_unit_height_mm':'单托含托限高 mm','max_unit_gross_g':'单托毛重限额 g','clearance_mm':'净空 mm',
        'usable_l_mm':'车厢可用长 mm','usable_w_mm':'车厢可用宽 mm','usable_h_mm':'车厢可用高 mm','door_w_mm':'门洞宽 mm','door_h_mm':'门洞高 mm','payload_g':'车辆载重 g'}


def jdump(value):return json.dumps(value,ensure_ascii=False,indent=2,default=str)
def scoped_key(key):return str(st.session_state.get('_ui_scope',''))+':'+str(key)
def txt(label,value=None,key=None):return st.text_input(label,value='' if value is None else str(value),key=scoped_key(key)) or None
def number(label,value=None,key=None):
    units=st.session_state.get('_input_units',{})
    kind='dimension' if label.endswith(' mm') else 'weight' if label.endswith(' g') else None
    if kind:
        unit=units.get(kind,'mm' if kind=='dimension' else 'g')
        factor={'mm':1,'cm':10,'m':1000,'g':1,'kg':1000}[unit]
        raw=txt(label.rsplit(' ',1)[0]+' '+unit,None if value is None else str(Decimal(value)/factor),str(key)+unit)
        return None if raw is None else convert_unit(raw,unit,kind)
    return st.number_input(label,min_value=0,value=None if value is None else int(value),step=1,key=scoped_key(key))
def save(w,s):
    revision=w.save_shipment(s,s['revision']);st.success(f'已保存 v{revision}');st.rerun()
def edit_json(label,value,key,height=160):return st.text_area(label,jdump(value),height=height,key=scoped_key(key))


COMMON_LABELS={'moc_no':'MOC 编号','insurance_no':'保险编号','marks':'唛头','bank_name':'银行名称','bank_account':'银行账户','bank_swift':'SWIFT','bank_address':'银行地址','purchase_contract_no':'采购合同号','insurance_scope':'保险范围','salesperson':'业务员','customs_registration':'海关登记号','dangerous_class':'货物危险属性','origin_country':'原产国','freight_note':'运费说明','warehouse_date':'入仓日期','own_marks':'自备唛头','split_transshipment':'分批 / 转运'}
LABELS.update(COMMON_LABELS)
LABELS.update({'inner_unit':'内包装单位（如 BOTTLE）','invoice_no':'发票号','invoice_date':'发票日期','contract_no':'合同号','gross_g':'含托毛重 g','net_g':'净重 g','pallet_count':'托数','carton_count':'箱数','volume_m3':'体积 m³','total_amount':'合计金额','unit_price':'单价','currency':'币种','pricing_unit':'计价单位','base_units_per_pricing_unit':'计价换算数','pricing_basis':'计价层级','conversion_note':'特殊换算依据','id':'编号','business_no':'业务号','allow_rotate_90':'允许水平旋转','confirmed':'已确认','availability':'参数适用性','customer_currency':'客户币种','customs_currency':'报关币种','constraints_confirmed':'运输限制已确认','height_includes_pallet':'限高含托','integer_units':'计价数量须为整数','quantity':'数量','name':'名称','unit':'单位','free_confirmed':'免费已确认','included_in_carton_gross':'已含箱毛重','packed_carton_id':'所在箱号','separate_line_id':'独立货物行号','amount':'金额','label':'项目','manual_note':'确认依据','allowed_pallet_ids':'允许使用的托盘','packing_extras':'本行辅材'})
PREFIXES={'shipper':'发货人','consignee':'收货人','manufacturer':'工厂','notify_party':'通知人','customer_price':'客户价格','customs_price':'报关价格','packing_spec':'箱规','pallet_choices':'候选托盘','route_legs':'运输段','extras':'整批辅材','totals':'整批汇总','plan':'方案','pallets':'逐托','prices':'金额','customer':'客户','customs':'报关','common_fields':'合同附加资料','transport_details':'运输资料','vehicle_snapshot':'车辆','reference_samples':'对照品','partial_cartons':'尾箱'}
ROLES={'unclassified':'尚未分类','contract':'合同','finished_coa':'成品 COA','reference_coa':'对照品 COA','purchase_invoice':'采购发票','customer_pdf':'客户 PDF','customs_pdf':'报关 PDF','carrier_return':'货代回件','factory_original':'工厂原件','transport_report':'运输报告'}

def scope_options(s,blank=False):return ([''] if blank else [])+['ALL']+[c['id'] for c in s['contracts']]+[l['id'] for l in s['lines']]
def scope_label(s,x):
    if x=='ALL':return '整批货物'
    if not x:return '待关联'
    for c in s['contracts']:
        if c['id']==x:return '合同：'+str(c['contract_no'] or x)
    for l in s['lines']:
        if l['id']==x:return '货物：'+str(l['name_cn'] or l['name_en'] or x)+' / '+str(l['batch_no'] or '待填批号')
    return x

def field_label(s,path):
    parts=path.split('.');out=[]
    if len(parts)>2 and parts[0] in ('lines','contracts') and parts[1].isdigit():
        group=parts.pop(0);index=int(parts.pop(0))
        item=s[group][index] if index<len(s[group]) else {}
        out.append(('货物 '+str(index+1)+' '+str(item.get('name_cn') or item.get('name_en') or '') if group=='lines' else '合同 '+str(item.get('contract_no') or index+1)))
    out.extend(LABELS.get(k,PREFIXES.get(k,str(int(k)+1) if k.isdigit() else k)) for k in parts)
    return ' / '.join(out)

def unit_controls(prefix):
    a,b=st.columns(2)
    with a:dim=st.selectbox('尺寸输入单位',['mm','cm','m'],key=prefix+'dimension')
    with b:weight=st.selectbox('重量输入单位',['g','kg'],key=prefix+'weight')
    st.session_state['_input_units']={'dimension':dim,'weight':weight}

def grid(label,rows,columns,key):
    st.caption(label)
    frame=pd.DataFrame(rows,columns=list(columns))
    # Keep integer columns nullable; blank cells remain unknown rather than becoming zero.
    for k in columns:
        if k in ('base_quantity','quantity','outer_l_mm','outer_w_mm','outer_h_mm','net_g','gross_g','max_layers','max_goods_height_mm','max_superimposed_g'):
            frame[k]=pd.array(frame[k],dtype='Int64')
        elif k in ('free_confirmed','included_in_carton_gross'):frame[k]=pd.array(frame[k],dtype='boolean')
    result=st.data_editor(frame,column_config={k:v for k,v in columns.items()},num_rows='dynamic',hide_index=True,key=scoped_key(key),width='stretch')
    return [{k:(None if pd.isna(v) else v.item() if hasattr(v,'item') else v) for k,v in row.items()} for row in result.to_dict('records')]

def adjustments_editor(items,key):
    rows=[{'label':a['label'],'amount':str(a['amount']),'reason':a['evidence'].get('manual_note') or ' / '.join(str(a['evidence'].get(k) or '') for k in ('file_id','locator'))} for a in items]
    result=grid('调整明细（运费填正数，折扣填负数；金额以文本保留十进制精度）',rows,{'label':'项目','amount':'金额','reason':'依据'},key)
    return [{'label':r['label'],'amount':r['amount'],'evidence':(items[i]['evidence'] if i<len(items) and r['reason']==rows[i]['reason'] else {'manual_note':r['reason']})} for i,r in enumerate(result) if any(v is not None for v in r.values())]


def run(w):
    theme()
    records=w.repo.list_shipments()
    from shipping.ui.agent import page as agent_page
    with st.sidebar:
        brand()
        st.button('单证工作台',key='nav_agent_home',width='stretch',on_click=lambda:st.session_state.update(nav_page='自动出单'))
        st.button('设置',key='nav_agent_settings',width='stretch',on_click=lambda:st.session_state.update(nav_page='设置'))
        with st.expander('打托与历史工具',expanded=st.session_state.get('nav_page','自动出单') not in ('自动出单','设置')):
            with st.expander('＋ 新建批次',expanded=not records):
                with st.form('new_shipment'):
                    business=st.text_input('业务号',placeholder='例如 SHIP-2026-001')
                    if st.form_submit_button('新建空白批次',type='primary'):
                        if business.strip():
                            s=Shipment(id=uuid.uuid4().hex,business_no=business).model_dump(mode='json');w.save_shipment(s,0)
                            st.session_state['nav_page']='资料与基础数据';st.session_state['selected_shipment']=s['id'];st.rerun()
                        else:st.error('请填写业务号')
                if st.button('加载双合同合成案例',key='load_demo',width='stretch'):
                    s=synthetic(True);s['id']='DEMO-'+uuid.uuid4().hex[:8];w.save_shipment(s,0)
                    st.session_state['selected_shipment']=s['id'];st.session_state['nav_page']='工作概览';st.rerun()
            if records:
                sid=st.selectbox('当前出运批次',[r['id'] for r in records],format_func=lambda x:w.repo.load(x)['business_no'],key='selected_shipment')
            st.markdown('<div class="nav-caption">WORKSPACE / 工作空间</div>',unsafe_allow_html=True)
            page=st.radio('工作流程',['自动出单']+PAGES,format_func=lambda x:'✧   自动出单' if x=='自动出单' else NAV[x],key='nav_page',label_visibility='collapsed')
        st.markdown('<div class="sidebar-bottom"><b>● 本地工作空间</b><span>3.3</span><br>资料保留在这台电脑</div>',unsafe_allow_html=True)
    if page=='自动出单':
        try:agent_page(w.repo)
        except Exception as e:st.error(str(e))
        return
    if page=='设置':
        from shipping.ui.agent import settings_page
        settings_page(w.repo)
        if records:
            with st.expander('旧工作台高级设置'):
                s=Shipment.model_validate(w.repo.load(sid)).model_dump(mode='json')
                settings(w,s,w.current_plan(sid))
        return
    if not records:
        st.markdown('<div class="page-title"><div><h1>开始你的第一批出运</h1><p>建立批次，整理资料，再完成打托与出单。</p></div></div>',unsafe_allow_html=True)
        with st.container(border=True):
            section('从一份清晰的货物资料开始')
            st.write('在左侧填写业务号新建批次，或者先加载合成案例体验完整流程。')
            st.caption('没有 AI 密钥也能使用。演示数据仅供软件试用。')
        return
    s=Shipment.model_validate(w.repo.load(sid)).model_dump(mode='json');plan=w.current_plan(sid)
    st.session_state['_ui_scope']=sid+':'+str(s['revision'])
    header(s,page)
    try:
        if page=='工作概览':overview(w,s,plan);return
        compact_summary(s,plan,validate_shipment(s))
        {'资料与基础数据':basic,'包装与运输':packing,'打托方案':planning,'单证生成':documents,'核对与版本':review,'设置':settings}[page](w,s,plan)
    except Exception as e:st.error(str(e))


def basic(w,s,plan):
    tabs=st.tabs(['主数据','合同与货物','来源确认','导入资料'])
    with tabs[0]:
        with st.form('parties'):
            value=deepcopy(s)
            for key,title in [('shipper','发货人'),('consignee','收货人'),('notify_party','通知人'),('manufacturer','工厂')]:
                with st.expander(title,expanded=key=='shipper'):
                    cols=st.columns(2)
                    for i,k in enumerate(value[key]):
                        with cols[i%2]:value[key][k]=txt(LABELS[k],value[key][k],key=key+k)
            section('本次运输')
            cols=st.columns(3)
            for i,key in enumerate(('origin','destination','transport_mode','trade_term','payment_term','planned_ship_date')):
                with cols[i%3]:value[key]=txt(LABELS[key],value[key],key=key)
            value['remarks']=txt(LABELS['remarks'],value['remarks'],key='remarks')
            if st.form_submit_button('保存基础数据',type='primary'):save(w,value)
    with tabs[1]:
        a,b=st.columns(2)
        with a:
            if st.button('新增合同'):
                value=deepcopy(s);value['contracts'].append(Contract(id='C-'+uuid.uuid4().hex[:8]).model_dump(mode='json'));save(w,value)
        with b:
            if s['contracts'] and st.button('新增货物行'):
                value=deepcopy(s);value['lines'].append(ShipmentLine(id='L-'+uuid.uuid4().hex[:8],contract_id=s['contracts'][0]['id']).model_dump(mode='json'));save(w,value)
        if s['contracts']:
            index=st.selectbox('编辑合同',range(len(s['contracts'])),format_func=lambda i:s['contracts'][i]['contract_no'] or s['contracts'][i]['id'])
            with st.form('contract'):
                value=deepcopy(s);contract=value['contracts'][index]
                section('合同与发票信息')
                cols=st.columns(3)
                for i,(k,label) in enumerate([('contract_no','合同号'),('invoice_no','发票号'),('invoice_date','发票日期'),('customer_currency','客户币种'),('customs_currency','报关币种')]):
                    with cols[i%3]:contract[k]=txt(label,contract[k],key='contract'+contract['id']+k)
                order=grid('合同订购总量；没有确认的产品可暂不填写',[{'product_code':k,'base_quantity':v} for k,v in contract['ordered_quantities'].items()],{'product_code':'产品代码','base_quantity':'合同基础数量'},'ordered'+contract['id'])
                with st.expander('MOC、银行、保险及其他合同资料'):
                    common={};cols=st.columns(3)
                    for i,(k,label) in enumerate(COMMON_LABELS.items()):
                        with cols[i%3]:common[k]=txt(label,contract['common_fields'].get(k),key='common'+contract['id']+k)
                st.write('客户金额调整');cust=adjustments_editor(contract['customer_adjustments'],'cust_adj'+contract['id'])
                st.write('报关金额调整');customs=adjustments_editor(contract['customs_adjustments'],'custom_adj'+contract['id'])
                if st.form_submit_button('保存合同',type='primary'):
                    contract.update(ordered_quantities={r['product_code']:r['base_quantity'] for r in order if r['product_code']},common_fields=common,customer_adjustments=cust,customs_adjustments=customs);save(w,value)
        if s['lines']:
            index=st.selectbox('编辑货物',range(len(s['lines'])),format_func=lambda i:(s['lines'][i]['name_cn'] or s['lines'][i]['id'])+' / '+str(s['lines'][i]['batch_no']))
            with st.form('line'):
                value=deepcopy(s);line=value['lines'][index]
                line['contract_id']=st.selectbox('所属合同',[c['id'] for c in s['contracts']],index=[c['id'] for c in s['contracts']].index(line['contract_id']))
                cols=st.columns(2)
                for i,k in enumerate(('name_cn','name_en','product_code','strength_text','batch_no','mfg_date','exp_date','base_unit','inner_unit','hs_code','storage_text')):
                    with cols[i%2]:line[k]=txt(LABELS[k],line[k],key='line'+line['id']+k)
                section('数量与包装关系')
                cols=st.columns(4)
                for i,k in enumerate(('base_quantity','units_per_inner','inners_per_carton','full_cartons')):
                    with cols[i]:line[k]=number(LABELS[k],line[k],key='line'+line['id']+k)
                for group,label in [('customer','客户价格'),('customs','报关价格')]:
                    st.write(label);p=line[group+'_price'];cols=st.columns(3)
                    for i,(k,title) in enumerate([('unit_price','单价（十进制）'),('currency','币种'),('pricing_unit','计价单位文字')]):
                        with cols[i]:p[k]=txt(title,p[k],key=line['id']+group+k)
                    bases={'auto':'按单位文字识别','base':'每基础单位','inner':'每内包装','carton':'每箱','custom':'特殊换算（须有依据）'}
                    p['pricing_basis']=st.selectbox('价格对应哪个包装层级',list(bases),index=list(bases).index(p.get('pricing_basis','auto')),format_func=bases.get,key=scoped_key(line['id']+group+'basis'))
                    custom_factor=number('特殊换算：每计价单位基础数',p['base_units_per_pricing_unit'],key=line['id']+group+'factor')
                    p['conversion_note']=txt('特殊换算依据',p.get('conversion_note'),key=line['id']+group+'reason')
                    factors={'base':1,'inner':line['units_per_inner'],'carton':line['units_per_inner']*line['inners_per_carton'] if line['units_per_inner'] and line['inners_per_carton'] else None}
                    p['base_units_per_pricing_unit']=factors.get(p['pricing_basis'],custom_factor)
                    st.caption('选择基础 / 内包装 / 箱时，保存会按包装数量自动换算；特殊换算需另行确认依据。')
                samples=grid('随货对照品',line['reference_samples'],{'name':'名称','batch_no':'批号','quantity':'数量','unit':'单位','free_confirmed':'免费已确认','included_in_carton_gross':'含在箱毛重内','packed_carton_id':'所在箱号','separate_line_id':'独立货物行号'},'samples'+line['id'])
                if st.form_submit_button('保存货物与价格',type='primary'):
                    for item in samples:
                        for k in ('free_confirmed','included_in_carton_gross'):item[k]=bool(item[k])
                    line['reference_samples']=samples;save(w,value)
    with tabs[2]:
        paths={p:v for p,v in leaves(s) if v is not None and not p.startswith(('facts','audit','revision','schema_version'))}
        pending=[p for p,v in paths.items() if not s['facts'].get(p,{}).get('confirmed') or s['facts'].get(p,{}).get('value')!=v]
        st.write(f'有 {len(pending)} 个字段等待来源确认。点选后记录确认依据。')
        all_fields=st.checkbox('我已逐项核实，选择全部待确认字段',key=scoped_key('all_pending'))
        selected=st.multiselect('待确认字段',pending,default=pending if all_fields else [],format_func=lambda p:field_label(s,p)+' = '+str(paths[p])[:70],key=scoped_key('pending'+str(all_fields)))
        note=st.text_input('确认依据 / 人工覆盖原因',key='source_note')
        if st.button('确认所选字段'):
            if not selected:st.info('先选择字段')
            else:w.confirm_fields(s['id'],selected,note,s['revision']);st.rerun()
        with st.expander('查看字段来源'):
            path=st.selectbox('字段',list(paths),format_func=lambda p:field_label(s,p))
            st.json(s['facts'].get(path,{'value':paths[path],'confirmed':False,'source_ref':None}))
    with tabs[3]:
        st.info('资料导入已统一到“自动出单”：把本票文件放在一起，点击“识别并填写”即可。')
        st.button('前往统一资料入口',key='legacy_go_agent',type='primary',on_click=lambda:st.session_state.update(nav_page='自动出单'))
        with st.expander('旧版手工归档与字段提取（高级）'):
            imports(w,s)


def imports(w,s):
    uploaded=st.file_uploader('选择资料或 ZIP',accept_multiple_files=True,type=['zip','xlsx','docx','pdf','png','jpg','jpeg','rar'])
    role=st.selectbox('文件角色',list(ROLES),format_func=ROLES.get)
    scope=st.selectbox('归属范围',scope_options(s,True),format_func=lambda x:scope_label(s,x))
    if st.button('导入所选资料') and uploaded:
        files=w.import_files(s['id'],[(f.name,f.getvalue()) for f in uploaded],role,scope);st.success(f'已登记 {len(files)} 个文件；相同内容自动去重')
    files=w.repo.files(s['id'])
    if not files:return
    selected=st.selectbox('查看文件',range(len(files)),format_func=lambda i,files=files:files[i]['name'])
    f=files[selected];st.caption(f"状态 {f['status']} · 角色 {f['linked_role']} · 归属 {f['linked_scope'] or '未关联'}")
    if st.button('保存本文件角色与归属'):
        with w.repo.connect() as con:
            con.execute('DELETE FROM file_links WHERE file_id=? AND shipment_id=? AND role=? AND scope=?',(f['id'],s['id'],f['linked_role'],f['linked_scope']))
            con.execute('INSERT OR IGNORE INTO file_links VALUES(?,?,?,?)',(f['id'],s['id'],role,scope))
        w.list_impacted_documents(s['id']);st.rerun()
    if st.button('本地提取 / 查看来源位置'):w.parse_file(f['id']);st.rerun()
    parsed=w.repo.cache_get('local-v1:'+f['id'])
    if parsed:
        st.dataframe(parsed['entries'],hide_index=True,width='stretch')
        if f['name'].lower().endswith('.pdf'):
            with st.expander('PDF 原件预览'):
                uri='data:application/pdf;base64,'+base64.b64encode(w.repo.file_bytes(f['id'])).decode()
                st.components.v1.html(f'<iframe src="{uri}" width="100%" height="640"></iframe>',height=650)
        with st.expander('可选 AI 提取（明确发送后才调用）'):
            ai=w.repo.cache_get('ai-settings') or {}
            st.caption('密钥仅从 SHIPPING_AI_API_KEY 环境变量读取。提取结果是候选，不会覆盖主数据。')
            locators=st.multiselect('发送哪些段落 / 页 / 单元格',[e['locator'] for e in parsed['entries']])
            target=st.multiselect('希望提取的字段',observation_fields(s,w.current_plan(s['id']),f['linked_scope']),format_func=lambda p:field_label(s,p))
            image_nums=st.text_input('扫描 PDF 选中页图（例如 1,2；最多3页）','') if f['name'].lower().endswith('.pdf') else ''
            st.write('将发送：',f['name'],locators,('页图 '+image_nums) if image_nums else '')
            send=st.checkbox('我确认向已配置的外部模型发送这些资料')
            if st.button('发送并提取候选'):
                if not ai.get('endpoint') or not ai.get('model'):raise ValueError('请先在设置中配置接口地址和模型')
                images=pdf_page_images(w.repo.file_bytes(f['id']),[int(x.strip()) for x in image_nums.split(',') if x.strip()]) if image_nums else None
                provider=CompatibleChatProvider(ai['endpoint'],ai['model'])
                value=extract_candidates(w.repo,f['id'],f['linked_role'],f['linked_scope'],locators,target,provider,ai['model'],send,images)
                st.session_state['candidate:'+f['id']]=value
            if 'candidate:'+f['id'] in st.session_state:
                candidate=st.session_state['candidate:'+f['id']];st.dataframe([{'字段':field_label(s,o['field_path']),'候选值':str(o['value']),'来源':str(o['source_ref'])} for o in candidate['result']['observations']],hide_index=True)
                if st.button('将候选加入待核验列表'):
                    for o in candidate['result']['observations']:w.add_observation(s['id'],o)
                    st.success('已加入核对记录，未修改主数据')


def packing(w,s,plan):
    unit_controls('packing_units')
    tabs=st.tabs(['外箱与尾箱','托盘与辅材','运输段与车辆'])
    with tabs[0]:
        if not s['lines']:st.info('先添加货物行');return
        index=st.selectbox('货物行',range(len(s['lines'])),format_func=lambda i:s['lines'][i]['name_cn'] or s['lines'][i]['id'])
        saved_boxes=w.repo.cache_get('packing-library') or []
        if saved_boxes:
            chosen=st.selectbox('从箱规库复制',range(len(saved_boxes)),format_func=lambda i:f'箱规 {i+1}: '+str(saved_boxes[i]['outer_l_mm'])+'×'+str(saved_boxes[i]['outer_w_mm']))
            if st.button('应用此箱规快照'):
                value=deepcopy(s);value['lines'][index]['packing_spec']=deepcopy(saved_boxes[chosen]);save(w,value)
        with st.form('packing'):
            value=deepcopy(s);line=value['lines'][index];p=line['packing_spec'];cols=st.columns(3)
            for i,k in enumerate(('outer_l_mm','outer_w_mm','outer_h_mm','carton_net_g','carton_gross_g','max_layers','max_goods_height_mm','max_superimposed_g')):
                with cols[i%3]:p[k]=number(LABELS[k],p[k],key='packing'+line['id']+k)
            p['allow_rotate_90']=st.checkbox('允许水平旋转90°',p['allow_rotate_90'],key=scoped_key('rotate'+line['id']))
            for k in ('temp_min_c','temp_max_c'):p[k]=txt(LABELS[k],p[k],key='packing'+line['id']+k)
            partial=grid('尾箱（表内尺寸统一 mm、重量 g；非整箱必须独立填写）',[{**{k:v for k,v in c.items() if k!='stack_rule'},**c['stack_rule']} for c in line['partial_cartons']],{'id':'箱号','base_quantity':'基础数量','outer_l_mm':'长 mm','outer_w_mm':'宽 mm','outer_h_mm':'高 mm','net_g':'净重 g','gross_g':'毛重 g','max_layers':'最大层数','max_goods_height_mm':'最大货高 mm','max_superimposed_g':'叠压上限 g'},'partial'+line['id'])
            line['allowed_pallet_ids']=st.multiselect('本货物允许使用的托盘（不选表示全部候选）',[p['id'] for p in s['pallet_choices']],default=line.get('allowed_pallet_ids',[]),format_func=lambda x:next(p['name'] for p in s['pallet_choices'] if p['id']==x),key=scoped_key('allowed'+line['id']))
            if st.form_submit_button('保存包装参数',type='primary'):
                line['partial_cartons']=[{**{k:v for k,v in c.items() if k not in ('max_layers','max_goods_height_mm','max_superimposed_g')},'stack_rule':{k:c[k] for k in ('max_layers','max_goods_height_mm','max_superimposed_g')}} for c in partial];save(w,value)
        use_extras=st.checkbox('此货物单独设置辅材',line.get('packing_extras') is not None,key=scoped_key('line_extras'+line['id']))
        with st.form('line_extras'):
            value=deepcopy(s);extra=deepcopy(line.get('packing_extras') or s['extras'])
            if use_extras:
                for k in s['extras']:extra[k]=number(LABELS[k],extra.get(k),key='lineextra'+line['id']+k)
            if st.form_submit_button('保存本货物辅材设置'):
                value['lines'][index]['packing_extras']=extra if use_extras else None;save(w,value)
    with tabs[1]:
        defaults=json.loads((Path(__file__).parents[2]/'config/defaults.json').read_text())
        profiles=w.repo.cache_get('pallet-library') or defaults['pallet_presets']
        preset=st.selectbox('从托盘库复制',range(len(profiles)),format_func=lambda i:profiles[i]['name'])
        if st.button('添加托盘快照'):
            value=deepcopy(s);p=deepcopy(profiles[preset]);p['id']+='-'+uuid.uuid4().hex[:4];value['pallet_choices'].append(p);save(w,value)
        for index,pallet in enumerate(s['pallet_choices']):
            with st.form('pallet'+str(index)):
                value=deepcopy(s);p=value['pallet_choices'][index];p['name']=txt('托盘名称',p['name'],key='pname'+str(index))
                cols=st.columns(3)
                for i,k in enumerate(('length_mm','width_mm','height_mm','tare_g','max_payload_g')):
                    with cols[i%3]:p[k]=number(LABELS[k],p[k],key=str(index)+k)
                remove=st.checkbox('删除这个候选托盘',key=scoped_key('remove'+str(index)))
                if st.form_submit_button('保存此托盘'):
                    if remove:value['pallet_choices'].pop(index)
                    save(w,value)
        with st.form('extras'):
            value=deepcopy(s)
            for k in s['extras']:value['extras'][k]=number(LABELS[k],value['extras'][k],key=k)
            st.caption('附加尺寸填写总增量；确认没有则填 0。留空会阻止方案确认。')
            if st.form_submit_button('保存辅材'):save(w,value)
    with tabs[2]:
        defaults=json.loads((Path(__file__).parents[2]/'config/defaults.json').read_text())
        presets=w.repo.cache_get('route-presets') or [{k:v for k,v in p.items() if k!='source'} for p in defaults['route_presets']]
        selected_preset=st.selectbox('业务限高预设',range(len(presets)),format_func=lambda i:presets[i]['mode']+' '+str(presets[i]['max_unit_height_mm'])+' mm')
        if st.button('复制预设为新运输段'):
            value=deepcopy(s);leg=deepcopy(presets[selected_preset]);leg['id']='LEG-'+uuid.uuid4().hex[:6];value['route_legs'].append(RouteLeg.model_validate(leg).model_dump(mode='json'));save(w,value)
        if st.button('新增运输段'):
            value=deepcopy(s);value['route_legs'].append(RouteLeg(id='LEG-'+uuid.uuid4().hex[:6],mode='road').model_dump(mode='json'));save(w,value)
        for index,leg in enumerate(s['route_legs']):
            vehicle_library=w.repo.cache_get('vehicle-library') or defaults['vehicle_presets']
            with st.expander('为 '+leg['id']+' 从车辆库复制快照'):
                pick=st.selectbox('车辆档案',range(len(vehicle_library)),format_func=lambda i:vehicle_library[i]['name'],key='pick_vehicle'+str(index))
                if st.button('复制车辆到本段',key='copy_vehicle'+str(index)):
                    value=deepcopy(s);value['route_legs'][index]['vehicle_snapshot']=deepcopy(vehicle_library[pick]);save(w,value)
            with st.form('leg'+str(index)):
                value=deepcopy(s);l=value['route_legs'][index];st.write(l['id'])
                l['mode']=st.selectbox('运输方式',['road','air','sea'],index=['road','air','sea'].index(l['mode']),key=scoped_key('mode'+str(index)))
                for k in ('max_unit_height_mm','max_unit_gross_g','clearance_mm'):l[k]=number(LABELS[k],l[k],key='leg'+str(index)+k)
                for key in ('max_unit_height_mm','max_unit_gross_g'):
                    if l[key] is None:l['availability'][key]=st.selectbox(LABELS[key]+' 留空含义',['unknown','not_applicable'],index=int(l['availability'].get(key)=='not_applicable'),key=scoped_key('avail'+str(index)+key))
                l['height_includes_pallet']=st.checkbox('确认限高包含托盘和外包装',l['height_includes_pallet'] is True,key=scoped_key('includes'+str(index)))
                l['constraints_confirmed']=st.checkbox('本运输段参数已确认',l['constraints_confirmed'],key=scoped_key('confirmed'+str(index)))
                l['repalletize']=st.checkbox('途中换托（首版不支持）',l['repalletize'],key=scoped_key('repallet'+str(index)))
                use=st.checkbox('填写车辆快照',l['vehicle_snapshot'] is not None,key=scoped_key('vehicle'+str(index)))
                v=l['vehicle_snapshot'] or Vehicle(name='待填车辆').model_dump(mode='json')
                if use:
                    v['name']=txt('车辆名称',v['name'],key='vname'+str(index));v['thermal_type']=st.selectbox('车辆类型',['ambient','cold_chain'],index=int(v['thermal_type']=='cold_chain'),key=scoped_key('vtype'+str(index)))
                    cols=st.columns(3)
                    for i,k in enumerate(('usable_l_mm','usable_w_mm','usable_h_mm','door_w_mm','door_h_mm','payload_g')):
                        with cols[i%3]:v[k]=number(LABELS[k],v[k],key='v'+str(index)+k)
                    for k in ('temp_min_c','temp_max_c'):v[k]=txt(LABELS[k],v[k],key='v'+str(index)+k)
                if st.form_submit_button('保存运输段'):
                    l['vehicle_snapshot']=v if use else None;save(w,value)


def planning(w,s,plan):
    st.caption('条带摆放会比较两种朝向；全程不换托时，采用各运输段最严格约束。')
    with st.expander('人工调整每托箱数 / 候选摆法'):
        manual_rows=grid('每行按顺序填写各托箱数，例如 40,20；留空自动分托',[{'line_id':l['id'],'counts':','.join(str(n) for n in (plan or {}).get('manual_counts',{}).get(l['id'],[]))} for l in s['lines']],{'line_id':'货物行编号','counts':'各托箱数（逗号分隔）'},'manual_counts')
        layout_index=st.number_input('候选摆法序号（0为首选）',min_value=0,value=0,step=1)
    if st.button('计算打托方案',type='primary'):
        w.calculate_plan(s['id'],{r['line_id']:[int(n.strip()) for n in str(r['counts']).replace('，',',').split(',')] for r in manual_rows if r['counts']},layout_index);st.rerun()
    if not plan:st.info('尚无当前参数对应的方案。先补充箱规、托盘和限高，再计算。');return
    st.write('方案状态：',{'provisional':'待确认','confirmed':'已确认','stale':'已过期'}[plan['status']])
    if plan['issues']:st.dataframe([{'检查':i['message'],'结果':{'PASS':'通过','FAIL':'不通过','NOT_CHECKED':'待补充','NEEDS_CONFIRMATION':'待确认'}[i['status']],'范围':i['scope']} for i in plan['issues']],hide_index=True,width='stretch')
    rows=[]
    for p in plan['pallets']:
        e=effective(p);line=next(l for l in s['lines'] if l['id']==p['line_id']);rows.append({'托号':p['id'],'产品/批号':str(line['name_cn'] or line['name_en'])+' / '+str(line['batch_no']),'体积 m³':str(totals([p])['volume_m3']),'箱数':p['carton_count'],'每层':p['per_layer'],'层数':p['layers'],'顶层':p['boxes_last_layer'],
        '毛重 kg':None if e['gross_g'] is None else e['gross_g']/1000,'长 mm':e['length_mm'],'宽 mm':e['width_mm'],'含托高 mm':e['height_mm'],'净重 kg':None if e['net_g'] is None else e['net_g']/1000})
    st.dataframe(rows,hide_index=True,width='stretch')
    for p in plan['pallets']:
        with st.expander(p['id']+' · 每层俯视图',expanded=len(plan['pallets'])<=2):
            cols=st.columns(2)
            with cols[0]:st.caption('完整层');st.image(layout_svg(p),width=400)
            with cols[1]:
                st.caption('顶层');top=deepcopy(p);top['layout']=p['top_layout'];st.image(layout_svg(top),width=400)
            st.caption('顶层按中心优先填充；底层完整支撑。含托高 '+str(effective(p)['height_mm'])+' mm')
    if st.button('确认当前方案'):
        w.confirm_plan(s['id'],plan['id']);st.rerun()


def documents(w,s,plan):
    scope=st.selectbox('出单范围',scope_options(s),format_func=lambda x:scope_label(s,x))
    kind=st.selectbox('单证类型',list(TYPES),format_func=lambda x:TYPES[x])
    if st.button('生成可编辑草稿',type='primary'):
        d=w.render_document(s['id'],kind,scope);st.success('已生成 '+Path(d['file_path']).name)
    docs=w.documents(s['id'])
    for d in reversed(docs):
        with st.expander(f'{TYPES[d["type"]]} · {d["scope"]} · {d["stage"]} · 源数据 v{d["source_revision"]}',expanded=d['stage']!='stale'):
            st.download_button('下载历史文件' if d['stage']=='stale' else '下载 '+Path(d['file_path']).name,w.repo.safe_path(d['file_path']).read_bytes(),on_click='ignore',file_name=Path(d['file_path']).name,key='download'+d['id'])
            if st.button('检查批准门槛',key='gate'+d['id']):st.write(w.approval_issues(s['id'],d['type'],d['scope']))
            if d['stage']=='draft' and st.button('批准这个版本',key='approve'+d['id']):w.approve_document(s['id'],d['id']);st.rerun()
    with st.expander('生成资料包（只包含明确选择的文件）'):
        package_type=st.selectbox('资料包用途',['customer','customs','forwarder','delivery'])
        selected_docs=st.multiselect('选择本系统单证',[d['id'] for d in docs if d['stage']!='stale'],format_func=lambda x:Path(next(d['file_path'] for d in docs if d['id']==x)).name)
        files=[f for f in w.repo.files(s['id']) if f['linked_scope']==scope];selected_files=st.multiselect('选择附件',[f['id'] for f in files],format_func=lambda x:next(f['name'] for f in files if f['id']==x))
        pdf=st.checkbox('合并为 PDF（须全部为已确认 PDF）')
        if st.button('按白名单打包'):
            data=w.assemble_package(s['id'],package_type,scope,selected_docs,selected_files,pdf)
            st.download_button('下载资料包',data,on_click='ignore',file_name=s['business_no']+'_'+package_type+('.pdf' if pdf else '.zip'))


def review(w,s,plan):
    tabs=st.tabs(['实测回填','回件字段核对','PDF确认','版本与报告'])
    with tabs[0]:
        unit_controls('actual_units')
        if plan:
            pid=st.selectbox('实测托盘',[p['id'] for p in plan['pallets']]);target=next(p for p in plan['pallets'] if p['id']==pid)
            st.dataframe([{'项目':LABELS.get(k,k),'预计值':v,'已录实测':(target.get('actual') or {}).get(k)} for k,v in target['estimated'].items()],hide_index=True)
            with st.form('actual'):
                actual={};cols=st.columns(3)
                for i,k in enumerate(('length_mm','width_mm','height_mm','gross_g','net_g')):
                    with cols[i%3]:actual[k]=number(LABELS.get(k,k),(target.get('actual') or {}).get(k),key='actual'+pid+k)
                note=st.text_input('工厂实测依据');confirmed=st.checkbox('确认以上为含托外廓 / 含托总毛重')
                if st.form_submit_button('保存实测并重检'):
                    w.record_actuals(s['id'],plan['id'],pid,{k:v for k,v in actual.items() if v is not None},{'manual_note':note},confirmed);st.rerun()
            with st.form('actual_difference'):
                reason=st.text_input('实测小于预计外廓时，填写针对本托的复核依据')
                if st.form_submit_button('确认尺寸差异原因'):
                    w.review_actual_difference(s['id'],plan['id'],pid,reason);st.rerun()
    with tabs[1]:
        files=w.repo.files(s['id'])
        if not files:st.info('先导入需要核对的原件或回件')
        else:
            selected_file=st.selectbox('依据文件及归属',range(len(files)),format_func=lambda i,files=files:files[i]['name']+' · '+ROLES.get(files[i]['linked_role'],files[i]['linked_role'])+' · '+scope_label(s,files[i]['linked_scope']))
            source=files[selected_file];file_id=source['id'];role=source['linked_role'];scope=source['linked_scope']
            fields=observation_fields(s,plan,scope)
            with st.form('observation'):
                path=st.selectbox('核对字段',fields,index=fields.index('lines.0.batch_no') if 'lines.0.batch_no' in fields else 0,format_func=lambda p:field_label(s,p))
                value=st.text_input('原件看到的值（空值表示未读到）');unit=st.text_input('单位（可空）');locator=st.text_input('位置，例如 page:1 或 箱单!D15')
                raw=st.text_area('原文摘录')
                if st.form_submit_button('保存核对记录'):
                    w.add_observation(s['id'],{'file_id':file_id,'doc_role':role,'scope':scope,'field_path':path,'value':value or None,'unit':unit or None,'source_ref':{'file_id':file_id,'locator':locator or None,'raw_text':raw},'extraction_status':'MANUAL'});st.rerun()
        issues=w.compare_documents(s['id']);st.dataframe([{'字段':field_label(s,i['field_path'] or ''),'结果':i['status'],'主数据 / 计划值':str(i['expected']),'原件值':str(i['observed']),'说明':i['message']} for i in issues],hide_index=True,width='stretch')
        observation_actions(w,s)
        with st.form('resolve'):
            selected=st.selectbox('记录问题处理',[i['id'] for i in issues],format_func=lambda x:next(str(i['field_path'])+' '+i['status'] for i in issues if i['id']==x))
            note=st.text_input('处理说明（硬错误仍须改正基础数据）')
            if st.form_submit_button('保存处理说明'):w.resolve_issue(s['id'],selected,note);st.rerun()
    with tabs[2]:
        st.write('用 Office / WPS 导出 PDF 后，在资料页上传、关联合同和角色，再逐页核对。')
        files=[f for f in w.repo.files(s['id']) if f['name'].lower().endswith('.pdf')]
        if files:
            f=st.selectbox('待确认 PDF',range(len(files)),format_func=lambda i,files=files:files[i]['name']);item=files[f]
            b=w.repo.file_bytes(item['id']);st.download_button('打开原 PDF',b,on_click='ignore',file_name=Path(item['name']).name)
            uri='data:application/pdf;base64,'+base64.b64encode(b).decode();st.components.v1.html(f'<iframe src="{uri}" width="100%" height="550"></iframe>',height=560)
            scope=st.selectbox('确认范围',scope_options(s),index=scope_options(s).index(item['linked_scope']) if item['linked_scope'] in scope_options(s) else 0,format_func=lambda x:scope_label(s,x));note=st.text_area('逐页核对结论');confirmed=st.checkbox('已核对字段、件数、重量、价格与版面')
            if st.button('确认此 PDF'):w.confirm_pdf(s['id'],item['id'],scope,note,confirmed);st.success('已记录核对人操作和当前数据依赖')
    with tabs[3]:
        if s['revision']>1:
            old=st.number_input('比较历史版本',min_value=1,max_value=s['revision']-1,value=s['revision']-1)
            st.dataframe([d for d in diff(w.repo.load(s['id'],old),s) if not d['field_path'].startswith('facts')],hide_index=True,width='stretch')
        st.download_button('下载内部核对报告 HTML',w.report(s['id']),on_click='ignore',file_name='核对报告.html')
        st.download_button('下载内部核对报告 JSON',w.report(s['id'],'json'),on_click='ignore',file_name='核对报告.json')
        st.json(s['audit'])


def settings(w,s,plan):
    tabs=st.tabs(['档案与预设','模板','模型','备份恢复','完整结构'])
    with tabs[0]:
        defaults=json.loads((Path(__file__).parents[2]/'config/defaults.json').read_text())
        for key,label,default,model in [('pallet-library','托盘库',defaults['pallet_presets'],PalletSpec),('vehicle-library','车辆库',defaults['vehicle_presets'],Vehicle),
          ('packing-library','箱规库',[],PackingSpec),('route-presets','业务运输预设',[{k:v for k,v in p.items() if k!='source'} for p in defaults['route_presets']],RouteLeg)]:
            with st.form(key):
                raw=edit_json(label,w.repo.cache_get(key) or default,key+'json',220)
                if st.form_submit_button('保存'+label):
                    value=[model.model_validate(x).model_dump(mode='json') for x in json.loads(raw)];w.repo.cache_set(key,value);st.success('已保存；历史快照保持不变')
        st.caption('2600 / 1550 mm 是可修改的业务预设，不是通用承运标准。车辆尺寸须填真实可用数据。')
        with st.form('currency_decimals'):
            precision=edit_json('本批次币种金额小数位',s['currency_decimals'],'currency_decimals',100)
            if st.form_submit_button('保存币种精度'):
                value=deepcopy(s);value['currency_decimals']=json.loads(precision);save(w,value)
    with tabs[1]:
        kind=st.selectbox('模板',list(TYPES),format_func=lambda x:TYPES[x]);spec=w.template_spec(kind)
        st.json(spec)
        with st.form('template_register'):
            raw=edit_json('注册新版本 / 编辑映射 JSON',spec,'spec_edit',260)
            upload=st.file_uploader('新版本无签章模板（可选）',type=['xlsx','docx'])
            if st.form_submit_button('校验并保存为新版本'):
                w.register_template(kind,json.loads(raw),upload.getvalue() if upload else None)
                st.success('已注册，当前与历史模板均纳入备份；旧产物须重新核验')
        if plan and st.button('用当前批次测试生成'):
            scope='ALL' if kind in ('booking','plan') else s['contracts'][0]['id'];d=w.render_document(s['id'],kind,scope);st.download_button('下载测试生成',w.repo.safe_path(d['file_path']).read_bytes(),on_click='ignore',file_name=Path(d['file_path']).name)
    with tabs[2]:
        ai=w.repo.cache_get('ai-settings') or {}
        with st.form('ai-settings'):
            endpoint=st.text_input('完整 Chat Completions 兼容接口地址',ai.get('endpoint',''))
            model=st.text_input('模型名称',ai.get('model',''))
            st.caption('密钥在环境变量 SHIPPING_AI_API_KEY 中设置；本页不保存密钥。')
            if st.form_submit_button('保存模型配置'):w.repo.cache_set('ai-settings',{'endpoint':endpoint,'model':model});st.success('已保存，尚未发送资料')
        st.dataframe(w.repo.records('ai_usage'),hide_index=True)
    with tabs[3]:
        if st.button('生成完整本地备份'):
            st.download_button('下载备份 ZIP',w.backup(),on_click='ignore',file_name='出运工作台_内部备份.zip')
        restore=st.file_uploader('选择本工作台备份 ZIP',type=['zip'],key='restore')
        ack=st.checkbox('恢复备份替换当前数据；系统先自动保存恢复前备份')
        if st.button('校验并恢复') and restore and ack:w.restore(restore.getvalue());st.rerun()
    with tabs[4]:
        st.caption('高级入口：可编辑完整结构；仍执行单位、价格、归属和版本校验。事实来源在 facts 中单独保存。')
        with st.form('full_json'):
            raw=edit_json('完整批次 JSON',s,'fulljson',600)
            if st.form_submit_button('校验并保存完整结构'):save(w,json.loads(raw))


def observation_actions(w,s):
    active=w.active_observations(s['id'])
    if not active:return
    with st.expander('核验候选 / 接受主数据修改 / 纠正误录',expanded=True):
        oid=st.selectbox('选择当前有效记录',[o['id'] for o in active],format_func=lambda x:next(field_label(s,o['field_path'])+' = '+str(o['value'])+' · '+o['extraction_status']+' · '+x[:6] for o in active if o['id']==x))
        item=next(o for o in active if o['id']==oid)
        st.write('来源：',item['source_ref']);st.caption('归属：'+scope_label(s,item['scope'])+' · '+ROLES.get(item['doc_role'],item['doc_role']))
        try:
            preview=w.preview_observation(s['id'],oid)
            st.dataframe([{'字段':field_label(s,preview['field_path']),'当前值':str(preview['before']),'接受后':str(preview['after'])}],hide_index=True)
        except ValueError as exc:
            preview=None;st.info('此记录目前不能写入主数据：'+str(exc))
        locator=st.text_input('核实后的来源位置',item['source_ref'].get('locator') or '',key=scoped_key('verifyloc'+oid))
        reason=st.text_input('针对本记录的核验 / 纠错依据',key=scoped_key('obsreason'+oid))
        if st.button('我已查看原件，核验这条记录'):
            w.verify_observation(s['id'],oid,reason,locator);st.rerun()
        accept=st.checkbox('我确认上述差异，应带来源写入主数据',key=scoped_key('accept'+oid))
        if st.button('接受所选候选',disabled=preview is None or not accept):
            w.accept_observation(s['id'],oid,s['revision'],reason);st.rerun()
        replacements=[o for o in active if o['id']!=oid and all(o[k]==item[k] for k in ('field_path','scope','doc_role'))]
        if replacements:
            replacement=st.selectbox('用哪条已核验记录替代它',[o['id'] for o in replacements],format_func=lambda x:next(str(o['value'])+' · '+o['extraction_status']+' · '+x[:6] for o in replacements if o['id']==x))
            if st.button('记录理由并替代旧记录'):
                w.supersede_observation(s['id'],oid,replacement,reason);st.rerun()
        void=st.checkbox('确认这是误录 / 不适用记录，保留历史并作废',key=scoped_key('void'+oid))
        if st.button('作废所选记录',disabled=not void):w.void_observation(s['id'],oid,reason);st.rerun()
        with st.expander('原始记录与处理历史'):
            st.json({'原始记录':w.repo.records('observations',s['id']),'处理事件':w.observation_history(s['id'])})
