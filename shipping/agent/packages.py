import io,json,zipfile
from pathlib import Path


def attachment_package(repo,job):
 selected=set(job.get('attachment_selection',[]));available={f['id']:f for f in job['files']+job.get('attachments',[])}
 if not selected:raise ValueError('请先选择这次要交付的原件')
 if not selected<=set(available):raise ValueError('选择的原件已变化，请重新选择')
 out=io.BytesIO();names=set();manifest=[]
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  for fid in sorted(selected):
   f=available[fid];name=Path(f['name']).name
   if name in names:name=Path(name).stem+'_'+fid[:8]+Path(name).suffix
   names.add(name);z.writestr(name,repo.file_bytes(fid));manifest.append({'文件':name,'SHA256':fid,'处理':'原件未修改'})
  z.writestr('文件清单.json',json.dumps(manifest,ensure_ascii=False,indent=2))
 return out.getvalue()
