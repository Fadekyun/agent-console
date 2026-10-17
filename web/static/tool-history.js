export function setupToolHistory({api,el,session,message}) {
  const panel=document.querySelector('#session-tool-history');let generation=0;
  document.querySelector('#show-tool-history').onclick=async()=>{
    const selected=session();if(!selected)return;const request=++generation;
    const current=()=>generation===request&&session()?.id===selected.id;
    const path=`/api/workbench/sessions/${encodeURIComponent(selected.id)}/tool-calls`;
    panel.replaceChildren(el('h3','Tool calls'),el('p','Loading…'));panel.hidden=false;
    async function load(before,thread){
      const query=new URLSearchParams();if(before!=null)query.set('before',String(before));if(thread)query.set('thread',thread);
      const data=await api(`${path}?${query}`);if(!current())return;
      if(before==null)panel.replaceChildren(el('h3','Tool calls'),el('p',data.notice,'small muted'));
      if(!data.available)return;
      for(const call of data.calls){
        const row=el('article',null,'tool-call'),codes=call.exit_codes?.length?` · exits ${call.exit_codes.join(', ')}`:'';
        row.append(el('strong',call.tool),el('p',`${call.at?new Date(call.at).toLocaleString():'Time unavailable'} · ${call.duration_seconds==null?'Duration unavailable':call.duration_seconds.toFixed(2)+' s'} · ${call.status}${codes}`,'small muted'));
        if(call.output_offset!=null){
          const details=el('details'),summary=el('summary','Output'),output=el('pre',call.preview||'Empty output');
          const values=new URLSearchParams({offset:String(call.output_offset),call_id:call.id,thread:data.thread});
          const link=el('a','Download redacted output');link.href=`${path}/output?${values}&download=true`;link.setAttribute('download','tool-output.txt');
          details.append(summary,output,link);let loaded=false;
          details.ontoggle=async()=>{if(!details.open||loaded)return;loaded=true;try{const result=await api(`${path}/output?${values}`);if(!current())return;output.textContent=result.text||'Empty output';if(result.truncated)details.append(el('p','Output limited to 256 KiB. Original native history is preserved.','small muted'));}catch(error){loaded=false;if(current())message(error.message);}};
          row.append(details);
        }else row.append(el('p','Output is outside this bounded page or has not been recorded.','small muted'));
        panel.append(row);
      }
      if(!data.calls.length)panel.append(el('p','No tool calls in this bounded segment.'));
      if(data.next_cursor!=null){const more=el('button','Older tool calls');more.onclick=async()=>{more.disabled=true;try{await load(data.next_cursor,data.thread);if(current())more.remove();}catch(error){if(current()){more.disabled=false;message(error.message);}}};panel.append(more);}
    }
    try{await load();}catch(error){if(current()){panel.replaceChildren(el('h3','Tool calls'),el('p',error.message,'danger'));}}
  };
  return {reset(){generation++;panel.hidden=true;panel.replaceChildren();}};
}
