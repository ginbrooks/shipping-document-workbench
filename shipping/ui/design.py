"""Desktop presentation only; business states derive from existing facts and checks."""
from html import escape
from pathlib import Path
import streamlit as st
from shipping.validation import validate_shipment,compare_observations,get_path
from shipping.expectations import required_paths

PAGES=['工作概览','资料与基础数据','包装与运输','打托方案','单证生成','核对与版本','设置']
NAV={'工作概览':'◫   工作概览','资料与基础数据':'▤   资料与基础数据','包装与运输':'▥   包装与运输','打托方案':'▦   打托方案','单证生成':'▧   单证生成','核对与版本':'◎   核对与版本','设置':'⚙   设置'}
DESCRIPTIONS={'工作概览':'这一批货的资料、包装、单证与待办。','资料与基础数据':'录入一次，供这批货的所有单证使用。','包装与运输':'确认真实箱规、托型和运输限制。','打托方案':'检查逐托布局，再确认工厂实际装载。','单证生成':'按合同与用途生成、核验和归档。','核对与版本':'对照原件处理差异，每次修改都有依据。','设置':'管理本地档案、模板与备份。'}

def esc(value):return escape(str(value if value is not None else '—'))
def go(page):st.session_state['nav_page']=page

def theme():
    st.markdown('<style>'+Path(__file__).with_name('assets').joinpath('workbench.css').read_text()+'</style>',unsafe_allow_html=True)
    st.markdown('<style>'+Path(__file__).with_name('assets').joinpath('progress.css').read_text()+'</style>',unsafe_allow_html=True)

    st.markdown('<style>'+Path(__file__).with_name('assets').joinpath('document-desk.css').read_text()+'</style>',unsafe_allow_html=True)

def brand():
    st.markdown('''<div class="brand"><div class="brand-icon"><svg viewBox="0 0 32 32" fill="none"><path d="m16 3 12 7v13l-12 7-12-7V10L16 3Z" stroke="currentColor" stroke-width="1.6"/><path d="m4 10 12 7 12-7M16 17v13M10 6l12 7" stroke="currentColor" stroke-width="1.6"/></svg></div><div><strong>出运工作台</strong><span>SHIPMENT DESK</span></div></div>''',unsafe_allow_html=True)

def header(s,page):
    demo=bool(s.get('remarks') and '合成' in s['remarks'])
    st.markdown(f'''<div class="topline"><span>工作空间 <b>/</b> {esc(s['business_no'])}</span><div><span class="local-dot"></span> 本机保存 <span class="version-chip">v{s['revision']}</span></div></div><div class="page-title"><div><h1>{esc(page)}</h1><p>{DESCRIPTIONS[page]}</p></div><span class="quiet-badge">{'合成演示 · 非实际货物' if demo else '当前批次'}</span></div>''',unsafe_allow_html=True)

def overview_state(w,s,plan):
    relevant=[]
    for c in s['contracts']:relevant+=required_paths(s,'customer',c['id'],w.template_spec('customer'))
    data_ready=bool(s['lines'] and s['contracts'] and relevant) and all(get_path(s,p) not in (None,'') and s['facts'].get(p,{}).get('confirmed') and s['facts'][p]['value']==get_path(s,p) for p in relevant)
    packing_ready=bool(plan and plan['status']=='confirmed')
    actual_ready=bool(plan and plan['pallets']) and all((p.get('actual') or {}).get('confirmed') and all((p.get('actual') or {}).get(k) is not None for k in ('length_mm','width_mm','height_mm','gross_g')) for p in plan['pallets'])
    obs=w.active_observations(s['id']);checks=compare_observations(s,obs,plan)
    reviewed=bool(obs) and all(i['status']=='PASS' for i in checks)
    current={}
    for d in w.documents(s['id']):
        if d['stage']!='stale':current[(d['type'],d['scope'])]=d
    docs=list(current.values());approved=bool(docs) and all(d['stage'] in ('approved','released') for d in docs)
    steps=[{'title':title,'done':done,'page':page} for title,done,page in [('资料确认',data_ready,'资料与基础数据'),('方案确认',packing_ready,'打托方案'),('实测回填',actual_ready,'核对与版本'),('回件核对',reviewed,'核对与版本'),('内部批准',approved,'单证生成')]]
    pending=[]
    if not data_ready:pending.append({'title':'补充并确认基础资料','detail':'核实合同、货物与收发货人信息，为单证提供来源。','page':'资料与基础数据'})
    if not packing_ready:pending.append({'title':'确认本批打托方案','detail':'检查箱规与运输约束，生成逐托布局。','page':'打托方案'})
    if not actual_ready:pending.append({'title':'回填工厂实测','detail':'需要含托外廓、含托总毛重及实测依据。','page':'核对与版本'})
    if not reviewed:pending.append({'title':'核验原件与货代回件','detail':f'{sum(i["status"]!="PASS" for i in checks)} 项待核验或差异' if obs else '尚未录入原件核对记录。','page':'核对与版本'})
    if not approved:pending.append({'title':'检查单证并内部批准','detail':'确认当前版本的必填、来源与实测检查。','page':'单证生成'})
    return {'steps':steps,'pending':pending,'document_count':len(docs),'documents':docs,'source_count':len(w.repo.files(s['id'])),'checks':checks}

def overview(w,s,plan):
    state=overview_state(w,s,plan);done=sum(x['done'] for x in state['steps'])
    number=len(plan['pallets']) if plan else '—';mode={'air':'空运','road':'陆运','sea':'海运'}.get(s['transport_mode'],s['transport_mode'] or '方式待定')
    st.markdown(f'''<div class="shipment-hero"><div class="hero-top"><span class="eyebrow">CURRENT SHIPMENT / 当前批次</span><span class="hero-status">{done} / 5 阶段完成</span></div><div class="hero-title">{esc(s['business_no'])}</div><div class="route"><div><small>起运地</small><strong>{esc(s['origin'] or '待填起运地')}</strong></div><div class="route-track"><span> {esc(mode)} </span><i></i><b>›</b></div><div><small>目的地</small><strong>{esc(s['destination'] or '待填目的地')}</strong></div></div><div class="hero-footer"><span>预计出运 <b>{esc(s['planned_ship_date'] or '待确认')}</b></span><span>合同 <b>{len(s['contracts'])} 份</b></span><span>运输包装 <b>{number} 托</b></span></div></div>''',unsafe_allow_html=True)
    strip=''.join(f'<div class="step {"done" if x["done"] else "pending"}"><span>{"✓" if x["done"] else f"{i+1:02}"}</span><div><strong>{x["title"]}</strong><small>{"已完成" if x["done"] else "待处理"}</small></div></div>' for i,x in enumerate(state['steps']))
    st.markdown('<div class="workflow-strip">'+strip+'</div>',unsafe_allow_html=True)
    left,right=st.columns([1.55,1],gap='large')
    with left:
        with st.container(key='cargo_panel',border=True):
            section('本批货物',f'{len(s["lines"])} 个货物行')
            if s['lines']:
                for line in s['lines']:
                    st.markdown(f'''<div class="cargo-row"><span class="cargo-icon">▥</span><div><strong>{esc(line['name_cn'] or line['name_en'] or '待填产品')}</strong><small>{esc(line['strength_text'] or '规格待填')} <em>·</em> {esc(line['batch_no'] or '批号待填')}</small></div><div class="cargo-quantity">{esc(format(line['base_quantity'],',') if line['base_quantity'] is not None else '—')}<small>{esc(line['base_unit'] or '单位待填')}</small></div></div>''',unsafe_allow_html=True)
            else:st.caption('尚无货物。先添加合同和货物行。')
            st.button('编辑货物资料  →',key='overview_cargo',on_click=go,args=('资料与基础数据',),type='tertiary')
        with st.container(key='document_panel',border=True):
            section('单证与资料',f'{state["document_count"]} 份当前单证 · {state["source_count"]} 份资料归属')
            if state['documents']:
                from shipping.templating import TYPES
                for d in state['documents'][:3]:
                    stage={'draft':'草稿','approved':'已批准','released':'已放行'}.get(d['stage'],d['stage'])
                    st.markdown(f'<div class="doc-row"><span class="doc-glyph">▤</span><div><strong>{TYPES[d["type"]]}</strong><small>{esc(d["scope"])} · v{d["source_revision"]}</small></div><span class="quiet-badge">{stage}</span></div>',unsafe_allow_html=True)
            else:st.markdown('<div class="empty-doc"><span>▤</span><strong>还没有生成单证</strong><p>完成基础资料和包装方案后，生成这一批的草稿。</p></div>',unsafe_allow_html=True)
            st.button('进入单证中心  →',key='overview_documents',on_click=go,args=('单证生成',),width='stretch')
    with right:
        with st.container(key='todo_panel',border=True):
            section('接下来处理',f'{len(state["pending"])} 项')
            for i,item in enumerate(state['pending'][:3]):
                st.markdown(f'<div class="todo-item"><span>{i+1:02}</span><div><strong>{item["title"]}</strong><p>{esc(item["detail"])}</p></div></div>',unsafe_allow_html=True)
            target=state['pending'][0] if state['pending'] else {'title':'查看单证','page':'单证生成'}
            st.button(target['title']+'  →',key='overview_primary',on_click=go,args=(target['page'],),type='primary',width='stretch')
        st.markdown('<div class="local-note"><span class="local-dot"></span><div><strong>保存在这台 Mac</strong><p>原件与修改记录留在本机。<br>定期在设置中导出完整备份。</p></div></div>',unsafe_allow_html=True)
    st.markdown('<div class="page-foot">SHIPMENT DESK <span>保存每一步依据，让修改可追溯。</span></div>',unsafe_allow_html=True)

def section(title,detail=''):
    st.markdown(f'<div class="section-head"><h3>{esc(title)}</h3><span>{esc(detail)}</span></div>',unsafe_allow_html=True)

def compact_summary(s,plan,checks):
    pending=sum(i['status']!='PASS' for i in checks)
    st.markdown(f'<div class="compact-summary"><span>货物 <b>{len(s["lines"])} 行</b></span><span>包装 <b>{len(plan["pallets"]) if plan else "待计算"} {"托" if plan else ""}</b></span><span>基础校验 <b>{str(pending)+" 项待处理" if pending else "无已检出差异"}</b></span><span class="save-hint">填写后请点击对应保存按钮</span></div>',unsafe_allow_html=True)
