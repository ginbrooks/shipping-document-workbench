"""Local PDF rendering for layout validation and preview; no remote service."""
import os,shutil,subprocess,tempfile,hashlib
from pathlib import Path


def converter():
 configured=os.environ.get('SHIPPING_SOFFICE')
 if configured and Path(configured).is_file():return configured
 bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice'
 if bundled.is_file():return str(bundled)
 return shutil.which('soffice')


def preview_key(source):
 from .layout import VERSION
 return VERSION+':'+hashlib.sha256(source.read_bytes()).hexdigest()


def pdf_preview(repo,output):
 source=repo.safe_path(output['path']);target=source.with_suffix('.pdf')
 marker=target.with_suffix('.pdf.version')
 if target.exists() and marker.exists() and marker.read_text()==preview_key(source):return target.read_bytes()
 exe=converter()
 if not exe:raise ValueError('本机缺少排版转换组件，无法完成页面检查。请安装 LibreOffice 后重试。')
 with tempfile.TemporaryDirectory(prefix='shipping-preview-') as td:
  root=Path(td);profile=root/'profile';profile.mkdir();dest=root/'pdf';dest.mkdir()
  env={**os.environ}
  if os.name!='nt':env['TMPDIR']='/private/tmp'
  font_config=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/libreoffice-headless/libreoffice/LibreOfficeDev.app/Contents/Resources/fontconfig/fonts.conf'
  if font_config.exists():env['FONTCONFIG_FILE']=str(font_config)
  try:r=subprocess.run([exe,'-env:UserInstallation='+profile.as_uri(),'--headless','--convert-to','pdf','--outdir',str(dest),str(source)],capture_output=True,timeout=60,env=env)
  except subprocess.TimeoutExpired:raise ValueError('本机排版转换超过 60 秒，本次未完成。请重试；资料和已确认内容保留。') from None
  converted=dest/source.with_suffix('.pdf').name
  if r.returncode or not converted.is_file():raise ValueError('本机排版转换失败，请重试；没有重新调用识别服务')
  data=converted.read_bytes();target.write_bytes(data);marker.write_text(preview_key(source));return data


def page_images(data):
 import pypdfium2 as pdfium
 with pdfium.PdfDocument(data) as pdf:
  for i in range(len(pdf)):
   page=pdf[i];bitmap=page.render(scale=1.2);pic=bitmap.to_pil();yield i+1,pic.copy();pic.close();bitmap.close();page.close()
