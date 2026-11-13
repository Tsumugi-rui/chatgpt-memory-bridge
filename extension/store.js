(function(root){
 'use strict';if(root.BridgeStore)return;
 function pool(){
  const values=new Map();let bytes=0;
  return {intern(value){const key=JSON.stringify(value);if(values.has(key))return values.get(key);values.set(key,value);bytes+=key.length*2;return value;},get bytes(){return bytes;},get size(){return values.size;}};
 }
 function internJob(job,p){for(const s of job.snapshots) s.messages=s.messages.map(m=>p.intern(m));return job;}
 function pack(job){
  const dictionary=[],ids=new Map();
  const snapshots=job.snapshots.map(s=>({...s,messages:s.messages.map(m=>{const key=JSON.stringify(m);if(!ids.has(key)){ids.set(key,dictionary.length);dictionary.push(m);}return ids.get(key);})}));
  return {format:'memory-bridge-reference-v2',job:{...job,snapshots},dictionary};
 }
 function unpack(record){
  if(record?.format!=='memory-bridge-reference-v2'||!Array.isArray(record.dictionary)||!Array.isArray(record.job?.snapshots))throw Error('断点格式不正确');
  return {...record.job,snapshots:record.job.snapshots.map(s=>({...s,messages:s.messages.map(id=>{if(!Number.isInteger(id)||id<0||id>=record.dictionary.length)throw Error('断点消息引用损坏');return record.dictionary[id];})}))};
 }
 async function hash(bytes){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');}
 async function encode(job){
  const raw=new TextEncoder().encode(JSON.stringify(pack(job)));
  const zipped=new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer());
  const chunks=[];for(let i=0;i<zipped.length;i+=16384)chunks.push(btoa(String.fromCharCode(...zipped.subarray(i,i+16384))));
  return {codec:'bridge-gzip-v2',chunks,sha256:await hash(zipped),rawBytes:raw.length,compressedBytes:zipped.length,savedAt:new Date().toISOString()};
 }
 async function decode(value){
  if(value?.codec==='bridge-gzip-v2'){
   const pieces=value.chunks.map(c=>Uint8Array.from(atob(c),x=>x.charCodeAt(0)));const size=pieces.reduce((n,b)=>n+b.length,0);const bytes=new Uint8Array(size);let offset=0;
   for(const piece of pieces){bytes.set(piece,offset);offset+=piece.length;}
   if(await hash(bytes)!==value.sha256)throw Error('断点校验失败；已保留原数据，请勿移除插件');
   const raw=await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text();return unpack(JSON.parse(raw));
  }
  if(Array.isArray(value?.snapshots))return value; // Lossless migration from v1; same extension key.
  throw Error('无法识别断点；已保留原数据');
 }
 root.BridgeStore={pool,internJob,pack,unpack,encode,decode};if(typeof module!=='undefined')module.exports=root.BridgeStore;
})(globalThis);
