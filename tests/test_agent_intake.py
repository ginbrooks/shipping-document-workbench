"""User workflow tests: one explicit action, automatic routing, safe retries."""
import io
from pathlib import Path
import pytest
from docx import Document
from shipping.repository import Repository
from shipping.agent.core import JobStore


class Upload(io.BytesIO):
 def __init__(self,name,data):super().__init__(data);self.name=name;self.size=len(data)


def contract_upload():
 doc=Document();doc.add_paragraph('国内采购合同\n合同编号：C001\n一、商品信息\n测试片\n25mg\n二、品质要求')
 out=io.BytesIO();doc.save(out)
 return Upload('合同.docx',out.getvalue())


def test_attach_auto_creates_job_and_duplicate_preserves_revision(tmp_path):
 from shipping.agent.flow import attach_uploads
 repo=Repository(tmp_path);store=JobStore(repo);upload=contract_upload()
 job=attach_uploads(repo,None,[upload]);assert len(job['files'])==1
 assert job['targets']==['customer','booking']
 same=attach_uploads(repo,job,[upload]);assert same==job
 assert store.get(job['id'])==job
 bad=Upload('bad.exe',b'data')
 with pytest.raises(ValueError):attach_uploads(repo,job,[bad])
 assert store.get(job['id'])==job


def test_failed_extraction_keeps_uploaded_files_and_old_manual_values(tmp_path,monkeypatch):
 from shipping.agent.flow import attach_uploads,extract_and_clean
 from shipping.agent import flow
 repo=Repository(tmp_path);job=attach_uploads(repo,None,[contract_upload()])
 def fail(*args,**kwargs):raise ValueError('模拟服务不可用')
 monkeypatch.setattr(flow,'extract_api',fail)
 with pytest.raises(ValueError):extract_and_clean(repo,job,{job['files'][0]['id']+':1':'text'},{},'test',True,False)
 assert JobStore(repo).get(job['id'])==job
 assert repo.file_bytes(job['files'][0]['id'])


def test_pdf_text_pages_skip_vision_but_scanned_page_is_kept(tmp_path):
 from shipping.agent.vision import collect_images
 from pypdf import PdfWriter,PdfReader
 from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
 from PIL import Image
 writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
 font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
 page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
 stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 40 750 Td (Contract C001 with enough native readable text for extraction.) Tj ET')
 page[NameObject('/Contents')]=writer._add_object(stream)
 image_pdf=io.BytesIO();Image.new('RGB',(40,40),'white').save(image_pdf,format='PDF');image_pdf.seek(0)
 writer.add_page(PdfReader(image_pdf).pages[0]);buf=io.BytesIO();writer.write(buf)
 repo=Repository(tmp_path);file=repo.put_file('mixed.pdf',buf.getvalue())
 images=collect_images(repo,{'files':[file]})
 assert len(images)==1 and images[0]['sources']==[file['id']+':page:2']


def test_single_action_intake_fills_and_navigates_without_technical_controls(tmp_path,monkeypatch):
 from streamlit.testing.v1 import AppTest
 import streamlit as st
 from shipping.agent import credentials,flow
 from shipping.ui import agent
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path))
 monkeypatch.setattr(credentials,'available',lambda:True)
 monkeypatch.setattr(credentials,'load_key',lambda endpoint:'synthetic-valid-key')
 repo=Repository(tmp_path);repo.cache_set('agent-public-config',{'endpoint':'https://api.deepseek.com','model':'deepseek-flash'})
 uploads=[contract_upload()];monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:uploads)
 calls=[]
 def extract(repo,job,sources,config,key,authorized,with_images,on_progress=None):
  calls.append((authorized,with_images,len(job['files'])))
  return flow.apply_extraction(job,flow.local_rules(sources),sources,'synthetic protocol test',True)
 monkeypatch.setattr(agent,'extract_and_clean',extract)
 app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=20).run()
 assert not calls and not app.exception
 app.radio(key='agent_step_new').set_value('资料').run()
 job=JobStore(repo).list()[0]
 assert not calls and job['files']
 assert not any('密钥' in e.label or '模型' in e.label for e in app.text_input)
 assert not any('API' in e.label or '发送' in e.label for e in app.checkbox)
 assert any('https://api.deepseek.com' in e.value for e in app.caption)
 app.button(key='agent_extract_'+job['id']).click().run()
 assert not app.exception and not app.error
 assert calls==[(True,True,1)]
 job=JobStore(repo).list()[0]
 assert app.radio(key='agent_step_'+job['id']).value=='核对'
 assert job['products'][0]['values']['name_cn']=='测试片'


def test_missing_configuration_has_settings_entry_without_legacy_shipment(tmp_path,monkeypatch):
 from streamlit.testing.v1 import AppTest
 from shipping.agent import credentials
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path))
 monkeypatch.setattr(credentials,'available',lambda:False)
 app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run()
 assert not app.exception
 app.radio(key='agent_step_new').set_value('资料').run()
 assert app.button(key='agent_extract_new').disabled
 app.button(key='agent_go_settings_new').click().run()
 assert not app.exception
 assert app.text_input(key='agent_endpoint')
 assert app.text_input(key='agent_api_key')


def test_first_upload_failure_can_retry_once_without_reupload(tmp_path,monkeypatch):
 from streamlit.testing.v1 import AppTest
 import streamlit as st
 from shipping.agent import credentials,flow
 from shipping.ui import agent
 monkeypatch.setenv('SHIPPING_DATA_DIR',str(tmp_path))
 monkeypatch.setattr(credentials,'available',lambda:True)
 monkeypatch.setattr(credentials,'load_key',lambda endpoint:'synthetic-key')
 uploads=[contract_upload()];monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:uploads)
 calls=[]
 def extract(repo,job,sources,*args,**kw):
  calls.append(True)
  if len(calls)==1:raise ValueError('模拟请求超时')
  return flow.apply_extraction(job,flow.local_rules(sources),sources,'synthetic test',True)
 monkeypatch.setattr(agent,'extract_and_clean',extract)
 app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=20).run()
 app.radio(key='agent_step_new').set_value('资料').run()
 job=JobStore(Repository(tmp_path)).list()[0]
 assert not calls and job['files']
 app.button(key='agent_extract_'+job['id']).click().run()
 job=JobStore(Repository(tmp_path)).list()[0];uploads.clear()
 assert any('模拟请求超时' in e.value for e in app.error)
 assert not app.button(key='agent_extract_'+job['id']).disabled
 app.button(key='agent_extract_'+job['id']).click().run()
 assert not app.exception and not app.error
 assert len(calls)==2 and app.radio(key='agent_step_'+job['id']).value=='核对'
