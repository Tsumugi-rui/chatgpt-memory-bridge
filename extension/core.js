(function(root){
  'use strict'; if(root.BridgeCore)return;
  const normalize=s=>String(s||'').replace(/\s+/g,' ').trim();
  const equal=(a,b)=>a.role===b.role && (a.id&&b.id ? a.id===b.id : normalize(a.text)===normalize(b.text));
  const emptyUser=m=>m.role==='user'&&!m.id&&!normalize(m.text)&&!m.assets?.length;
  const sameId=(a,b)=>!!(a?.id&&a.id===b?.id&&a.role===b.role);
  function placeholderMatch(a,b,i,j){
    return a[i].role===b[j].role&&(emptyUser(a[i])||emptyUser(b[j]))
      &&sameId(a[i-1],b[j-1])&&sameId(a[i+1],b[j+1]);
  }
  function align(a,b){
    const candidates=[];
    for(let offset=1-b.length;offset<a.length;offset++){
      let overlap=0,stable=false,valid=true;
      for(let j=0;j<b.length;j++){
        const i=j+offset;if(i<0||i>=a.length)continue;
        if(!equal(a[i],b[j])&&!placeholderMatch(a,b,i,j)){valid=false;break;}
        overlap++;if(a[i].id&&b[j].id&&a[i].id===b[j].id)stable=true;
      }
      if(valid&&(overlap>=2||stable))candidates.push({offset,overlap});
    }
    if(!candidates.length)return null;
    const best=Math.max(...candidates.map(x=>x.overlap));const winners=candidates.filter(x=>x.overlap===best);
    return winners.length===1?winners[0].offset:null;
  }
  function combine(a,b,offset){
    const start=Math.min(0,offset),end=Math.max(a.length,offset+b.length),result=[];
    for(let i=start;i<end;i++){
      const old=a[i],fresh=b[i-offset];
      if(old&&fresh){
        if(emptyUser(fresh)&&!emptyUser(old)){result.push(old);continue;}
        const versions=[...(old.versions||[])];
        if((old.text!==fresh.text||old.html!==fresh.html)&&!versions.some(v=>v.text===old.text&&v.html===old.html))versions.push({text:old.text,html:old.html,source:old.source});
        result.push({...old,...fresh,id:fresh.id||old.id,versions});
      }else result.push(old||fresh);
    }return result;
  }
  function append(previous,snapshot){
    const segments=[...(previous?.segments||[])];
    if(snapshot.messages.length){let segment=snapshot.messages.map(m=>({...m,source:snapshot.index}));let joined=true;
      while(joined){joined=false;for(let i=0;i<segments.length;i++){const offset=align(segments[i],segment);if(offset!==null){segment=combine(segments[i],segment,offset);segments.splice(i,1);joined=true;break;}}}
      segments.push(segment);
    }
    segments.sort((a,b)=>(a[0].source||0)-(b[0].source||0));
    return {segments,messages:segments.flat(),gaps:Math.max(0,segments.length-1)};
  }
  function assemble(snapshots){let result={segments:[],messages:[],gaps:0};for(const snapshot of snapshots)result=append(result,snapshot);return result;}
  const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function markdown(job,result){return '# '+job.title+'\n\n来源：'+job.url+'\n\n'+result.segments.map((segment,i)=>(i?'\n\n> 此处片段尚未连接，可能缺少消息。\n\n':'')+segment.map((m,j)=>'## '+(m.role==='user'?'用户':'ChatGPT')+'\n\n'+m.text+'\n'+(m.assets||[]).map(a=>'\n['+(a.name||a.kind)+']('+a.url+')').join('')).join('\n')).join('\n');}
  function exportName(title,url,date=new Date()){
    let clean=String(title||'').replace(/\s*[-–—|]\s*ChatGPT\s*$/i,'').replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g,'_').replace(/\s+/g,' ').trim().replace(/[. ]+$/g,'');
    let short='';for(const c of clean){if((short+c).length>48)break;short+=c;}
    short=short.replace(/[. ]+$/g,'')||'未命名对话';
    const pad=(n,width=2)=>String(n).padStart(width,'0');
    const stamp=date.getFullYear()+'-'+pad(date.getMonth()+1)+'-'+pad(date.getDate())+'_'+pad(date.getHours())+'-'+pad(date.getMinutes())+'-'+pad(date.getSeconds())+'-'+pad(date.getMilliseconds(),3);
    const minutes=-date.getTimezoneOffset(),zone='UTC'+(minutes>=0?'+':'-')+pad(Math.floor(Math.abs(minutes)/60))+pad(Math.abs(minutes)%60);
    let id;try{id=new URL(url).pathname.split('/').filter(Boolean).at(-1);}catch(e){}
    id=String(id||'unknown').replace(/[^A-Za-z0-9-]/g,'_').slice(0,40);
    return 'ChatGPT__'+short+'__'+stamp+'_'+zone+'__'+id+'.zip';
  }
  root.BridgeCore={normalize,equal,align,assemble,append,escape,markdown,exportName};
  if(typeof module!=='undefined')module.exports=root.BridgeCore;
})(globalThis);
