let mres,authHeaders=()=>({});  // pages may override to add credentials
const $=id=>document.getElementById(id),money=c=>'$'+(c/100).toFixed(2);
// For values inside inline handlers (onclick="f(${js(v)})"): JSON string literal, then HTML-escaped.
// HTML entities are decoded before the handler runs, so esc() alone inside '...' is not safe there.
const js=v=>esc(JSON.stringify(String(v)));
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,opt={}){const r=await fetch(path,{...opt,headers:{'Content-Type':'application/json',...authHeaders(),...opt.headers}});
  const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||j.detail||r.statusText);return j}
const post=(p,b)=>api(p,{method:'POST',body:JSON.stringify(b||{})});
function prompt2(title,placeholder,type='text',err='',noInput=false){$('mt').textContent=title;const i=$('mi');i.value='';i.placeholder=placeholder;i.type=type;i.inputMode=type==='number'?'decimal':'text';
  i.classList.toggle('hide',noInput);$('me').textContent=err;$('scrim').classList.remove('hide');if(!noInput)setTimeout(()=>i.focus(),50);return new Promise(r=>mres=r)}
function closeM(v){$('scrim').classList.add('hide');mres&&mres(v);mres=null}
