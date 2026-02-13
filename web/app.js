const app = document.getElementById('app');
const audio = document.getElementById('ttsAudio');
let state = {user:null, chats:[], currentChat:null, messages:[], personas:[], workspaces:[], settings:null, models:[], status:'Thinking', showViz:false};

const VIZ = {N:120, bandWidth:2, maxOffset:150, attack:0.35, release:0.1, spring:0.12, damping:0.82, ringR:180};
let ctx, analyser, source, freq, dots=[];

function el(tag, attrs={}, children=[]) { const n=document.createElement(tag); Object.entries(attrs).forEach(([k,v])=>k==='class'?n.className=v:k.startsWith('on')?n.addEventListener(k.slice(2),v):n[k]=v); (Array.isArray(children)?children:[children]).forEach(c=>n.append(c?.nodeType?c:document.createTextNode(c??''))); return n; }
async function api(path, opts={}){ const r=await fetch(path,{headers:{'Content-Type':'application/json',...(opts.headers||{})},...opts}); const t=await r.text(); let j={}; try{j=JSON.parse(t)}catch{} if(!r.ok) throw new Error(j.error||t||r.status); return j; }

function ensureAudioGraph(){
  if(ctx) return;
  ctx = new (window.AudioContext||window.webkitAudioContext)();
  source = ctx.createMediaElementSource(audio);
  analyser = ctx.createAnalyser(); analyser.fftSize = 512;
  source.connect(analyser); analyser.connect(ctx.destination);
  freq = new Uint8Array(analyser.frequencyBinCount);
  const bands = [...Array(analyser.frequencyBinCount).keys()].sort(()=>Math.random()-0.5);
  dots = [...Array(VIZ.N)].map((_,i)=>({band:bands[i%bands.length], amp:0, vel:0}));
}

function vizCanvas(){
  const c=el('canvas',{id:'vizCanvas'}); c.width=innerWidth; c.height=innerHeight;
  addEventListener('resize',()=>{c.width=innerWidth;c.height=innerHeight});
  const g=c.getContext('2d');
  const loop=()=>{requestAnimationFrame(loop); if(!state.showViz) return;
    g.clearRect(0,0,c.width,c.height); g.fillStyle='#f4f8ff'; g.fillRect(0,0,c.width,c.height);
    if(!analyser) return; analyser.getByteFrequencyData(freq);
    g.globalCompositeOperation='lighter';
    const cx=c.width/2, cy=c.height/2;
    for(let i=0;i<dots.length;i++){
      const d=dots[i], a=(i/dots.length)*Math.PI*2;
      let raw=0; for(let b=0;b<VIZ.bandWidth;b++) raw += (freq[(d.band+b)%freq.length]||0)/255; raw/=VIZ.bandWidth;
      const target=Math.min(VIZ.maxOffset, raw*VIZ.maxOffset);
      const k=target>d.amp?VIZ.attack:VIZ.release; d.vel += (target-d.amp)*k*VIZ.spring; d.vel*=VIZ.damping; d.amp+=d.vel;
      const r=VIZ.ringR+d.amp; const x=cx+Math.cos(a)*r, y=cy+Math.sin(a)*r;
      g.fillStyle='rgba(80,160,255,.18)'; g.beginPath(); g.arc(x,y,18,0,Math.PI*2); g.fill();
      g.fillStyle='rgba(0,96,220,.9)'; g.beginPath(); g.arc(x,y,6,0,Math.PI*2); g.fill();
    }
    g.globalCompositeOperation='source-over';
  }; loop(); return c;
}

async function refresh(){
  try{state.models=(await api('/api/models')).models||[]}catch{state.models=[]}
  try{state.workspaces=(await api('/api/workspaces')).items; state.personas=(await api('/api/personas')).items; state.chats=(await api('/api/chats')).items; state.settings=(await api('/api/settings')).settings; state.user=true;}catch{state.user=false}
  render();
}

async function sendChat(text){
  state.status='Thinking'; render();
  const personaId=document.getElementById('personaSel')?.value||state.currentChat?.persona_id;
  const model=document.getElementById('modelSel')?.value||state.currentChat?.model_override;
  const memoryMode=document.getElementById('memSel')?.value||'auto';
  const r=await api('/api/chat',{method:'POST',body:JSON.stringify({text,chatId:state.currentChat?.id,personaId,model,memoryMode})});
  state.currentChat={...(state.currentChat||{}),id:r.chatId,persona_id:personaId,model_override:model,memory_mode:memoryMode};
  const detail=await api(`/api/chats/${r.chatId}`); state.messages=detail.messages; state.status='Speaking'; render();
  if(state.settings?.tts_provider && state.settings.tts_provider!=='disabled'){
    ensureAudioGraph();
    const t=await api('/api/tts',{method:'POST',body:JSON.stringify({text:r.text,chatId:r.chatId,personaId,format:state.settings.tts_format||'wav'})});
    audio.src=t.audioUrl; await audio.play();
  } else state.status='Thinking';
  refresh();
}
audio.addEventListener('ended',()=>{state.status='Thinking';render()});

let recorder, chunks=[];
async function startRec(){ensureAudioGraph(); await ctx.resume(); const stream=await navigator.mediaDevices.getUserMedia({audio:true}); recorder=new MediaRecorder(stream,{mimeType:'audio/webm'}); chunks=[]; recorder.ondataavailable=e=>chunks.push(e.data); recorder.start(); state.status='Listening'; render();}
async function stopRec(){if(!recorder) return; recorder.stop(); await new Promise(r=>recorder.onstop=r); const blob=new Blob(chunks,{type:'audio/webm'}); const fd=new FormData(); fd.append('file', blob, 'audio.webm'); state.status='Thinking'; render(); const r=await fetch('/api/stt',{method:'POST',body:fd}); const j=await r.json(); if(j.text) document.getElementById('chatInput').value=j.text;}

function authView(){
  const box=el('div',{class:'main'},[
    el('h2',{textContent:'Nice Assistant Login'}),
    el('input',{id:'u',placeholder:'username'}),
    el('input',{id:'p',placeholder:'password',type:'password'}),
    el('div',{class:'row'},[
      el('button',{textContent:'Create account',onclick:async()=>{await api('/api/users',{method:'POST',body:JSON.stringify({username:u.value,password:p.value})}); alert('Account created');}}),
      el('button',{textContent:'Login',onclick:async()=>{await api('/api/login',{method:'POST',body:JSON.stringify({username:u.value,password:p.value})}); await refresh();}})
    ]),
    el('p',{textContent:'On first login you will be prompted to create workspace/persona/default model.'})
  ]); return box;
}

async function ensureWizard(){
  if(state.settings?.onboarding_done) return;
  const wsName=prompt('Welcome! Name your first Workspace','Main Workspace'); if(!wsName) return;
  const ws=await api('/api/workspaces',{method:'POST',body:JSON.stringify({name:wsName})});
  const pName=prompt('Create your first Persona','Assistant');
  const sys=prompt('Optional personality/system prompt','Be helpful and concise.');
  await api('/api/personas',{method:'POST',body:JSON.stringify({workspaceId:ws.id,name:pName,systemPrompt:sys,defaultModel:state.models[0]||''})});
  await api('/api/settings',{method:'POST',body:JSON.stringify({global_default_model:state.models[0]||'',default_memory_mode:'auto',stt_provider:'disabled',tts_provider:'disabled',tts_format:'wav',onboarding_done:1})});
  await refresh();
}

function render(){
  app.innerHTML='';
  if(!state.user){app.append(authView()); return;}
  const viz=el('div',{class:`viz-wrap ${state.showViz?'show':''}`},[vizCanvas()]);
  const status=el('div',{class:'status',textContent:state.status});
  const toggle=el('button',{class:'top-toggle',textContent:state.showViz?'Hide Visualizer':'Show Visualizer',onclick:()=>{state.showViz=!state.showViz; render();}});

  const sidebar=el('div',{class:'sidebar'},[
    el('h3',{textContent:'Chats'}),
    el('button',{textContent:'New Chat',onclick:async()=>{const c=await api('/api/chats',{method:'POST',body:JSON.stringify({title:'New chat',memoryMode:state.settings?.default_memory_mode||'auto'})}); state.currentChat={id:c.id}; const d=await api(`/api/chats/${c.id}`); state.messages=d.messages; render(); refresh();}}),
    ...state.chats.map(c=>el('div',{class:'msg'},[el('button',{textContent:c.title||c.id,onclick:async()=>{state.currentChat=c; const d=await api(`/api/chats/${c.id}`); state.messages=d.messages; render();}})])),
    el('hr'),el('h4',{textContent:'Memory Editor'}),
    el('button',{textContent:'Add Global Memory',onclick:async()=>{const t=prompt('Memory text'); if(t){await api('/api/memory/global',{method:'POST',body:JSON.stringify({content:t})});}}}),
    el('button',{textContent:'Settings',onclick:async()=>{
      const tts=prompt('TTS provider (disabled/openai/local)',state.settings?.tts_provider||'disabled');
      const stt=prompt('STT provider (disabled/openai/local)',state.settings?.stt_provider||'disabled');
      const key=prompt('OpenAI API key (optional)',state.settings?.openai_api_key||'');
      await api('/api/settings',{method:'POST',body:JSON.stringify({...state.settings,tts_provider:tts,stt_provider:stt,openai_api_key:key})}); await refresh();
    }})
  ]);

  const main=el('div',{class:'main'},[
    el('div',{class:'toolbar'},[
      el('select',{id:'personaSel'}, state.personas.map(p=>el('option',{value:p.id,textContent:p.name,selected:p.id===state.currentChat?.persona_id}))),
      el('select',{id:'modelSel'}, state.models.map(m=>el('option',{value:m,textContent:m,selected:m===state.currentChat?.model_override||m===state.settings?.global_default_model}))),
      el('select',{id:'memSel'}, ['off','manual','auto'].map(m=>el('option',{value:m,textContent:m,selected:m===(state.currentChat?.memory_mode||state.settings?.default_memory_mode||'auto')}))),
      el('button',{textContent:'Save selected msg to memory',onclick:async()=>{const t=prompt('Paste message text to save'); if(t) await api('/api/memory/persona/'+(document.getElementById('personaSel').value),{method:'POST',body:JSON.stringify({content:t})});}})
    ]),
    el('div',{class:'messages'}, state.messages.map(m=>el('div',{class:'msg'},[el('span',{class:m.role==='user'?'user':'assistant',textContent:m.role+': '}), m.text]))),
    el('div',{class:'row'},[
      el('input',{id:'chatInput',placeholder:'Type message...',style:'flex:1'}),
      el('button',{textContent:'Send',onclick:()=>sendChat(document.getElementById('chatInput').value)}),
      el('button',{textContent:'Hold to Talk',onpointerdown:startRec,onpointerup:stopRec,onpointercancel:stopRec})
    ])
  ]);

  app.append(el('div',{class:'layout'},[sidebar,main]),status,toggle,viz);
}

refresh().then(ensureWizard);
