"""One quiet, event-driven progress card; animation never estimates API completion."""
from html import escape
import streamlit as st


def progress_html(stages,stage,title,detail='',state='running',meta=''):
 if state not in {'running','complete','error'}:raise ValueError('Invalid progress state')
 stage=max(0,min(int(stage),len(stages)-1))
 esc=lambda value:escape(str(value),quote=True)
 steps=[]
 for i,name in enumerate(stages):
  status='done' if state=='complete' or i<stage else 'active' if i==stage else 'pending'
  icon='✓' if status=='done' else f'{i+1:02}'
  current=' aria-current="step"' if i==stage and state=='running' else ''
  steps.append(f'<div class="charge-stage {status}"{current}><div class="charge-cell"><i></i></div><div class="charge-step-label"><span>{icon}</span>{esc(name)}</div></div>')
 badge={'running':'正在处理','complete':'处理完成','error':'处理暂停'}[state]
 note=meta or ('正在等待处理结果，无需重复点击' if state=='running' else '候选内容已就位，请继续核实' if state=='complete' else '本次处理未完成，请查看提示后重试')
 return (f'<section class="charge-panel {state}" aria-busy="{str(state=="running").lower()}">'
 '<div class="charge-top"><div class="charge-brand"><span class="charge-emblem" aria-hidden="true">'
 '<svg viewBox="0 0 32 32" fill="none"><path d="m16 3 11 6.5v13L16 29 5 22.5v-13L16 3Z M5 9.5 16 16l11-6.5M16 16v13M10.5 6.3l11 6.5" stroke="currentColor" stroke-width="1.3"/></svg>'
 '</span><span>SHIPMENT DESK<span class="charge-brand-sub">资料处理</span></span></div>'
 f'<span class="charge-badge"><i></i>{badge}</span></div>'
 f'<div class="charge-heading"><div role="status" aria-live="polite"><h3>{esc(title)}</h3><p>{esc(detail)}</p></div>'
 f'<span class="charge-index" aria-label="阶段 {len(stages) if state=="complete" else stage+1:02} / {len(stages):02}">{len(stages) if state=="complete" else stage+1:02}<small> / {len(stages):02}</small></span></div>'
 f'<div class="charge-stages" style="--stage-count:{len(stages)}">'+''.join(steps)+'</div>'
 f'<div class="charge-foot"><span class="charge-signal" aria-hidden="true"><i></i><i></i><i></i><i></i></span><span>{esc(note)}</span></div></section>')


class ProcessingCard:
 def __init__(self,with_images=False,local_only=False):
  self.stages=('读取资料',) if local_only else ('读取资料','识别图片','整理字段','校验回填') if with_images else ('读取资料','整理字段','校验回填')
  self.stage=0;self.title='正在读取资料';self.detail='准备本票文件';self.meta='本机处理 · 此阶段不会调用 API'
  self.slot=st.empty();self.update()
 def update(self,stage=None,title=None,detail=None,state='running',meta=None):
  if stage is not None:self.stage=stage
  if title is not None:self.title=title
  if detail is not None:self.detail=detail
  if meta is not None:self.meta=meta
  self.slot.markdown(progress_html(self.stages,self.stage,self.title,self.detail,state,self.meta),unsafe_allow_html=True)
 def api_progress(self,message):
  if message.startswith('正在校验'):
   self.verify();return
  image_stage=message.startswith(('图片识别：','API 正在读取','图片读取完成：')) and '识别图片' in self.stages
  self.update(stage=self.stages.index('识别图片' if image_stage else '整理字段'),title='正在识别附件图片' if image_stage else '正在整理产品与批次',detail=message,meta='按实际处理阶段更新 · 等待时无需重复点击')
 def verify(self):
  self.update(stage=len(self.stages)-1,title='正在校验并填入表单',detail='核对字段来源、检查冲突并保存候选值',meta='即将进入「核实与补充」')
 def finish(self,title,detail=''):
  self.update(title=title,detail=detail,state='complete',meta='处理完成 · 请查看结果')
 def fail(self):
  self.update(title='本次处理未完成',detail='错误原因见下方提示；可处理后重试',state='error',meta='处理已停止 · 不会把未完成的结果标为成功')
