import json,platform,uuid
from pathlib import Path
import pytest


def test_keychain_persists_across_processes_updates_and_deletes():
 from shipping.agent.credentials import load_key,save_key,delete_key,HELPER
 if platform.system()!='Darwin' or not HELPER.exists():pytest.skip('Requires built native Mac Keychain helper')
 endpoint='https://keychain-test.invalid/'+uuid.uuid4().hex
 # Synthetic value, never a real API credential. Each wrapper call is a new process.
 try:
  assert load_key(endpoint) is None
  save_key(endpoint,'synthetic-test-value')
  assert load_key(endpoint)=='synthetic-test-value'
  assert load_key(endpoint+'/chat/completions')=='synthetic-test-value'
  assert load_key(endpoint+'/other') is None
  save_key(endpoint,'synthetic-replacement')
  assert load_key(endpoint)=='synthetic-replacement'
  delete_key(endpoint)
  assert load_key(endpoint) is None
  delete_key(endpoint)
 finally:delete_key(endpoint)


def test_credentials_validate_endpoint_and_never_put_secret_in_arguments(monkeypatch):
 from shipping.agent import credentials as c
 for endpoint in ['http://api.example.com','https://user:pass@api.example.com','https://api.example.com?key=secret','']:
  with pytest.raises(ValueError):c.account_for(endpoint)
 captured=[]
 def run(args,**kwargs):
  captured.append((args,kwargs));return type('Result',(),{'returncode':0,'stdout':b'{"ok":true}'})()
 monkeypatch.setattr(c.subprocess,'run',run)
 monkeypatch.setattr(c,'available',lambda:True)
 c.save_key('https://api.example.com','synthetic-secret')
 args,kwargs=captured[0]
 assert 'synthetic-secret' not in repr(args)
 assert json.loads(kwargs['input'])['secret']=='synthetic-secret'
 def fail(*args,**kwargs):raise c.subprocess.TimeoutExpired('synthetic-secret',30)
 monkeypatch.setattr(c.subprocess,'run',fail)
 with pytest.raises(ValueError) as exc:c.load_key('https://api.example.com')
 assert 'synthetic-secret' not in str(exc.value)


def test_invalid_api_key_is_rejected_without_echoing_its_contents():
 from shipping.agent.credentials import validate_api_key
 for invalid in ['含中文的测试内容','ab cd','ab\ncd']:
  with pytest.raises(ValueError) as exc:validate_api_key(invalid)
  assert invalid not in str(exc.value)
 assert validate_api_key(' synthetic-token ' )=='synthetic-token'


def test_save_rejects_invalid_content_before_touching_existing_key(monkeypatch):
 from shipping.agent import credentials as c
 calls=[]
 monkeypatch.setattr(c,'_call',lambda *args: calls.append(args))
 for invalid in ['中文说明内容','synthetic token','abc\ndef']:
  with pytest.raises(ValueError):c.save_key('https://api.deepseek.com',invalid)
 assert calls==[]
 c.save_key('https://api.deepseek.com',' synthetic-token ')
 assert calls==[('save','https://api.deepseek.com','synthetic-token')]
