import io,json,os,uuid,zipfile,sqlite3,tempfile,shutil,html
from pathlib import Path
from copy import deepcopy
from datetime import date
from .repository import Repository,canonical,now
from .models import Shipment,Observation,Fact,SourceRef,Plan,DocumentRecord,leaves
from .ingestion import unpack,digest,extract_local
from .planning import calculate_plan,geometry_hash,effective,totals,check_route,actual_checks
from .validation import validate_shipment,compare_observations,get_path,set_path,issue
from .templating import render,build_context,load_spec,validate_spec,TYPES,TEMPLATES,TemplateSpec
from .expectations import required_paths,applies,resolve_expected
from .versioning import document_hash,diff


class Workbench:
    def __init__(self,data_root):
        self.repo=Repository(data_root)
        self.template_root=self.repo.root/'templates'
        if not self.template_root.exists():shutil.copytree(TEMPLATES,self.template_root)
        self._upgrade_builtin_templates()

    def _upgrade_builtin_templates(self):
        upgrades=json.loads((TEMPLATES.parent/'config/template_upgrades.json').read_text())
        for kind,old_hash in upgrades.items():
            old=self.template_spec(kind);current=load_spec(kind)
            if old['version']==current['version'] or digest(canonical(old).encode())!=old_hash:continue
            if current['source_path']:
                asset=kind+'-builtin-'+current['version']+Path(current['source_path']).suffix
                target=self.template_root/asset
                if target.exists() and digest(target.read_bytes())!=current['source_sha256']:raise ValueError('BUILTIN_TEMPLATE_ASSET_CONFLICT')
                target.write_bytes((TEMPLATES/current['source_path']).read_bytes());current['source_path']=asset
            self.register_template(kind,current)

    def template_spec(self,kind):return load_spec(kind,self.template_root)

    def register_template(self,kind,spec,uploaded=None):
        old=self.template_spec(kind);value=TemplateSpec.model_validate(deepcopy(spec)).model_dump()
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}',value['version']) or '..' in value['version'] or value['output_format'] not in ('xlsx','docx'):raise ValueError('TEMPLATE_VERSION_OR_FORMAT_INVALID')
        if (self.template_root/'history'/(kind+'-'+value['version']+'.json')).exists():raise ValueError('TEMPLATE_VERSION_ALREADY_USED')
        if value['type']!=kind or value['id']!=old['id']:raise ValueError('TEMPLATE_ID_MISMATCH')
        validate_spec(value,self.template_root) if uploaded is None else None
        if old['version']==value['version']:raise ValueError('TEMPLATE_NEW_VERSION_REQUIRED')
        history=self.template_root/'history';history.mkdir(exist_ok=True)
        previous=history/(kind+'-'+old['version']+'.json')
        if previous.exists():raise ValueError('TEMPLATE_HISTORY_IMMUTABLE')
        if uploaded is not None:
            value['source_path']=kind+'-'+uuid.uuid4().hex+'.'+value['output_format']
            value['source_sha256']=digest(uploaded);value['visual_checked']=False;value['sensitive_assets_reviewed']=False
            path=self.template_root/value['source_path'];path.write_bytes(uploaded)
        try:validate_spec(value,self.template_root)
        except Exception:
            if uploaded is not None:path.unlink(missing_ok=True)
            raise
        previous.write_text(canonical(old));previous.chmod(0o444)
        target=self.template_root/(kind+'.json');temp=target.with_suffix('.tmp');temp.write_text(canonical(value));os.replace(temp,target)
        for record in self.repo.list_shipments():self._invalidate(record['id'])
        return value

    def save_shipment(self,value,expected_revision):
        s=Shipment.model_validate(value).model_dump(mode='json')
        if expected_revision:
            old=self.repo.load(s['id'],expected_revision)
            changes=[d for d in diff(old,s) if not d['field_path'].startswith(('facts','audit','revision'))]
            for change in changes:
                f=s['facts'].get(change['field_path'])
                if f and f.get('value')!=change['after']:f['confirmed']=False
            if changes:s['audit']=old['audit']+[{'at':now(),'reason':'保存主数据修改','changes':changes}]
        revision=self.repo.save(s['id'],s,expected_revision)
        self._invalidate(s['id'])
        return revision

    def confirm_fields(self,sid,paths,note,expected_revision):
        if not note.strip():raise ValueError('SOURCE_NOTE_REQUIRED')
        s=self.repo.load(sid)
        for path in paths:
            value=get_path(s,path)
            if value is None:raise ValueError('CANNOT_CONFIRM_NULL: '+path)
            s['facts'][path]=Fact(value=value,source_ref=SourceRef(manual_note=note),confirmed=True).model_dump(mode='json')
        s['audit'].append({'at':now(),'reason':note,'confirmed_paths':paths})
        return self.save_shipment(s,expected_revision)

    def import_files(self,sid,files,role='unclassified',scope=''):
        if sum(len(data) for _,data in files)>100*1024**2:raise ValueError('UPLOAD_TOO_LARGE')
        imported=[]
        # Fully validate archives before committing any of their members.
        members=[entry for name,data in files for entry in unpack(name,data)]
        if len(members)>1000 or sum(len(e['data']) for e in members)>500*1024**2:raise ValueError('UPLOAD_EXPANSION_LIMIT')
        for entry in members:
            imported.append(self.repo.put_file(entry['name'],entry['data'],role,scope,entry['status'],sid))
        return imported

    def parse_file(self,file_id):
        key='local-v1:'+file_id
        cached=self.repo.cache_get(key)
        if cached is not None:return cached
        f=next(f for f in self.repo.files() if f['id']==file_id)
        value=extract_local(f['name'],self.repo.file_bytes(file_id));self.repo.cache_set(key,value);return value

    def extract_candidates(self,file_id,role,scope,selected_locators,fields,explicit_send=False,image_pages=None):
        from .extraction import extract_candidates,CompatibleChatProvider
        settings=self.repo.cache_get('ai-settings') or {}
        if not settings.get('endpoint') or not settings.get('model'):raise ValueError('AI_CONFIGURATION_REQUIRED')
        self.parse_file(file_id)
        provider=CompatibleChatProvider(settings['endpoint'],settings['model'])
        return extract_candidates(self.repo,file_id,role,scope,selected_locators,fields,provider,settings['model'],explicit_send,image_pages)

    def calculate_plan(self,sid,manual_counts=None,layout_index=0):
        s=self.repo.load(sid);p=calculate_plan(s,manual_counts,layout_index)
        existing=self.current_plan(sid)
        if existing and existing['manual_counts']==(manual_counts or {}) and existing['layout_index']==layout_index:return existing
        p['id']=str(uuid.uuid4());p['created_at']=now();p['source_revision']=s['revision']
        Plan.model_validate(p)
        self.repo.put('plans',sid,p,p['id']);self._invalidate(sid)
        return self.current_plan(sid)

    def current_plan(self,sid):
        s=self.repo.load(sid);h=geometry_hash(s)
        plans=[p for p in self.repo.records('plans',sid) if p['input_hash']==h]
        return plans[-1] if plans else None

    def confirm_plan(self,sid,plan_id):
        p=self.current_plan(sid);s=self.repo.load(sid)
        if p is None or p['id']!=plan_id:raise ValueError('STALE_PLAN')
        p['issues']=[i for i in p['issues'] if not i['code'].startswith('ACTUAL_')]+[i for pallet in p['pallets'] for i in actual_checks(pallet)]
        blocked=[i for i in p['issues'] if i['status']!='PASS']
        blocked += [i for i in validate_shipment(s) if i['status']=='FAIL' and i['code']!='P01']
        for path,v in leaves(s):
            if path.startswith(('route_legs','pallet_choices','extras')) or (path.startswith('lines.') and (any(k in path for k in ('.packing_spec.','.allowed_pallet_ids.','.packing_extras.','.partial_cartons.')) or path.split('.')[-1] in ('base_quantity','base_unit','units_per_inner','inners_per_carton','full_cartons'))):
                if v is not None and '.availability.' not in path:
                    fact=s['facts'].get(path,{})
                    if fact.get('value')!=v or not fact.get('confirmed'):blocked.append(issue('SOURCE_UNCONFIRMED',path=path))
        if blocked:raise ValueError('PLAN_CONFIRMATION_BLOCKED: '+','.join(sorted({i['code'] for i in blocked})))
        p['status']='confirmed';p['confirmed_at']=now();self.repo.put('plans',sid,p,p['id']);return p

    def record_actuals(self,sid,plan_id,pallet_id,actual,source_ref,confirmed=False):
        p=self.current_plan(sid)
        if not p or p['id']!=plan_id:raise ValueError('STALE_PLAN')
        SourceRef.model_validate(source_ref)
        if not source_ref.get('manual_note') and not (source_ref.get('file_id') and source_ref.get('locator')):raise ValueError('ACTUAL_SOURCE_REQUIRED')
        allowed={'length_mm','width_mm','height_mm','gross_g','net_g'}
        if not actual or actual.keys()-allowed:raise ValueError('ACTUAL_FIELDS_INVALID')
        if any(not isinstance(v,int) or isinstance(v,bool) or v<=0 for v in actual.values()):raise ValueError('ACTUAL_VALUE_INVALID')
        new=deepcopy(p);new['id']=str(uuid.uuid4());new['supersedes']=p['id'];new['created_at']=now();new['status']='provisional'
        target=next(x for x in new['pallets'] if x['id']==pallet_id)
        target['actual']={**(target.get('actual') or {}),**actual,'source_ref':source_ref,'confirmed':confirmed}
        target['actual'].pop('difference_review',None)
        physical=actual_checks(target)
        if any(i['status']=='FAIL' for i in physical):raise ValueError(','.join(i['code'] for i in physical if i['status']=='FAIL'))
        e=effective(target)
        if e['net_g'] is not None and e['gross_g'] is not None and e['net_g']>e['gross_g']:raise ValueError('GROSS_BELOW_NET')
        if e['gross_g'] is not None and target['pallet_spec_snapshot']['tare_g'] is not None and target['pallet_spec_snapshot']['max_payload_g'] is not None:
            if e['gross_g']-target['pallet_spec_snapshot']['tare_g']>target['pallet_spec_snapshot']['max_payload_g']:raise ValueError('ACTUAL_EXCEEDS_PALLET_PAYLOAD')
        s=self.repo.load(sid);new['totals']=totals(new['pallets']);new['route_checks']=check_route(new['pallets'],s)
        new['issues']=[i for i in p['issues'] if i not in p.get('route_checks',[]) and not i['code'].startswith('ACTUAL_')]+new['route_checks']+[i for pallet in new['pallets'] for i in actual_checks(pallet)]
        self.repo.put('plans',sid,new,new['id']);self._invalidate(sid);return new

    def documents(self,sid):
        return self.repo.records('documents',sid)

    def _invalidate(self,sid):
        s=self.repo.load(sid);p=self.current_plan(sid)
        for d in self.documents(sid):
            try:valid=p is not None and d['input_hash']==document_hash(s,p,d['type'],d['scope'],self.template_spec(d['type']))
            except (ValueError,KeyError):valid=False
            if valid and d['stage'] in ('approved','released') and self.approval_issues(sid,d['type'],d['scope']):valid=False
            if not valid and d['stage']!='stale':
                d['previous_stage']=d['stage'];d['stage']='stale';self.repo.put('documents',sid,d,d['id'])

    def list_impacted_documents(self,sid):
        self._invalidate(sid);return [d for d in self.documents(sid) if d['stage']=='stale']

    def render_document(self,sid,kind,scope='ALL'):
        s=self.repo.load(sid);p=self.current_plan(sid)
        if not p:raise ValueError('CURRENT_PLAN_REQUIRED')
        spec=self.template_spec(kind);h=document_hash(s,p,kind,scope,spec)
        for doc in self.documents(sid):
            if doc['input_hash']==h and doc['stage']!='stale':
                if digest(self.repo.safe_path(doc['file_path']).read_bytes())!=doc['file_hash']:raise ValueError('DOCUMENT_HASH_MISMATCH')
                return doc
        ctx=build_context(s,p,kind,scope);version=1+sum(d['type']==kind and d['scope']==scope for d in self.documents(sid))
        safe=lambda t:''.join(c if c.isalnum() or c in '-_' else '_' for c in str(t))[:90]
        name=f'{safe(s["business_no"])}_{safe(scope)}_{TYPES[kind]}_v{version:02}_{date.today().isoformat()}.{spec["output_format"]}'
        rid=str(uuid.uuid4());folder=self.repo.root/'exports'/sid/rid;folder.mkdir(parents=True,exist_ok=True)
        output=folder/name;temp=folder/('pending.'+spec['output_format'])
        try:
            render(kind,ctx,temp,spec,self.template_root);os.replace(temp,output)
        except Exception:
            temp.unlink(missing_ok=True);raise
        d={'id':rid,'type':kind,'scope':scope,'template_id':spec['id'],'template_version':spec['version'],'template_hash':spec['source_sha256'],
           'input_hash':h,'source_revision':s['revision'],'stage':'draft','file_path':str(output.relative_to(self.repo.root)),
           'file_hash':digest(output.read_bytes()),'issues':self.approval_issues(sid,kind,scope),'approved_at':None,'created_at':now()}
        DocumentRecord.model_validate(d)
        self.repo.put('documents',sid,d,rid);return d

    def approval_issues(self,sid,kind,scope):
        s=self.repo.load(sid);p=self.current_plan(sid);spec=self.template_spec(kind)
        issues=[]
        if not spec['sensitive_assets_reviewed'] or not spec['visual_checked']:issues.append('TEMPLATE_REVIEW_REQUIRED')
        if not p or p['status']!='confirmed':issues.append('PLAN_NOT_CONFIRMED')
        expanded=required_paths(s,kind,scope,spec)
        for path in expanded:
            value=get_path(s,path);fact=s['facts'].get(path,{})
            if value is None or value=='' or not fact.get('confirmed') or fact.get('value')!=value:issues.append('REQUIRED_UNCONFIRMED:'+path)
        for i in validate_shipment(s):
            if not applies(s,i.get('field_path') or '',i.get('scope'),kind,scope):continue
            if i['status']=='FAIL' or (i['code']=='P01' and i['status']!='PASS' and kind in ('customer','customs','booking')):
                issues.append(i['code']+':'+str(i.get('field_path')))
        groups=('customer','customs') if kind=='booking' else (kind,) if kind in ('customer','customs') else ()
        for index,line in enumerate(s['lines']):
            if not applies(s,f'lines.{index}',None,kind,scope):continue
            for group in groups:
                price=line[group+'_price']
                if price.get('pricing_basis')=='custom':
                    path=f'lines.{index}.{group}_price.conversion_note';fact=s['facts'].get(path,{})
                    if not price.get('conversion_note') or not fact.get('confirmed') or fact.get('value')!=price['conversion_note']:issues.append('CONVERSION_SOURCE_REQUIRED:'+path)
        for index,c in enumerate(s['contracts']):
            if not applies(s,f'contracts.{index}',None,kind,scope):continue
            for group in groups:
                for a in c[group+'_adjustments']:
                    if not (a['evidence'].get('manual_note') or (a['evidence'].get('file_id') and a['evidence'].get('locator'))):issues.append('ADJUSTMENT_SOURCE_REQUIRED')
        if p:
            issues.extend(i['code'] for i in p['issues'] if i['status']!='PASS')
            for pallet in p['pallets']:
                if scope not in ('ALL',pallet['contract_id'],pallet['line_id']):continue
                actual=pallet.get('actual') or {}
                if not actual.get('confirmed') or any(actual.get(k) is None for k in ('length_mm','width_mm','height_mm','gross_g')):issues.append('FACTORY_ACTUALS_REQUIRED:'+pallet['id'])
        for i in self.compare_documents(sid):
            path=i.get('field_path')
            if not path or not applies(s,path,i.get('scope'),kind,scope):continue
            critical=path in expanded or path.startswith(('plan.','prices.')) or (path.startswith('lines.') and any(k in path for k in ('batch_no','quantity','name_','strength','mfg_date','exp_date','packing_spec','price')))
            if i['status']=='FAIL' or (critical and i['status']!='PASS'):
                issues.append('SOURCE_CHECK_'+i['status']+':'+path)
        return sorted(set(issues))

    def approve_document(self,sid,doc_id):
        self._invalidate(sid);d=next(x for x in self.documents(sid) if x['id']==doc_id)
        issues=self.approval_issues(sid,d['type'],d['scope'])
        if d['stage']=='stale':issues.append('DOCUMENT_STALE')
        if issues:raise ValueError('APPROVAL_BLOCKED: '+', '.join(issues))
        d['stage']='approved';d['approved_at']=now();self.repo.put('documents',sid,d,d['id']);return d

    def add_observation(self,sid,value):
        o=Observation.model_validate(value).model_dump(mode='json')
        if o['file_id'] and o['file_id'] not in {f['id'] for f in self.repo.files(sid)}:raise ValueError('FILE_NOT_LINKED')
        rid=self.repo.put('observations',sid,o)
        self._invalidate(sid)
        return rid

    def compare_documents(self,sid):
        s=self.repo.load(sid);observations=self.active_observations(sid)
        issues=compare_observations(s,observations,self.current_plan(sid))
        resolutions=self.repo.records('resolutions',sid)
        for i in issues:
            fingerprint=digest(canonical(i).encode())
            i['id']=fingerprint
            match=next((r for r in reversed(resolutions) if r['issue_hash']==fingerprint),None)
            if match:i['resolution']=match['note'];i['resolved_at']=match['at']
        return issues

    def resolve_issue(self,sid,issue_hash,note):
        if not note.strip():raise ValueError('RESOLUTION_REQUIRED')
        # Recording an explanation does not erase FAIL; underlying data must be corrected for hard errors.
        return self.repo.put('resolutions',sid,{'issue_hash':issue_hash,'note':note,'at':now()})

    def report(self,sid,fmt='html'):
        value={'shipment_id':sid,'revision':self.repo.load(sid)['revision'],'issues':validate_shipment(self.repo.load(sid))+self.compare_documents(sid),'observation_history':self.observation_history(sid)}
        if fmt=='json':return json.dumps(value,ensure_ascii=False,indent=2).encode()
        rows=''.join('<tr>'+''.join('<td>'+html.escape(str(i.get(k,'')))+'</td>' for k in ('code','status','field_path','expected','observed','source_refs','resolution'))+'</tr>' for i in value['issues'])
        history='<h2>核对记录审计</h2><pre>'+html.escape(json.dumps(value['observation_history'],ensure_ascii=False,indent=2))+'</pre>'
        return ('<!doctype html><meta charset="utf-8"><title>内部核对报告</title><style>body{font:15px system-ui;margin:40px}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:12px;vertical-align:top}</style><h1>内部核对报告</h1><table><tr><th>规则</th><th>结果</th><th>字段</th><th>主数据</th><th>原件</th><th>来源位置</th><th>处理记录</th></tr>'+rows+'</table>'+history).encode()

    def confirm_pdf(self,sid,file_id,scope,note,confirmed):
        from pypdf import PdfReader
        if not confirmed or not note.strip():raise ValueError('PDF_REVIEW_REQUIRED')
        matches=[f for f in self.repo.files(sid) if f['id']==file_id and f['linked_scope']==scope]
        if len(matches)!=1:raise ValueError('PDF_SCOPE_OR_ROLE_AMBIGUOUS')
        f=matches[0]
        if not f['name'].lower().endswith('.pdf'):raise ValueError('PDF_REQUIRED')
        reader=PdfReader(io.BytesIO(self.repo.file_bytes(file_id)))
        if not reader.pages:raise ValueError('PDF_EMPTY')
        s=self.repo.load(sid);p=self.current_plan(sid)
        kind={'customs_pdf':'customs','customer_pdf':'customer','carrier_return':'awb'}.get(f.get('linked_role'),'delivery')
        return self.repo.put('pdf_reviews',sid,{'file_id':file_id,'scope':scope,'note':note,'at':now(),'kind':kind,
                            'revision':s['revision'],'input_hash':document_hash(s,p,kind,scope,self.template_spec(kind))})

    def assemble_package(self,sid,package_type,scope,document_ids,attachment_ids,pdf_only=False):
        allowed_docs={'customer':{'customer','awb'},'customs':{'customs'},'forwarder':{'booking','awb','delivery','plan'},'delivery':{'delivery','plan'}}
        allowed_roles={'customer':{'customer_pdf','finished_coa','reference_coa','carrier_return'},'customs':{'customs_pdf','finished_coa','transport_report'},'forwarder':{'carrier_return','transport_report'},'delivery':{'factory_original','finished_coa','reference_coa'}}
        if package_type not in allowed_docs:raise ValueError('PACKAGE_TYPE_INVALID')
        if not document_ids and not attachment_ids:raise ValueError('PACKAGE_EMPTY')
        self._invalidate(sid);members=[];manifest=[];transformations=[];docs={d['id']:d for d in self.documents(sid)}
        matching_files=[f for f in self.repo.files(sid) if f['linked_scope']==scope]
        if any(sum(f['id']==fid for f in matching_files)>1 for fid in attachment_ids):raise ValueError('ATTACHMENT_ROLE_AMBIGUOUS')
        linked_files={f['id']:f for f in matching_files}
        has_carrier=any(linked_files.get(a,{}).get('linked_role')=='carrier_return' for a in attachment_ids)
        for did in document_ids:
            d=docs[did]
            if d['type'] not in allowed_docs[package_type] or d['stage']=='stale' or d['scope']!=scope:raise ValueError('DOCUMENT_NOT_ALLOWED')
            b=self.repo.safe_path(d['file_path']).read_bytes()
            if digest(b)!=d['file_hash']:raise ValueError('DOCUMENT_HASH_MISMATCH')
            if has_carrier and d['type']=='awb':
                transformations.append({'document':did,'action':'omitted_self_awb'});continue
            name=Path(d['file_path']).name
            if has_carrier and d['type']=='customer':
                from openpyxl import load_workbook
                workbook=load_workbook(io.BytesIO(b));removed=[ws.title for ws in workbook if ws.title=='Page 1' or ws.title.startswith('Page 1_')]
                if not removed:raise ValueError('AWB_REPLACEMENT_MAPPING_REQUIRED')
                for title in removed:del workbook[title]
                buffer=io.BytesIO();workbook.save(buffer);b=buffer.getvalue();name=Path(name).stem+'_已替换提单.xlsx'
                transformations.append({'document':did,'action':'removed_self_awb_sheets','sheets':removed})
            members.append((name,b))
        files=linked_files;reviews=self.repo.records('pdf_reviews',sid)
        for aid in attachment_ids:
            if aid not in files:raise ValueError('ATTACHMENT_NOT_ALLOWED')
            f=files[aid]
            if f.get('linked_role') not in allowed_roles[package_type] or f.get('linked_scope')!=scope:raise ValueError('ATTACHMENT_NOT_ALLOWED')
            if f['status']=='UNPARSED_RAR':raise ValueError('UNPARSED_ATTACHMENT')
            if f['name'].lower().endswith('.pdf'):
                s=self.repo.load(sid);p=self.current_plan(sid)
                kind={'customs_pdf':'customs','customer_pdf':'customer','carrier_return':'awb'}.get(f.get('linked_role'),'delivery')
                h=document_hash(s,p,kind,scope,self.template_spec(kind))
                if not any(r['file_id']==aid and r['scope']==scope and r['input_hash']==h for r in reviews):raise ValueError('PDF_NOT_CONFIRMED')
            members.append((Path(f['name']).name,self.repo.file_bytes(aid)))
        if has_carrier and any(files[a].get('linked_role')=='customer_pdf' for a in attachment_ids):raise ValueError('CUSTOMER_PDF_AWB_PAGE_MAPPING_REQUIRED')
        if pdf_only:
            from pypdf import PdfWriter
            writer=PdfWriter()
            for name,data in members:
                if not name.lower().endswith('.pdf'):raise ValueError('PDF_REQUIRED')
                writer.append(io.BytesIO(data))
            out=io.BytesIO();writer.write(out);result=out.getvalue()
        else:
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
                for i,(name,data) in enumerate(members):
                    clean=f'{i+1:02}_{name}';z.writestr(clean,data);manifest.append({'name':clean,'sha256':digest(data)})
                z.writestr('manifest.json',json.dumps({'files':manifest,'transformations':transformations},ensure_ascii=False,indent=2))
            result=out.getvalue()
        self.repo.put('package_exports',sid,{'type':package_type,'scope':scope,'documents':document_ids,'attachments':attachment_ids,'sha256':digest(result),'at':now()})
        return result

    def backup(self):
        out=io.BytesIO();manifest={}
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'shipping.sqlite3'
            source=sqlite3.connect(self.repo.db);target=sqlite3.connect(db);source.backup(target);target.close();source.close()
            members=[('shipping.sqlite3',db.read_bytes()),('backup_info.json',canonical({'format':2,'app_version':'1.1','schema_version':1}).encode())]
            for folder in ('uploads','exports','templates','agent_profiles'):
                members.extend((str(p.relative_to(self.repo.root)),p.read_bytes()) for p in sorted((self.repo.root/folder).rglob('*')) if p.is_file())
            with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
                for name,data in members:z.writestr(name,data);manifest[name]=digest(data)
                z.writestr('backup_manifest.json',json.dumps(manifest))
        return out.getvalue()

    def restore(self,archive):
        entries=unpack('backup.zip',archive);files={e['name'].removeprefix('backup.zip/'):e['data'] for e in entries}
        manifest=json.loads(files.pop('backup_manifest.json'))
        if set(files)!=set(manifest) or any(digest(data)!=manifest[name] for name,data in files.items()):raise ValueError('BACKUP_HASH_MISMATCH')
        if 'shipping.sqlite3' not in files:raise ValueError('BACKUP_DATABASE_MISSING')
        if any(name not in ('shipping.sqlite3','backup_info.json') and not name.startswith(('uploads/','exports/','templates/','agent_profiles/')) for name in files):raise ValueError('BACKUP_PATH_NOT_ALLOWED')
        with tempfile.TemporaryDirectory(dir=self.repo.root.parent) as td:
            staging=Path(td)/'restored';staging.mkdir()
            for name,data in files.items():
                p=staging/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            conn=sqlite3.connect(staging/'shipping.sqlite3')
            if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('BACKUP_DATABASE_INVALID')
            for row in conn.execute('SELECT payload FROM shipment_revisions'):Shipment.model_validate_json(row[0])
            conn.close()
            info=json.loads(files.get('backup_info.json',b'{"format":1}'))
            if info['format'] not in (1,2) or info.get('schema_version',1)!=1:raise ValueError('BACKUP_VERSION_UNSUPPORTED')
            if info['format']==2 and any(not (staging/'templates'/(kind+'.json')).is_file() for kind in TYPES):raise ValueError('BACKUP_TEMPLATES_MISSING')
            if (staging/'templates').exists():
                for config in (staging/'templates').rglob('*.json'):
                    validate_spec(json.loads(config.read_text()),staging/'templates')
            previous=self.backup();backup_dir=self.repo.root.parent/(self.repo.root.name+'_restore_backups');backup_dir.mkdir(exist_ok=True)
            (backup_dir/(uuid.uuid4().hex+'.zip')).write_bytes(previous)
            old=Path(td)/'previous';self.repo.root.rename(old);staging.rename(self.repo.root)
            for p in (self.repo.root/'uploads').glob('*'):p.chmod(0o444)
        self.__init__(self.repo.root)
        return {'restored':True,'previous_backup':str(backup_dir)}

    def _provenance(self,sid,o):
        ref=o['source_ref']
        if o.get('doc_role') not in ('contract','finished_coa','reference_coa','purchase_invoice','customer_pdf','customs_pdf','carrier_return','factory_original','transport_report'):return False
        if not o.get('file_id') or ref.get('file_id')!=o['file_id'] or not (ref.get('locator') or '').strip():return False
        matches=[f for f in self.repo.files(sid) if f['id']==o['file_id'] and f['linked_role']==o['doc_role'] and f['linked_scope']==o['scope']]
        if not matches:return False
        self.repo.file_bytes(o['file_id']);return True

    def observation_history(self,sid):return self.repo.records('observation_events',sid)

    def active_observations(self,sid):
        records={o['id']:deepcopy(o) for o in self.repo.records('observations',sid)};inactive=set()
        for event in self.observation_history(sid):
            oid=event['observation_id']
            if event['action'] in ('void','supersede'):inactive.add(oid)
            elif event['action']=='verify' and oid in records:
                records[oid]['extraction_status']='VERIFIED';records[oid]['source_ref'].update(locator=event['locator'],manual_note=event['reason'])
        out=[]
        for oid,o in records.items():
            if oid not in inactive:o['_provenance_valid']=self._provenance(sid,o);out.append(o)
        return out

    def _active_observation(self,sid,oid):
        record=next((o for o in self.active_observations(sid) if o['id']==oid),None)
        if record is None:raise ValueError('OBSERVATION_NOT_ACTIVE')
        return record

    def verify_observation(self,sid,oid,reason,locator=None):
        if not reason.strip():raise ValueError('VERIFICATION_REASON_REQUIRED')
        o=self._active_observation(sid,oid)
        if locator is not None:o['source_ref']['locator']=locator
        if not self._provenance(sid,o):raise ValueError('SOURCE_ROLE_SCOPE_LOCATOR_REQUIRED')
        _,_,valid=resolve_expected(self.repo.load(sid),self.current_plan(sid),o['field_path'],o['scope'])
        if not valid:raise ValueError('OBSERVATION_SCOPE_INVALID')
        self.repo.put('observation_events',sid,{'observation_id':oid,'action':'verify','reason':reason,'locator':o['source_ref']['locator'],'at':now()});self._invalidate(sid)

    def supersede_observation(self,sid,old_id,new_id,reason):
        if not reason.strip():raise ValueError('REPLACEMENT_REASON_REQUIRED')
        if old_id==new_id:raise ValueError('CANNOT_REPLACE_SELF')
        old=self._active_observation(sid,old_id);new=self._active_observation(sid,new_id)
        if any(old[k]!=new[k] for k in ('field_path','scope','doc_role')):raise ValueError('REPLACEMENT_SCOPE_FIELD_MISMATCH')
        if new['extraction_status'] not in ('MANUAL','VERIFIED') or not new['_provenance_valid']:raise ValueError('REPLACEMENT_MUST_BE_VERIFIED')
        self.repo.put('observation_events',sid,{'observation_id':old_id,'action':'supersede','replacement_id':new_id,'reason':reason,'at':now()});self._invalidate(sid)

    def void_observation(self,sid,oid,reason):
        if not reason.strip():raise ValueError('VOID_REASON_REQUIRED')
        self._active_observation(sid,oid);self.repo.put('observation_events',sid,{'observation_id':oid,'action':'void','reason':reason,'at':now()});self._invalidate(sid)

    def preview_observation(self,sid,oid):
        o=self._active_observation(sid,oid);s=self.repo.load(sid);path=o['field_path']
        allowed={p for p,_ in leaves(s) if not p.startswith(('facts','audit','revision','schema_version')) and p not in ('id','business_no') and not p.endswith(('.id','.contract_id'))}
        if path not in allowed:raise ValueError('CANDIDATE_FIELD_NOT_WRITABLE')
        if not self._provenance(sid,o):raise ValueError('SOURCE_ROLE_SCOPE_LOCATOR_REQUIRED')
        _,_,valid=resolve_expected(s,self.current_plan(sid),path,o['scope'])
        if not valid:raise ValueError('OBSERVATION_SCOPE_INVALID')
        if o['doc_role']=='reference_coa' and 'reference_samples' not in path:raise ValueError('REFERENCE_SOURCE_MISMATCH')
        if 'price' in path and o['doc_role'] not in ('contract','customer_pdf','customs_pdf'):raise ValueError('PRICE_SOURCE_ROLE_INVALID')
        before=get_path(s,path);after=o['value']
        if after is None:raise ValueError('CANDIDATE_NULL_NOT_ACCEPTABLE')
        from .models import convert_unit
        if path.endswith('_g') and o.get('unit'):after=convert_unit(after,o['unit'],'weight')
        elif path.endswith('_mm') and o.get('unit'):after=convert_unit(after,o['unit'],'dimension')
        elif isinstance(before,int) and not isinstance(before,bool) or path.split('.')[-1] in ('base_quantity','units_per_inner','inners_per_carton','full_cartons','base_units_per_pricing_unit'):
            from decimal import Decimal
            try:number=Decimal(str(after))
            except ArithmeticError as exc:raise ValueError('CANDIDATE_INTEGER_REQUIRED') from exc
            if not number.is_finite() or number!=number.to_integral_value():raise ValueError('CANDIDATE_INTEGER_REQUIRED')
            after=int(number)
        elif isinstance(before,bool):
            if not isinstance(after,bool):raise ValueError('CANDIDATE_BOOLEAN_REQUIRED')
        trial=deepcopy(s);set_path(trial,path,after);normalized=Shipment.model_validate(trial).model_dump(mode='json');after=get_path(normalized,path)
        return {'observation_id':oid,'field_path':path,'before':before,'after':after,'source_ref':o['source_ref'],'revision':s['revision']}

    def accept_observation(self,sid,oid,expected_revision,reason):
        if not reason.strip():raise ValueError('ACCEPTANCE_REASON_REQUIRED')
        preview=self.preview_observation(sid,oid);s=self.repo.load(sid)
        if s['revision']!=expected_revision:
            from .repository import RevisionConflict
            raise RevisionConflict('VERSION_CONFLICT')
        set_path(s,preview['field_path'],preview['after']);s['facts'][preview['field_path']]={'value':preview['after'],'confirmed':True,'source_ref':{**preview['source_ref'],'manual_note':reason}}
        revision=self.save_shipment(s,expected_revision);self.verify_observation(sid,oid,reason)
        self.repo.put('observation_events',sid,{'observation_id':oid,'action':'accept','reason':reason,'revision':revision,'before':preview['before'],'after':preview['after'],'at':now()});return revision

    def review_actual_difference(self,sid,plan_id,pallet_id,reason):
        if not reason.strip():raise ValueError('ACTUAL_REVIEW_REASON_REQUIRED')
        p=self.current_plan(sid)
        if not p or p['id']!=plan_id:raise ValueError('STALE_PLAN')
        pallet=next(x for x in p['pallets'] if x['id']==pallet_id)
        if not pallet.get('actual'):raise ValueError('ACTUAL_REQUIRED')
        if any(i['status']=='FAIL' for i in actual_checks(pallet)):raise ValueError('ACTUAL_PHYSICAL_ERROR_CANNOT_OVERRIDE')
        pallet['actual']['difference_review']={'reason':reason,'at':now()}
        p['issues']=[i for i in p['issues'] if not i['code'].startswith('ACTUAL_')]+[i for item in p['pallets'] for i in actual_checks(item)]
        self.repo.put('plans',sid,p,p['id']);self.repo.put('actual_reviews',sid,{'plan_id':plan_id,'pallet_id':pallet_id,'reason':reason,'at':now()});self._invalidate(sid)
