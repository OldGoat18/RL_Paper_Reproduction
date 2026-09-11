/* The workbench renders execution metadata only. */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const token = $('meta[name="harness-token"]').content;
let state = {projects: [], executions: []};
let catalog = null;
let outputSummary = null;
let selected = localStorage.getItem('rl-project') || '';
let filter = 'all';
let selectedExecution = null;
const selectedRecords = new Set();
let toastTimer;
let llmChoice = null;
let analyzing = false;
let analysisError = '';
let runEntry = null;
let previewVersion = 0;
let previewTimer;
let lastPlan = null;
const defaultSeeds = () => Array.from({length:30}, (_, i) => i).join(', ');
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<i data-lucide="${name}"></i>`;
const icons = () => window.lucide?.createIcons();
const project = () => state.projects.find(p => p.id === selected);
const outputPath = record => {
  const path = record.detected_output_path;
  if (!path) return '';
  return path.startsWith('/') || /^[A-Za-z]:/.test(path) || path.startsWith('~') ? path : `${record.project}/${path}`;
};
function toast(message, error = false) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.toggle('error-toast', error);
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 5000);
}
async function api(path, data) {
  const options = data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json', 'X-Harness-Token':token}, body:JSON.stringify(data)};
  const response = await fetch(`/api/${path}`, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function switchView(view) {
  $('#executions-view').hidden = view !== 'executions';
  $('#resources-view').hidden = view !== 'resources';
  $('#project-view').hidden = view !== 'project';
  const label = view === 'executions' ? 'Experiments' : view === 'resources' ? 'Resources' : 'Project setup';
  $('h1').textContent = label;
  $('#breadcrumb').textContent = label;
  $$('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === view));
}
function filteredRecords() {
  const current = project();
  return state.executions.filter(record => !current || record.project === current.path);
}
function duration(record) {
  if (record.status === 'queued') return 'Queued';
  const elapsed = Math.max(0, Math.floor(((record.end_time ? new Date(record.end_time) : new Date()) - new Date(record.start_time)) / 1000));
  if (!Number.isFinite(elapsed)) return '-';
  return elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
}
function renderRows() {
  const records = filteredRecords();
  $$('.record-select:checked').forEach(input => selectedRecords.add(input.value));
  const selectable = new Set(records.filter(r => !['queued','preparing','running','paused','held'].includes(r.status)).map(r => r.execution_id));
  [...selectedRecords].forEach(id => {
    const record = state.executions.find(r => r.execution_id === id);
    if (!record || !selectable.has(id)) selectedRecords.delete(id);
  });
  $('#total').textContent = records.length;
  $('#running').textContent = records.filter(r => r.status === 'running').length;
  $('#completed').textContent = records.filter(r => r.status === 'success').length;
  $('#failed').textContent = records.filter(r => r.status === 'failed').length;
  const query = $('#search').value.toLowerCase();
  const shown = records.filter(r => (filter === 'all' || r.status === filter || filter === 'running' && r.status === 'queued') && `${r.execution_id} ${r.task} ${r.command?.join(' ') || ''} ${r.detected_output_path || ''}`.toLowerCase().includes(query));
  const statuses = {success:'Completed',running:'Running',failed:'Failed',queued:'Queued',preparing:'Preparing',paused:'Paused',held:'Held',cancelled:'Cancelled',unknown:'Unknown'};
  $('#execution-rows').innerHTML = shown.map(r => `<tr>
    <td><div class="execution-name"><span class="row-icon">${icon(r.task === 'evaluate' ? 'circle-check' : 'terminal')}</span><div><button class="execution-link" data-detail="${escapeHtml(r.execution_id)}">${escapeHtml(r.task || 'Execution')}</button><div class="execution-id mono">${escapeHtml(r.execution_id.slice(0,8))}</div></div></div></td>
    <td><span class="badge ${Object.keys(statuses).includes(r.status) ? r.status : ''}">${escapeHtml(statuses[r.status] || r.status)}</span></td>
    <td class="mono">${escapeHtml(r.seed ?? '-')} <span class="muted">/ ${escapeHtml(r.repeat ?? '-')}</span></td>
    <td>${r.start_time ? escapeHtml(new Date(r.start_time).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})) : '-'}</td>
    <td class="mono muted">${duration(r)}</td>
    <td class="output-cell mono" title="${escapeHtml(outputPath(r) || 'Uncertain')}">${escapeHtml(r.detected_output_path || 'Uncertain')}</td>
    <td><div class="row-actions"><input type="checkbox" class="record-select" value="${escapeHtml(r.execution_id)}" ${selectedRecords.has(r.execution_id)?'checked':''} ${['queued','preparing','running','paused','held'].includes(r.status)?'disabled':''} aria-label="Select record">${['queued','preparing','running'].includes(r.status) && r.supervisor_id ? `<button class="icon" data-cancel="${escapeHtml(r.execution_id)}" title="Stop execution" aria-label="Stop execution">${icon('square')}</button>` : ''}<button class="icon" data-detail="${escapeHtml(r.execution_id)}" title="Execution details" aria-label="Execution details">${icon('arrow-up-right')}</button>${!['queued','preparing','running'].includes(r.status) ? `<button class="icon" data-delete="${escapeHtml(r.execution_id)}" title="Delete record" aria-label="Delete record">${icon('trash-2')}</button>` : ''}</div></td>
  </tr>`).join('');
  $('#empty').hidden = shown.length > 0;
  $('#record-count').textContent = `${shown.length} execution${shown.length === 1 ? '' : 's'}`;
  icons();
  if (selectedExecution && $('#detail-dialog').open) renderDetail(selectedExecution);
}
function renderProject() {
  const p = project();
  const selection = $('#project-select');
  selection.innerHTML = state.projects.length ? state.projects.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`).join('') : '<option value="">No projects</option>';
  selection.value = selected;
  $('#project-path').textContent = p?.path || 'No project selected';
  $('#project-output').textContent = p?.detection?.detected_output_path || 'Uncertain';
  const runtime = p?.runtime;
  $('#project-runtime').textContent = runtime ? `${runtime.status || 'checking'} · ${runtime.python || state.python}` : 'Checking...';
  $('#project-runtime').title = runtime?.error || runtime?.dependency_root || '';
  $('#new-run').disabled = !p;
  $('#catalog-status').textContent = catalog ? `${catalog.entries.length} runnable script${catalog.entries.length === 1 ? '' : 's'} found` : p ? 'Scanning source...' : 'Select a project';
  $('#catalog-summary').innerHTML = catalog ? [...catalog.algorithms.map(a=>`<span class="catalog-chip">${icon('cpu')}${escapeHtml(a)}</span>`), ...catalog.environments.slice(0,24).map(e=>`<span class="catalog-chip">${icon('box')}${escapeHtml(e)}</span>`)].join('') : '';
  const openGroups = new Set($$('#catalog-entries details[open]').map(d=>d.dataset.group));
  $('#catalog-entries').innerHTML = [...new Set((catalog?.entries || []).map(e=>e.subproject))].map(group=>{
    const entries=catalog.entries.filter(e=>e.subproject===group);
    return '<details class="analysis-subproject" data-group="'+escapeHtml(group)+'" '+(openGroups.has(group)?'open':'')+'><summary>'+escapeHtml(entries[0].project_name)+' / '+entries.length+' entry points</summary>'+entries.map(e=>'<div class="catalog-entry"><div><strong>'+escapeHtml(e.path)+'</strong><div class="mono muted">'+escapeHtml(e.directory)+'</div></div><div class="tags">'+[...e.algorithms,...e.environments].map(a=>'<span class="tag">'+escapeHtml(a)+'</span>').join('')+'</div><button class="secondary" data-script="'+escapeHtml(e.id)+'">'+icon('sliders-horizontal')+'Configure</button></div>').join('')+'</details>';
  }).join('');
  $('#analyze').disabled = !p || analyzing;
  $('#analyze-project').disabled = !p || analyzing;
  $('#use-llm').disabled = !state.llm_available;
  $('#use-llm').checked = state.llm_available && (llmChoice ?? true);
  $('#use-llm').title = state.llm_available ? 'LLM analysis' : 'LLM service not configured';
  if (state.llm_error) $('#use-llm').title = state.llm_error;
  $('#configure-llm').innerHTML = `${icon('settings-2')}${state.llm_available ? 'AI settings' : 'Configure AI'}`;
  $('#metadata-root').textContent = state.metadata_root || '';
  $('#python-path').textContent = state.python || '';
  const detection = p?.detection;
  if (!$('#analysis-report')) {
    const report=document.createElement('div');report.id='analysis-report';$('#analysis-status').after(report);
  }
  const report=p?.analysis;
  const openReports = new Set($$('#analysis-report details[open]').map(d=>d.querySelector('summary').textContent));
  $('#analysis-report').innerHTML=report ? (report.catalog.subprojects || []).map(s=>'<details class="analysis-subproject"><summary>'+escapeHtml(s.name)+' / '+escapeHtml(s.dependency_status || 'unscanned')+' / '+s.files_scanned+' source files</summary><p class="mono">'+escapeHtml(s.path)+'</p><pre>'+escapeHtml(s.dependency_error || JSON.stringify({files:s.dependencies?.dependency_files,requirements:s.dependencies?.requirements,options:s.dependencies?.pip_options,warnings:s.warnings,limits:s.limits},null,2))+'</pre></details>').join('') : '';
  $$('#analysis-report details').forEach(d=>d.open=openReports.has(d.querySelector('summary').textContent));
  $('#analysis-status').textContent = analyzing ? 'Analyzing...' : analysisError || (detection ? `${detection.method === 'llm' ? 'LLM' : 'Static'} analysis / ${detection.confidence} confidence` : 'Not analyzed');
  $('#candidates').innerHTML = detection?.candidates?.length ? detection.candidates.map(c => `<div class="candidate"><strong class="mono">${escapeHtml(c.path)}</strong><span>${escapeHtml(c.source)} <span class="badge">${escapeHtml(c.confidence)}</span></span><code>${escapeHtml(c.evidence)}</code></div>`).join('') : '<div class="muted">No confirmed output locations</div>';
  const history=outputSummary?.executions || [];
  $('#project-output-history').innerHTML = outputSummary?.path ? `<p class="mono muted">${escapeHtml(outputSummary.path)} · ${outputSummary.exists?'directory exists':'directory not found yet'}</p>` + (history.length ? history.map(r=>`<div><div><strong>${escapeHtml(r.status||'unknown')}</strong><p class="mono muted">seed ${escapeHtml(r.seed ?? '-')} · repeat ${escapeHtml(r.repeat ?? '-')} · ${escapeHtml((r.command||[]).join(' '))}</p></div></div>`).join('') : '<div class="muted">No completed executions recorded for this output location.</div>') : '<div class="muted">Output location is uncertain; no history comparison available.</div>';
  $('#presets').innerHTML = Object.entries(p?.presets || {}).map(([task, command]) => `<div><div><strong>${escapeHtml(task)}</strong><p class="mono muted">${escapeHtml(command)}</p></div><button class="icon" data-preset="${escapeHtml(task)}" title="Run ${escapeHtml(task)}" aria-label="Run ${escapeHtml(task)}">${icon('play')}</button></div>`).join('') || '<div class="muted">No project presets</div>';
  $('.project-visual').hidden = !p?.presets || !p.path.endsWith('ppo_cartpole');
  icons();
}
function renderResources() {
  const resources=state.resources||{};
  const cpu=Number(resources.cpu_percent); const memory=Number(resources.memory_percent);
  $('#resource-cpu').textContent=Number.isFinite(cpu)?`${cpu.toFixed(1)}%`:'Unavailable';
  $('#resource-memory').textContent=Number.isFinite(memory)?`${memory.toFixed(1)}%`:'Unavailable';
  $('#resource-cpu-bar').style.width=Number.isFinite(cpu)?`${Math.min(100,cpu)}%`:'0%';
  $('#resource-memory-bar').style.width=Number.isFinite(memory)?`${Math.min(100,memory)}%`:'0%';
  $('#resource-slots').textContent=`${resources.running||0} / ${resources.parallel_limit||'-'}`;
  $('#resource-running').textContent=`${resources.active||0} active or queued`;
  const active=state.executions.filter(r=>['queued','preparing','running','paused','held'].includes(r.status));
  $('#resource-executions').innerHTML=active.length?active.map(r=>`<div><div><strong>${escapeHtml(r.task||'Execution')}</strong><p class="mono muted">${escapeHtml((r.command||[]).join(' '))}</p></div><div class="actions">${r.status==='held'?`<button class="secondary" data-start="${r.execution_id}">${icon('play')}Start</button>`:''}${r.status==='running'?`<button class="secondary" data-pause="${r.execution_id}">${icon('pause')}Pause</button>`:''}${r.status==='paused'?`<button class="secondary" data-resume="${r.execution_id}">${icon('play')}Resume</button>`:''}</div></div>`).join(''):'<div class="muted">No active executions</div>';
  icons();
}
async function openLLMConfig() {
  const config = await api('llm/config');
  const form = $('#llm-form');
  form.reset();
  form.elements.working_directory.value = project()?.path || '';
  form.elements.provider.value = config.provider === 'custom' ? 'custom' : 'openai-compatible';
  form.elements.base_url.value = config.base_url || '';
  form.elements.model.value = config.model || '';
  form.elements.api_key.value = '';
  form.elements.timeout_seconds.value = config.timeout_seconds || 45;
  $('#llm-config-status').textContent = config.api_key_configured ? 'An API key is stored. Leave the field blank to keep it.' : 'No API key is stored.';
  $('.form-error', form).textContent = '';
  $('#llm-dialog').showModal();
  icons();
}
async function refresh() {
  try {
    state = await api('state');
    if (!state.projects.some(p => p.id === selected)) selected = state.projects[0]?.id || '';
    renderProject();
    catalog = selected ? await api(`catalog?project_id=${encodeURIComponent(selected)}`) : null;
    outputSummary = selected ? await api(`outputs?project_id=${encodeURIComponent(selected)}`) : null;
    renderProject();
    renderRows();
    renderResources();
    $('#connection').textContent = 'Local engine';
    $('#updated').textContent = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
  } catch (error) {
    $('#connection').textContent = 'Disconnected';
    throw error;
  }
}
function fillPreset(task) {
  const form = $('#run-form');
  const preset = project()?.presets?.[task];
  if (!preset) { preview(); return; }
  form.elements.command.value = preset.replaceAll('{python}', `"${state.python}"`);
  form.dataset.baseCommand = form.elements.command.value;
  form.elements.seeds.value = defaultSeeds();
  preview();
}
async function openRun(task = 'train', entryId = null) {
  if (!project()) return $('#project-dialog').showModal();
  catalog = await api('catalog?project_id=' + encodeURIComponent(selected));
  const form = $('#run-form');
  form.reset();
  if (!$('#entry-select')) {
    const config = document.createElement('div');
    config.innerHTML = '<label>Entry point<select id="entry-select"></select></label><div class="form-grid"><label>Training steps<input name="steps" type="number" min="1" value="5000000" required></label><label class="check"><input name="start_immediately" type="checkbox" checked>Start when resources are available</label></div><div id="step-support" class="runtime-status"></div><div class="form-grid"><details class="tag-picker" open><summary>Algorithms</summary><div id="algorithm-select" class="selection-grid"></div></details><details class="tag-picker" open><summary>Environments</summary><div id="environment-select" class="selection-grid"></div></details></div>';
    form.elements.command.closest('label').before(config);
    $('#entry-select').addEventListener('change', () => configureEntry($('#entry-select').value));
    installSelectionTools('#algorithm-select');
    installSelectionTools('#environment-select');
    const comparison = document.createElement('div');
    comparison.id = 'output-comparison'; comparison.className = 'runtime-status';
    $('#command-preview').before(comparison);
  }
  form.elements.environment.closest('label').hidden = true;
  $('#python-select').innerHTML = (state.python_environments || [{python:state.python,name:'current'}]).map(e=>'<option value="'+escapeHtml(e.python)+'">'+escapeHtml(e.name)+'</option>').join('');
  $('#python-select').value = project().runtime?.python || state.python;
  form.elements.task.value = task;
  form.elements.seeds.value = defaultSeeds();
  form.elements.steps.value = '5000000';
  form.elements.start_immediately.checked = true;
  form.elements.repeats.value = '1';
  $('.form-error', form).textContent = '';
  const groups = [...new Set(catalog.entries.map(e=>e.project_name))];
  $('#entry-select').innerHTML = groups.map(group=>'<optgroup label="'+escapeHtml(group)+'">'+catalog.entries.filter(e=>e.project_name===group).map(e=>'<option value="'+escapeHtml(e.id)+'">'+escapeHtml(e.path)+'</option>').join('')+'</optgroup>').join('');
  const defaults = await api('defaults?project_id=' + encodeURIComponent(selected));
  const entry = catalog.entries.find(e=>e.id===entryId) || catalog.entries.find(e=>e.command.replaceAll('{python}',state.python)===defaults.command) || catalog.entries.find(e=>e.confidence==='high') || catalog.entries[0];
  $('#entry-select').value = entry?.id || '';
  configureEntry(entry?.id);
  $('#run-dialog').showModal();
  icons();
}
function configureEntry(id) {
  const form = $('#run-form');
  runEntry = catalog.entries.find(e=>e.id===id) || null;
  form.dataset.entryId = id || '';
  form.elements.command.value = (runEntry?.command || '').replaceAll('{python}',state.python);
  form.dataset.baseCommand = form.elements.command.value;
  form.elements.working_directory.value = project().path + '/' + (runEntry?.directory || '.');
  form.elements.working_directory.value = form.elements.working_directory.value.replace(/\/\.$/, '');
  form.elements.output.value = '';
  for (const [key, selector] of [['algorithms','#algorithm-select'],['environments','#environment-select']]) {
    const values = runEntry?.[key] || [];
    $(selector).innerHTML = values.map((value,i)=>'<label class="tag-option"><input type="checkbox" value="'+escapeHtml(value)+'" '+(i===0?'checked':'')+'><span>'+escapeHtml(value)+'</span></label>').join('');
    const search = $(selector).parentElement.querySelector('.selection-search');
    if (search) search.value = '';
  }
  $('#step-support').textContent = runEntry?.steps_flag ? 'Training steps: ' + runEntry.steps_flag : 'Training step argument unconfirmed. Enter a supported total-step flag in the command.';
  refreshEnvironmentStatus();
  preview();
}
function installSelectionTools(selector) {
  const grid = $(selector);
  const bar = document.createElement('div');
  bar.className = 'selection-toolbar';
  bar.innerHTML = '<input class="selection-search" type="search" placeholder="Filter" aria-label="Filter options"><button type="button" title="Select visible options" aria-label="Select visible options">'+icon('list-checks')+'</button><button type="button" title="Clear selection" aria-label="Clear selection">'+icon('x')+'</button>';
  grid.before(bar);
  bar.querySelector('input').addEventListener('input', event=>{
    $$('.tag-option',grid).forEach(label=>label.hidden=!label.textContent.toLowerCase().includes(event.target.value.toLowerCase()));
  });
  const buttons = bar.querySelectorAll('button');
  buttons[0].addEventListener('click',()=>{$$('.tag-option:not([hidden]) input',grid).forEach(i=>i.checked=true);preview();});
  buttons[1].addEventListener('click',()=>{$$('input',grid).forEach(i=>i.checked=false);preview();});
  let drag = null;
  grid.addEventListener('pointerdown',event=>{
    if (event.button!==0 || event.target.closest('label')) return;
    drag={x:event.clientX,y:event.clientY, original:new Set($$('input:checked',grid).map(i=>i.value))};
    grid.setPointerCapture(event.pointerId);
    const box=document.createElement('div');box.className='selection-box';grid.append(box);
  });
  grid.addEventListener('pointermove',event=>{
    if(!drag)return;
    const left=Math.min(drag.x,event.clientX),top=Math.min(drag.y,event.clientY);
    const right=Math.max(drag.x,event.clientX),bottom=Math.max(drag.y,event.clientY);
    const bounds=grid.getBoundingClientRect(),box=$('.selection-box',grid);
    box.style.cssText='left:'+(left-bounds.left+grid.scrollLeft)+'px;top:'+(top-bounds.top+grid.scrollTop)+'px;width:'+(right-left)+'px;height:'+(bottom-top)+'px';
    $$('.tag-option:not([hidden])',grid).forEach(label=>{
      const r=label.getBoundingClientRect(),input=$('input',label);
      input.checked=(event.ctrlKey && drag.original.has(input.value)) || (r.left<right && r.right>left && r.top<bottom && r.bottom>top);
    });
  });
  const finish=()=>{if(drag){drag=null;$('.selection-box',grid)?.remove();preview();}};
  grid.addEventListener('pointerup',finish);grid.addEventListener('pointercancel',finish);
}
function runPayload() {
  const form=$('#run-form'),fields=Object.fromEntries(new FormData(form));
  const seeds=fields.seeds.trim()?fields.seeds.split(',').map(s=>{if(!/^\d+$/.test(s.trim()))throw new Error('Seeds must be comma-separated integers');return Number(s.trim());}):[];
  return {project_id:selected,entry_id:form.dataset.entryId || undefined,task:fields.task,
    command:form.dataset.baseCommand || fields.command,steps:fields.task==='train'?Number(fields.steps):undefined,
    selections:{algorithms:$$('#algorithm-select input:checked').map(i=>i.value),environments:$$('#environment-select input:checked').map(i=>i.value),seeds},
    repeats:Number(fields.repeats),output:fields.output,environment:JSON.parse(fields.environment_vars||'{}'),
    working_directory:fields.working_directory,python:fields.python,auto_install:form.elements.auto_install.checked,
    start_immediately:form.elements.start_immediately.checked};
}
let environmentTimer;
async function refreshEnvironmentStatus() {
  const form = $('#run-form');
  const status = $('#environment-status');
  if (!form.elements.python.value || !form.elements.working_directory.value) return;
  status.className = 'runtime-status pending'; status.textContent = 'Checking selected Python and project dependencies...';
  clearTimeout(environmentTimer);
  try {
    const result = await api(`environment?project_id=${encodeURIComponent(selected)}&python=${encodeURIComponent(form.elements.python.value)}&working_directory=${encodeURIComponent(form.elements.working_directory.value)}`);
    if (result.errors?.length) { status.className='runtime-status failed'; status.textContent=`Environment incompatible: ${result.errors.join('; ')}`; }
    else if (result.missing_dependencies?.length || result.conda_pending?.length) { status.className='runtime-status pending'; status.textContent=`Ready to install: ${[...(result.missing_dependencies||[]), ...(result.conda_pending||[])].join(', ')}`; }
    else { status.className='runtime-status ready'; status.textContent=`Environment ready · Python ${result.python_version} · ${result.conda_env || 'system'}${result.native_check && result.native_check.returncode === 0 ? ' · MPI verified' : ''}`; }
  } catch (error) { status.className='runtime-status failed'; status.textContent=error.message; }
}
function preview() {
  clearTimeout(previewTimer);
  const version=++previewVersion;
  lastPlan=null;
  previewTimer=setTimeout(async()=>{
    try {
      const plan=await api('command',runPayload());
      if(version!==previewVersion)return;
      lastPlan=plan;
      if(document.activeElement!==$('#run-form').elements.command) $('#run-form').elements.command.value=plan.template;
      $('#command-preview').textContent=plan.executions.length+' executions\n'+plan.executions.slice(0,4).map(e=>e.display).join('\n')+(plan.executions.length>4?'\n...':'');
      const pairs=plan.completed_pairs || [];
      $('#output-comparison').className='runtime-status '+(pairs.length?'pending':'ready');
      $('#output-comparison').textContent=pairs.length ? 'Previously completed combinations: '+pairs.map(p=>p.algorithm+' / '+p.environment+' / seed '+p.seed+' / steps '+(p.steps ?? 'unknown')).join('; ') : 'No completed matching algorithm/environment pairs in Harness records.';
      $('.form-error',$('#run-form')).textContent='';
    } catch(error) {
      if(version!==previewVersion)return;
      $('#command-preview').textContent=error.message;
    }
  },180);
}
function renderDetail(id) {
  const record = state.executions.find(r => r.execution_id === id);
  if (!record) return;
  const details = [['Execution ID',record.execution_id],['Project',record.project],['Status',record.status],['Task',record.task],['Seed / repeat',`${record.seed ?? '-'} / ${record.repeat ?? '-'}`],['Git commit',record.git_commit || 'Unavailable'],['Start',record.start_time],['End',record.end_time || '-'],['Exit code',record.exit_code ?? '-'],['Environment',JSON.stringify(record.environment)],['Confidence',record.output_path_confidence]];
  const control = record.status==='running' ? `<button class="secondary" data-pause="${escapeHtml(record.execution_id)}">${icon('pause')}Pause</button>` : record.status==='paused' ? `<button class="secondary" data-resume="${escapeHtml(record.execution_id)}">${icon('play')}Resume</button>` : record.status==='held' ? `<button class="secondary" data-start="${escapeHtml(record.execution_id)}">${icon('play')}Start</button>` : '';
  $('#execution-detail').innerHTML = `<dl class="metadata-grid">${details.map(([key,value])=>`<dt>${escapeHtml(key)}</dt><dd class="mono">${escapeHtml(value)}</dd>`).join('')}</dl><pre class="detail-command">${escapeHtml(JSON.stringify(record.command,null,2))}</pre>${record.error ? `<p class="form-error">${escapeHtml(record.error)}</p>`:''}<div class="detail-output"><h2>Output location</h2><p class="mono">${escapeHtml(outputPath(record) || 'Uncertain')}</p><div class="actions"><button class="secondary" data-copy="${escapeHtml(outputPath(record))}" ${outputPath(record)?'':'disabled'}>${icon('copy')}Copy path</button><button class="secondary" data-open="${escapeHtml(record.execution_id)}" ${outputPath(record)?'':'disabled'}>${icon('folder-open')}Open folder</button></div></div>${(record.output_candidates || []).map(c=>`<div class="candidate"><span class="mono">${escapeHtml(c.path)}</span><span>${escapeHtml(c.source)}</span></div>`).join('')}<footer>${control}<button class="secondary" data-retry="${escapeHtml(record.execution_id)}">${icon('rotate-ccw')}Rerun</button><button class="secondary" data-download="${escapeHtml(record.execution_id)}">${icon('download')}Metadata</button>${!['queued','preparing','running','paused'].includes(record.status) ? `<button class="secondary" data-delete="${escapeHtml(record.execution_id)}">${icon('trash-2')}Delete record</button>` : ''}</footer>`;
  if (record.stderr || record.stdout) {
    const diagnostics = document.createElement('div');
    diagnostics.innerHTML = `${record.stderr ? `<h3>stderr</h3><pre class="diagnostic">${escapeHtml(record.stderr)}</pre>` : ''}${record.stdout ? `<h3>stdout</h3><pre class="diagnostic">${escapeHtml(record.stdout)}</pre>` : ''}`;
    $('#execution-detail').prepend(diagnostics);
  }
  icons();
}
async function analyze() {
  if (!project() || analyzing) return;
  analyzing = true;
  analysisError = '';
  const buttons = [$('#analyze'),$('#analyze-project')];
  buttons.forEach(b=>{b.disabled=true;});
  $('#analysis-status').textContent = 'Analyzing...';
  try { await api('analyze',{project_id:selected,llm:$('#use-llm').checked}); await refresh(); toast('Project analysis complete'); }
  catch(error) { toast(error.message,true); analysisError = `Analysis failed: ${error.message}`; }
  finally { analyzing = false; renderProject(); }
}
$$('[data-view]').forEach(button=>button.addEventListener('click',()=>switchView(button.dataset.view)));
$$('[data-filter]').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter; $$('[data-filter]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-selected',String(b===button));});renderRows();}));
$('#project-select').addEventListener('change',event=>{selected=event.target.value;localStorage.setItem('rl-project',selected);renderProject();renderRows();});
$('#search').addEventListener('input',renderRows);
$('#new-run').addEventListener('click',()=>openRun().catch(error=>toast(error.message,true)));
$('#resources-new-run').addEventListener('click',()=>openRun().catch(error=>toast(error.message,true)));
$('#empty-run').addEventListener('click',()=>openRun().catch(error=>toast(error.message,true)));
let browseParent = null;
let browseCurrent = '';
function ensureBrowseSelect() {
  if ($('#browse-select')) return;
  const button = document.createElement('button');
  button.type = 'button';
  button.id = 'browse-select';
  button.className = 'primary';
  button.textContent = 'Select this folder';
  $('.browser-toolbar').append(button);
  button.addEventListener('click', () => {
    if (browseCurrent) {
      $('#project-form').elements.path.value = browseCurrent;
      $('#project-form [type=submit]').focus();
    }
  });
}
async function browseDirectory(path) {
  const form = $('#project-form');
  ensureBrowseSelect();
  $('.form-error', form).textContent = '';
  $('#browse-list').textContent = 'Loading...';
  try {
    const result = await api('browse?path=' + encodeURIComponent(path || project()?.path || state.working_directory || ''));
    browseParent = result.parent;
    browseCurrent = result.path;
    form.elements.path.value = result.path;
    $('#browse-current').textContent = result.path;
    $('#browse-up').disabled = !browseParent;
    $('#browse-list').innerHTML = result.entries.length ? result.entries.map(entry => `<button type="button" class="browse-entry" data-directory="${escapeHtml(entry.path)}">${icon('folder')}<span>${escapeHtml(entry.name)}</span>${icon('chevron-right')}</button>`).join('') : '<p class="muted">No subdirectories</p>';
    icons();
  } catch (error) {
    $('#browse-list').innerHTML = '<p class="muted">Unable to read this directory.</p>';
    $('.form-error', form).textContent = 'Directory browser: ' + error.message;
  }
}
$('#add-project').addEventListener('click',()=>{$('#project-form').reset();$('.form-error',$('#project-form')).textContent='';$('#project-dialog').showModal();browseDirectory(project()?.path || state.working_directory);});
$('#browse-up').addEventListener('click',()=>browseParent && browseDirectory(browseParent));
$('#browse-list').addEventListener('click',event=>{const entry=event.target.closest('[data-directory]');if(entry)browseDirectory(entry.dataset.directory);});
$('#project-form [name=path]').addEventListener('change',event=>browseDirectory(event.target.value));
$('#refresh').addEventListener('click',()=>refresh().catch(e=>toast(e.message,true)));
$('#analyze').addEventListener('click',analyze);
$('#analyze-project').addEventListener('click',()=>{switchView('project');analyze();});
$('#configure-llm').addEventListener('click',()=>openLLMConfig().catch(error=>toast(error.message,true)));
$('#use-llm').addEventListener('change',event=>{llmChoice=event.target.checked;});
$('#llm-dialog').addEventListener('close',()=>{$('#llm-form').elements.api_key.value='';});
$('#llm-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const form=event.target; const submit=$('[type=submit]',form); submit.disabled=true; $('#test-llm').disabled=true;
  try {
    const result=await api('llm/config',Object.fromEntries(new FormData(form)));
    $('#llm-dialog').close(); llmChoice=true; await refresh(); toast(`AI configured: ${result.model}`);
  } catch(error) { $('.form-error',form).textContent=error.message; }
  finally { submit.disabled=false; $('#test-llm').disabled=false; }
});
$('#test-llm').addEventListener('click',async()=>{
  const form=$('#llm-form'); if(!form.reportValidity()) return;
  const button=$('#test-llm'); button.disabled=true; $('[type=submit]',form).disabled=true;
  $('.form-error',form).textContent=''; $('#llm-config-status').textContent='Connecting...';
  try { const result=await api('llm/test',Object.fromEntries(new FormData(form))); $('#llm-config-status').textContent=`Connection successful: ${result.model}`; }
  catch(error) { $('#llm-config-status').textContent='Connection failed'; $('.form-error',form).textContent=error.message; }
  finally { button.disabled=false; $('[type=submit]',form).disabled=false; }
});
$('#task-select').addEventListener('change',event=>fillPreset(event.target.value));
$('#run-form').addEventListener('input',preview);
$('#run-form').addEventListener('change',preview);
document.addEventListener('change',event=>{if(event.target.classList.contains('record-select')){if(event.target.checked)selectedRecords.add(event.target.value);else selectedRecords.delete(event.target.value);}});
function ensureBulkDelete() {
  const toolbar=$('.table-toolbar');
  if (!toolbar || $('#bulk-delete')) return;
  const button=document.createElement('button'); button.id='bulk-delete'; button.className='secondary'; button.innerHTML=`${icon('trash-2')}Delete selected`;
  button.addEventListener('click',async()=>{const ids=[...selectedRecords]; if(!ids.length)return toast('Select completed records first',true); try {const result=await api('delete',{execution_ids:ids}); result.deleted.forEach(id=>selectedRecords.delete(id)); await refresh(); toast(`${result.deleted.length} record(s) deleted`);}catch(e){toast(e.message,true);}});
  toolbar.append(button); icons();
}
$('#run-form').addEventListener('input', event=>{if(event.target.name==='command') event.currentTarget.dataset.baseCommand=event.target.value; preview();});
$('#python-select').addEventListener('change',event=>{const form=$('#run-form');const old=state.python;form.elements.command.value=form.elements.command.value.replaceAll(old,event.target.value);preview();});
$('#python-select').addEventListener('change',refreshEnvironmentStatus);
$('#run-form [name=working_directory]').addEventListener('change',refreshEnvironmentStatus);
$$('.close').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
$('#project-form').addEventListener('submit',async event=>{
  event.preventDefault(); const form=event.target; const submit=$('[type=submit]',form); submit.disabled=true;
  try { const p=await api('projects',Object.fromEntries(new FormData(form))); selected=p.id; localStorage.setItem('rl-project',selected); $('#project-dialog').close(); await refresh(); await analyze(); }
  catch(error){$('.form-error',form).textContent=error.message;} finally{submit.disabled=false;}
});
$('#run-form').addEventListener('submit',async event=>{
  event.preventDefault(); const form=event.target; const submit=$('[type=submit]',form); submit.disabled=true;
  try {
    const payload=runPayload();
    const plan=await api('command',payload);
    if (plan.completed_pairs?.length && !confirm('These algorithm/environment pairs have completed runs. Create '+plan.executions.length+' executions again?')) return;
    const result=await api('launch',payload);
    $('#run-dialog').close();switchView('executions');filter='all';$$('[data-filter]').forEach(b=>{b.classList.toggle('selected',b.dataset.filter==='all');b.setAttribute('aria-selected',String(b.dataset.filter==='all'));});await refresh();toast(`${result.execution_ids.length} execution(s) queued`);
  }catch(error){$('.form-error',form).textContent=error.message;}finally{submit.disabled=false;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  try {
    if(button.dataset.detail){selectedExecution=button.dataset.detail;renderDetail(selectedExecution);$('#detail-dialog').showModal();}
    if(button.dataset.cancel){await api('cancel',{execution_id:button.dataset.cancel});toast('Cancellation requested');await refresh();}
    if(button.dataset.open){await api('open-output',{execution_id:button.dataset.open});toast('Output folder opened');}
    if(button.dataset.copy){await navigator.clipboard.writeText(button.dataset.copy);toast('Path copied');}
    if(button.dataset.delete){if(!confirm('Delete this execution record?')) return; const id=button.dataset.delete; await api('delete',{execution_id:id}); selectedRecords.delete(id); if(selectedExecution===id){$('#detail-dialog').close();selectedExecution=null;} await refresh();toast('Record deleted');}
    if(button.dataset.start){await api('start',{execution_id:button.dataset.start}); await refresh(); toast('Execution started');}
    if(button.dataset.pause){await api('pause',{execution_id:button.dataset.pause}); await refresh(); toast('Execution paused');}
    if(button.dataset.resume){await api('resume',{execution_id:button.dataset.resume}); await refresh(); toast('Execution resumed');}
    if(button.dataset.preset)await openRun(button.dataset.preset);
    if(button.dataset.script) await openRun('train',button.dataset.script);
    if(button.dataset.retry){
      const r=state.executions.find(r=>r.execution_id===button.dataset.retry);
      $('#detail-dialog').close(); await openRun(r.task);
      const form=$('#run-form');
      form.elements.command.value=r.command.map(a=>"'"+a.replaceAll("'","'\\''")+"'").join(' ');
      form.dataset.baseCommand=form.elements.command.value;
      form.dataset.entryId='';
      form.elements.seeds.value=r.seed == null?'':String(r.seed);
      form.elements.working_directory.value=r.working_directory || r.project;
      form.elements.output.value=r.detected_output_path||'';
      form.elements.steps.value=String(r.training_steps || 5000000);
      $$('#algorithm-select input,#environment-select input').forEach(i=>i.checked=false);
      preview();
    }
    if(button.dataset.download){const r=state.executions.find(r=>r.execution_id===button.dataset.download);const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`execution-${r.execution_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  }catch(error){toast(error.message,true);}
});
refresh().catch(error=>toast(error.message,true));
ensureBulkDelete();
setInterval(()=>refresh().catch(()=>{}),3000);
