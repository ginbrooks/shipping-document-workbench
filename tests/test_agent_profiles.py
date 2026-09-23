from shipping.agent.profiles import customer_changes,booking_changes
from shipping.agent.core import normalise_result,resolve
from test_agent_core import result
import pytest

def job():return normalise_result(result(),{'s1':'采购合同 C001','s2':'测试片 25mg 3000000片'})

def test_missing_values_clear_old_samples_prices_and_weights():
 j=job();j=resolve(j,{'p1.samples_text':'无','c1.invoice_no':'INV-NEW'},'本票')
 c=customer_changes(j,j['products'][0]);assert c['箱单!C31']=='' and c['发票!F32']==''
 assert c['发票!L25']=='待补' and c['箱单!H15']=='待补'
 assert '发票!A1' not in c and '发票!B6' not in c and '发票!F28' not in c
 assert not any('OLD-DEMO' in str(v) or 'OLD-DEMO-REGISTRATION' in str(v) for v in c.values())

def test_capacity_failure_not_silent_relayout():
 j=job();j['products']*=31
 with pytest.raises(ValueError,match='30 个产品'):booking_changes(j)

def test_masthead_date_does_not_depend_on_process_locale():
 j=resolve(job(),{'shipment.shipping_date':'2026-09-13'},'日期确认')
 assert booking_changes(j)['0:0:0']['value']=='2026年 09月13 日'
