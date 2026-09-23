"""Opt-in macOS Keychain persistence. No plaintext-file or command-argument fallback."""
import json,platform,subprocess
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit

HELPER=Path(__file__).resolve().parents[2]/'desktop'/'keychain'


def available():
 return platform.system()=='Darwin' and HELPER.is_file()


def account_for(endpoint):
 try:
  parsed=urlsplit(endpoint.strip())
  if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError
  port=parsed.port
  host=parsed.hostname.lower()
  if ':' in host:host='['+host+']'
  if port and port!=443:host+=':'+str(port)
  path=parsed.path.rstrip('/')
  if path.endswith('/chat/completions'):path=path[:-len('/chat/completions')]
  return urlunsplit(('https',host,path,'',''))
 except (ValueError,AttributeError):raise ValueError('请填写有效 HTTPS 接口地址，不含账号、密码、查询参数或片段') from None


def _call(operation,endpoint,secret=None):
 account=account_for(endpoint)
 if not available():raise ValueError('此环境尚未安装 Mac 钥匙串组件；仍可临时输入密钥使用')
 payload={'operation':operation,'account':account}
 if secret is not None:payload['secret']=secret
 try:
  result=subprocess.run([str(HELPER)],input=json.dumps(payload).encode(),capture_output=True,timeout=15,check=False)
  response=json.loads(result.stdout) if result.returncode==0 else {}
 except (OSError,subprocess.SubprocessError,ValueError):
  raise ValueError('系统钥匙串操作未完成；请重试或临时输入密钥') from None
 if not response.get('ok'):
  raise ValueError('无法访问系统钥匙串，请在“钥匙串访问”中解锁登录钥匙串后重试；也可临时输入密钥')
 return response.get('secret')


def load_key(endpoint):return _call('load',endpoint)


def save_key(endpoint,secret):
 secret=validate_api_key(secret)
 if len(secret.encode())>8192:raise ValueError('密钥内容过长，请检查是否误粘贴了其他内容')
 _call('save',endpoint,secret)


def delete_key(endpoint):_call('delete',endpoint)


def validate_api_key(secret):
 if not secret or not secret.strip():raise ValueError('请先输入 API 密钥')
 value=secret.strip()
 if not value.isascii() or any(ch.isspace() or ord(ch)<33 or ord(ch)>126 for ch in value):
  raise ValueError('密钥包含中文、空格或其他无效字符，请重新粘贴官网生成的完整 API 密钥；不要粘贴说明文字')
 return value
