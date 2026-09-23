"""Review is the workspace; uploads assist it rather than unlock it."""
from copy import deepcopy
from html import escape
import streamlit as st
from shipping.agent.core import JobStore,LABELS,questions
from shipping.agent.catalog import NAMES,CATALOG,owners,fields_for,document_checks
from shipping.agent.business_flows import FLOW_KEYS,COMPONENTS,select_flow,LONG_FIELDS,FIELD_HELP,settlement_changes,customs_amount
from shipping.agent.review import blank_job,review_view,review_items,material_gaps,DraftStore,save_review,STATUS,objects,transfer_options,transfer_fields,title_for,empty_object,accept_candidate,prune_empty
from shipping.agent.review_sections import review_sections,uploaded_materials
from shipping.repository import now
from shipping.agent.defaults import apply_customs_defaults
from shipping.agent.review import display_evidence


def working_view(repo,job):
 draft=DraftStore(repo).get(job)
 view=deepcopy(job)
 if not view['contracts']:view['contracts']=deepcopy(draft['view']['contracts'])
 if not view['products']:view['products']=deepcopy(draft['view']['products'])
 return view


def persist_edits(repo,job,kind,confirm=False,force=False,confirm_paths=None):
 store=JobStore(repo);draft=DraftStore(repo).get(job)
 if not confirm and not force and not draft['answers'] and job['id']!='new':
  view=deepcopy(job);view['review_first']=True
  return view
 saved=save_review(store,job,draft['answers'],kind,confirm=confirm,confirm_paths=confirm_paths)
 if job['id']=='new':
  st.session_state.pop('review_new',None)
  group=st.session_state.get('review_group_new_'+kind)
  if group:st.session_state['review_group_'+saved['id']+'_'+kind]=group
 st.session_state['agent_next_selected']=saved['id']
 return saved


def page(repo):
 from .agent import intake,business_files_page
 store=JobStore(repo);jobs=store.list();lookup={j['id']:j for j in jobs};names={j['id']:j['name'] for j in jobs}
 names['new']='＋ 新的一票'
 for jid in list(names):
  if jid!='new' and sum(x['name']==names[jid] for x in jobs)>1:names[jid]+=' · '+jid[:6]
 st.markdown('<div class="desk-kicker">DOCUMENT WORKSPACE</div><div class="desk-heading"><h1>出运单证</h1><p>先看需要什么信息，上传自动填写，也可以直接填写。</p></div>',unsafe_allow_html=True)
 next_id=st.session_state.pop('agent_next_selected',None)
 if next_id in names:st.session_state['agent_selected']=next_id
 if st.session_state.get('agent_selected') not in names:st.session_state['agent_selected']=jobs[-1]['id'] if jobs else 'new'
 top=st.columns([1,1.25],gap='large')
 with top[1]:jid=st.selectbox('当前票',list(names),format_func=names.get,key='agent_selected')
 if jid=='new':
  if 'review_new' not in st.session_state:
   st.session_state['review_new']=(repo.cache_get('review-draft:new') or {}).get('view') or blank_job('customs_set')
  job=deepcopy(st.session_state['review_new'])
 else:job=store.get(jid)
 job=working_view(repo,job)
 # Keep unsaved blank scope identities stable even before a first field is edited.
 cache_key='review_view_'+jid
 if not (lookup.get(jid) or {}).get('products'):
  previous=st.session_state.get(cache_key)
  if previous:
   job['contracts']=previous['contracts'];job['products']=previous['products']
 st.session_state[cache_key]=deepcopy(job)
 with top[0]:
  key='agent_document_'+jid
  if key not in st.session_state:st.session_state[key]=job.get('active_document') or __import__('shipping.agent.business_flows',fromlist=['flow_for']).flow_for(job['targets'][0])
  if st.session_state[key] not in FLOW_KEYS:
   from shipping.agent.business_flows import flow_for
   st.session_state[key]=flow_for(st.session_state[key])
  def changed():
   kind=st.session_state[key]
   if jid=='new':
    st.session_state['review_new']=select_flow(job,kind)
   else:
    latest=store.get(jid);store.save(select_flow(latest,kind),latest['revision'])
  kind=st.selectbox('本次办理的资料',list(FLOW_KEYS),format_func=NAMES.get,key=key,on_change=changed)
 job['active_document']=kind
 if kind not in job['targets']:job['targets'].append(kind)
 apply_customs_defaults(job)
 with st.expander('本票名称'):
  name=st.text_input('给这票起个名字',value=job['name'] if job['name']!='未命名新票' else '',key='review_name_'+jid,placeholder='可以稍后再命名')
  if name.strip():job['name']=name.strip()
  if st.button('保存名称',key='review_name_save_'+jid):
   try:persist_edits(repo,job,kind,force=True);st.rerun()
   except ValueError as e:st.error(str(e))
 if st.session_state.pop('agent_done_'+jid,None):st.success('资料已填入，请核对新内容；尚缺的信息排在最前面。')
 pending=questions(job,[kind]);rows=review_items(job,kind)
 missing=sum(x['status']=='missing' for x in rows);uncertain=sum(x['status'] in ('conflict','pending') for x in rows)
 st.markdown(f'<div class="document-heading"><div><span>{escape(CATALOG[kind].category)}</span><h2>{escape(NAMES[kind])}</h2><p>{escape(" · ".join(COMPONENTS[kind]))}</p></div><div class="document-status">待补 {missing} 项 · 待核对 {uncertain} 项</div></div>',unsafe_allow_html=True)
 step_key='agent_step_'+jid;next_step=st.session_state.pop('agent_next_step_'+jid,None)
 if next_step:st.session_state[step_key]=next_step
 if step_key not in st.session_state:st.session_state[step_key]='核对'
 step=st.radio('这票进度',['资料','核对','文件'],horizontal=True,key=step_key,label_visibility='collapsed')
 if step=='资料':materials_page(repo,job,kind)
 elif step=='文件':
  pending=questions(prune_empty(job),[kind])
  if DraftStore(repo).get(job)['answers']:
   st.warning('还有编辑草稿未保存为核对值，请回到核对页保存后生成。');pending=pending+[{'label':'未保存的编辑草稿'}]
  if job.get('unmatched'):pending=pending+[{'label':'未确认的资料归属'}];st.warning('还有识别内容未确认产品归属，请回到核对页处理。')
  business_files_page(repo,job,store,kind,pending)
 else:review_page(repo,job,kind,step_key)


def materials_page(repo,job,kind):
 from .agent import intake
 intake(repo,job,kind,review_mode=True)
 with st.expander('补充资料建议'):
  for gap in material_gaps(job,kind):
   st.markdown('**'+gap['category']+'** · '+'、'.join(gap['fields']))
   st.caption('可补充'+gap['material']+'，也可到核对页直接填写已核实的数据。')
  if kind=='settlement':st.caption('运输回件和产地证需上传正式 PDF；填写编号不能代替原件。')


def material_list(job):
 files=uploaded_materials(job)
 with st.expander('已上传资料 · '+str(len(files))+' 份',expanded=False):
  if not files:st.caption('尚未上传资料，可在左侧“资料”添加。');return
  rows=''.join('<tr><td>'+str(f['序号'])+'</td><td>'+escape(f['文件名'])+'</td></tr>' for f in files)
  st.markdown('<div class="review-file-list"><table><thead><tr><th>序号</th><th>文件名</th></tr></thead><tbody>'+rows+'</tbody></table></div>',unsafe_allow_html=True)


def fixed_information(repo,kind,job):
 with st.expander('模板固定信息'):
  try:
   if kind=='consignment':
    mode=job['shipment']['values'].get('transport_mode')
    if not mode:st.caption('选择并保存运输方式后，显示对应空运或陆运底稿的固定信息。');return
    from shipping.agent.profiles import original
    from shipping.agent.ooxml import members,W
    from lxml import etree
    data=original(repo,'booking' if mode=='空运' else 'road_booking')
    table=etree.fromstring(members(data)['word/document.xml']).findall('.//{'+W+'}tbl')[0]
    for row,label in [(2,'经营单位 / 装船人'),(3,'收货人'),(4,'通知人')]:
     cell=table.findall('{'+W+'}tr')[row].findall('{'+W+'}tc')[1]
     st.text(label+'：'+''.join(t.text or '' for t in cell.iter('{'+W+'}t')))
    st.caption('来自当前运输方式对应的已审核托书底稿。');return
   from shipping.agent.business_packages import profile
   fixed=profile(repo)
   names={'company_cn':'公司中文名','company_en':'公司英文名','address':'公司地址','consignee':'固定收货人','registration':'主体代码','co_exporter':'产地证出口人','co_consignee':'产地证收货人'}
   if kind=='customs_set':names={k:v for k,v in names.items() if k not in ('consignee','co_exporter','co_consignee')}
   for k,label in names.items():
    if fixed.get(k):st.text(label+'：'+str(fixed[k]))
   st.caption('来自已审核的本机底稿。固定资料有误请在设置中处理底稿。')
  except (ValueError,OSError,KeyError):st.caption('固定底稿尚未安装完整。可以先填写和保存，生成前请在设置安装对应底稿。')


def review_page(repo,job,kind,step_key):
 from .agent import samples_panel,returns_panel
 from shipping.agent.flow import extraction_current
 jid=job['id'];store=JobStore(repo);drafts=DraftStore(repo);draft=drafts.get(job);rows=review_items(job,kind)
 material_list(job)
 if draft['answers']:st.info('编辑草稿已在本机暂存；点击“保存本组核对”后用于生成。')
 elif job.get('review_saved_at'):st.caption('核对已保存 · '+job['review_saved_at'][:19].replace('T',' '))
 if draft['answers']:
  try:drafts.validate_base(job)
  except ValueError as e:
   st.warning(str(e));current=objects(job)
   st.dataframe([{'字段':LABELS[path.split('.',1)[1]],'当前已保存':current.get(path.split('.',1)[0],{}).get('values',{}).get(path.split('.',1)[1]),'编辑草稿':v} for path,v in draft['answers'].items()],hide_index=True)
   a,b=st.columns(2)
   for col,label,keep in [(a,'确认保留我的草稿',True),(b,'采用最新值，放弃冲突草稿',False)]:
    if col.button(label,key='draft_rebase_'+jid+str(keep)):
     drafts.rebase(job,keep)
     for key in list(st.session_state):
      if key.startswith('af_'+jid+'_'):del st.session_state[key]
     st.rerun()
 if job['files'] and not extraction_current(repo,job) and job.get('manual_files')!=sorted(f['id'] for f in job['files']):
  st.warning('新增资料尚未识别。可先识别，或核实字段后明确使用当前信息生成。')
  if st.button('本次使用已核对信息生成',key='review_manual_files_'+jid):
   try:
    saved=persist_edits(repo,job,kind);saved['manual_files']=sorted(f['id'] for f in saved['files']);saved['audit'].append({'at':now(),'event':'manual_files_reviewed','files':saved['manual_files']});store.save(saved,saved['revision']);st.rerun()
   except ValueError as e:st.error(str(e))
 for issue in document_checks(job,kind):st.error(issue)
 if kind=='settlement':
  st.caption('已沿用同票草件信息；核对实际变化和正式回件即可。')
  changes=settlement_changes(job)
  if changes:st.dataframe([{'字段':LABELS[x['field']],'草件原值':x['before'] or '未填','现在值':x['after'] or '未填'} for x in changes],hide_index=True,width='stretch')
 candidate_panel(repo,job,kind)
 sections=review_sections(job,kind,rows);lookup={s['id']:s for s in sections}
 def group_label(key):
  group=lookup[key]
  status='待补 '+str(group['missing']) if group['missing'] else '待核对 '+str(group['pending']) if group['pending'] else '字段已齐'
  if group['missing'] and group['pending']:status+=' / 待核对 '+str(group['pending'])
  return group['title']+' · '+status
 with st.container(key='review-group-navigation'):
  group_id=st.radio('核对分组',list(lookup),format_func=group_label,horizontal=True,key='review_group_'+jid+'_'+kind,label_visibility='collapsed')
 group=lookup[group_id];visible_rows=group['rows']
 files={f['id']:f['name'] for f in job['files']}
 def inputs(items):
  cols=st.columns(2,gap='large')
  for i,row in enumerate(items):
   k=row['key'];path=row['path'];wk='af_'+jid+'_'+str(job['revision'])+'_'+path
   value=draft['answers'].get(path,row['value'])
   if wk not in st.session_state:st.session_state[wk]=value
   def remember(path=path,wk=wk):drafts.put(job,{path:st.session_state[wk]})
   with cols[i%2]:
    st.caption(row['title'])
    label=LABELS[k]+' · '+STATUS[row['status']]
    help_text='\n\n'.join(x for x in [FIELD_HELP.get(k),'填写位置：'+row['template_location']] if x)
    if k=='transport_mode':st.selectbox(label,['','空运','陆运'],format_func=lambda x:x or '请选择运输方式',key=wk,on_change=remember,help='保存后按空运或陆运显示相应字段。')
    elif k in LONG_FIELDS:st.text_area(label,key=wk,height=100,on_change=remember,help=help_text)
    else:st.text_input(label,key=wk,on_change=remember,help=help_text)
    if path in draft['answers']:
     try:__import__('shipping.agent.core',fromlist=['checked']).checked(k,draft['answers'][path])
     except ValueError as e:st.error(str(e))
    if row['choices']:st.warning('资料不一致：'+' / '.join(str(x) for x in row['choices']))
    if row['evidence']:
     evidence=display_evidence(row['evidence'])
     with st.popover('查看依据 · '+str(len(evidence))+' 条',help='展开查看字段来源；相同的来源、原文和值只显示一次。'):
      for e in evidence:
       source=e.get('source') or '';fid,_,loc=source.partition(':')
       origin='人工核实' if source=='manual' else '程序计算' if source=='calculated' else '公司默认规则' if source=='business_default' else '历史票 '+e.get('source_name',e.get('source_job','')) if source=='history' else files.get(fid,'原件')+(' · '+loc if loc else '')
       st.caption(origin)
       if row['choices']:st.write('候选值：'+str(e.get('value') or ''))
       if e.get('quote'):st.write(str(e['quote']))
 with st.container(border=True,key='review-group-panel'):
  st.markdown('<div class="review-group-title">'+escape(group['title'])+'</div>',unsafe_allow_html=True)
  st.caption(group['hint'])
  inputs([r for r in visible_rows if r['status'] in ('missing','conflict','pending')])
  for status in ('filled','optional'):
   part=[r for r in visible_rows if r['status']==status]
   if part:
    with st.expander(STATUS[status]+' · '+str(len(part))+' 项',expanded=False):inputs(part)
  if kind=='customs_set' and group_id=='invoice':
   for p in job['products']:
    amount=customs_amount(p['values'])
    if amount:st.caption((p['values'].get('name_cn') or '产品')+' · 报关货值 '+p['values'].get('customs_currency','')+' '+amount)
  st.caption('保存已编辑的信息，并确认本组待核对值；其他组仍保留待核对状态。')
  if st.button('保存本组核对',type='primary',key='review_save_'+jid):
   try:persist_edits(repo,job,kind,confirm=True,confirm_paths={r['path'] for r in visible_rows});st.session_state['review_saved_notice']=True;st.rerun()
   except ValueError as e:st.error(str(e))
 if st.session_state.pop('review_saved_notice',False):st.success('已保存。本票其他资料直接复用，缺项已重新排序。')
 if kind=='customs_set' and group_id=='samples' and jid!='new':samples_panel(repo,job,store)
 if kind=='settlement' and group_id=='transport' and jid!='new':returns_panel(repo,job,store,allow_upload=False)
 fixed_information(repo,kind,job)
 structure_panel(repo,job,kind)
 history_panel(repo,job,kind)
 with st.expander('查看字段原文依据'):
  data=[{'归属':r['title'],'字段':LABELS[r['key']],'当前值':r['value'],'状态':STATUS[r['status']],'原文':'；'.join(dict.fromkeys(str(e.get('quote','')) for e in display_evidence(r['evidence'])))} for r in rows]
  st.dataframe(data,hide_index=True,width='stretch')
 st.button('查看文件与生成',key='agent_to_files_'+jid,on_click=lambda:st.session_state.update({step_key:'文件'}))


def structure_panel(repo,job,kind):
 jid=job['id'];store=JobStore(repo)
 with st.expander('增加合同、产品或批次'):
  options=[c['id'] for c in job['contracts']]+['new'];names={c['id']:title_for(job,c,'contract') for c in job['contracts']};names['new']='新增合同'
  cid=st.selectbox('产品所属合同',options,format_func=names.get,key='structure_contract_'+jid)
  if st.button('增加一个空白产品',key='structure_product_'+jid):
   try:
    saved=persist_edits(repo,job,kind)
    if cid=='new':c=empty_object('c',kind='other');saved['contracts'].append(c);cid=c['id']
    saved['products'].append(empty_object('p',contract_id=cid,batches=[]));store.save(saved,saved['revision']);st.rerun()
   except ValueError as e:st.error(str(e))
  if CATALOG[kind].batch:
   pid=st.selectbox('批次所属产品',[p['id'] for p in job['products']],format_func=lambda x:title_for(job,objects(job)[x],'product'),key='structure_batch_product_'+jid)
   if st.button('增加一个批次',key='structure_batch_'+jid):
    try:
     saved=persist_edits(repo,job,kind);p=objects(saved)[pid]
     if not p.get('batches') and any(p['values'].get(k) for k in ('batch_no','mfg_date','exp_date')):
      b=empty_object('b');b.pop('placeholder',None)
      for key in ('batch_no','mfg_date','exp_date'):
       if key in p['values']:
        b['values'][key]=p['values'].pop(key);b['evidence'][key]=p['evidence'].pop(key,[])
        if key in p.get('conflicts',{}):b['conflicts'][key]=p['conflicts'].pop(key)
        if key in p.get('review_pending',[]):b.setdefault('review_pending',[]).append(key);p['review_pending'].remove(key)
        if key in p.get('confirmed',{}):b.setdefault('confirmed',{})[key]=p['confirmed'].pop(key)
      p.setdefault('batches',[]).append(b)
     p.setdefault('batches',[]).append(empty_object('b'));store.save(saved,saved['revision']);st.rerun()
    except ValueError as e:st.error(str(e))
  empty=[(p['id'],p) for p in job['products'] if not p['values'] and not p.get('batches') and len(job['products'])>1]+[(b['id'],b) for p in job['products'] for b in p.get('batches',[]) if not b['values']]
  for oid,obj in empty:
   if st.button('移除空白项 · '+oid[:6],key='remove_empty_'+jid+'_'+oid):
    saved=persist_edits(repo,job,kind)
    saved['products']=[p for p in saved['products'] if p['id']!=oid or p['values'] or p.get('batches')]
    for p in saved['products']:p['batches']=[b for b in p.get('batches',[]) if b['id']!=oid or b['values']]
    store.save(saved,saved['revision']);st.rerun()


def history_panel(repo,job,kind):
 store=JobStore(repo);sources=[j for j in store.list() if j['id']!=job['id'] and j['products']]
 with st.expander('从历史票带入信息'):
  if not sources:st.caption('保存过其他票后，可在这里选择需要重复使用的信息。');return
  source_id=st.selectbox('来源票',[j['id'] for j in sources],format_func=lambda x:next(j['name']+' · '+x[:6] for j in sources if j['id']==x),key='history_source_'+job['id'])
  source=store.get(source_id);source_scopes=list(owners(source,kind));target_scopes=list(owners(job,kind))
  source_ids=[o.get('id') or 'shipment' for o,t in source_scopes]
  oid=st.selectbox('来源信息',source_ids,format_func=lambda x:next(title_for(source,o,t) for o,t in source_scopes if (o.get('id') or 'shipment')==x),key='history_owner_'+job['id']+'_'+source_id)
  owner=next(t for o,t in source_scopes if (o.get('id') or 'shipment')==oid)
  targets=[o.get('id') or 'shipment' for o,t in target_scopes if t==owner]
  if not targets:st.caption('请先在本票增加对应的合同、产品或批次。');return
  tid=st.selectbox('带入到本票',targets,format_func=lambda x:next(title_for(job,o,t) for o,t in target_scopes if (o.get('id') or 'shipment')==x),key='history_target_'+job['id']+'_'+owner)
  options=transfer_options(source,oid,kind);names={x['key']:x['label']+' = '+str(x['value']) for x in options}
  keys=st.multiselect('选择要带入的字段',list(names),default=[x['key'] for x in options if x['default']],format_func=names.get,key='history_fields_'+job['id']+'_'+source_id+'_'+oid+'_'+kind)
  old=objects(job)[tid]['values'];new=objects(source)[oid]['values'];conflicts=[k for k in keys if old.get(k) not in (None,'',new[k])]
  if keys:st.dataframe([{'字段':LABELS[k],'本票已有':old.get(k) or '未填','将带入':new[k]} for k in keys],hide_index=True,width='stretch')
  overwrite=st.checkbox('我已核对差异，允许覆盖上表中本票已有的不同值',key='history_overwrite_'+job['id']) if conflicts else False
  st.caption('带入后先作为待核对值保存；不复制原票文件、签章或回件批准。价格需同时选择单位和币种，数量需对应单位。')
  if st.button('带入所选信息',disabled=not keys or bool(conflicts and not overwrite),key='history_apply_'+job['id']):
   try:
    saved=persist_edits(repo,job,kind);saved=transfer_fields(saved,source,oid,tid,keys,kind,overwrite);store.save(saved,saved['revision']);st.rerun()
   except ValueError as e:st.error(str(e))


def candidate_panel(repo,job,kind):
 for item in job.get('unmatched',[]):
  with st.expander('确认识别内容归属 · '+(item['product']['values'].get('name_cn') or item['product']['values'].get('name_en') or '未命名产品'),expanded=True):
   st.write({LABELS.get(k,k):v for k,v in item['product']['values'].items()})
   cids=[c['id'] for c in job['contracts']]+['new'];names={c['id']:title_for(job,c,'contract') for c in job['contracts']};names['new']='作为新合同'
   cid=st.selectbox('对应合同',cids,format_func=names.get,key='match_contract_'+item['id'])
   pids=[p['id'] for p in job['products'] if p['contract_id']==cid]+['new'];pnames={p['id']:title_for(job,p,'product') for p in job['products']};pnames['new']='作为新产品'
   pid=st.selectbox('对应产品',pids,format_func=pnames.get,key='match_product_'+item['id']+'_'+cid)
   a,b=st.columns(2)
   if a.button('应用对应关系',key='match_apply_'+item['id']):
    try:
     saved=persist_edits(repo,job,kind);saved=accept_candidate(saved,item['id'],None if cid=='new' else cid,None if pid=='new' else pid);JobStore(repo).save(saved,saved['revision']);st.rerun()
    except ValueError as e:st.error(str(e))
   if b.button('本票不采用这项识别内容',key='match_skip_'+item['id']):
    saved=persist_edits(repo,job,kind);saved['unmatched']=[x for x in saved['unmatched'] if x['id']!=item['id']];saved['audit'].append(dict(at=now(),event='candidate_not_adopted',candidate=item['id']));JobStore(repo).save(saved,saved['revision']);st.rerun()
