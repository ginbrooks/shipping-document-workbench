"""Company-approved defaults, distinct from facts extracted from attachments."""
from datetime import datetime
from zoneinfo import ZoneInfo


def apply_customs_defaults(job,today=None):
 if job.get('active_document')!='customs_set' and 'customs_set' not in job.get('targets',[]):return job
 today=today or datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
 def fill(obj,key,value,quote,dependency=None):
  if not value or key in obj.get('conflicts',{}) or key in obj.get('confirmed',{}):return
  evidence=obj.setdefault('evidence',{}).get(key,[])
  if any(e.get('source') in ('manual','history') for e in evidence):return
  prior=obj['values'].get(key)
  follows=dependency and evidence and all(e.get('source')=='business_default' for e in evidence)
  if prior not in (None,'') and not follows:return
  if prior==value:return
  obj['values'][key]=value
  obj['evidence'][key]=[dict(source='business_default',value=value,quote=quote,**({'depends_on':dependency} if dependency else {}))]
  if key not in obj.setdefault('review_pending',[]):obj['review_pending'].append(key)
 for c in job.get('contracts',[]):
  number=c['values'].get('contract_no')
  if 'contract_no' not in c.get('conflicts',{}):
   for key in ('customs_invoice_no','customs_contract_no'):
    fill(c,key,number,'公司默认规则：沿用本合同编号 '+str(number or ''),c['id']+'.contract_no')
  fill(c,'customs_invoice_date',today,'公司默认规则：首次填写时的当天日期（北京时间）')
 fill(job['shipment'],'customs_marks','N/M','公司默认规则：报关唛头为 N/M，可按本票修改')
 return job
