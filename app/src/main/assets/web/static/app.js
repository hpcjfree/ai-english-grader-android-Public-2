const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const api=async(url,opt={})=>{const r=await fetch(url,opt);const txt=await r.text();let data;try{data=JSON.parse(txt)}catch{data=txt}if(!r.ok)throw new Error(data?.detail||data||r.statusText);return data};
let toastTimer;
const toast=m=>{const t=$('#toast');t.textContent=m;t.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove('show'),3200)};
const esc=(x='')=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let currentGroup=null,currentStyle=null;

const pageMeta={
  grade:['WORKSPACE','作文批改'],history:['ARCHIVE','历史记录'],schemes:['RUBRIC TUNING','批改微调'],styles:['TEACHER CALIBRATION','风格学习'],settings:['SYSTEM','设置']
};

function setTheme(theme){
  document.documentElement.dataset.theme=theme;
  localStorage.setItem('grader-theme',theme);
  $('#themeToggle').textContent=theme==='dark'?'☀':'◐';
  document.querySelector('meta[name="theme-color"]').content=theme==='dark'?'#1d1b20':'#6750a4';
}
setTheme(localStorage.getItem('grader-theme')||((window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light'));
$('#themeToggle').onclick=()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark');

async function initDevice(){
  let server='desktop';
  try{server=(await api('/api/device')).device}catch{}
  const w=window.innerWidth, touch=navigator.maxTouchPoints>1;
  const device=w<=720?'mobile':(w<=1024||(/Macintosh/.test(navigator.userAgent)&&touch)?'tablet':server);
  document.body.dataset.device=device;
  $('#deviceTag').textContent={mobile:'手机界面',tablet:'Pad 界面',desktop:'电脑界面'}[device]||'响应式界面';
}
window.addEventListener('resize',()=>{clearTimeout(window.__resizeT);window.__resizeT=setTimeout(initDevice,150)});

function showPage(name){
  $$('#nav button').forEach(x=>x.classList.toggle('active',x.dataset.page===name));
  $$('.page').forEach(x=>x.classList.remove('active'));
  $('#page-'+name).classList.add('active');
  const [eyebrow,title]=pageMeta[name]||['',''];$('#pageEyebrow').textContent=eyebrow;$('#pageTitle').textContent=title;
  window.scrollTo({top:0,behavior:'smooth'});
  if(name==='history')loadHistory();
  if(name==='settings')loadSettings();
}
$$('#nav button').forEach(b=>b.onclick=()=>showPage(b.dataset.page));

$('#hasSample').onchange=e=>$('#sampleBox').classList.toggle('hidden',!e.target.checked);
$('#scAdh').oninput=e=>$('#adhVal').textContent=e.target.value;

async function ocr(input,target,label,status){
  const fs=input.files;if(!fs.length)return toast('请先选择图片');
  const f=new FormData();[...fs].forEach(x=>f.append('files',x));f.append('label',label);
  if(status)$(status).textContent='AI 正在提取…';
  try{const d=await api('/api/ocr',{method:'POST',body:f});$(target).value=d.text;toast(`提取完成 · ${d.usage.total_tokens} tokens`)}
  catch(e){toast(e.message)}finally{if(status)$(status).textContent=''}
}
$('#ocrQuestion').onclick=()=>ocr($('#qFiles'),'#gQuestion','英语作文题目','#qStatus');
$('#ocrSample').onclick=()=>ocr($('#sFiles'),'#gSample','英语作文范文');
$('#ocrRubric').onclick=()=>ocr($('#rubricFiles'),'#scRubric','英语作文评分标准');

async function loadOptions(){
  const [s,st]=await Promise.all([api('/api/schemes'),api('/api/styles')]);
  $('#gScheme').innerHTML=s.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join('');
  $('#gStyle').innerHTML='<option value="">默认风格</option>'+st.filter(x=>x.status==='ready').map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join('');
}
async function loadGroups(){
  const gs=await api('/api/groups');
  $('#groupList').innerHTML=gs.length?gs.map(g=>`<article class="card"><h3>${esc(g.name)}</h3><span class="tag">${g.status==='active'?'● 进行中':'已结束'}</span><span class="tag">${new Date(g.created_at).toLocaleDateString()}</span><p class="muted">${esc((g.question_text||'未填写题目').slice(0,120))}</p>${g.status==='active'?`<button data-resume-id="${g.id}" data-resume-name="${esc(g.name)}">继续批改 →</button>`:''}</article>`).join(''):'<div class="empty-state"><span>☷</span><b>暂无批改组</b><small>上方创建一次新的批改即可开始</small></div>';
  $$('[data-resume-id]').forEach(b=>b.onclick=()=>startSession(+b.dataset.resumeId,b.dataset.resumeName));
}
$('#createGroup').onclick=async()=>{
  const name=$('#gName').value.trim();if(!name)return toast('请填写批改名称');
  try{const g=await api('/api/groups',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,question_text:$('#gQuestion').value,sample_text:$('#hasSample').checked?$('#gSample').value:'',scheme_id:+$('#gScheme').value||null,style_id:+$('#gStyle').value||null})});startSession(g.id,g.name);loadGroups()}catch(e){toast(e.message)}
};
function startSession(id,name){
  currentGroup=id;$('#groupCreate').classList.add('hidden');$('#gradingMode').classList.remove('hidden');$('#activeGroupName').textContent=name;
  $('#resultBox').className='resultBox empty';$('#resultBox').innerHTML='<div class="empty-state"><span>✦</span><b>等待下一份作文</b><small>短按拍照按钮开始识别与批改</small></div>';window.scrollTo({top:0,behavior:'smooth'});
}
function leaveSession(){currentGroup=null;$('#groupCreate').classList.remove('hidden');$('#gradingMode').classList.add('hidden');loadGroups()}
$('#leaveSession').onclick=leaveSession;

$('#essayFiles').onchange=async e=>{
  if(!currentGroup||!e.target.files.length)return;
  const f=new FormData();[...e.target.files].forEach(x=>f.append('files',x));
  $('#gradeStatus').textContent='✦ AI 正在识别并批改…';$('#cameraBtn').disabled=true;
  try{const d=await api(`/api/groups/${currentGroup}/grade`,{method:'POST',body:f});renderResult(d.result,d.usage);toast('批改完成，可以继续下一份')}
  catch(err){toast(err.message)}finally{$('#gradeStatus').textContent='';$('#cameraBtn').disabled=false;e.target.value=''}
};
function renderResult(r,u){
  const issues=(r.language_issues||[]).map(x=>`<div class="issue sev${x.severity}"><b>${esc(x.line)} · ${x.severity===3?'严重':x.severity===2?'中等':'轻微'}</b><div><s>${esc(x.original)}</s></div><div>${esc(x.issue)}</div><div><b>建议：</b>${esc(x.suggestion)}</div></div>`).join('')||'<div class="issue">未发现明确语病。</div>';
  const imp=(r.improvements||[]).map(x=>`<div class="issue"><b>${esc(x.line)}</b><div>${esc(x.original)} → ${esc(x.suggestion)}</div><div class="muted">${esc(x.reason)}</div></div>`).join('')||'<div class="issue">暂无额外修改建议。</div>';
  $('#resultBox').className='resultBox';
  $('#resultBox').innerHTML=`<div class="score">${esc(r.score)}${r.full_score!=null?` <small>/ ${esc(r.full_score)}</small>`:''}</div><p>${esc(r.short_review||'')}</p><h3>语病</h3><div class="issues">${issues}</div><h3>可修改内容</h3><div class="issues">${imp}</div><p class="note">识别置信度：${Math.round((r.confidence||0)*100)}% · 本次 ${u.total_tokens} tokens</p>`;
}

let holdTimer=null,holdEnded=false;
function beginHold(){
  if(!currentGroup||$('#cameraBtn').disabled)return;holdEnded=false;$('#cameraBtn').classList.add('holding');$('#holdHint').textContent='继续按住，5 秒后结束…';
  holdTimer=setTimeout(async()=>{holdEnded=true;try{await api(`/api/groups/${currentGroup}/end`,{method:'POST'});toast('本次批改已结束');leaveSession()}catch(e){toast(e.message)}},5000);
}
function endHold(){if(holdTimer)clearTimeout(holdTimer);holdTimer=null;$('#cameraBtn').classList.remove('holding');if(currentGroup)$('#holdHint').textContent='长按 5 秒结束'}
$('#cameraBtn').addEventListener('pointerdown',beginHold);
['pointerup','pointercancel','pointerleave'].forEach(ev=>$('#cameraBtn').addEventListener(ev,endHold));
$('#cameraBtn').addEventListener('click',e=>{e.preventDefault();if(holdEnded){holdEnded=false;return}if(currentGroup&&!$('#cameraBtn').disabled)$('#essayFiles').click()});

$('#saveScheme').onclick=async()=>{
  const x={name:$('#scName').value.trim(),rubric_text:$('#scRubric').value,strictness:$('#scStrict').value,allow_zero:$('#scZero').checked,rubric_adherence:+$('#scAdh').value,extra_requirements:$('#scExtra').value};
  if(!x.name)return toast('请填写方案名称');
  try{await api('/api/schemes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(x)});toast('微调方案已保存');$('#scName').value='';loadSchemes();loadOptions()}catch(e){toast(e.message)}
};
async function loadSchemes(){
  const xs=await api('/api/schemes');
  $('#schemeList').innerHTML=xs.map(x=>`<article class="card"><h3>${esc(x.name)}</h3><span class="tag">${{strict:'严格',default:'默认',lenient:'宽松'}[x.strictness]||x.strictness}</span><span class="tag">标准遵守 ${x.rubric_adherence}/10</span><p>${esc((x.rubric_text||'无自定义评分标准').slice(0,180))}</p><small>${esc(x.extra_requirements||'')}</small></article>`).join('');
}

$('#newStyle').onclick=async()=>{
  const name=$('#stName').value.trim(),full=+$('#stFull').value;if(!name||!full)return toast('请填写风格名称和满分');
  try{const s=await api('/api/styles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,full_score:full})});currentStyle=s.id;$('#styleTrainer').classList.remove('hidden');$('#trainerTitle').textContent=`正在学习：${s.name}（满分 ${s.full_score}）`;$('#exScore').max=s.full_score;$('#styleTrainer').scrollIntoView({behavior:'smooth'});loadStyles()}catch(e){toast(e.message)}
};
$('#addExample').onclick=async()=>{
  if(!currentStyle)return toast('请先新建风格');const file=$('#exFile').files[0],score=$('#exScore').value;if(!file||score==='')return toast('请选择作文并输入老师给分');
  const f=new FormData();f.append('file',file);f.append('score',score);
  try{await api(`/api/styles/${currentStyle}/examples`,{method:'POST',body:f});toast('评分样例已加入');$('#exFile').value='';$('#exScore').value='';loadStyles()}catch(e){toast(e.message)}
};
$('#finalizeStyle').onclick=async()=>{
  if(!currentStyle)return;$('#trainStatus').textContent='✦ AI 正在总结评分风格…';
  try{const d=await api(`/api/styles/${currentStyle}/finalize`,{method:'POST'});$('#trainStatus').textContent=JSON.stringify(d.summary,null,2);toast('风格学习完成');loadStyles();loadOptions()}catch(e){toast(e.message);$('#trainStatus').textContent=''}
};
async function loadStyles(){
  const xs=await api('/api/styles');
  $('#styleList').innerHTML=xs.length?xs.map(x=>`<article class="card"><h3>${esc(x.name)}</h3><span class="tag">满分 ${x.full_score}</span><span class="tag">${x.status==='ready'?'✦ 已学习':'学习中'}</span><p>${x.example_count} 个评分样例</p><small>${esc(x.summary_json?.summary||'尚未生成总结')}</small></article>`).join(''):'<div class="empty-state"><span>✦</span><b>暂无风格</b></div>';
}

async function loadHistory(){
  const xs=await api('/api/history');
  $('#historyList').innerHTML=xs.length?xs.map(x=>`<article class="photoCard" data-history-id="${x.id}"><img src="${x.preview||''}" alt="作文预览" loading="lazy"><div class="meta"><b>${esc(x.group_name)}</b><div>得分 ${esc(x.result.score)}${x.result.full_score!=null?' / '+esc(x.result.full_score):''}</div><small>${new Date(x.created_at).toLocaleString()}</small></div></article>`).join(''):'<div class="empty-state"><span>◷</span><b>暂无批改记录</b></div>';
  $$('[data-history-id]').forEach(c=>c.onclick=()=>showHistory(+c.dataset.historyId));
}
async function showHistory(id){
  const x=await api('/api/history/'+id),r=x.result;$('#historyDetail').classList.remove('hidden');
  $('#historyDetail').innerHTML=`<div class="section-head"><div><span class="section-kicker">DETAIL</span><h2>${esc(x.group_name)}</h2><p>${new Date(x.created_at).toLocaleString()}</p></div></div><div class="action-row">${x.image_paths.map(p=>`<img src="${p}" style="width:150px;max-height:200px;object-fit:cover" alt="作文照片">`).join('')}</div><div class="score">${esc(r.score)}${r.full_score!=null?` <small>/ ${esc(r.full_score)}</small>`:''}</div><p>${esc(r.short_review||'')}</p><h3>语病</h3><div class="issues">${(r.language_issues||[]).map(i=>`<div class="issue sev${i.severity}"><b>${esc(i.line)}</b> ${esc(i.original)} — ${esc(i.issue)} → ${esc(i.suggestion)}</div>`).join('')||'<div class="issue">未发现明确语病。</div>'}</div><h3>可修改内容</h3><div class="issues">${(r.improvements||[]).map(i=>`<div class="issue"><b>${esc(i.line)}</b> ${esc(i.original)} → ${esc(i.suggestion)}</div>`).join('')||'<div class="issue">暂无额外修改建议。</div>'}</div>`;
  $('#historyDetail').scrollIntoView({behavior:'smooth'});
}

function selectedProxyMode(){return $('input[name="proxyMode"]:checked')?.value||'direct'}
function syncProxyUI(){const mode=selectedProxyMode();$('#proxyUrlField').classList.toggle('hidden',mode!=='custom');if(mode==='direct')$('#proxyStatus').textContent='当前将使用直连，并完全忽略 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY。';else if(mode==='system')$('#proxyStatus').textContent='保存后将读取系统代理；socks:// 会自动转换为 socks5://。';else $('#proxyStatus').textContent=$('#setProxyUrl').value.trim()?`将使用：${$('#setProxyUrl').value.trim()}`:'请填写 HTTP、HTTPS 或 SOCKS5 代理地址。'}
$$('input[name="proxyMode"]').forEach(r=>r.onchange=syncProxyUI);$('#setProxyUrl').oninput=syncProxyUI;

async function loadSettings(){
  const [s,u]=await Promise.all([api('/api/settings'),api('/api/usage')]);
  $('#setProvider').value=s.provider;$('#setUrl').value=s.base_url;$('#setKey').value=s.api_key;$('#setModel').value=s.model;$('#setTemp').value=s.temperature;$('#setMax').value=s.max_tokens;$('#setTimeout').value=s.request_timeout;$('#setProxyUrl').value=s.proxy_url||'';
  const radio=$(`input[name="proxyMode"][value="${s.proxy_mode||'direct'}"]`);if(radio)radio.checked=true;syncProxyUI();
  if(s.proxy_status?.description)$('#proxyStatus').textContent='已保存：'+s.proxy_status.description;
  $('#usage').innerHTML=`<div class="stat"><span>API 调用</span><b>${u.calls}</b></div><div class="stat"><span>输入 Token</span><b>${u.prompt_tokens}</b></div><div class="stat"><span>输出 Token</span><b>${u.completion_tokens}</b></div><div class="stat"><span>总 Token</span><b>${u.total_tokens}</b></div>`;
}
$('#saveSettings').onclick=async()=>{
  const x={provider:$('#setProvider').value,base_url:$('#setUrl').value,api_key:$('#setKey').value,model:$('#setModel').value,temperature:+$('#setTemp').value,max_tokens:+$('#setMax').value,request_timeout:+$('#setTimeout').value,proxy_mode:selectedProxyMode(),proxy_url:$('#setProxyUrl').value};
  try{await api('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(x)});toast('设置已保存');await loadSettings()}catch(e){toast(e.message)}
};
$('#testVision').onclick=async()=>{
  const el=$('#testStatus');el.className='inline-status test-status';el.textContent='正在连接视觉模型…';
  try{const d=await api('/api/settings/test',{method:'POST'});el.classList.add('ok');el.textContent=`✓ 成功 · ${d.reply} · ${d.usage.total_tokens} tokens`;toast('视觉模型连接成功')}
  catch(e){el.classList.add('error');el.textContent='失败：'+e.message;toast('视觉模型测试失败，详细原因已显示在设置页')}
};

(async()=>{await initDevice();await Promise.all([loadOptions(),loadGroups(),loadSchemes(),loadStyles()])})();
