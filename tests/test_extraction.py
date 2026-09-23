import json,io,zipfile
import pytest,httpx
from shipping.repository import Repository
from shipping.ingestion import unpack,extract_local
from shipping.extraction import MockProvider,CompatibleChatProvider,extract_candidates


def test_A17_no_ai_and_explicit_cache(tmp_path):
    r=Repository(tmp_path);r.cache_set('local-v1:f',{'entries':[{'locator':'page:1','text':'Batch: TEST'}]})
    p=MockProvider()
    with pytest.raises(ValueError,match='EXPLICIT'):extract_candidates(r,'f','contract','c',['page:1'],['batch_no'],p,'mock')
    a=extract_candidates(r,'f','contract','c',['page:1'],['batch_no'],p,'mock',True)
    b=extract_candidates(r,'f','contract','c',['page:1'],['batch_no'],p,'mock',True)
    assert p.calls==1 and b['cached'] and a['usage']==[None]


def test_A17_real_adapter_protocol_and_failure(monkeypatch):
    monkeypatch.setenv('SHIPPING_AI_API_KEY','SYNTHETIC-KEY')
    def handle(request):
        body=json.loads(request.content);assert body['model']=='configured-model'
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"observations":[],"missing_fields":[]}'}}],'usage':{'total_tokens':12}})
    p=CompatibleChatProvider('https://example.invalid/v1/chat/completions','configured-model',transport=httpx.MockTransport(handle))
    result,usage=p.extract([],{});assert result['observations']==[] and usage['total_tokens']==12
    monkeypatch.delenv('SHIPPING_AI_API_KEY')
    with pytest.raises(ValueError,match='KEY_MISSING'):p.extract([],{})


def test_A16_nested_zip_depth_symlink_and_scan():
    def zipped(name,data):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr(name,data)
        return out.getvalue()
    z=zipped('coa.zip',zipped('coa.rar',b'rar'))
    assert unpack('outer.zip',z)[0]['status']=='UNPARSED_RAR'
    z=zipped('a.zip',zipped('b.zip',zipped('c.zip',zipped('x.pdf',b'x'))))
    with pytest.raises(ValueError,match='DEPTH'):unpack('outer.zip',z)
    from pypdf import PdfWriter
    w=PdfWriter();w.add_blank_page(width=100,height=100);b=io.BytesIO();w.write(b)
    assert extract_local('scan.pdf',b.getvalue())['entries'][0]['status']=='NOT_CHECKED_SCAN'
