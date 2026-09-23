"""Contract-first workspace; each processing action is explicit and resumable."""
from pathlib import Path
from copy import deepcopy
import io,json,zipfile
import streamlit as st
from shipping.agent import credentials
from shipping.agent.diagnostics import record_processing_error
from shipping.ui.progress import ProcessingCard
from shipping.agent.core import JobStore,LABELS,questions,resolve,checks,REQUIRED,BATCH_FIELDS
from shipping.agent.flow import prepare_sources,read_entries,read_report,extraction_current,extract_and_clean,attach_uploads
from shipping.agent.profiles import render_outputs,original,FILES
from shipping.ingestion import digest
from shipping.repository import now

from html import escape
from shipping.agent.catalog import CATALOG,NAMES,fields_for,owners,input_hash,document_checks
from shipping.agent.workflow import add_product,select_document,current_outputs,fact_object
from shipping.agent.business_flows import FLOW_KEYS,COMPONENTS,flow_for,select_flow,settlement_changes,customs_amount,review_rows,LONG_FIELDS,FIELD_HELP
from shipping.agent.preview import pdf_preview,page_images,converter
from shipping.agent.layout import layout_current

PHASES={'uploaded':'等待资料','local_ready':'文字已读取','needs_input':'有内容需要补充','ready':'数据齐全','drafts':'草稿已生成','reviewed':'人工已核对'}


def generate(repo,job,store,kind=None,on_progress=None):
 kind=kind or job.get('active_document') or job['targets'][0]
 if kind!='attachments' and job.get('files') and not extraction_current(repo,job) and job.get('manual_files')!=sorted(f['id'] for f in job['files']):raise ValueError('资料尚未完成识别。请回到资料页处理，或明确选择手动核对。')
 if kind in FLOW_KEYS:
  from shipping.agent.business_packages import render_package
  outputs=render_package(repo,job,kind,on_progress=on_progress)
 else:outputs=render_outputs(repo,job,[kind])
 j=deepcopy(job)
 if kind=='shipping_draft':
  from shipping.agent.business_flows import fact_snapshot
  j['draft_baseline']={'revision':job['revision'],'facts':fact_snapshot(job,kind)}
 for old in j['outputs']:
  if old['type']==kind:old['superseded']=True
 j['outputs'].extend(outputs);j['phase']='drafts'
 j['audit'].append({'at':now(),'event':'generated','document':kind,'outputs':[x['sha256'] for x in outputs]})
 return store.save(j,job['revision'])


def read_with_progress(repo,job,allow_image_fallback=False,card=None):
 own_card=card is None
 card=card or ProcessingCard(local_only=True)
 def update(event):
  card.update(stage=0,title='正在读取本票资料',detail=f"{event['file_name']} · {event['stage']}",meta=f"已处理 {event['completed_files']}/{event['total_files']} 份文件 · 本机处理，不会调用 API")
 try:sources=prepare_sources(repo,job,on_progress=update,allow_image_fallback=allow_image_fallback)
 except (ValueError,OSError):
  card.fail()
  raise
 if own_card:card.finish('本机读取完成',f"已处理 {len(job['files'])} 份文件 · 尚未调用 API")
 return sources


def connection_key(endpoint,force=False):
 """Session credential storage is independent of password-widget lifetime."""
 state=st.session_state
 if force or state.get('agent_key_endpoint')!=endpoint:
  state['agent_key_endpoint']=endpoint;state['agent_session_key']=''
  state['agent_key_saved']=False;state['agent_saved_key_digest']=None
  try:
   saved=credentials.load_key(endpoint) if credentials.available() else None
   state['agent_session_key']=saved or '';state['agent_key_saved']=bool(saved)
   state['agent_saved_key_digest']=digest(saved.strip().encode()) if saved else None
   state.pop('agent_key_error',None)
  except ValueError as exc:state['agent_key_error']=str(exc)
 key=state.get('agent_session_key','')
 try:
  valid=credentials.validate_api_key(key) if key.strip() else ''
  state['agent_key_status']='valid' if valid else 'unavailable' if state.get('agent_key_error') else 'missing'
  return valid
 except ValueError:
  state['agent_key_status']='invalid';return ''


def keychain_controls(repo,endpoint,model):
 """Load once per endpoint/session; callbacks run before widgets on the next rerun."""
 state=st.session_state
 def load():
  connection_key(endpoint,force=True)
  state['agent_api_key']=state.get('agent_session_key','')
 def save():
  try:
   credentials.save_key(endpoint,credentials.validate_api_key(state.get('agent_api_key','')))
   repo.cache_set('agent-public-config',{'endpoint':endpoint.strip(),'model':model.strip()})
   state['agent_key_saved']=True;state['agent_saved_key_digest']=digest(state['agent_api_key'].strip().encode());state.pop('agent_key_error',None)
  except ValueError as exc:state['agent_key_error']=str(exc)
 def forget():
  try:
   credentials.delete_key(endpoint)
   state['agent_api_key']='';state['agent_session_key']='';state['agent_key_saved']=False;state['agent_saved_key_digest']=None;state.pop('agent_key_error',None)
  except ValueError as exc:state['agent_key_error']=str(exc)
 if state.get('agent_key_endpoint')!=endpoint:load()
 if 'agent_api_key' not in state:state['agent_api_key']=state.get('agent_session_key','')
 key=st.text_input('API 密钥',type='password',key='agent_api_key')
 state['agent_session_key']=key
 valid_key='';validation_error=None
 if key.strip():
  try:valid_key=credentials.validate_api_key(key)
  except ValueError as exc:validation_error=str(exc)
 state['agent_key_status']='invalid' if validation_error else 'valid' if valid_key else 'unavailable' if state.get('agent_key_error') else 'missing'
 matches_saved=bool(state.get('agent_key_saved') and state.get('agent_saved_key_digest')==digest(key.strip().encode()))
 if state.get('agent_key_error'):st.warning(state['agent_key_error'])
 if validation_error:
  st.warning('密钥已保存并读取，但格式无效。请在此处替换为官网复制的完整密钥，再点“记住密钥”；重开后会自动读取。' if matches_saved else validation_error)
 elif matches_saved:st.success('此接口的密钥已保存在本机钥匙串，重开工作台会自动读取。')
 elif valid_key:st.caption('当前输入的密钥尚未保存，仅在本次会话使用；点击“记住密钥”可长期保存在本机。')
 elif state['agent_key_status']=='missing':st.info('请在上方输入 API 密钥。点击“记住密钥”后，下次重开无需再填。')
 a,b,c=st.columns(3)
 with a:st.button('记住密钥（本机）',key='agent_remember_key',on_click=save,disabled=not key.strip() or not credentials.available())
 with b:st.button('删除已保存密钥',key='agent_forget_key',on_click=forget,disabled=not state.get('agent_key_saved'))
 with c:st.button('重新读取钥匙串',key='agent_reload_key',on_click=load,disabled=not credentials.available())
 if not credentials.available():st.caption('此环境没有 Mac 钥匙串组件，仅支持临时输入密钥。')
 return valid_key


def settings_page(repo):
 st.header('识别服务设置')
 st.caption('配置一次即可。之后在资料页点击“识别并填写”，程序会自动读取文字、识别图片并填写核对表。')
 config=repo.cache_get('agent-public-config') or {'endpoint':'https://api.deepseek.com','model':'deepseek-flash'}
 endpoint=st.text_input('接口地址',value=config['endpoint'],key='agent_endpoint')
 model=st.text_input('模型名称',value=config.get('model','deepseek-flash'),key='agent_model')
 keychain_controls(repo,endpoint,model)
 if st.button('保存接口地址与模型',key='agent_save_api'):
  from urllib.parse import urlsplit
  parsed=urlsplit(endpoint.strip())
  if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not model.strip():st.error('请填写不含密钥的 HTTPS 接口地址和模型名称。')
  else:
   repo.cache_set('agent-public-config',{'endpoint':endpoint.strip(),'model':model.strip()})
   st.success('设置已保存。可以返回自动出单上传资料。')
 st.divider()
 with st.expander('单证底稿'):
  from shipping.agent.profiles import ALL_FILES,ALL_HASHES,install_profile
  rows=[]
  for t,name in ALL_FILES.items():
   try:original(repo,t);state='已安装'
   except ValueError:state='待导入'
   rows.append({'底稿':name,'状态':state})
  st.dataframe(rows,hide_index=True,width='stretch')
  templates=st.file_uploader('导入已适配的原始 Word / Excel',type=['docx','xlsx'],accept_multiple_files=True,key='install_profiles')
  if st.button('安装底稿',disabled=not templates,key='install_profiles_start'):
   try:
    import tempfile
    for upload in templates:
     kind=next((k for k,h in ALL_HASHES.items() if h==digest(upload.getvalue())),None)
     if kind is None:raise ValueError(upload.name+' 与已适配底稿不一致，需要先核对字段位置。')
     with tempfile.TemporaryDirectory() as td:
      p=Path(td)/ALL_FILES[kind];p.write_bytes(upload.getvalue());install_profile(repo,p,kind)
    st.success('底稿已安装，固定公司信息会从你的底稿读取。')
   except ValueError as exc:st.error(str(exc))
  customs_template=st.file_uploader('报关 / 产地证固定资料（已适配的报关或出运草件 PDF）',type=['pdf'],key='business_template')
  if st.button('安装 PDF 固定资料',disabled=not customs_template):
   try:
    from shipping.agent.business_packages import install_business_profile,install_co_profile
    if digest(customs_template.getvalue())=='bf42e2f1e09b8896fd705b44e2c491eeaf8dae1a96b5b68e3a80dce4621c9448':install_co_profile(repo,customs_template.getvalue())
    else:install_business_profile(repo,customs_template.getvalue())
    st.success('固定资料已安装；未导入旧票数据或印章。')
   except ValueError as exc:st.error(str(exc))
 with st.expander('本机数据备份'):
  st.caption('备份包含业务资料和底稿，只供内部保存；不包含钥匙串密钥。')
  if st.button('创建备份',key='agent_backup_create'):
   from shipping.services import Workbench
   st.session_state['agent_backup_bytes']=Workbench(repo.root).backup()
  if st.session_state.get('agent_backup_bytes'):st.download_button('下载内部备份',st.session_state['agent_backup_bytes'],file_name='出运工作台_内部备份.zip',key='agent_backup_download',on_click='ignore')
  archive=st.file_uploader('恢复内部备份',type=['zip'],key='agent_restore_file')
  if archive and st.button('校验并恢复备份',key='agent_restore'):
   try:
    from shipping.services import Workbench
    Workbench(repo.root).restore(archive.getvalue());st.session_state.clear();st.rerun()
   except (ValueError,KeyError,OSError) as exc:st.error('备份未恢复：'+str(exc))
 st.button('返回自动出单',key='agent_back',on_click=lambda:st.session_state.update(nav_page='自动出单'))


def intake(repo,job,kind=None,review_mode=False):
 kind=kind or (job or {}).get("active_document") or ((job or {}).get("targets") or ["customs_set"])[0]
 spec=CATALOG[kind]
 store=JobStore(repo);jid=job['id'] if job else 'new'
 prior_error=st.session_state.pop('agent_error_'+jid,None)
 if prior_error:st.error(prior_error)
 st.subheader('添加'+spec.name+'所需资料')
 st.caption(spec.inputs)
 if files_count:=len((job or {}).get('files',[])):st.caption(f'本票已有 {files_count} 份资料可复用，无需重复上传。')
 uploads=st.file_uploader('添加本票资料',type=['pdf','docx','xlsx','png','jpg','jpeg'],accept_multiple_files=True,key='agent_upload_'+jid)
 if review_mode:
  st.caption('上传后自动保存到本机。点击“识别并填写”才会开始识别；正式回件也在这里上传。' if kind=='settlement' else '上传后自动保存到本机。点击“识别并填写”才会开始识别。')
  if uploads:
   try:
    from shipping.agent.review_sections import save_materials
    saved=save_materials(repo,job,uploads,kind)
    if saved['id']!=job['id'] or saved['revision']!=job['revision']:
     if jid=='new':st.session_state.pop('review_new',None)
     st.session_state['agent_next_selected']=saved['id'];st.session_state['agent_next_step_'+saved['id']]='资料';st.rerun()
   except (ValueError,OSError) as exc:st.error(str(exc));return
 files=(job or {}).get('files',[])
 if files or uploads:
  names=[f['name'] for f in files]+[f.name for f in uploads or [] if digest(f.getvalue()) not in {x['id'] for x in files}]
  st.write('本次处理资料：'+'、'.join(names))
  if job and extraction_current(repo,job) and not uploads:st.caption('这些资料已填写，可直接进入“核对”；补传后再点击下方按钮。')
  elif job and job['products']:st.info('补传后会联合已有资料更新结果，并保留你已经确认的内容。')
 config=repo.cache_get('agent-public-config') or {'endpoint':'https://api.deepseek.com','model':'deepseek-flash'}
 key=connection_key(config['endpoint'])
 st.caption('点击“识别并填写”，将先尝试本机读取已适配模板；如需自动识别，即确认将以上文字及必要图片发送至 '+config['endpoint']+' 。已完成的结果会优先使用缓存。')
 if not key or not config.get('model','').strip():
  status=st.session_state.get('agent_key_status')
  st.info('识别服务尚未配置好，请先到设置中'+('修正已保存的无效密钥。' if status=='invalid' else '检查密钥读取失败提示。' if status=='unavailable' else '设置接口和密钥；以后无需每票填写。'))
  st.button('前往设置',key='agent_go_settings_'+jid,on_click=lambda:st.session_state.update(nav_page='设置'))
 start=st.button('识别并填写',key='agent_extract_'+jid,type='primary',disabled=not (files or uploads))
 if start:
  card=ProcessingCard(with_images=True)
  def failed(message):
   card.fail()
   if job and jid!=job['id']:
    st.session_state['agent_error_'+job['id']]=message
    st.rerun()  # Show the persisted first job and its retry button immediately.
   st.error(message)
  try:
   if review_mode:
    from .review_first import persist_edits
    job=persist_edits(repo,job,kind)
   elif job is None:job=store.create(Path(uploads[0].name).stem,[kind])
   job=attach_uploads(repo,job,uploads)
   st.session_state['agent_next_selected']=job['id']
   sources=read_with_progress(repo,job,allow_image_fallback=True,card=card)
   from shipping.agent.native import recognise
   from shipping.agent.flow import apply_extraction
   native=recognise(repo,job,sources)
   if native is not None:new=apply_extraction(job,native,sources,'本机模板读取',replace_machine=True)
   elif not key:raise ValueError('这些资料需要识别服务；请到设置配置密钥，或到“核对”直接填写')
   else:new=extract_and_clean(repo,job,sources,{**config,'document_kind':kind},key,True,True,on_progress=card.api_progress)
   card.verify();new=select_flow(new,kind);job=store.save(new,job['revision'])
   card.finish('已填写，开始核对')
   st.session_state['agent_next_step_'+job['id']]='核对'
   st.session_state['agent_done_'+job['id']]=f'已填写 {len(job["products"])} 个产品、{job.get("cleaning_summary",{}).get("batches",0)} 个批次。请核对已填内容，补充缺失或冲突项。'
   st.rerun()
  except (ValueError,OSError) as exc:
   failed(str(exc)+'。请检查提示后点击“识别并填写”重试。')
  except Exception as exc:
   error_id=record_processing_error(repo,exc,card.stages[card.stage])
   failed('处理时出现程序错误，流程已停止；资料和已完成缓存保留。错误编号：'+(error_id or '诊断记录未能保存'))
 if not review_mode:
  with st.expander('暂不识别，直接填写'):
   st.caption('资料会先保存在本机。适合已有明确数据、无需自动识别的时候。')
   product_name=st.text_input('产品名称',key='agent_manual_name_'+jid,placeholder='填写本票产品名称') if not (job or {}).get('products') else ''
   contract_no=st.text_input('报关合同协议号' if kind=='customs_set' else '合同号',key='agent_manual_contract_'+jid) if not (job or {}).get('products') else ''
   if st.button('保存资料并开始核对',key='agent_manual_'+jid,disabled=not ((job or {}).get('products') or product_name.strip())):
    try:
     if job is None:job=store.create(product_name.strip(),[kind])
     job=attach_uploads(repo,job,uploads)
     if not job['products']:
      job=add_product(job,contract_no,product_name)
      if kind=='customs_set' and contract_no.strip():job=resolve(job,{job['contracts'][-1]['id']+'.customs_contract_no':contract_no},'本次手动填写的报关合同协议号')
     job=select_flow(job,kind);job['manual_entry']=True;job['manual_files']=sorted(f['id'] for f in job['files'])
     job=store.save(job,job['revision']);st.session_state['agent_next_selected']=job['id'];st.session_state['agent_next_step_'+job['id']]='核对';st.rerun()
    except ValueError as exc:st.error(str(exc))
 if files:
  with st.expander('处理明细与识别原文'):
   labels={'read':'已读取','empty':'未读到文字','error':'读取失败','pending':'待处理'}
   st.dataframe([{'文件':f['name'],'状态':labels.get(read_report(repo,f)['status'],'待处理')} for f in files],hide_index=True,width='stretch')
   for f in files:
    entries=read_entries(repo,f)
    if entries:
     st.write(f['name'])
     for entry in entries:
      source=f['id']+':'+entry['locator'];st.text(entry['locator']+'\n'+job.get('extracted_source_texts',{}).get(source,entry['text']))


def page(repo):
 from .review_first import page as review_workspace
 return review_workspace(repo)


def review_page(repo,job,store,kind,pending,step_key):
 jid=job['id']
 if not job['products']:st.info('先上传并识别资料，或在资料页直接填写。');return
 if not extraction_current(repo,job) and job.get('manual_files')!=sorted(f['id'] for f in job['files']):st.warning('资料有更新，识别后再生成；下方保留此前填写内容。')
 for issue in document_checks(job,kind):st.error(issue)
 if kind=='settlement':
  changes=settlement_changes(job)
  if changes:st.dataframe([{'字段':LABELS[x['field']],'草件原值':x['before'] or '未填','现在值':x['after'] or '未填'} for x in changes],hide_index=True,width='stretch')
  else:st.caption('草件共有信息已沿用；目前没有保存后的变更。')
 st.caption('只显示'+NAMES[kind]+'使用的内容。已填写项可展开检查，修改后点击保存。')
 file_names={f['id']:f['name'] for f in job['files']}
 def source_label(source):
  if source=='manual':return '人工核实'
  if source=='calculated':return '程序计算'
  fid,_,location=source.partition(':');return file_names.get(fid,'原件')+' · '+location
 question_map={q['key']:q for q in pending};scopes=list(owners(job,kind));rows=[]
 with st.form('agent_fields_'+jid+'_'+str(job['revision'])):
  answers={}
  for obj,owner in scopes:
   oid=obj.get('id') or 'shipment';values=obj['values']
   title={'contract':'单证编号及抬头' if kind=='customs_set' else '合同及发票','product':'产品与报关明细' if kind=='customs_set' else '产品','batch':'批次','shipment':'运输及费用' if kind=='customs_set' else '本票信息'}[owner]
   title+=' · '+(values.get('name_cn') or values.get('name_en') or values.get('batch_no') or values.get('contract_no') or '')
   fields=fields_for(kind,owner,job=job)
   if owner=='product' and obj.get('batches'):fields-={'batch_no','mfg_date','exp_date'}
   ordered=[k for k in LABELS if k in fields]
   missing=[k for k in ordered if oid+'.'+k in question_map];filled=[k for k in ordered if k not in missing]
   st.markdown('<div class="review-owner">'+escape(title.rstrip(' ·'))+'</div>',unsafe_allow_html=True)
   def inputs(keys):
    cols=st.columns(2,gap='large')
    for i,k in enumerate(keys):
     path=oid+'.'+k;value0=str(values.get(k) or '');evidence=obj['evidence'].get(k,[])
     with cols[i%2]:
      label=LABELS[k]+(' · 待核对' if path in question_map else '')
      widget_key='af_'+jid+'_'+str(job['revision'])+'_'+path
      if k=='transport_mode':
       modes=['','空运','陆运'];value=st.selectbox(label,modes,index=modes.index(value0) if value0 in modes else 0,format_func=lambda x:x or '请选择运输方式',key=widget_key)
      elif k in LONG_FIELDS:value=st.text_area(label,value=value0,key=widget_key,height=110,help=FIELD_HELP.get(k))
      else:value=st.text_input(label,value=value0,key=widget_key,help=FIELD_HELP.get(k))
      if value!=value0 or (k in obj.get('conflicts',{}) and value.strip()):answers[path]=value
      if k in obj.get('conflicts',{}):
       st.warning('资料不一致：'+' / '.join(str(v) for v in obj['conflicts'][k]))
       for e in evidence:st.caption(str(e.get('value',''))+' · '+source_label(e.get('source',''))+' · '+str(e.get('quote','')))
      elif value0:st.caption('；'.join(dict.fromkeys(source_label(e.get('source','')) for e in evidence)) or '已填写')
      elif path in question_map:
       rejected=[issue for issue in job.get('issues',[]) if issue.get('key')==path]
       if rejected:
        for issue in rejected:st.caption(issue.get('message','需要核实')+(' · 原文：'+str(issue['quote']) if issue.get('quote') else ''))
       else:st.caption('资料中没有明确依据，请补充。')
      for e in evidence:rows.append({'归属':title,'字段':LABELS[k],'已填值':value0,'原文':e.get('quote',''),'来源':source_label(e.get('source',''))})
   inputs(missing)
   if filled:
    with st.expander(f'已填写及选填 · {title}（{len(filled)} 项）',expanded=not missing and kind!='settlement'):inputs(filled)
   if kind=='customs_set' and owner=='product':
    amount=customs_amount(values)
    if amount:st.caption('报关货值：'+values.get('customs_currency','')+' '+amount+'（不含样品和运保费）')
  reason=st.text_input('修改或补充依据',value='本票人工核实',key='agent_answer_reason_'+jid)
  if st.form_submit_button('保存核对',type='primary'):
   try:
    updated=resolve(job,answers,reason);store.save(updated,job['revision']);st.session_state['agent_saved_'+jid]=True;st.rerun()
   except ValueError as exc:st.error(str(exc))
 if st.session_state.pop('agent_saved_'+jid,False):st.success('已保存，本票其他文件会复用这些信息。')
 if kind=='customs_set':samples_panel(repo,job,store)
 if kind=='settlement':returns_panel(repo,job,store)
 st.button('查看文件与生成',key='agent_to_files_'+jid,type='primary',on_click=lambda:st.session_state.update({step_key:'文件'}))
 with st.expander('查看原文依据'):
  if rows:st.dataframe(rows,hide_index=True,width='stretch')
  else:st.caption('暂无识别来源记录。')
 with st.expander('补充产品或批次'):
  with st.form('agent_add_product_'+jid):
   cno=st.text_input('所属合同号',key='add_contract_'+jid);pname=st.text_input('产品名称',key='add_name_'+jid)
   if st.form_submit_button('添加产品'):
    try:store.save(add_product(job,cno,pname),job['revision']);st.rerun()
    except ValueError as exc:st.error(str(exc))
  if CATALOG[kind].batch:
   with st.form('agent_add_batch_'+jid):
    pid=st.selectbox('所属产品',[p['id'] for p in job['products']],format_func=lambda x:next(p['values'].get('name_cn',x) for p in job['products'] if p['id']==x));bno=st.text_input('批号')
    if st.form_submit_button('添加批次'):
     if bno.strip():
      from uuid import uuid4
      updated=deepcopy(job);p=next(p for p in updated['products'] if p['id']==pid)
      if any(b['values'].get('batch_no')==bno.strip() for b in p.get('batches',[])):st.error('这个批号已经存在')
      else:p.setdefault('batches',[]).append(fact_object({'batch_no':bno.strip()},id='b'+uuid4().hex[:12]));store.save(updated,job['revision']);st.rerun()
     else:st.error('请填写批号')


def files_page(repo,job,store,kind,pending):
 jid=job['id'];outputs=current_outputs(job,kind)
 if pending:st.info(f'这份文件还有 {len(pending)} 项待核对，可先生成检查稿；补齐后再确认交付。')
 if job['products'] and st.button('生成'+NAMES[kind],key='agent_generate_'+jid,type='primary'):
  try:
   with st.spinner('正在填写模板并检查排版…'):generate(repo,job,store,kind)
   st.rerun()
  except (ValueError,OSError) as exc:st.error(str(exc))
 if not outputs:
  if any(o['type']==kind for o in job.get('outputs',[])):st.warning('这份文件使用的数据已修改，请重新生成。旧文件保留在历史记录中。')
  else:st.caption('生成后可预览并下载这份文件。')
 for output in outputs:
  path=repo.safe_path(output['path']);data=path.read_bytes()
  if digest(data)!=output['sha256']:st.error('文件校验失败，请重新生成');continue
  layout_ok=layout_current(repo,output);reviewed=bool(output.get('reviewed')) and layout_ok
  if layout_ok:st.caption('排版检查通过 · '+str(output['layout_check']['pages'])+' 页 · 已检查文字完整、越界及压线')
  else:st.warning('此文件还没有通过当前排版检查，请重新生成后再确认交付。')
  st.markdown('<div class="export-heading"><strong>'+escape(path.name)+'</strong><span>'+('已核对' if reviewed else '待核对')+'</span></div>',unsafe_allow_html=True)
  a,b=st.columns([1,3])
  a.download_button('下载 '+path.name,data,file_name=path.name.replace('_待核对','') if reviewed else path.name,key='agent_dl_'+output['sha256'],on_click='ignore',width='stretch')
  if b.button('预览页面',key='preview_'+output['sha256']):
   try:
    with st.spinner('正在本机生成预览…'):pdf_preview(repo,output)
    st.session_state['preview_show_'+output['sha256']]=True
   except ValueError as exc:st.warning(str(exc))
  if st.session_state.get('preview_show_'+output['sha256']):
   pdf=path.with_suffix('.pdf').read_bytes()
   with st.expander('页面预览',expanded=True):
    for page,pic in page_images(pdf):st.image(pic,caption=f'第 {page} 页',width='stretch')
    st.download_button('下载 PDF',pdf,file_name=path.with_suffix('.pdf').name,key='pdf_'+output['sha256'],on_click='ignore')
 if outputs:
  ack=st.checkbox('我已检查这份文件的数据、批次和页面显示',key='agent_review_ack_'+jid+'_'+kind+'_'+str(job['revision']))
  if st.button('确认这份文件',disabled=not ack or bool(pending) or bool(document_checks(job,kind)) or not all(layout_current(repo,o) for o in outputs),key='agent_review_'+jid):
   updated=deepcopy(job);hashes={o['sha256'] for o in outputs}
   for o in updated['outputs']:
    if o['sha256'] in hashes:o['reviewed']=True
   updated['audit'].append({'at':now(),'event':'document_reviewed','document':kind});store.save(updated,job['revision']);st.rerun()
 with st.expander('这份文件的历史记录'):
  previous=[o for o in job.get('output_history',[])+job.get('outputs',[]) if o['type']==kind and o not in outputs]
  for o in reversed(previous):
   path=repo.safe_path(o['path'])
   if path.exists() and digest(path.read_bytes())==o['sha256']:st.download_button('历史版本 · '+o.get('created_at','')[:16],path.read_bytes(),file_name='历史_'+path.name,key='history_'+digest(o['path'].encode()),on_click='ignore')
  if not previous:st.caption('暂无旧版本。')


def attachment_page(repo,job,store):
 jid=job['id'] if job else 'new'
 uploads=st.file_uploader('添加交货附件原件',type=['pdf','docx','xlsx','png','jpg','jpeg','zip'],accept_multiple_files=True,key='agent_attachments_'+jid)
 if st.button('保存附件',disabled=not uploads,key='agent_save_attachments_'+jid):
  try:
   if job is None:job=store.create('交货资料',['attachments'])
   job=attach_uploads(repo,job,uploads,allow_archives=True);st.session_state['agent_next_selected']=job['id'];st.rerun()
  except ValueError as exc:st.error(str(exc))
 if not job or not (job['files']+job.get('attachments',[])):st.caption('COA、鉴定和签章文件原样保留，不重新制作。');return
 choices={f['id']:f['name'] for f in job['files']+job.get('attachments',[])}
 with st.form('attachment_select_'+jid):
  selected=st.multiselect('本次交付的原件',list(choices),default=[x for x in job.get('attachment_selection',[]) if x in choices],format_func=choices.get)
  if st.form_submit_button('打包所选原件',type='primary'):
   try:
    updated=deepcopy(job);updated['attachment_selection']=selected;updated=store.save(updated,job['revision']);generate(repo,updated,store,'attachments');st.rerun()
   except ValueError as exc:st.error(str(exc))
 for out in current_outputs(job,'attachments'):
  path=repo.safe_path(out['path']);data=path.read_bytes()
  if digest(data)==out['sha256']:st.download_button('下载交货附件包',data,file_name=path.name,key='attach_download_'+out['sha256'],on_click='ignore')


def business_files_page(repo,job,store,kind,pending):
 """One scoped action produces the full selected package, with explicit return binding."""
 from shipping.agent.review_export import workbook
 from shipping.agent.business_packages import package_issues,package_current,validate_returns
 from shipping.agent.review import prune_empty
 outputs=current_outputs(job,kind);jid=job['id'];issues=package_issues(prune_empty(job),kind)
 if kind=='settlement':issues+=validate_returns(repo,job)
 st.subheader(NAMES[kind])
 if pending:st.info(f'还有 {len(pending)} 项资料待补齐，请到核对页保存后生成。')
 for issue in issues:st.warning(issue)
 if st.button('生成'+NAMES[kind],type='primary',disabled=bool(pending or issues),key='package_generate_'+jid+'_'+kind):
  status=st.empty()
  try:
   with st.spinner('正在填写、排版并组装资料…'):generate(repo,job,store,kind,on_progress=lambda label:status.info(label))
   st.rerun()
  except (ValueError,OSError) as exc:status.empty();st.error(str(exc))
 if not outputs:
  if any(o['type']==kind for o in job.get('outputs',[])):st.warning('资料或回件已修改，请重新生成。旧版本保留在历史记录中。')
  else:st.caption('生成后会得到整套 PDF、可编辑文件和资料包；无需逐份生成。')
 for out in outputs:
  valid=package_current(repo,out);reviewed=valid and out.get('reviewed',False)
  if not valid:st.error('文件或排版规则已变化，请重新生成。');continue
  st.success(('已核对 · ' if reviewed else '待人工核对 · ')+str(out['layout_check']['pages'])+' 页 · 自动排版检查通过')
  st.dataframe([{'资料':x['name'],'页码':str(x['start']) if x['pages']==1 else str(x['start'])+'–'+str(x['start']+x['pages']-1),'检查':'原件保留' if x.get('original') else '排版通过'} for x in out['components']],hide_index=True,width='stretch')
  zpath=repo.safe_path(out['path']);ppath=repo.safe_path(out['pdf_path']);cols=st.columns(2)
  def filename(path):return path.name.replace('_待核对','') if reviewed else path.name
  cols[0].download_button('下载整套 PDF',ppath.read_bytes(),file_name=filename(ppath),key='package_pdf_'+out['sha256'],on_click='ignore',width='stretch')
  cols[1].download_button('下载资料包（含可编辑文件）',zpath.read_bytes(),file_name=filename(zpath),key='package_zip_'+out['sha256'],on_click='ignore',width='stretch')
  if st.button('预览整套资料',key='package_preview_'+out['sha256']):st.session_state['package_show_'+out['sha256']]=True
  if st.session_state.get('package_show_'+out['sha256']):
   with st.expander('整套页面',expanded=True):
    for number,pic in page_images(ppath.read_bytes()):st.image(pic,caption='第 '+str(number)+' 页',width='stretch')
  ack=st.checkbox('我已核对整套资料的数据、对应回件和页面显示',key='package_ack_'+out['sha256']+'_'+str(job['revision']))
  if st.button('确认本套资料',disabled=not ack or bool(pending or issues),key='package_review_'+out['sha256']):
   updated=deepcopy(job)
   for old in updated['outputs']:
    if old['sha256']==out['sha256']:old['reviewed']=True
   updated['audit'].append({'at':now(),'event':'package_reviewed','document':kind,'sha256':out['sha256']});store.save(updated,job['revision']);st.rerun()
 with st.expander('核对表及历史版本'):
  st.download_button('下载核对表',workbook(job,kind),file_name=NAMES[kind]+'_核对信息.xlsx',key='flow_review_'+jid+'_'+kind,on_click='ignore')
  for o in reversed(job.get('output_history',[])+job.get('outputs',[])):
   legacy={'shipping_draft':{'customer','invoice','packing','advice','awb'},'settlement':{'customer'},'consignment':{'booking','road_booking'},'customs_set':set()}
   if (o['type']!=kind and o['type'] not in legacy[kind]) or o in outputs:continue
   path=repo.safe_path(o['path'])
   if path.exists() and digest(path.read_bytes())==o['sha256']:st.download_button('历史版本 · '+o.get('created_at','')[:16],path.read_bytes(),file_name='历史_'+path.name,key='package_history_'+digest(o['path'].encode()),on_click='ignore')


def samples_panel(repo,job,store):
 from shipping.agent.business_packages import absent,save_samples
 import pandas as pd
 labels={'name':'样品名称','quantity':'数量','unit':'单位','unit_price':'单价（报关币种）','net_kg':'净重 kg','hs_code':'商品编码','origin':'原产国','source':'境内货源地','tax':'征免方式','purpose':'用途 / 不收汇说明'}
 for p in job['products']:
  v=p['values'];rows=job.get('customs_samples',{}).get(p['id'],[])
  if not v.get('samples_declaration') and not rows:continue
  if absent(v.get('samples_declaration')) and not rows:continue
  with st.expander('报关样品明细 · '+v.get('name_cn',p['id']),expanded=True):
   st.caption('主货净重填写不含样品的净重；这里逐项填写样品净重。托数、毛重和体积使用包含样品后的实测总数。')
   st.caption('样品说明：'+v.get('samples_declaration','待核对'))
   with st.form('samples_'+job['id']+'_'+p['id']+'_'+str(job['revision'])):
    frame=pd.DataFrame(rows,columns=list(labels)).fillna('')
    values=st.data_editor(frame,num_rows='dynamic',hide_index=True,width='stretch',column_config={k:st.column_config.TextColumn(v) for k,v in labels.items()})
    if st.form_submit_button('保存样品明细'):
     try:store.save(save_samples(job,p['id'],values.fillna('').to_dict('records')),job['revision']);st.rerun()
     except ValueError as exc:st.error(str(exc))


def returns_panel(repo,job,store,allow_upload=True):
 from shipping.agent.business_packages import parse_pages,save_returns,return_page_count
 from pypdf import PdfReader
 st.subheader('正式回件')
 if allow_upload:
  uploads=st.file_uploader('添加已确认运输单据和正式产地证',type=['pdf'],accept_multiple_files=True,key='return_uploads_'+job['id'])
  if st.button('保存回件',disabled=not uploads,key='return_save_uploads_'+job['id']):
   try:attach_uploads(repo,job,uploads,allow_archives=True);st.rerun()
   except ValueError as exc:st.error(str(exc))
 files={f['id']:f for f in job['files']+job.get('attachments',[]) if Path(f['name']).suffix.lower()=='.pdf'}
 if not files:st.caption('请在左侧“资料”上传正式 PDF；每个产品对应一份运输回件和一份产地证。');return
 with st.form('returns_'+job['id']+'_'+str(job['revision'])):
  selections=[]
  for p in job['products']:
   st.write(p['values'].get('name_cn') or p['values'].get('name_en') or p['id'])
   for role,label,refkey in [('transport','运输回件','transport_document_no'),('certificate','正式产地证','certificate_no')]:
    old=next((r for r in job.get('returns',[]) if r['product_id']==p['id'] and r['role']==role),{});ids=['']+list(files)
    ref=p['values'].get(refkey,'');default=old.get('file_id','') if old.get('file_id','') in ids else ''
    fid=st.selectbox(label+' · '+(ref or '编号待补'),ids,index=ids.index(default),format_func=lambda i:files[i]['name'] if i else '请选择对应原件',key='return_file_'+p['id']+'_'+role)
    pages=st.text_input(label+'页码（留空使用整份；例如 1,3 或 2-4）',value=','.join(map(str,old.get('pages',[]))),key='return_pages_'+p['id']+'_'+role)
    selections.append(dict(product_id=p['id'],role=role,file_id=fid,pages_text=pages,reference=ref,confirmed=True))
  ack=st.checkbox('已核对所选回件的编号、产品、数量、重量与页码，确认与本票一致',key='return_ack_'+str(job['revision']))
  if st.form_submit_button('确认回件对应关系'):
   try:
    if not ack:raise ValueError('请先核对并勾选回件对应关系')
    for r in selections:
     if not r['file_id'] or not r['reference']:raise ValueError('请先补齐单号 / 证书号并选择对应回件')
     total=return_page_count(repo,r['file_id']);r['pages']=parse_pages(r.pop('pages_text'),total)
    store.save(save_returns(repo,job,selections),job['revision']);st.rerun()
   except (ValueError,OSError) as exc:st.error(str(exc))
