(function(root){
 'use strict';
 const sha=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');
 async function* split(blob,name,limit=120*1024*1024){
  if(blob.size<=limit){yield {blob,name,index:1,count:1};return;}
  const size=limit-4096;if(size<1024)throw Error('分卷大小过小');
  const count=Math.ceil(blob.size/size),hashes=[];
  if(count>10000)throw Error('分卷数超过上限');
  for(let i=0;i<count;i++)hashes.push(await sha(await blob.slice(i*size,(i+1)*size).arrayBuffer()));
  const set_id=await sha(new TextEncoder().encode(JSON.stringify([hashes,blob.size])));
  for(let i=0;i<count;i++){
   const metadata={format:'memory-bridge-volume-v1',set_id,index:i+1,count,total_bytes:blob.size,original_name:name,hashes};
   const part=BridgeZip.zip([{name:'bridge-volume.json',data:JSON.stringify(metadata)},{name:'payload.bin',data:new Uint8Array(await blob.slice(i*size,(i+1)*size).arrayBuffer())}]);
   if(part.size>limit)throw Error('分卷元数据超过容量');
   yield {blob:part,name:name.replace(/\.zip$/i,'')+'.part-'+String(i+1).padStart(4,'0')+'-of-'+count+'.zip',index:i+1,count};
  }
 }
 root.BridgeVolumes={split,sha};
 if(typeof module!=='undefined')module.exports=root.BridgeVolumes;
})(globalThis);
