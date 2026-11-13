(function(root){
 'use strict';
 const wait=ms=>new Promise(r=>setTimeout(r,ms));
 function anchors(){
  const roots=document.querySelectorAll('nav,aside,[data-testid*="sidebar"],#history');
  return roots.length?[...new Set([...roots].flatMap(r=>[...r.querySelectorAll('a[href]')]))]:[...document.querySelectorAll('a[href]')];
 }
 function links(){
  const items=new Map();
  // Read rendered links only, never guess or call a private history endpoint.
  for(const a of anchors()){
   const u=new URL(a.href,location.href),match=/\/(?:c)\/([A-Za-z0-9-]+)\/?$/.exec(u.pathname);
   if(u.origin===location.origin&&match){const url='https://chatgpt.com/c/'+match[1];items.set(url,{url,id:match[1],title:(a.textContent||a.getAttribute('aria-label')||match[1]).trim()});}
  }return [...items.values()];
 }
 async function list(){
  const all=new Map(),positions=new Map();let stable=0,last='',steps=0;
  try{
   while(stable<6&&steps++<300){
    for(const item of links())all.set(item.url,item);
    const containers=new Set();
    for(const a of anchors().filter(a=>a.href.includes('/c/')))for(let p=a.parentElement;p&&p!==document.body;p=p.parentElement){
     if(p.scrollHeight>p.clientHeight+20&&/auto|scroll/.test(getComputedStyle(p).overflowY)){containers.add(p);break;}
    }
    for(const p of containers){if(!positions.has(p)){positions.set(p,p.scrollTop);p.scrollTop=0;}else p.scrollTop+=Math.max(p.clientHeight*.8,300);}
    await wait(650);const stamp=[all.size,...[...containers].map(p=>p.scrollHeight+':'+p.scrollTop)].join('|');
    stable=stamp===last?stable+1:0;last=stamp;
   }
  }finally{for(const [p,top] of positions)p.scrollTop=top;}
  return {items:[...all.values()],scope:'rendered_sidebar_links',limit_reached:steps>=300,note:'仅包含网页侧边栏可加载的对话；归档、项目内未展开或未加载的对话可能未列出，可粘贴对话链接添加。'};
 }
 root.BridgeBrowserControl={links,list};
})(globalThis);
