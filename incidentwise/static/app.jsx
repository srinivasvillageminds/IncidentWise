/* IncidentWise shared application logic.
   Loaded by BOTH skins (index.html = classic, modern.html = modern) via
   <script type="text/babel" src="app.jsx"> - one brain, two faces.
   Components target CSS class names only; skins own all styling. */

const {useState, useEffect, useRef, useCallback} = React;

/* ================= utilities ================= */
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

function md(text){
  let out = [], lines = esc(text).split('\n'), list = null;
  const close = () => { if(list){ out.push(list==='ul'?'</ul>':'</ol>'); list=null; } };
  const nextListType = (i) => {
    for(let j=i+1;j<lines.length;j++){
      if(!lines[j].trim()) continue;
      if(/^\s*[-*]\s+/.test(lines[j])) return 'ul';
      if(/^\s*\d+[.)]\s+/.test(lines[j])) return 'ol';
      return null;
    }
    return null;
  };
  for(let i=0;i<lines.length;i++){
    const line = lines[i];
    const h = line.match(/^(#{2,4})\s+(.*)/);
    const ul = line.match(/^\s*[-*]\s+(.*)/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)/);
    if(h){ close(); const l = h[1].length>3?4:3; out.push(`<h${l}>${h[2]}</h${l}>`); }
    else if(ul){ if(list!=='ul'){close(); out.push('<ul>'); list='ul';} out.push(`<li>${ul[1]}</li>`); }
    else if(ol){ if(list!=='ol'){close(); out.push('<ol>'); list='ol';} out.push(`<li>${ol[1]}</li>`); }
    else if(!line.trim()){ if(!(list && nextListType(i)===list)) close(); }
    else { close(); out.push(`<p>${line}</p>`); }
  }
  close();
  return out.join('')
    .replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g,'$1<em>$2</em>')
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/\[(W?\d{1,2})\]/g,'<sup class="cite" data-n="$1">[$1]</sup>');
}

async function streamSSE(url, payload, on){
  const resp = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                                 body: JSON.stringify(payload)});
  if(!resp.ok) throw new Error('HTTP '+resp.status);
  const reader = resp.body.getReader(), dec = new TextDecoder();
  let buf = '';
  while(true){
    const {done, value} = await reader.read();
    if(done) break;
    buf += dec.decode(value, {stream:true});
    let i;
    while((i = buf.indexOf('\n\n')) >= 0){
      const block = buf.slice(0,i); buf = buf.slice(i+2);
      let ev = 'message', data = '';
      for(const l of block.split('\n')){
        if(l.startsWith('event: ')) ev = l.slice(7).trim();
        else if(l.startsWith('data: ')) data += l.slice(6);
      }
      if(!data) continue;
      const d = JSON.parse(data);
      if(ev === 'error') throw new Error(d.message);
      if(on[ev]) on[ev](d);
    }
  }
}

async function jpost(url, payload){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(payload)});
  if(!r.ok) throw new Error('HTTP '+r.status);
  return r.json();
}

const useStored = (key, init) => {
  const [v, setV] = useState(() => {
    try{ const s = localStorage.getItem(key); return s !== null ? JSON.parse(s) : init; }
    catch{ return init; }
  });
  useEffect(() => { try{ localStorage.setItem(key, JSON.stringify(v)); }catch{} }, [key, v]);
  return [v, setV];
};

function cleanForSpeech(t){
  return t.replace(/\[(W?\d{1,2})\]/g,'').replace(/[#*_`|>-]/g,' ').replace(/\s+/g,' ').trim();
}
function parseModelSel(sel){
  if(!sel) return {backend:null, model:null};
  const ix = sel.indexOf(':');
  return {backend: sel.slice(0,ix), model: sel.slice(ix+1)};
}

function useDictation(lang, onText){
  const [listening, setListening] = useState(false);
  const recRef = useRef(null);
  const supported = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  const toggle = useCallback((baseText) => {
    if(listening){ try{ recRef.current && recRef.current.stop(); }catch{} return; }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if(!SR) return;
    const rec = new SR();
    rec.lang = lang; rec.interimResults = true; rec.continuous = false;
    rec.onresult = e => {
      let text = '';
      for(const r of e.results) text += r[0].transcript;
      onText((baseText ? baseText + ' ' : '') + text);
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recRef.current = rec;
    setListening(true);
    rec.start();
  }, [listening, lang, onText]);
  return {listening, toggle, supported};
}

/* ================= icons (inline SVG, skin-agnostic) ================= */
const Ic = ({d, size=16}) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    {Array.isArray(d) ? d.map((p,i)=><path key={i} d={p}/>) : <path d={d}/>}
  </svg>);
const IcSend  = () => <Ic d={["M22 2 11 13","M22 2 15 22 11 13 2 9z"]}/>;
const IcMic   = () => <Ic d={["M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z","M19 10v2a7 7 0 0 1-14 0v-2","M12 19v4"]}/>;
const IcStop  = () => <Ic d="M6 6h12v12H6z"/>;
const IcGear  = () => <Ic d={["M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z","M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"]}/>;
const IcCopy  = () => <Ic d={["M20 9h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2z","M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"]} size={14}/>;
const IcCheck = () => <Ic d="M20 6 9 17l-5-5" size={14}/>;
const IcPin   = () => <Ic d={["M12 17v5","M9 4.5h6l-1 5 3 3v2H7v-2l3-3-1-5z"]} size={15}/>;
const IcClose = () => <Ic d={["M18 6 6 18","M6 6l12 12"]} size={15}/>;
const IcPlus  = () => <Ic d={["M12 5v14","M5 12h14"]} size={14}/>;
const IcTrash = () => <Ic d={["M3 6h18","M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2","M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"]} size={13}/>;
const IcHist  = () => <Ic d={["M3 3v5h5","M3.05 13A9 9 0 1 0 6 5.3L3 8","M12 7v5l4 2"]} size={17}/>;

/* group history the way people remember it, not by raw timestamp */
function groupLabel(iso){
  if(!iso) return 'Earlier';
  const then = new Date(iso.replace(' ', 'T'));
  const days = Math.floor((Date.now() - then.getTime()) / 86400000);
  if(days <= 0) return 'Today';
  if(days === 1) return 'Yesterday';
  if(days < 7) return 'This week';
  if(days < 30) return 'This month';
  return 'Earlier';
}

/* ================= shared components ================= */
function Toggle({on, set}){
  return <div className={'toggle'+(on?' on':'')} onClick={()=>set(!on)}><i/></div>;
}

function answerClipText(m){
  let t = (m.content || '').trim();
  const src = m.sources || [], web = m.web || [];
  if(src.length || web.length){
    t += '\n\nSources:';
    src.forEach(s => { t += `\n[${s.n}] ${s.title || s.file} — ${s.category}`
      + (s.doc_type && s.doc_type !== 'other' ? ` (${s.doc_type.replace('_',' ')})` : '')
      + `, p.${s.page}/${s.pages_total}` + (s.doc_url ? ` — ${s.doc_url}` : ''); });
    web.forEach((w,i) => { t += `\n[W${i+1}] ${w.title} — ${w.url} (unverified web)`; });
  }
  return t;
}

function CopyBtn({getText, title}){
  const [ok, setOk] = useState(false);
  return <button className="copybtn" title={title || 'Copy'}
    onClick={async () => {
      try{ await navigator.clipboard.writeText(getText()); setOk(true);
           setTimeout(() => setOk(false), 1200); }catch{}
    }}>{ok ? <IcCheck/> : <IcCopy/>}</button>;
}

function SourceCards({items, web, pipeline, weak, openMap, setOpenMap, flash, refsMap}){
  if((!items || !items.length) && (!web || !web.length) && !weak) return null;
  const card = (key, num, title, meta, detail, badge) => (
    <div key={key} data-n={num}
         ref={el => { if(refsMap) refsMap.current[num] = el; }}
         className={'src'+(flash===String(num)?' flash':'')}
         onClick={e => { if(e.target.tagName!=='A') setOpenMap(o=>({...o,[num]:!o[num]})); }}>
      <div className="t"><span className="num">{num}</span>{title}{badge}</div>
      <div className="m">{meta}</div>
      {openMap[num] && <div className="detail">{detail}</div>}
    </div>
  );
  return (
    <div>
      {weak && <div className="warnline">Retrieval confidence is low — the indexed corpus may not
        cover this. Treat the answer with extra caution.</div>}
      {((items&&items.length)||(web&&web.length)) ? (
      <div className="sources">
        <div className="slabel">Sources{pipeline ? <span className="pipe"> · {pipeline}</span> : null}</div>
        <div className="srcgrid">
          {(items||[]).map(s => card(s.n, s.n, s.title || s.file,
            `${s.category}${s.doc_type && s.doc_type!=='other' ? ' · '+s.doc_type.replace('_',' ') : ''} · p.${s.page}/${s.pages_total}`,
            <React.Fragment>
              “{s.snippet}…”<br/>
              {s.doc_url ? <a href={s.doc_url} target="_blank" rel="noopener">original PDF ↗</a> : null}
              {s.source_url ? <React.Fragment> · <a href={s.source_url} target="_blank" rel="noopener">source page ↗</a></React.Fragment> : null}
            </React.Fragment>, null))}
          {(web||[]).map((w,i) => card('w'+i, 'W'+(i+1), w.title,
            w.official ? 'official (gov.in) · unverified' : 'web · unverified',
            <React.Fragment>{w.snippet}<br/><a href={w.url} target="_blank" rel="noopener">{w.url}</a></React.Fragment>,
            w.official ? <span className="badge">GOV</span> : null))}
        </div>
      </div>) : null}
    </div>
  );
}

function BotBody({m}){
  const [openMap, setOpenMap] = useState({});
  const [flash, setFlash] = useState(null);
  const refsMap = useRef({});
  const onClick = e => {
    const sup = e.target.closest && e.target.closest('sup.cite');
    if(!sup) return;
    const n = sup.dataset.n;
    setOpenMap(o => ({...o, [n]: true}));
    setFlash(n);
    setTimeout(() => setFlash(null), 1200);
    const el = refsMap.current[n];
    if(el) el.scrollIntoView({behavior:'smooth', block:'center'});
  };
  return (
    <div className="mbody">
      {m.error
        ? <div className="errline">{m.error}</div>
        : <div onClick={onClick}
               dangerouslySetInnerHTML={{__html: md(m.content) + (m.streaming ? '<span class="cursor"></span>' : '')}}/>}
      {!m.streaming && !m.error &&
        <SourceCards items={m.sources} web={m.web} pipeline={m.pipeline} weak={m.weak}
                     openMap={openMap} setOpenMap={setOpenMap} flash={flash} refsMap={refsMap}/>}
    </div>
  );
}

function Composer({busy, onSend, lang}){
  const [text, setText] = useState('');
  const taRef = useRef(null);
  const {listening, toggle, supported} = useDictation(lang, setText);
  useEffect(() => {
    const ta = taRef.current; if(!ta) return;
    ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
  }, [text]);
  const submit = () => {
    const q = text.trim();
    if(!q || busy) return;
    setText('');
    onSend(q);
  };
  return (
    <div className="comp-wrap">
      <div className="comp">
        <textarea ref={taRef} rows="1" value={text}
          placeholder="Ask about an incident, standard, audit requirement…"
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); submit(); } }}/>
        {supported &&
          <button className={'iconbtn'+(listening?' live':'')} title="Voice input"
                  onClick={() => toggle(text)}>{listening ? <IcStop/> : <IcMic/>}</button>}
        <button className="send" disabled={busy} onClick={submit} title="Send"><IcSend/></button>
      </div>
      <div className="disclaimer">Informational only — verify safety-critical decisions against the
        original standard and your site's competent authority.</div>
    </div>
  );
}

/* ================= Ask view ================= */
function ChatView({settings, messages, setMessages, onCompleted, sendRef}){
  const [busy, setBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const mainRef = useRef(null);
  useEffect(() => { const m = mainRef.current; if(m) m.scrollTop = 1e9; }, [messages]);

  const speak = (text) => {
    if(!window.speechSynthesis) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(cleanForSpeech(text).slice(0, 2500));
    u.rate = settings.rate; u.lang = settings.lang;
    u.onend = () => setSpeaking(false);
    setSpeaking(true);
    speechSynthesis.speak(u);
  };
  const stopSpeak = () => { try{ speechSynthesis.cancel(); }catch{} setSpeaking(false); };

  const send = useCallback(async (q) => {
    stopSpeak();
    setBusy(true);
    let turns = [];
    setMessages(ms => {
      turns = ms.filter(m => !m.error && !m.streaming)
                .map(m => ({role: m.role==='user' ? 'user' : 'assistant', content: m.content}))
                .slice(-6);
      return [...ms, {role:'user', content:q},
              {role:'bot', content:'', streaming:true, sources:[], web:[]}];
    });
    const patch = p => setMessages(ms => {
      const copy = ms.slice();
      copy[copy.length-1] = {...copy[copy.length-1], ...p};
      return copy;
    });

    // --- typewriter smoothing -------------------------------------------
    // Fast API models return the answer in a few large bursts, so painting on
    // every token makes it POP into existence. We buffer arriving text and
    // paint it at a steady rate. Adaptive: the bigger the backlog, the bigger
    // the step, so we never fall far behind a genuinely fast model.
    let painted = '';        // what the user can see
    let pending = '';        // received, not yet painted
    let streamDone = false;
    let raf = 0;

    const finish = () => {
      patch({streaming:false});
      if(settings.voiceOut && painted) speak(painted);
      setTimeout(() => { onCompleted && onCompleted(); }, 80);
    };
    const paint = () => {
      if(pending.length){
        const step = Math.max(3, Math.ceil(pending.length / 8));  // ~180+ chars/s
        painted += pending.slice(0, step);
        pending = pending.slice(step);
        patch({content: painted});
      }
      if(streamDone && !pending.length){ raf = 0; finish(); return; }
      raf = requestAnimationFrame(paint);
    };
    raf = requestAnimationFrame(paint);
    // --------------------------------------------------------------------

    try{
      const {backend, model} = parseModelSel(settings.answerModel);
      // Stamp the answer with the model that actually produced it, so a
      // conversation that spans a model change stays honest about which
      // answer came from which model.
      patch({answeredBy: model || (settings.defaultModelLabel || 'server default'),
             answeredMode: settings.mode});
      await streamSSE('/api/chat', {
        message: q, history: turns,
        category: settings.category || null, mode: settings.mode,
        vertical: settings.vertical, k: settings.k, backend, model,
        guard: settings.guard,
      }, {
        sources: d => patch({sources:d.items, web:d.web||[], weak:d.weak,
                             pipeline:d.pipeline, vertical:d.vertical}),
        token: d => { pending += d.t; },     // queue it; the painter draws it
        done: () => { streamDone = true; },  // painter finishes, then calls finish()
      });
    }catch(err){
      if(raf) cancelAnimationFrame(raf);
      patch({streaming:false, error:String(err.message || err)});
    }finally{
      setBusy(false);
    }
  }, [settings, setMessages, onCompleted]);

  useEffect(() => { if(sendRef) sendRef.current = send; }, [send, sendRef]);

  const chips = [
    'What are the most common root causes across the OISD case studies?',
    'Summarize the key lessons from PNGRB incident investigation reports.',
    'What were the incidents in 2018?',
    'How often are safety audits required for petroleum installations?',
  ];

  return (
    <React.Fragment>
      <div className="main" ref={mainRef}>
        <div className="col">
          {messages.length === 0 &&
            <div className="welcome">
              <h2>What do you want to know?</h2>
              <p>Grounded in OISD, PNGRB &amp; allied corpora — every claim cited to its source.
                 Analytics questions are answered by counting, not generating.</p>
              <div className="chips">
                {chips.map((c,i) => <button key={i} className="chip" onClick={()=>send(c)}>{c}</button>)}
              </div>
            </div>}
          {messages.map((m,i) =>
            <div key={i} className={'msg '+(m.role==='user'?'user':'bot')}>
              <div className="who">{m.role==='user'
                ? <React.Fragment>You <CopyBtn getText={()=>m.content} title="Copy question"/></React.Fragment>
                : <React.Fragment>IncidentWise
                    {m.answeredBy ? <span className="mtag" title="Model that produced this answer">
                      {m.answeredBy}</span> : null}
                    {m.vertical && !m.streaming ? <span className="vtag">{m.vertical}</span> : null}
                    {!m.streaming && !m.error ?
                      <CopyBtn getText={()=>answerClipText(m)} title="Copy answer with sources"/> : null}
                  </React.Fragment>}</div>
              {m.role==='user' ? <div className="mbody">{m.content}</div> : <BotBody m={m}/>}
            </div>)}
        </div>
      </div>
      {speaking && <button className="speak-pill" onClick={stopSpeak}>◼ Stop reading</button>}
      <Composer busy={busy} onSend={send} lang={settings.lang}/>
    </React.Fragment>
  );
}

/* ================= Drill view ================= */
function DrillView({settings, drill, setDrill, onCompleted}){
  const [refineText, setRefineText] = useState('');
  const mainRef = useRef(null);
  useEffect(() => { const m = mainRef.current; if(m) m.scrollTop = 1e9; }, [drill.out, drill.stage]);

  const units = ['Refinery unit','Gas processing plant','CGD / CNG station','Cross-country pipeline',
                 'Storage terminal / tank farm','Pharma API plant','Fertilizer plant','Generic process plant'];
  const quick = ['hot work during turnaround','confined space entry','LPG leak at pump seal',
                 'SIMOPS: welding near live exchanger','steam trap / VR line spillage'];
  const {backend, model} = parseModelSel(settings.answerModel);

  const set = p => setDrill(s => ({...s, ...p}));

  const generate = async (t) => {
    const th = (t ?? drill.theme).trim();
    if(!th || drill.busy) return;
    set({theme: th, busy:true, out:null, error:null, stage:'starting…'});
    try{
      await streamSSE('/api/drill/create',
        {theme: th, unit_type: drill.unit, difficulty: drill.level,
         category: settings.category || null, refine: true, save: true,
         backend, model}, {
        stage: d => set({stage: d.stage + (d.scores ?
          ' · scores: ' + Object.entries(d.scores).map(([k,v])=>`${k.split('_')[0]} ${v}`).join(', ') : '')}),
        result: d => set({out: d, stage: null}),
        done: () => { set({busy:false});
          setTimeout(() => { onCompleted && onCompleted(); }, 80); },
      });
    }catch(err){
      set({busy:false, stage:null, error:String(err.message || err)});
    }
  };

  const refine = async () => {
    const ins = refineText.trim();
    if(!ins || !drill.out || drill.busy) return;
    set({busy:true, stage:'refining per your instruction…'});
    try{
      const r = await jpost('/api/drill/refine',
        {spec: drill.out.spec, instruction: ins, backend, model});
      if(r.error) throw new Error(r.error);
      set({out: {...drill.out, spec: r.spec, markdown: r.markdown,
                 revisions: (drill.out.revisions||0)+1},
           busy:false, stage:null});
      setRefineText('');
      setTimeout(() => { onCompleted && onCompleted(); }, 80);
    }catch(err){
      set({busy:false, stage:null, error:String(err.message || err)});
    }
  };

  const setStatus = async (status) => {
    if(!drill.out || !drill.out.id) return;
    const notes = window.prompt(`Reviewer notes for "${status}" (optional):`) || '';
    const r = await jpost(`/api/drills/${drill.out.id}/status`, {status, notes});
    if(r.ok) set({out: {...drill.out, status}});
  };

  return (
    <div className="main" ref={mainRef}>
      <div className="col">
        <div className="card">
          <div className="drill-head"/>
          <h2 className="cardtitle">Incident Drill Generator</h2>
          <p className="cardsub">
            Structured, corpus-grounded hypothetical scenarios with self-critique and an expert
            validation workflow. Follow-up instructions revise the SAME scenario — context is kept.</p>
          <div className="frow">
            <div className="fitem" style={{flex:2}}>
              <label>Theme / hazard</label>
              <input type="text" value={drill.theme} placeholder="e.g. hot work near hydrocarbon line"
                     onChange={e=>set({theme: e.target.value})}
                     onKeyDown={e=>{ if(e.key==='Enter') generate(); }}/>
            </div>
            <div className="fitem">
              <label>Unit type</label>
              <select value={drill.unit} onChange={e=>set({unit: e.target.value})}>
                {units.map(u => <option key={u}>{u}</option>)}
              </select>
            </div>
          </div>
          <div className="frow">
            <div className="fitem">
              <label>Audience level</label>
              <div className="seg">
                {['basic','intermediate','advanced'].map(l =>
                  <button key={l} className={drill.level===l?'on':''} onClick={()=>set({level:l})}>{l}</button>)}
              </div>
            </div>
            <div className="fitem" style={{display:'flex', alignItems:'flex-end'}}>
              <button className="primary" style={{width:'100%'}} disabled={drill.busy || !drill.theme.trim()}
                      onClick={()=>generate()}>{drill.busy ? 'Working…' : 'Generate scenario'}</button>
            </div>
          </div>
          <div className="chips" style={{justifyContent:'flex-start'}}>
            {quick.map(q => <button key={q} className="chip" onClick={()=>generate(q)}>{q}</button>)}
          </div>
          {drill.stage && <div className="stageline">⏳ {drill.stage}</div>}
          {drill.error && <div className="errline" style={{marginTop:10}}>{drill.error}</div>}
        </div>

        {drill.out &&
          <div className="card">
            <div className="warnline" style={{marginTop:0, marginBottom:12}}>
              {drill.out.status === 'validated'
                ? '✔ VALIDATED by expert — usable in meetings.'
                : 'DRAFT — requires validation by a competent safety professional before use.'}
              {drill.out.revisions ? ` · ${drill.out.revisions} revision(s)` : ''}
            </div>
            <BotBody m={{content: drill.out.markdown, streaming:false,
                         sources: drill.out.sources || [], web: [],
                         pipeline: 'drill engine v2', weak:false}}/>
            <div style={{marginTop:14, display:'flex', gap:8, flexWrap:'wrap'}}>
              <CopyBtn getText={()=>drill.out.markdown} title="Copy markdown"/>
              <button className="ghost" onClick={()=>window.print()}>Print</button>
              {drill.out.id && drill.out.status !== 'validated' &&
                <button className="ghost" onClick={()=>setStatus('validated')}>Mark validated</button>}
              {drill.out.id &&
                <button className="ghost" onClick={()=>setStatus('rejected')}>Reject</button>}
            </div>
            <div className="comp" style={{marginTop:14}}>
              <textarea rows="1" value={refineText} style={{padding:'10px 0'}}
                placeholder="Refine this scenario… e.g. 'set it during monsoon night shift'"
                onChange={e=>setRefineText(e.target.value)}
                onKeyDown={e=>{ if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); refine(); } }}/>
              <button className="send" disabled={drill.busy} onClick={refine} title="Refine">↻</button>
            </div>
          </div>}

        <HandoverCard settings={settings}/>
      </div>
    </div>
  );
}

function HandoverCard({settings}){
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const {backend, model} = parseModelSel(settings.answerModel);
  const run = async () => {
    setBusy(true); setErr(null);
    try{
      const r = await jpost('/api/handover', {backend, model});
      if(r.error) throw new Error(r.error);
      setRes(r);
    }catch(e){ setErr(String(e.message || e)); }
    finally{ setBusy(false); }
  };
  return (
    <div className="card">
      <h3 className="cardtitle">Pre-handover check</h3>
      <p className="cardsub">
        One incident-informed brief across ALL open permits, grouped by plant proximity,
        split by job class. Seed demo permits first: <code>python seed_permits.py</code></p>
      <button className="primary" disabled={busy} onClick={run}>
        {busy ? 'Analyzing open permits…' : 'Generate handover brief'}</button>
      {err && <div className="errline" style={{marginTop:10}}>{err}</div>}
      {res && <div style={{marginTop:14}}>
        <div className="stageline">{res.jobs} open permit(s) analyzed</div>
        {(res.review.areas||[]).map((a,i) =>
          <div key={i} style={{marginBottom:14}}>
            <h4 style={{margin:'8px 0 4px'}}>{(a.tags||[]).join(', ')}
              <span className="vtag">{(a.open_permits||[]).join(', ')}</span></h4>
            {Object.entries(a.checks||{}).map(([cls, list]) => (list && list.length ?
              <div key={cls} style={{fontSize:13.5, margin:'6px 0'}}>
                <b style={{textTransform:'capitalize'}}>{cls.replace('_',' ')}:</b>
                <ul style={{margin:'4px 0'}}>{list.map((c,j)=><li key={j}>{c}</li>)}</ul>
              </div> : null))}
            {(a.interaction_watchouts||[]).length ?
              <div className="warnline">⚠ {(a.interaction_watchouts||[]).join(' · ')}</div> : null}
          </div>)}
        {(res.review.general||[]).length ?
          <div style={{fontSize:13.5}}><b>Plant-wide:</b>
            <ul style={{margin:'4px 0'}}>{res.review.general.map((g,i)=><li key={i}>{g}</li>)}</ul>
          </div> : null}
        <div className="disclaimer" style={{textAlign:'left', margin:'8px 0 0'}}>{res.disclaimer}</div>
      </div>}
    </div>
  );
}

/* ================= Analytics view ================= */
function Bars({data}){
  const entries = Object.entries(data || {});
  const max = Math.max(1, ...entries.map(([,v]) => v));
  return <div>{entries.map(([k,v]) =>
    <div className="barrow" key={k}>
      <div className="bl">{k==='0' ? 'undated' : k}</div>
      <div className="bt"><div className="bf" style={{width: Math.max(4,(v/max)*100)+'%'}}/></div>
      <div className="bc">{v}</div>
    </div>)}</div>;
}

function AnalyticsView({onAsk}){
  const [data, setData] = useState(null);
  const load = () => fetch('/api/analytics').then(r=>r.json()).then(setData).catch(()=>setData({available:false}));
  useEffect(load, []);
  const chips = ['What were the incidents in 2018?', 'How many pipeline fire incidents are recorded?',
                 'Incidents by year', 'Total fatalities across recorded incidents'];
  return (
    <div className="main">
      <div className="col">
        <div className="card">
          <h2 className="cardtitle">Incident Analytics</h2>
          <p className="cardsub">
            Counted deterministically from the expert-editable incident database — no language
            model produces these numbers. Build/refresh with <code>python facts.py</code>.</p>
          {!data ? <div className="stageline">loading…</div>
          : data.available === false ? <div className="warnline">No incident database yet —
              run <code>python facts.py</code> after ingest, then <button className="ghost" onClick={load}>refresh</button></div>
          : <React.Fragment>
              <div className="statgrid">
                <div className="stat"><b>{data.records}</b><small>incident records</small></div>
                <div className="stat"><b>{data.total_fatalities}</b><small>fatalities (where stated)</small></div>
                <div className="stat"><b>{data.total_injuries}</b><small>injuries (where stated)</small></div>
              </div>
              <h4 style={{margin:'12px 0 6px'}}>By incident year
                <span className="vtag">{data.n_incident_dated} records · date stated in the report</span></h4>
              <Bars data={data.by_year_incident_dated}/>
              <h4 style={{margin:'18px 0 6px'}}>By publication year
                <span className="vtag">{data.n_publication_dated} records · incident date NOT stated</span></h4>
              <Bars data={data.by_year_publication_dated}/>
              <div className="warnline" style={{marginTop:8}}>
                Publication year ≠ incident year. These {data.n_publication_dated} case studies
                don't state when the incident occurred, so they're grouped by the year the
                document was published — never counted as incidents of that year.
                {data.n_undated ? ` ${data.n_undated} record(s) have no date at all.` : ''}
              </div>
              <h4 style={{margin:'18px 0 6px'}}>By type</h4>
              <Bars data={data.by_type}/>
              <div style={{marginTop:14, display:'flex', gap:8, flexWrap:'wrap'}}>
                <button className="ghost" onClick={load}>Refresh</button>
                {chips.map(c => <button key={c} className="chip" onClick={()=>onAsk(c)}>{c}</button>)}
              </div>
            </React.Fragment>}
        </div>
      </div>
    </div>
  );
}

/* ================= Bench / test playground ================= */
const SUITES = {
  ask:   {n: 24, title: 'Ask suite',
          sub: '24 questions of deliberately mixed corpus-relevance: strong-corpus, regulatory (corpus→web fallback with [W#] citations), analytics (counted), out-of-scope, INJECTION, and the hard MIXED case ("tell me about the VDU accident… also good restaurants nearby") — where the assistant must answer the safety half and decline the tail.'},
  guard: {n: 10, title: 'Guardrail suite',
          sub: 'The tricky inputs only (off-topic, mixed, injection) re-run at each guard level, so you can see exactly what each rung buys — and what it costs in latency.'},
  drill: {n: 4,  title: 'Drill suite',
          sub: '4 permit-class scenarios (cold work, hot work, confined space, SIMOPS) per model. The judge hunts specifically for absurd causality ("gas test skipped due to bad weather"), setting/narrative contradictions, role misuse, physical impossibility, and fabricated lab values or clause numbers.'},
};

function BenchView({modelsInfo}){
  const [suite, setSuite] = useState('ask');
  const [sel, setSel] = useState({});
  const [judge, setJudge] = useState('');
  const [guard, setGuard] = useState('l1');
  const [levels, setLevels] = useState({off:true, l1:true, l2:true, l3:false});
  const [rows, setRows] = useState([]);
  const [summary, setSummary] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [showRows, setShowRows] = useState(false);
  const all = (modelsInfo && modelsInfo.models) || [];
  const chosen = Object.keys(sel).filter(k => sel[k]);
  const lv = Object.keys(levels).filter(k => levels[k]);

  const loadReport = (s) => fetch('/api/bench/report?suite=' + (s==='drill'?'drill':'ask'))
    .then(r=>r.json()).then(d=>{
      if(d && d.summary){ setSummary(d.summary); setRows(d.rows||[]); }
      else { setSummary(null); setRows([]); }
    }).catch(()=>{});
  useEffect(() => { if(!busy) loadReport(suite); }, [suite]);

  const run = async () => {
    if(!chosen.length || busy) return;
    setBusy(true); setErr(null); setRows([]); setSummary(null);
    try{
      await streamSSE('/api/bench',
        {models: chosen, judge: judge || null, suite, guard, levels: lv}, {
        row: d => setRows(r => [...r, d]),
        summary: d => setSummary(d),
      });
    }catch(e){ setErr(String(e.message || e)); }
    finally{ setBusy(false); }
  };

  const perModel = suite==='guard' ? chosen.length * lv.length : chosen.length;
  const total = perModel * SUITES[suite].n;
  const isDrill = suite === 'drill';

  return (
    <div className="main">
      <div className="col">
        <div className="card">
          <h2 className="cardtitle">Test Playground</h2>
          <div className="seg" style={{marginBottom:14}}>
            {Object.keys(SUITES).map(s =>
              <button key={s} className={suite===s?'on':''} onClick={()=>setSuite(s)}>
                {s === 'ask' ? 'Ask' : s === 'guard' ? 'Guardrails' : 'Drill'}</button>)}
          </div>
          <p className="cardsub">{SUITES[suite].sub}</p>

          <label style={{fontSize:10.5, color:'var(--muted)', fontWeight:800,
                         textTransform:'uppercase', letterSpacing:'.8px'}}>Models</label>
          <div className="benchmodels" style={{marginTop:6}}>
            {all.map(m =>
              <label key={m.id} className={'benchopt'+(sel[m.id]?' on':'')}>
                <input type="checkbox" checked={!!sel[m.id]}
                       onChange={e=>setSel(s=>({...s, [m.id]: e.target.checked}))}/>
                {m.label}
              </label>)}
            {!all.length && <div className="stageline">no models reported — is the server up?</div>}
          </div>

          {suite==='guard' &&
            <React.Fragment>
              <label style={{fontSize:10.5, color:'var(--muted)', fontWeight:800,
                             textTransform:'uppercase', letterSpacing:'.8px', marginTop:14,
                             display:'block'}}>Guard levels to compare</label>
              <div className="benchmodels" style={{marginTop:6}}>
                {['off','l1','l2','l3'].map(l =>
                  <label key={l} className={'benchopt'+(levels[l]?' on':'')}>
                    <input type="checkbox" checked={!!levels[l]}
                           onChange={e=>setLevels(s=>({...s,[l]:e.target.checked}))}/>
                    {l.toUpperCase()}
                  </label>)}
              </div>
            </React.Fragment>}

          <div className="frow" style={{marginTop:14}}>
            <div className="fitem">
              <label>{isDrill ? 'Absurdity judge' : 'Sanity judge'}</label>
              <select value={judge} onChange={e=>setJudge(e.target.value)}>
                <option value="">Server default backend</option>
                {all.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
            </div>
            {suite==='ask' &&
              <div className="fitem">
                <label>Guard level</label>
                <select value={guard} onChange={e=>setGuard(e.target.value)}>
                  <option value="off">Off</option><option value="l1">L1</option>
                  <option value="l2">L2</option><option value="l3">L3</option>
                </select>
              </div>}
            <div className="fitem" style={{display:'flex', alignItems:'flex-end'}}>
              <button className="primary" style={{width:'100%'}}
                      disabled={busy || !chosen.length || (suite==='guard' && !lv.length)}
                      onClick={run}>
                {busy ? `Running… ${rows.length}/${total}`
                      : `Run ${SUITES[suite].title.toLowerCase()} (${total} run${total===1?'':'s'})`}</button>
            </div>
          </div>
          {err && <div className="errline" style={{marginTop:10}}>{err}</div>}
          {busy && <div className="stageline">⏳ {rows.length}/{total} — local models are slow;
            you can switch tabs, this keeps running.</div>}
        </div>

        {summary && !isDrill &&
          <div className="card">
            <h3 className="cardtitle">Comparative results — {summary.suite === 'guard'
              ? 'guardrails' : 'ask'}</h3>
            <table className="benchtable">
              <thead><tr><th>Model{summary.suite==='guard' ? ' @ level' : ''}</th>
                <th>Fetch</th><th>Answer</th>{summary.suite!=='guard' && <th>Sanity /5</th>}</tr></thead>
              <tbody>
                {Object.entries(summary.models||{}).map(([m,v]) =>
                  <tr key={m}><td>{m}</td>
                    <td>{v.fetch_pct}%</td><td>{v.answer_pct}%</td>
                    {summary.suite!=='guard' && <td>{v.sanity_avg}</td>}</tr>)}
              </tbody>
            </table>
            {summary.suite === 'ask' && Object.entries(summary.models||{}).map(([m,v]) =>
              v.by_kind ? <div key={m} style={{marginTop:12}}>
                <div className="stageline" style={{margin:'4px 0'}}><b>{m}</b> — pass rate by question kind</div>
                <Bars data={v.by_kind}/>
              </div> : null)}
            <div className="stageline">judge: {summary.judge} · {summary.duration_s}s ·
              saved to testbench_report.json</div>
            <button className="ghost" onClick={()=>setShowRows(s=>!s)}>
              {showRows ? 'Hide' : 'Show'} per-question rows ({rows.length})</button>
          </div>}

        {summary && isDrill &&
          <div className="card">
            <h3 className="cardtitle">Comparative results — drill generation</h3>
            <table className="benchtable">
              <thead><tr><th>Model</th><th>Overall /5</th><th>Absurdities per drill</th>
                <th>Verdicts</th></tr></thead>
              <tbody>
                {Object.entries(summary.models||{}).map(([m,v]) =>
                  <tr key={m}><td>{m}</td><td>{v.overall}</td>
                    <td className={v.absurdities_per_drill > 1 ? 'bbad' : 'bok'}>
                      {v.absurdities_per_drill}</td>
                    <td>{Object.entries(v.verdicts||{}).map(([k,n])=>`${k}:${n}`).join(' · ')}</td></tr>)}
              </tbody>
            </table>
            {Object.entries(summary.models||{}).map(([m,v]) =>
              <div key={m} style={{marginTop:12}}>
                <div className="stageline" style={{margin:'4px 0'}}><b>{m}</b> — average score by axis</div>
                <Bars data={v.avg_scores}/>
              </div>)}
            <div className="stageline">judge: {summary.judge} · {summary.duration_s}s ·
              saved to drillbench_report.json</div>
            <button className="ghost" onClick={()=>setShowRows(s=>!s)}>
              {showRows ? 'Hide' : 'Show'} per-drill detail ({rows.length})</button>
          </div>}

        {showRows && rows.length > 0 && !isDrill &&
          <div className="card">
            {rows.map((r,i) =>
              <div key={i} className="benchrow">
                <div><span className="vtag">{r.kind}</span> <b>{r.q_id}</b> · {r.model}
                  {r.guard ? ` · guard ${r.guard}` : ''} ·
                  <span className={r.fetch?'bok':'bbad'}> fetch {r.fetch?'✓':'✗'}</span> ·
                  <span className={r.answer?'bok':'bbad'}> answer {r.answer?'✓':'✗'}</span> ·
                  sanity {r.sanity} · {r.mode}{r.web_results ? ` · ${r.web_results} web` : ''} · {r.latency_s}s</div>
                <div className="benchq">{r.q}</div>
                {r.answer_head && <div className="benchq" style={{opacity:.75}}>→ {r.answer_head}…</div>}
                {r.issue && r.issue !== 'none' && r.issue !== 'deterministic path' &&
                  <div className="benchissue">issue: {r.issue}</div>}
              </div>)}
          </div>}

        {showRows && rows.length > 0 && isDrill &&
          <div className="card">
            {rows.map((r,i) =>
              <div key={i} className="benchrow">
                <div><b>{r.q_id}</b> · {r.model} · avg {r.avg}/5 ·
                  <span className={r.verdict==='use'?'bok':r.verdict==='discard'?'bbad':''}>
                    {' '}verdict: {r.verdict}</span> · {r.latency_s}s</div>
                <div className="benchq"><b>{r.title}</b> — {r.theme}</div>
                {r.narrative_head && <div className="benchq" style={{opacity:.75}}>{r.narrative_head}…</div>}
                {(r.absurdities||[]).map((a,j) =>
                  <div key={j} className="benchissue">⚠ {a}</div>)}
                {r.error && <div className="benchissue">error: {r.error}</div>}
              </div>)}
          </div>}
      </div>
    </div>
  );
}

/* ================= Sidebar — slim rail, expands on hover, pin to keep open ====== */
function Sidebar({items, activeId, onSelect, onNew, onDelete, pinned, setPinned}){
  // group by recency, preserving the server's updated_at ordering
  const groups = [];
  let last = null;
  items.forEach(it => {
    const g = groupLabel(it.updated_at || it.created_at);
    if(g !== last){ groups.push({label:g, items:[]}); last = g; }
    groups[groups.length-1].items.push(it);
  });

  return (
    <div className={'sidebar'+(pinned?' pinned':'')}>
      <div className="sb-head">
        <span className="sb-icon"><IcHist/></span>
        <span className="sb-title sb-expand">History</span>
        <button className={'sb-pin sb-expand'+(pinned?' on':'')}
                title={pinned ? 'Unpin sidebar' : 'Keep sidebar open'}
                onClick={()=>setPinned(!pinned)}><IcPin/></button>
      </div>

      <button className="sb-new" onClick={onNew} title="New conversation">
        <IcPlus/><span className="sb-expand">New conversation</span></button>

      <div className="sb-list">
        {items.length === 0 &&
          <div className="sb-empty sb-expand">Questions and generated drills land here —
            colour-coded, titled, and they survive a restart.</div>}
        {groups.map(g =>
          <React.Fragment key={g.label}>
            <div className="sb-group sb-expand">{g.label}</div>
            {g.items.map(it =>
              <div key={it.id} className={'hitem'+(it.id===activeId?' on':'')}
                   onClick={()=>onSelect(it)} title={it.title}>
                <span className={'kdot '+it.kind}/>
                <span className="ht sb-expand">{it.title}</span>
                <button className="hdel sb-expand" title="Delete"
                  onClick={e=>{e.stopPropagation(); onDelete(it);}}><IcTrash/></button>
              </div>)}
          </React.Fragment>)}
      </div>

      <div className="sb-hint sb-expand">
        <kbd>Enter</kbd> send · <kbd>Shift</kbd>+<kbd>Enter</kbd> newline
      </div>
    </div>
  );
}

/* ================= Settings ================= */
function SettingsPanel({settings, set, health, stats, modelsInfo, onClose}){
  const s = settings;
  return (
    <React.Fragment>
      <h2>Settings
        <button className={'pinbtn'+(s.pinned?' on':'')}
                title={s.pinned ? 'Unpin panel' : 'Pin panel open'}
                onClick={()=>set({pinned: !s.pinned})}><IcPin/></button>
        {!s.pinned && <button className="pinbtn" title="Close" onClick={onClose}><IcClose/></button>}
      </h2>
      <div className="sect">
        <div className="shead">Appearance</div>
        <div className="srow"><span>Dark theme</span>
          <Toggle on={s.theme==='dark'} set={v=>set({theme: v?'dark':'light'})}/></div>
      </div>
      <div className="sect">
        <div className="shead">Retrieval &amp; model</div>
        <div className="srow"><span>Accuracy</span>
          <select style={{width:170}} value={s.mode} onChange={e=>set({mode:e.target.value})}>
            <option value="medium">Medium · fastest</option>
            <option value="good">Good · hybrid</option>
            <option value="best">Best · rerank</option>
          </select></div>
        <div className="srow"><span>Vertical</span>
          <select style={{width:170}} value={s.vertical} onChange={e=>set({vertical:e.target.value})}>
            <option value="auto">Auto-detect</option>
            <option value="incidents">Incidents (corpus only)</option>
            <option value="regulatory">Regulatory (+ web)</option>
          </select></div>
        <div className="srow"><span>Category</span>
          <select style={{width:170}} value={s.category} onChange={e=>set({category:e.target.value})}>
            <option value="">All categories</option>
            {Object.keys((stats&&stats.categories)||{}).sort().map(c=><option key={c} value={c}>{c}</option>)}
          </select></div>
        <div className="srow"><span>Excerpts per answer</span>
          <select style={{width:170}} value={s.k} onChange={e=>set({k:+e.target.value})}>
            <option value={4}>4 · terse</option><option value={6}>6 · balanced</option>
            <option value={8}>8 · thorough</option>
          </select></div>
        <div className="srow"><span>Answer model</span>
          <select style={{width:170}} value={s.answerModel || ''}
                  onChange={e=>set({answerModel: e.target.value})}>
            <option value="">{modelsInfo && modelsInfo.default
              ? `Default · ${modelsInfo.default.model}` : 'Server default'}</option>
            {((modelsInfo && modelsInfo.models) || []).map(m =>
              <option key={m.id} value={m.id}>{m.label}</option>)}
          </select></div>
        <div className="srow"><span>Guardrails</span>
          <select style={{width:170}} value={s.guard || 'l1'}
                  title="off: none · L1: deterministic scope gate · L2: + LLM intent classifier · L3: + Llama Guard (ollama pull llama-guard3:1b)"
                  onChange={e=>set({guard: e.target.value})}>
            <option value="off">Off</option>
            <option value="l1">L1 · scope gate</option>
            <option value="l2">L2 · + intent classifier</option>
            <option value="l3">L3 · + guard model</option>
          </select></div>
      </div>
      <div className="sect">
        <div className="shead">Voice</div>
        <div className="srow"><span>Read answers aloud</span>
          <Toggle on={s.voiceOut} set={v=>set({voiceOut:v})}/></div>
        <div className="srow"><span>Language</span>
          <select style={{width:170}} value={s.lang} onChange={e=>set({lang:e.target.value})}>
            <option value="en-IN">English (India)</option>
            <option value="en-US">English (US)</option>
            <option value="hi-IN">Hindi</option>
          </select></div>
        <div className="srow"><span>Speech rate</span>
          <select style={{width:170}} value={s.rate} onChange={e=>set({rate:+e.target.value})}>
            <option value={0.9}>Slow</option><option value={1}>Normal</option>
            <option value={1.15}>Fast</option>
          </select></div>
      </div>
      <div className="sect">
        <div className="shead">System</div>
        <div className="kv">
          Chat model: <b>{health ? (health.chat
            ? `${health.chat.model} · ${health.chat.backend}`
            : (health.ollama.model || '—')) : '—'}</b>
          {s.answerModel ? <React.Fragment> (overridden:
            <b> {s.answerModel.slice(s.answerModel.indexOf(':')+1)}</b>)</React.Fragment> : null}<br/>
          Embeddings: <b>{health?(health.ollama.embed_model||'—'):'—'}</b> · always local<br/>
          Index: <b>{health?health.index_chunks.toLocaleString():'—'} chunks</b>
          {stats && stats.documents ? <React.Fragment> · <b>{stats.documents} documents</b></React.Fragment> : null}<br/>
          Ollama: <b>{health ? (health.ollama.reachable?'connected':'unreachable') : '—'}</b>
        </div>
        <div style={{marginTop:10}}>
          <a className="ghost" style={{textDecoration:'none', display:'inline-block'}}
             href="/classic.html">Zero-dependency fallback UI</a>
        </div>
      </div>
    </React.Fragment>
  );
}

/* ================= App ================= */
const SETTINGS_DEFAULTS = {
  theme:'dark', mode:'good', vertical:'auto', category:'', k:6,
  voiceOut:false, lang:'en-IN', rate:1, answerModel:'', pinned:false,
  sidebarPinned:false, guard:'l1',
};

function App(){
  const [stored, setSettings] = useStored('sgpt.settings', SETTINGS_DEFAULTS);
  const settings = {...SETTINGS_DEFAULTS, ...stored};
  const set = p => setSettings(s => ({...SETTINGS_DEFAULTS, ...s, ...p}));
  const [tab, setTab] = useState('ask');
  const [drawer, setDrawer] = useState(false);
  const [health, setHealth] = useState(null);
  const [stats, setStats] = useState(null);
  const [modelsInfo, setModelsInfo] = useState(null);

  const [messages, setMessages] = useState([]);
  const EMPTY_DRILL = {theme:'', unit:'Refinery unit', level:'intermediate',
                       out:null, stage:null, error:null, busy:false};
  const [drill, setDrill] = useState(EMPTY_DRILL);
  const messagesRef = useRef(messages);
  useEffect(() => { messagesRef.current = messages; }, [messages]);
  const drillRef = useRef(drill);
  useEffect(() => { drillRef.current = drill; }, [drill]);
  const askIdRef = useRef(null);
  const drillIdRef = useRef(null);
  const titleRef = useRef(null);
  const sendRef = useRef(null);

  const [histItems, setHistItems] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const refreshHist = () => fetch('/api/history').then(r=>r.json())
    .then(d=>setHistItems(d.items||[])).catch(()=>{});

  useEffect(() => { window.__SGPT_MOUNTED__ = true; }, []);
  useEffect(() => { document.documentElement.dataset.theme = settings.theme; }, [settings.theme]);
  useEffect(() => {
    fetch('/api/health').then(r=>r.json()).then(setHealth).catch(()=>setHealth({err:1}));
    fetch('/api/stats').then(r=>r.json()).then(setStats).catch(()=>{});
    fetch('/api/models').then(r=>r.json()).then(d => {
      setModelsInfo(d);
      // remember the server default so answers can be stamped even when the
      // user hasn't overridden the model
      if(d && d.default && d.default.model) set({defaultModelLabel: d.default.model});
    }).catch(()=>{});
    refreshHist();
  }, []);

  const saveAsk = async () => {
    const msgs = messagesRef.current.map(m => ({...m, streaming:false}));
    if(!msgs.length) return;
    const firstQ = (msgs.find(m=>m.role==='user')||{}).content || 'conversation';
    // Professional title, the ChatGPT/Claude way: LLM names the chat once.
    if(!titleRef.current){
      try{
        const firstA = (msgs.find(m=>m.role==='bot' && !m.error)||{}).content || '';
        const t = await jpost('/api/title', {question:firstQ, answer:firstA.slice(0,400)});
        titleRef.current = (t.title||'').trim() || null;
      }catch{}
    }
    try{
      const r = await jpost('/api/history', {id: askIdRef.current, kind:'ask',
        title: titleRef.current || firstQ.slice(0,64), payload:{messages: msgs}});
      askIdRef.current = r.id; setActiveId(r.id); refreshHist();
    }catch(e){ console.warn('history save failed', e); }
  };
  const saveDrill = async () => {
    const d = drillRef.current;
    if(!d.out) return;
    try{
      const r = await jpost('/api/history', {id: drillIdRef.current, kind:'drill',
        title: (d.out.spec && d.out.spec.title) || d.theme || 'drill',
        payload:{theme:d.theme, unit:d.unit, level:d.level, out:d.out}});
      drillIdRef.current = r.id; setActiveId(r.id); refreshHist();
    }catch(e){ console.warn('drill history save failed', e); }
  };

  const selectItem = async (it) => {
    try{
      const item = await (await fetch('/api/history/'+it.id)).json();
      if(item.error) return;
      if(item.kind === 'ask'){
        setMessages((item.payload.messages || []).map(m => ({...m, streaming:false})));
        askIdRef.current = item.id;
        titleRef.current = item.title || null;
        setTab('ask');
      }else{
        setDrill({...EMPTY_DRILL, theme:item.payload.theme||'', unit:item.payload.unit||'Refinery unit',
                  level:item.payload.level||'intermediate', out:item.payload.out||null});
        drillIdRef.current = item.id;
        setTab('drill');
      }
      setActiveId(item.id);
    }catch{}
  };
  const deleteItem = async (it) => {
    try{ await fetch('/api/history/'+it.id, {method:'DELETE'}); }catch{}
    if(it.id === activeId) setActiveId(null);
    refreshHist();
  };
  const newChat = () => {
    setMessages([]); askIdRef.current = null; titleRef.current = null;
    setActiveId(null); setTab('ask');
  };
  const askFromAnalytics = (q) => {
    setTab('ask');
    setTimeout(() => { if(sendRef.current) sendRef.current(q); }, 50);
  };

  const serverModel = (health && health.chat && health.chat.model) ||
                      (health && health.ollama && health.ollama.model) || '';
  // The header must show what will ACTUALLY answer the next question:
  // the Settings override if set, otherwise the server default.
  const selected = settings.answerModel
    ? settings.answerModel.slice(settings.answerModel.indexOf(':') + 1)
    : '';
  const chatModel = selected || serverModel;
  const statusText = !health ? 'checking…'
    : health.err ? 'backend unreachable'
    : health.ok ? `${chatModel} · ${health.index_chunks.toLocaleString()} chunks`
    : !health.ollama.reachable ? 'Ollama not running (needed for embeddings)'
    : !health.ollama.embed_model_available ? `pull ${health.ollama.embed_model}`
    : (health.chat ? !health.chat.ready : !health.ollama.model_available)
      ? (health.chat && health.chat.backend !== 'ollama'
         ? `${health.chat.backend} API key missing` : `pull ${chatModel}`)
    : health.index_chunks === 0 ? 'index empty — run ingest.py' : 'not ready';
  const dotCls = !health ? '' : (health.ok ? 'ok' : 'bad');

  const panel = <SettingsPanel settings={settings} set={set} health={health && !health.err ? health : null}
                               stats={stats} modelsInfo={modelsInfo} onClose={()=>setDrawer(false)}/>;

  return (
    <div className="shell">
      <Sidebar items={histItems} activeId={activeId} onSelect={selectItem}
               onNew={newChat} onDelete={deleteItem}
               pinned={settings.sidebarPinned}
               setPinned={v=>set({sidebarPinned:v})}/>
      <div className="center">
        <div className="hdr">
          <div className="logo"><img src="/icon.svg" alt="" width="30" height="30"/></div>
          <div className="brand">
            <h1>IncidentWise</h1>
            <small>{stats && stats.documents
              ? `${stats.documents} documents · ${Object.keys(stats.categories||{}).length} categories`
              : 'Indian process-safety intelligence'}</small>
          </div>
          <div className="tabs">
            <button className={'tab'+(tab==='ask'?' on':'')} onClick={()=>setTab('ask')}>Ask</button>
            <button className={'tab drill'+(tab==='drill'?' on':'')} onClick={()=>setTab('drill')}>Drill</button>
            <button className={'tab'+(tab==='analytics'?' on':'')} onClick={()=>setTab('analytics')}>Analytics</button>
            <button className={'tab'+(tab==='bench'?' on':'')} onClick={()=>setTab('bench')}>Playground</button>
          </div>
          <div className="spacer"/>
          <div className="status"><span className={'dot '+dotCls}/><span>{statusText}</span></div>
          <button className="iconbtn" title="Settings" onClick={()=>setDrawer(d=>!d)}><IcGear/></button>
        </div>
        <div className="viewhost" style={{display: tab==='ask' ? 'flex' : 'none'}}>
          <ChatView settings={settings} messages={messages} setMessages={setMessages}
                    onCompleted={saveAsk} sendRef={sendRef}/>
        </div>
        <div className="viewhost" style={{display: tab==='drill' ? 'flex' : 'none'}}>
          <DrillView settings={settings} drill={drill} setDrill={setDrill} onCompleted={saveDrill}/>
        </div>
        <div className="viewhost" style={{display: tab==='analytics' ? 'flex' : 'none'}}>
          <AnalyticsView onAsk={askFromAnalytics}/>
        </div>
        <div className="viewhost" style={{display: tab==='bench' ? 'flex' : 'none'}}>
          <BenchView modelsInfo={modelsInfo}/>
        </div>
      </div>
      {settings.pinned
        ? <div className="drawer pinned">{panel}</div>
        : (drawer && <React.Fragment>
            <div className="scrim" onClick={()=>setDrawer(false)}/>
            <div className="drawer overlay">{panel}</div>
          </React.Fragment>)}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App/>);
