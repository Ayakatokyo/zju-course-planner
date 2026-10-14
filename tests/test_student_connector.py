"""Synthetic-only Node checks for the restricted browser connector contract."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONNECTOR = ROOT / "browser" / "student_source_connector"


@unittest.skipUnless(shutil.which("node"), "Node is required for connector contract checks")
class StudentConnectorTests(unittest.TestCase):
    def node(self, source):
        setup = f"const assert = require('node:assert/strict'); const core = require({json.dumps(str(CONNECTOR / 'core.js'))});\n"
        result = subprocess.run(["node", "-e", setup + "(async () => {\n" + source + "\n})().catch(e => { process.stderr.write(e.stack); process.exit(1); });"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_manifest_has_only_required_hosts_and_no_secret_permissions(self):
        manifest = json.loads((CONNECTOR / "manifest.json").read_text())
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["host_permissions"], ["https://zdbk.zju.edu.cn/*", "http://127.0.0.1/*"])
        self.assertEqual(manifest.get("permissions", []), [])
        self.assertNotIn("externally_connectable", manifest)
        self.assertNotIn("web_accessible_resources", manifest)
        self.assertFalse(manifest["content_scripts"][0]["all_frames"])

    def test_arbitrary_url_account_auth_and_extra_parameter_are_rejected(self):
        self.node("""
for (const extra of [{url:'https://example.invalid/'}, {xh:'SYNTHETIC-OTHER'}, {password:'SYNTHETIC'}, {headers:{Cookie:'SYNTHETIC'}}]) {
  assert.throws(() => core.validateSpec({operation:'academic',target_term:'2026-2027-A',...extra}), /invalid_request_spec/);
}
for (const scope of [{student_number:'SYNTHETIC-OTHER'}, {token:'SYNTHETIC'}, {academic_year_raw:'ticket=SYNTHETIC'}]) assert.throws(() => core.validateSpec({operation:'academic',scope}), /invalid_request_spec/);
assert.throws(() => core.validateSpec({operation:'login'}), /unsupported_operation/);
""")

    def test_bounded_page_and_timetable_single_request(self):
        self.node("""
for (const page of [0,4,1.5]) assert.throws(() => core.validateSpec({operation:'academic',target_term:'2026-2027-A',page}), /request_budget_exceeded/);
for (const page_size of [0,101,1.5]) assert.throws(() => core.validateSpec({operation:'academic',target_term:'2026-2027-A',page_size}), /request_budget_exceeded/);
assert.equal(core.validateSpec({operation:'academic',target_term:'2026-2027-A'}).page_size,20);
assert.throws(() => core.buildRequest({operation:'timetable',target_term:'2026-2027-A',page:2},{operation:'timetable',route:core.DATA_ROUTES.timetable,view_mode:'list',filters:{academic_year_raw:'2026-2027',term_raw:'SOURCE-AUTUMN',academic_year_label:'2026-2027',term_label:'秋'}}), /request_budget_exceeded/);
""")

    def test_route_is_registered_only_from_same_origin_observation(self):
        self.node("""
assert.equal(core.routeObserved('academic', [core.ORIGIN+core.DATA_ROUTES.academic+'?doType=query']),true);
for (const url of ['https://example.invalid'+core.DATA_ROUTES.academic,core.ORIGIN+core.DATA_ROUTES.academic+'?ticket=SYNTHETIC',core.ORIGIN+'/cas/login', 'https://SYNTHETIC:SYNTHETIC@zdbk.zju.edu.cn'+core.DATA_ROUTES.academic]) assert.equal(core.routeObserved('academic',[url]),false);
assert.equal(core.routeObserved('exams',[core.ORIGIN+core.DATA_ROUTES.academic]),false);
""")

    def test_query_uses_source_controls_without_guessed_aw_codes(self):
        self.node("""
const reg={operation:'academic',route:core.DATA_ROUTES.academic,filters:{academic_year_raw:'2026-2027',term_raw:'SOURCE-COMBINED',academic_year_label:'2026-2027',term_label:'秋、冬'}};
const spec={operation:'academic',target_term:'2026-2027-AW'};
const req=core.buildRequest(spec,reg), form=new URLSearchParams(req.body);
assert.equal(req.url,core.ORIGIN+core.DATA_ROUTES.academic+'?doType=query');
assert.equal(req.credentials,'include'); assert.equal(req.redirect,'manual');
assert.equal(form.get('xn'),'2026-2027'); assert.equal(form.get('xq'),'SOURCE-COMBINED'); assert.equal(form.get('queryModel.showCount'),'20');
assert.equal(form.has('xnm'),false);assert.equal(form.has('xqm'),false);
assert.equal(form.has('xh'),false);
assert.throws(()=>core.buildRequest({...spec,target_term:'2026-2027-W'},reg),/scope_not_verified/);
assert.throws(()=>core.buildRequest(spec,null),/route_not_registered/);
""")

    def test_hidden_chosen_select_requires_matching_visible_label_and_single_selection(self):
        self.node("""
const observed={value:'1',option_label:'秋、冬',selected_count:1,visible:false,chosen_visible:true,chosen_same_parent:true,chosen_labels:['秋、冬']};
assert.deepEqual(core.confirmedSelect(observed),{value:'1',label:'秋、冬'});
for (const extra of [{chosen_visible:false},{chosen_same_parent:false},{chosen_labels:['冬']},{chosen_labels:['秋、冬','秋、冬']},{chosen_labels:[]},{selected_count:0},{selected_count:2}]) assert.equal(core.confirmedSelect({...observed,...extra}),null);
assert.deepEqual(core.confirmedSelect({...observed,visible:true,chosen_visible:false,chosen_labels:[]}),{value:'1',label:'秋、冬'});
""")

    def test_only_observed_nonidentity_menu_code_is_copied_to_query(self):
        self.node("""
const page=core.ORIGIN+core.DATA_ROUTES.academic+'?gnmkdm=N5083&layout=default&su=SYNTHETIC-PRIVATE';
assert.equal(core.observedMenuCode(page),'N5083');
for (const url of [core.ORIGIN+'/page?gnmkdm=OTHER',core.ORIGIN+'/page?gnmkdm=N5083&gnmkdm=N5084','https://example.invalid/page?gnmkdm=N5083']) assert.equal(core.observedMenuCode(url),null);
const reg={operation:'academic',route:core.DATA_ROUTES.academic,menu_code:core.observedMenuCode(page),filters:{academic_year_raw:'2026-2027',term_raw:'1',academic_year_label:'2026-2027',term_label:'秋、冬'}};
const req=core.buildRequest({operation:'academic',target_term:'2026-2027-AW'},reg),url=new URL(req.url),form=new URLSearchParams(req.body);
assert.equal(url.searchParams.get('gnmkdm'),'N5083');assert.equal(url.searchParams.has('su'),false);assert.equal(url.searchParams.has('layout'),false);
assert.equal(form.get('xn'),'2026-2027');assert.equal(form.get('xq'),'1');assert.equal(form.has('su'),false);
assert.throws(()=>core.buildRequest({operation:'academic',target_term:'2026-2027-AW'},{...reg,menu_code:'SYNTHETIC-PRIVATE'}),/route_not_registered/);
""")

    def test_observed_timetable_ui_registers_separate_fixed_query_route(self):
        self.node("""
const ui=core.ORIGIN+'/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N253508';
assert.equal(core.routeObserved('timetable',[ui]),true);assert.equal(core.routeObserved('academic',[ui]),false);
assert.equal(core.routeObserved('timetable',[core.ORIGIN+'/jwglxt/kbcx/other.html']),false);
const reg={operation:'timetable',route:core.DATA_ROUTES.timetable,view_mode:'list',menu_code:core.observedMenuCode(ui),filters:{academic_year_raw:'2026-2027',term_raw:'1|秋',academic_year_label:'2026-2027',term_label:'秋'}};
const req=core.buildRequest({operation:'timetable',target_term:'2026-2027-A'},reg),form=new URLSearchParams(req.body);
assert.equal(new URL(req.url).pathname,core.DATA_ROUTES.timetable);assert.equal(new URL(req.url).searchParams.get('gnmkdm'),'N253508');
assert.equal(form.get('xnm'),'2026-2027');assert.equal(form.get('xqm'),'1|秋');assert.equal(form.get('xqmmc'),'秋');assert.equal(form.get('xsfs'),'1');assert.equal(form.get('xxqf'),'1');
assert.throws(()=>core.buildRequest({operation:'timetable',target_term:'2026-2027-AW'},reg),/scope_not_verified/);
""")

    def test_observed_exam_filters_and_separate_final_midterm_fields(self):
        self.node("""
const reg={operation:'exams',route:core.DATA_ROUTES.exams,menu_code:'N509070',filters:{academic_year_raw:'2026-2027',term_raw:'1|秋',academic_year_label:'2026-2027',term_label:'秋'}};
const req=core.buildRequest({operation:'exams',target_term:'2026-2027-A'},reg),form=new URLSearchParams(req.body);
assert.equal(form.get('xxq'),'秋');assert.equal(form.get('xnxq'),'(2026-2027-1)-');assert.equal(form.has('xnm'),false);assert.equal(form.has('xqm'),false);assert.equal(form.has('xh'),false);
const fields=core.projectJSON('exams',{items:[{xkkh:'(2026-2027-1)-SYNTHETIC',kcmc:'合成课程',xxq:'秋',kssj:'',jsmc:'',qzkssj:'合成期中原文',qzjsmc:'合成期中地点',xm:'SYNTHETIC-NONIDENTITY'}]},'2026-2027-A').payload.items[0];
assert.equal(fields.kssj,'');assert.equal(fields.jsmc,'');assert.equal(fields.qzkssj,'合成期中原文');assert.equal(fields.qzjsmc,'合成期中地点');assert.equal(fields.xxq,'秋');assert.equal(Object.hasOwn(fields,'xm'),false);
for(const term_raw of ['1','1|秋冬','1|冬']) assert.throws(()=>core.buildRequest({operation:'exams',target_term:'2026-2027-A'},{...reg,filters:{...reg.filters,term_raw}}),/scope_not_verified/);
""")

    def test_timetable_view_requires_single_visible_mode_and_refuses_unmapped_supplements(self):
        self.node("""
const model={table_visible:true,list_visible:true,table_selected:false,list_selected:true};
assert.equal(core.confirmedTimetableView(model),'list');assert.equal(core.confirmedTimetableView({...model,table_selected:true,list_selected:false}),'table');
for(const patch of [{table_visible:false},{list_visible:false},{table_selected:true},{list_selected:false}])assert.equal(core.confirmedTimetableView({...model,...patch}),null);
const reg={operation:'timetable',route:core.DATA_ROUTES.timetable,view_mode:'table',filters:{academic_year_raw:'2026-2027',term_raw:'1|秋',academic_year_label:'2026-2027',term_label:'秋'}};
const form=new URLSearchParams(core.buildRequest({operation:'timetable',target_term:'2026-2027-A'},reg).body);
assert.equal(form.get('xsfs'),'0');assert.equal(form.get('xxqf'),'0');
assert.throws(()=>core.buildRequest({operation:'timetable',target_term:'2026-2027-A'},{...reg,view_mode:null}),/scope_not_verified/);
for(const key of ['fKbList','sjkList','xskList','ywkList','jxkList'])assert.throws(()=>core.projectJSON('timetable',{kbList:[],[key]:[{sjkcmc:'合成实践课程'}]},'2026-2027-A','table'),/unsupported_timetable_supplement/);
assert.equal(core.projectJSON('timetable',{kbList:[{kcmc:'合成课程'}],sjkList:[]},'2026-2027-A','table').payload.kbList.length,1);
""")

    def test_plan_http_uses_observed_source_scope_and_fixed_catalogue_parameters(self):
        self.node("""
const scope={cohort:2023,college_name:'合成学院',major_or_category_raw:'合成专业',program:null,study_path_raw:null};
const reg={operation:'plan_catalogue',route:core.PLAN_CATALOGUE_ROUTE,menu_code:'N153020',filters:{cohort:2023,college_name:'合成学院',major_name:'合成专业'},query_values:{synj:'2023',xydm:'SYN-COLLEGE',zydm:'SYN-MAJOR',dlbs:''}};
const spec={operation:'plan_catalogue',scope,page:1,page_size:20},req=core.buildPlanRequest(spec,reg),form=new URLSearchParams(req.body);
assert.equal(core.routeObserved('plan_catalogue',[core.ORIGIN+core.PLAN_CATALOGUE_ROUTE]),true);assert.equal(new URL(req.url).pathname,core.PLAN_CATALOGUE_ROUTE);assert.equal(new URL(req.url).searchParams.get('gnmkdm'),'N153020');
assert.equal(form.get('synj'),'2023');assert.equal(form.get('xydm'),'SYN-COLLEGE');assert.equal(form.get('zydm'),'SYN-MAJOR');assert.equal(form.has('xh'),false);assert.equal(form.has('su'),false);assert.equal(form.get('queryModel.showCount'),'20');
assert.throws(()=>core.buildPlanRequest({...spec,scope:{...scope,cohort:2026}},reg),/scope_not_verified/);
assert.throws(()=>core.buildPlanRequest(spec,{...reg,query_values:{...reg.query_values,su:'SYNTHETIC'}}),/scope_not_verified/);
const ref='PLAN-'+ 'c'.repeat(32), result=core.projectPlanJSON({items:[{xymc:'合成学院',zymc:'合成专业',jxjhh:'SYNTHETIC-PLAN-ID',Cookie:'SYNTHETIC'}],totalResult:1,currentPage:1,showCount:20},core.validateSpec(spec),reg,()=>ref);
assert.equal(result.payload.items[0].cohort,2023);assert.equal(result.payload.items[0].cohort_provenance,'confirmed_query_scope');assert.equal(result.payload.items[0].major_or_category_raw,null);assert.equal(result.payload.items[0].program,null);assert.equal(result.payload.items[0].title,null);assert.equal(Object.hasOwn(result.payload.items[0],'jxjhh'),false);assert.equal(Object.hasOwn(result.payload.items[0],'Cookie'),false);
const pdf=new URL(result.documents.get(ref));assert.equal(pdf.pathname,core.PLAN_DOCUMENT_ROUTE);assert.equal(pdf.searchParams.get('id'),'SYNTHETIC-PLAN-ID');assert.equal(pdf.searchParams.get('doType'),'query');assert.equal(pdf.searchParams.has('su'),false);
assert.throws(()=>core.projectPlanJSON({items:[{xymc:'别的学院',zymc:'合成专业',jxjhh:'SYNTHETIC-PLAN-ID'}]},core.validateSpec(spec),reg,()=>ref),/row_outside_confirmed_scope/);
""")

    def test_plan_http_fetch_returns_projection_and_ephemeral_document_registry(self):
        self.node("""
const scope={cohort:2023,college_name:'合成学院',major_name:'合成专业'},reg={operation:'plan_catalogue',route:core.PLAN_CATALOGUE_ROUTE,filters:scope,query_values:{synj:'2023',xydm:'SYN-COLLEGE',zydm:'SYN-MAJOR'}};
const bytes=new TextEncoder().encode(JSON.stringify({items:[{xymc:'合成学院',zymc:'合成专业',jxjhh:'SYNTHETIC-PLAN-ID'}],totalResult:1,currentPage:1}));
const result=await core.fetchPlanCatalogue({operation:'plan_catalogue',scope},reg,'SYNTHETIC-EPOCH',async(url,options)=>{assert.equal(options.credentials,'include');return {status:200,url,headers:{get:key=>key==='content-type'?'application/json':null},arrayBuffer:async()=>bytes.buffer};});
assert.equal(result.state,'business');assert.equal(result.http_status,200);assert.equal(result.payload.items.length,1);assert.equal(result.documents.size,1);assert.equal(result.source_subject_id,null);
assert.equal(Object.hasOwn(result,'capture_method'),false);assert.equal(Object.hasOwn(result.payload.items[0],'binding_status'),false);
""")

    def test_pdf_html_endpoint_is_fixed_and_only_accepts_registered_row_id(self):
        self.node("""
const url=core.planDocumentURL('SYNTHETIC-PLAN-ID');assert.equal(core.pdfURL(url),url);
for(const raw of [core.ORIGIN+'/jwglxt/pyfagl/arbitrary.html?id=SYNTHETIC&doType=query',url+'&su=SYNTHETIC',url+'&token=SYNTHETIC',url+'&id=OTHER',core.ORIGIN+core.PLAN_DOCUMENT_ROUTE+'?id=SYNTHETIC&doType=update'])assert.throws(()=>core.pdfURL(raw));
assert.throws(()=>core.planDocumentURL('SYNTHETIC&ticket=SECRET'));
const ref='PLAN-'+ 'd'.repeat(32),bytes=new TextEncoder().encode('%PDF-1.4 SYNTHETIC');
const result=await core.fetchPlanDocument({operation:'plan_document',plan_ref:ref},new Map([[ref,url]]),'SYNTHETIC-EPOCH',async(actual,options)=>{assert.equal(actual,url);assert.equal(options.method,'GET');return {status:200,url,headers:{get:key=>key==='content-type'?'application/pdf':null},arrayBuffer:async()=>bytes.buffer};});
assert.equal(result.state,'business');assert.equal(Object.hasOwn(result,'url'),false);
""")

    def test_conflicting_explicit_selection_year_is_refused_without_inference(self):
        self.node("""
for (const operation of ['academic','major_academic','exams']) {
  assert.throws(()=>core.projectJSON(operation,{items:[{xkkh:'(2023-2024-1)-SYNTHETIC',kcmc:'合成课程'}]},'2026-2027-AW'),/row_outside_confirmed_scope/);
  assert.equal(core.projectJSON(operation,{items:[{xkkh:'(2026-2027-1)-SYNTHETIC',kcmc:'合成课程'}]},'2026-2027-AW').payload.items.length,1);
}
const unknown=core.projectJSON('academic',{items:[{xkkh:'SYNTHETIC-UNKNOWN',kcmc:'合成课程'}]},'2026-2027-AW');
assert.equal(Object.hasOwn(unknown.payload.items[0],'academic_year_raw'),false);assert.equal(Object.hasOwn(unknown.payload.items[0],'earned_credits'),false);
assert.throws(()=>core.projectJSON('academic',{items:[{xkkh:{raw_value:'(2023-2024-1)-SYNTHETIC'},kcmc:'合成课程'}]},'2026-2027-AW'),/row_outside_confirmed_scope/);
const reg={operation:'academic',route:core.DATA_ROUTES.academic,filters:{academic_year_raw:'2026-2027',term_raw:'1',academic_year_label:'2026-2027',term_label:'秋、冬'}};
const bytes=new TextEncoder().encode(JSON.stringify({items:[{xkkh:'(2023-2024-1)-SYNTHETIC',kcmc:'合成课程'}]}));
const response=await core.fetchBusiness({operation:'academic',target_term:'2026-2027-AW'},reg,'SYNTHETIC-EPOCH',async()=>({status:200,headers:{get:key=>key==='content-type'?'application/json':null},arrayBuffer:async()=>bytes.buffer}));
assert.equal(response.state,'schema_changed');assert.equal(Object.hasOwn(response,'payload'),false);
""")

    def test_term_labels_confirm_combined_and_single_scope_without_raw_code_inference(self):
        self.node("""
for (const [label,code] of [['秋、冬学期','AW'],['春夏','SS'],['秋','A'],['冬','W'],['春','S'],['夏','SU']]) assert.equal(core.sourceTerm('2026-2027学年',label),'2026-2027-'+code);
for (const [year,label] of [['2026','秋冬'],['2026-2028','秋冬'],['2026-2027','全部'],['2026-2027','1|秋'],['2026-2027','未知']]) assert.equal(core.sourceTerm(year,label),null);
assert.throws(()=>core.validateSpec({operation:'academic',target_term:'2026-2028-AW'}),/invalid_request_spec/);
assert.throws(()=>core.validateSpec({operation:'academic',target_term:'2026-2027-AW',scope:{academic_year_raw:'2026-2027',term_raw:'1'}}),/invalid_request_spec/);
""")

    def test_business_projection_drops_credentials_unrelated_fields_and_preserves_attempts(self):
        self.node("""
const raw={items:[{xh:'SYNTHETIC-STUDENT',kcdm:'SYN-PE',kcmc:'合成体育',xf:'1.00',cj:'90',Cookie:'SYNTHETIC',password:'SYNTHETIC',address:'SYNTHETIC'},{xh:'SYNTHETIC-STUDENT',kcdm:'SYN-PE',kcmc:'合成体育',xf:'1.00',cj:'优秀'}],totalResult:'2',currentPage:1,showCount:20,headers:{Cookie:'SYNTHETIC'}};
const result=core.projectJSON('academic',raw);
assert.equal(result.payload.items.length,2); assert.equal(result.source_subject_id,'SYNTHETIC-STUDENT');
assert.deepEqual(Object.keys(result.payload.items[0]).sort(),['cj','kcdm','kcmc','xf']);
assert.equal(result.payload.items[0].xf,'1.00'); assert.equal(result.payload.items[1].cj,'优秀');
assert.equal(Object.hasOwn(result.payload,'headers'),false);
assert.equal(core.projectJSON('major_academic',raw).payload.items.length,2);
""")

    def test_credential_like_business_values_and_wrappers_fail_closed(self):
        self.node("""
for (const raw of [{items:[{kcmc:'Cookie=SYNTHETIC'}]},{items:[{kcmc:{raw_value:'Bearer SYNTHETIC'}}]},{items:[{kcmc:{password:'SYNTHETIC'}}]}]) assert.throws(()=>core.projectJSON('academic',raw));
const output=core.projectJSON('academic',{items:[{kcmc:{raw_value:'合成课程',value:'合成课程',state:'known',Cookie:'SYNTHETIC'}}]});
assert.equal(Object.hasOwn(output.payload.items[0].kcmc,'Cookie'),false);
""")

    def test_source_subject_only_explicit_business_ids_and_conflict_stops(self):
        self.node("""
assert.equal(core.projectJSON('academic',{items:[{kcmc:'合成课程'}]}).source_subject_id,null);
assert.equal(core.sourceSubject({name:'SYNTHETIC',cohort:2023},[]),null);
assert.throws(()=>core.projectJSON('academic',{xh:'SYNTHETIC-ONE',items:[{xh:'SYNTHETIC-TWO',kcmc:'合成课程'}]}),/source_subject_changed/);
assert.equal(core.sourceSubject({xh:'SYNTHETIC-ONE'},[{xh:'SYNTHETIC-ONE'}]),'SYNTHETIC-ONE');
""")

    def test_unknown_schema_and_ambiguous_container_are_not_business(self):
        self.node("""
for (const raw of [{data:[{kcmc:'合成课程'}]},{items:[],records:[]},{items:[{address:'SYNTHETIC'}]},{items:'SYNTHETIC'},{items:[],totalResult:'NaN'}]) assert.throws(()=>core.projectJSON('academic',raw));
assert.deepEqual(core.projectJSON('academic',{items:[],totalResult:0}).payload,{items:[],totalResult:0});
""")

    def test_timetable_segments_and_blank_exam_values_survive(self):
        self.node("""
const time=core.projectJSON('timetable',{kbList:[{kcdm:'SYN',sfqd:0,dsz:2,meetings:[{xqj:2,periods:[3,4],week_set:[1,2],cdmc:'合成教室'},{sksj:'原始秋冬时段'}]}]}).payload;
assert.equal(time.kbList[0].sfqd,0); assert.equal(time.kbList[0].meetings.length,2); assert.deepEqual(time.kbList[0].meetings[0].periods,[3,4]);
const exam=core.projectJSON('exams',{items:[{kcmc:'合成课',final_exam_time_raw:'',midterm_exam_time_raw:'',final_exam_place_raw:''}]}).payload.items[0];
assert.equal(exam.final_exam_time_raw,''); assert.equal(exam.midterm_exam_time_raw,''); assert.equal(Object.hasOwn(exam,'exam_date'),false);
""")

    def test_redirect_auth_forbidden_and_non200_never_read_body(self):
        self.node("""
const reg={operation:'academic',route:core.DATA_ROUTES.academic,filters:{academic_year_raw:'2026-2027',term_raw:'SOURCE',academic_year_label:'2026-2027',term_label:'秋'}};
for (const [status,type,expected] of [[302,'default','awaiting_user_login'],[0,'opaqueredirect','awaiting_user_login'],[401,'default','source_session_expired'],[403,'default','permission_denied'],[500,'default','source_unavailable']]) {
  const result=await core.fetchBusiness({operation:'academic',target_term:'2026-2027-A'},reg,'SYNTHETIC-EPOCH',async()=>({status,type,headers:{get:()=>null},arrayBuffer:()=>{throw Error('body must not be read')}}));
  assert.equal(result.state,expected); assert.equal(Object.hasOwn(result,'payload'),false);
}
""")

    def test_login_captcha_and_pledge_html_are_state_only(self):
        self.node("""
const reg={operation:'academic',route:core.DATA_ROUTES.academic,filters:{academic_year_raw:'2026-2027',term_raw:'SOURCE',academic_year_label:'2026-2027',term_label:'秋'}};
for (const [text,expected] of [['<input type="password" value="SYNTHETIC">','source_session_expired'],['验证码 SYNTHETIC','captcha_required'],['本人承诺遵守考试纪律，继续查看','user_confirmation_required'],['权限不足','permission_denied'],['<p>未知页面</p>','schema_changed']]) {
  const bytes=new TextEncoder().encode(text);
  const result=await core.fetchBusiness({operation:'academic',target_term:'2026-2027-A'},reg,'SYNTHETIC-EPOCH',async()=>({status:200,headers:{get:name=>name==='content-type'?'text/html':null},arrayBuffer:async()=>bytes.buffer}));
  assert.equal(result.state,expected); assert.equal(Object.hasOwn(result,'payload'),false); assert.equal(Object.hasOwn(result,'body'),false);
}
""")

    def test_bounded_stream_and_declared_length(self):
        self.node("""
await assert.rejects(()=>core.readLimited({headers:{get:()=>String(core.JSON_LIMIT+1)}},core.JSON_LIMIT),/response_too_large/);
let cancelled=false;
const stream=new ReadableStream({start(controller){controller.enqueue(new Uint8Array(8));controller.enqueue(new Uint8Array(8));},cancel(){cancelled=true;}});
await assert.rejects(()=>core.readLimited({headers:{get:()=>null},body:stream},10),/response_too_large/);
assert.equal(cancelled,true);
""")

    def test_json_fetch_returns_projected_payload_without_headers(self):
        self.node("""
const reg={operation:'academic',route:core.DATA_ROUTES.academic,filters:{academic_year_raw:'2026-2027',term_raw:'SOURCE',academic_year_label:'2026-2027',term_label:'秋'}};
const bytes=new TextEncoder().encode(JSON.stringify({items:[{xh:'SYNTHETIC-ID',kcmc:'合成课程',password:'SYNTHETIC'}],totalResult:1}));
const result=await core.fetchBusiness({operation:'academic',target_term:'2026-2027-A'},reg,'SYNTHETIC-EPOCH',async(url,options)=>{assert.equal(options.credentials,'include');return {status:200,url,headers:{get:name=>name==='content-type'?'application/json;charset=UTF-8':null},arrayBuffer:async()=>bytes.buffer};});
assert.equal(result.state,'business'); assert.equal(result.session_epoch,'SYNTHETIC-EPOCH'); assert.equal(result.source_subject_id,'SYNTHETIC-ID');
assert.equal(Object.hasOwn(result,'headers'),false); assert.deepEqual(result.payload.items,[{kcmc:'合成课程'}]);
""")

    def test_plan_catalogue_requires_source_scope_and_keeps_missing_path_unknown(self):
        self.node("""
const scope={cohort:2023,college_name:'合成学院',major_or_category_raw:'合成类'};
const rows=[{...scope,title:'合成方案',plan_ref:'PLAN-'+ 'a'.repeat(32),program:null,study_path_raw:null,private_url:'https://example.invalid'}];
const output=core.projectPlanRows(rows,scope,scope)[0];
assert.equal(output.program,null); assert.equal(output.study_path_raw,null); assert.equal(output.source_observation_kind,'directory_observation'); assert.equal(Object.hasOwn(output,'private_url'),false);
assert.throws(()=>core.projectPlanRows(rows,{...scope,cohort:2026},scope),/plan_scope_not_verified/);
assert.throws(()=>core.projectPlanRows(rows,scope,{}),/plan_scope_not_verified/);
assert.throws(()=>core.projectPlanRows(rows,{college_name:'合成学院'},scope),/plan_scope_not_verified/);
const directoryRows=[{cohort:2023,college_name:'合成学院',major_name:'合成类',plan_ref:'PLAN-'+ 'b'.repeat(32),title:null}];
const directoryFilters={cohort:2023,college_name:'合成学院',major_name:'合成类'};
const candidate=core.projectPlanRows(directoryRows,scope,directoryFilters)[0];
assert.equal(candidate.major_name,'合成类');assert.equal(candidate.major_or_category_raw,null);assert.equal(candidate.title,null);
assert.equal(Object.hasOwn(candidate,'binding_status'),false);
assert.throws(()=>core.projectPlanRows(directoryRows,{...scope,major_or_category_raw:'不同类'},directoryFilters),/plan_scope_not_verified/);
""")

    def test_plan_document_is_observed_opaque_ref_same_origin_pdf_only(self):
        self.node("""
const ref='PLAN-'+ 'a'.repeat(32);
for (const url of ['https://example.invalid/plan.pdf',core.ORIGIN+'/plan.pdf?ticket=SYNTHETIC',core.ORIGIN+'/cas/login.pdf',core.ORIGIN+'/plan.html']) assert.throws(()=>core.pdfURL(url));
let calls=0;
const missing=await core.fetchPlanDocument({operation:'plan_document',plan_ref:ref},new Map(),'SYNTHETIC-EPOCH',async()=>{calls++;});
assert.equal(missing.state,'route_not_confirmed'); assert.equal(calls,0);
const bytes=new TextEncoder().encode('%PDF-1.4\\nSYNTHETIC-DOCUMENT');
const result=await core.fetchPlanDocument({operation:'plan_document',plan_ref:ref},new Map([[ref,core.ORIGIN+'/synthetic-plan.pdf']]),'SYNTHETIC-EPOCH',async(url,options)=>{assert.equal(options.credentials,'include');assert.equal(options.method,'GET');return {status:200,url,headers:{get:key=>key==='content-type'?'application/pdf':null},arrayBuffer:async()=>bytes.buffer};});
assert.equal(result.state,'business'); assert.equal(Buffer.from(result.payload_base64,'base64').toString(),'%PDF-1.4\\nSYNTHETIC-DOCUMENT'); assert.equal(Object.hasOwn(result,'plan_ref'),false);
""")

    def test_pdf_mime_signature_and_size_are_checked(self):
        self.node("""
const ref='PLAN-'+ 'a'.repeat(32), documents=new Map([[ref,core.ORIGIN+'/synthetic.pdf']]);
for (const [type,text,expected] of [['text/html','<input type="password">','source_session_expired'],['application/pdf','SYNTHETIC','schema_changed']]) {
  const bytes=new TextEncoder().encode(text);
  const result=await core.fetchPlanDocument({operation:'plan_document',plan_ref:ref},documents,'SYNTHETIC-EPOCH',async()=>({status:200,headers:{get:name=>name==='content-type'?type:null},arrayBuffer:async()=>bytes.buffer}));
  assert.equal(result.state,expected); assert.equal(Object.hasOwn(result,'payload_base64'),false);
}
const large=await core.fetchPlanDocument({operation:'plan_document',plan_ref:ref},documents,'SYNTHETIC-EPOCH',async()=>({status:200,headers:{get:name=>name==='content-type'?'application/pdf':String(core.PDF_LIMIT+1)}}));
assert.equal(large.state,'schema_changed');
""")

    def test_loopback_is_fixed_numeric_host_and_known_path(self):
        self.node("""
assert.equal(core.loopbackURL(18876,'/pair'),'http://127.0.0.1:18876/pair');
for (const port of ['http://example.invalid',80,65536,'18876/../../']) assert.throws(()=>core.loopbackURL(port,'/pair'));
assert.throws(()=>core.loopbackURL(18876,'/arbitrary'));
""")

    def test_worker_and_content_do_not_persist_or_log_sensitive_state(self):
        worker = (CONNECTOR / "background.js").read_text()
        content = (CONNECTOR / "content.js").read_text()
        for text in (worker, content):
            self.assertNotIn("chrome.storage", text)
            self.assertNotIn("document.cookie", text)
            self.assertNotIn("console.", text)
            self.assertNotIn("localStorage", text)
        self.assertIn("sender.tab?.id !== current.tabId", worker)
        self.assertIn("sender.frameId !== 0", worker)
        self.assertIn("response.session_epoch !== current.epoch", worker)
        self.assertIn("message.session_epoch === epoch", content)

    def test_worker_pairing_epoch_and_fixed_tab_contract(self):
        worker_path = json.dumps(str(CONNECTOR / "background.js"))
        self.node("""
const vm=require('node:vm'),fs=require('node:fs');
const extension='a'.repeat(32), epoch='SYNTHETIC-EPOCH-0001', token='SYNTHETIC_CONNECTION_'.repeat(3);
let callback,requests=[];
const sandbox={StudentConnector:core,AbortController,TextDecoder,URL,setTimeout,clearTimeout,importScripts:()=>{},chrome:{runtime:{id:extension,getURL:path=>'chrome-extension://'+extension+'/'+path,onMessage:{addListener:cb=>callback=cb}},tabs:{query:async()=>[{id:101,url:core.ORIGIN+'/jwglxt/menu.html'}],sendMessage:async()=>({ok:true,state:'ready',operations:['academic'],session_epoch:epoch}),onRemoved:{addListener:()=>{}},onUpdated:{addListener:()=>{}}}},fetch:async(url,options)=>{requests.push({url,options});let result=url.endsWith('/pair')?{token,session_epoch:epoch}:{job:null};const bytes=new TextEncoder().encode(JSON.stringify(result));return {status:200,headers:{get:()=>null},arrayBuffer:async()=>bytes.buffer};}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(WORKER_PATH,'utf8'),sandbox);
const popup={id:extension,url:'chrome-extension://'+extension+'/popup.html'};
const invoke=(message,sender)=>new Promise(resolve=>callback(message,sender,resolve));
let unauthorized=false;callback({kind:'pair',port:18876,code:'SYNTHETIC'}, {id:extension,url:core.ORIGIN+'/page'},()=>{unauthorized=true;});
assert.equal(unauthorized,false);assert.equal(requests.length,0);
const paired=await invoke({kind:'pair',port:18876,code:'SYNTHETIC_CODE'},popup);
assert.equal(paired.ok,true);assert.equal(Object.hasOwn(paired,'token'),false);assert.equal(requests.length,1);
assert.equal(JSON.parse(requests[0].options.body).session_epoch,epoch);
callback({kind:'heartbeat',session_epoch:epoch},{id:extension,tab:{id:202},frameId:0},()=>{});await new Promise(r=>setTimeout(r,10));assert.equal(requests.length,1);
callback({kind:'heartbeat',session_epoch:epoch},{id:extension,tab:{id:101},frameId:0},()=>{});await new Promise(r=>setTimeout(r,10));assert.equal(requests.length,3);assert.equal(requests[1].options.headers.Authorization,'Bearer '+token);assert.equal(requests[1].url.endsWith('/heartbeat'),true);assert.equal(requests[2].url.endsWith('/next'),true);
callback({kind:'heartbeat',session_epoch:'SYNTHETIC-NEW-EPOCH'},{id:extension,tab:{id:101},frameId:0},()=>{});
const status=await invoke({kind:'status'},popup);assert.equal(status.connected,false);assert.equal(status.state,'source_session_expired');
assert.equal(requests[3].url.endsWith('/disconnect'),true);
""".replace("WORKER_PATH", worker_path))


if __name__ == "__main__":
    unittest.main()
