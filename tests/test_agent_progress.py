from shipping.ui.progress import progress_html


def test_api_wait_is_indeterminate_and_escapes_document_text():
 html=progress_html(('读取资料','识别图片','整理字段','校验回填'),1,'正在识别图片','<img src=x onerror=alert(1)>')
 assert 'aria-valuenow' not in html
 assert 'aria-busy="true"' in html
 assert '<img src=x' not in html and '&lt;img' in html
 assert html.count('class="charge-stage done"')==1
 assert html.count('class="charge-stage active"')==1
 assert '02 / 04' in html


def test_terminal_states_stop_animation_without_claiming_failed_steps_done():
 failed=progress_html(('读取资料','整理字段','校验回填'),1,'处理暂停','请求超时',state='error')
 assert 'charge-panel error' in failed and 'aria-busy="false"' in failed
 assert failed.count('class="charge-stage done"')==1
 complete=progress_html(('读取资料','整理字段','校验回填'),2,'已填入核实页','请核实候选值',state='complete')
 assert complete.count('class="charge-stage done"')==3
 assert 'charge-panel complete' in complete
