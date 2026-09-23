import io,json
from pathlib import Path
import pytest
from PIL import Image
from docx import Document
from shipping.repository import Repository


def doc_image(repo):
 pic=Image.new('RGB',(16,16),'white');buf=io.BytesIO();pic.save(buf,format='PNG');raw=buf.getvalue()
 doc=Document();doc.add_picture(io.BytesIO(raw));out=io.BytesIO();doc.save(out)
 return repo.put_file('label.docx',out.getvalue())


def test_program_extracts_images_with_original_anchors_and_deduplicates(tmp_path):
 from shipping.agent.vision import collect_images
 repo=Repository(tmp_path);f=doc_image(repo)
 images=collect_images(repo,{'files':[f]})
 assert len(images)==1 and images[0]['sources']==[f['id']+':word-image:image1.png']
 assert images[0]['data'].startswith(b'\x89PNG')
 assert images[0]['mime']=='image/png'


def test_vision_protocol_is_opt_in_cached_and_preserves_source_ids(tmp_path,monkeypatch):
 from shipping.agent import vision
 import httpx
 repo=Repository(tmp_path);f=doc_image(repo);job={'files':[f]};calls=[];real_client=httpx.Client
 def respond(req):
  body=json.loads(req.content);calls.append(body)
  assert body['model']=='deepseek-flash'
  content=body['messages'][1]['content']
  assert any(p['type']=='image_url' and p['image_url']['url'].startswith('data:image/png;base64,') for p in content)
  assert body['thinking']=={'type':'disabled'}
  result={'images':[{'id':'image_0','text':'产品批号：B001\n生产日期：2026.08.07','warnings':[]}]}
  return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]})
 monkeypatch.setattr(vision.httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(respond),**kw))
 config={'model':'deepseek-flash','endpoint':'https://api.deepseek.com'}
 with pytest.raises(ValueError,match='图片发送'):vision.read_images_api(repo,job,config,'synthetic-key',False)
 assert not calls
 result=vision.read_images_api(repo,job,config,'synthetic-key',True)
 assert list(result['sources'])==[f['id']+':word-image:image1.png']
 assert 'B001' in next(iter(result['sources'].values()))
 assert vision.read_images_api(repo,job,config,'synthetic-key',True)['sources']==result['sources']
 assert len(calls)==1
 with repo.connect() as c:
  payloads=''.join(row[0] for row in c.execute('SELECT payload FROM extraction_cache'))
 assert 'synthetic-key' not in payloads and 'data:image' not in payloads


def test_vision_rejects_missing_image_results_without_marking_complete(tmp_path,monkeypatch):
 from shipping.agent import vision
 import httpx
 repo=Repository(tmp_path);job={'files':[doc_image(repo)]};real_client=httpx.Client
 monkeypatch.setattr(vision.httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"images":[]}'}}]})),**kw))
 with pytest.raises(ValueError,match='图片结果不完整'):vision.read_images_api(repo,job,{'model':'deepseek-flash','endpoint':'https://api.deepseek.com'},'synthetic',True)
 with repo.connect() as c:assert c.execute("SELECT COUNT(*) FROM extraction_cache WHERE key LIKE 'agent-vision-%'").fetchone()[0]==0

def test_pipeline_uses_image_text_then_fills_fields_without_writing_job(tmp_path,monkeypatch):
 from shipping.agent import flow,vision
 from shipping.agent.core import JobStore
 from shipping.repository import canonical
 from shipping.ingestion import digest
 repo=Repository(tmp_path);store=JobStore(repo);j=store.create('program flow',['customer']);f=doc_image(repo);j['files']=[f];j=store.save(j,j['revision'])
 source=f['id']+':word-image:image1.png';local={source:'错误OCR'}
 repo.cache_set('agent-local-v3:'+f['id'],[{'locator':'word-image:image1.png','text':'错误OCR'}]);repo.cache_set('agent-read-report-v3:'+f['id'],{'status':'read','characters':5})
 image_text='合同 C001\n批号 B001\n生产日期 2026.08.07\n有效期 2029.07\n尺寸390X270X220mm'
 monkeypatch.setattr(vision,'read_images_api',lambda *args,**kwargs:{'sources':{source:image_text},'warnings':[],'cache_keys':{source:'test-cache'},'image_count':1})
 def clean(repo,sources,config,key,authorized,on_progress=None,file_names=None):
  assert sources[source]==image_text
  def fact(k,v,q):return {'key':k,'value':v,'quote':q,'source':source}
  raw={'contracts':[{'id':'c1','kind':'other','fields':[fact('contract_no','C001','合同 C001')]}],'products':[{'id':'p1','contract_id':'c1','fields':[fact('carton_length_mm','390','尺寸390X270X220mm')],'batches':[{'id':'b1','fields':[fact('batch_no','B001','批号 B001'),fact('mfg_date','2026-08-07','生产日期 2026.08.07'),fact('exp_date','2029-07','有效期 2029.07')]}]}],'shipment':[]}
  return raw,'DeepSeek'
 monkeypatch.setattr(flow,'extract_api',clean)
 new=flow.extract_and_clean(repo,j,local,{},'synthetic',True,True)
 assert new['products'][0]['values']['carton_length_mm']=='390'
 assert new['products'][0]['batches'][0]['values']['mfg_date']=='2026-08-07'
 assert new['extraction_local_source_hash']==digest(canonical(local).encode())
 assert flow.extraction_current(repo,new)
 assert new['products'][0]['batches'][0]['evidence']['mfg_date'][0]['method']=='api_image_read'
 assert store.get(j['id'])['products']==[]

def test_image_mode_can_continue_after_local_ocr_reads_nothing(tmp_path,monkeypatch):
 from shipping.agent import flow
 repo=Repository(tmp_path);f=doc_image(repo);j={'files':[f]}
 monkeypatch.setattr(flow,'ocr_image',lambda data:[])
 with pytest.raises(ValueError,match='未读'):flow.prepare_sources(repo,j)
 assert flow.prepare_sources(repo,j,allow_image_fallback=True)=={}
