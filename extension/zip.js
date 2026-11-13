(function(root){
 'use strict';if(root.BridgeZip)return;
 const encoder=new TextEncoder();const table=Uint32Array.from({length:256},(_,n)=>{for(let k=0;k<8;k++)n=n&1?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
 function crc(bytes){let c=0xffffffff;for(const b of bytes)c=table[(c^b)&255]^(c>>>8);return(c^0xffffffff)>>>0;}
 function header(size){const bytes=new Uint8Array(size);return {bytes,view:new DataView(bytes.buffer)};}
 function zip(files){
  const parts=[],directory=[];let offset=0;
  for(const file of files){
   const name=encoder.encode(file.name),data=typeof file.data==='string'?encoder.encode(file.data):file.data;
   if(!name.length||file.name.startsWith('/')||file.name.split('/').some(p=>p==='..')||file.name.includes('\\'))throw Error('无效 ZIP 路径');
   if(data.length>0xffffffff)throw Error('文件过大');
   const checksum=crc(data),h=header(30);h.view.setUint32(0,0x04034b50,true);h.view.setUint16(4,20,true);h.view.setUint16(6,0x800,true);h.view.setUint32(14,checksum,true);h.view.setUint32(18,data.length,true);h.view.setUint32(22,data.length,true);h.view.setUint16(26,name.length,true);
   parts.push(h.bytes,name,data);const c=header(46);c.view.setUint32(0,0x02014b50,true);c.view.setUint16(4,20,true);c.view.setUint16(6,20,true);c.view.setUint16(8,0x800,true);c.view.setUint32(16,checksum,true);c.view.setUint32(20,data.length,true);c.view.setUint32(24,data.length,true);c.view.setUint16(28,name.length,true);c.view.setUint32(42,offset,true);directory.push(c.bytes,name);offset+=30+name.length+data.length;
  }
  const size=directory.reduce((n,x)=>n+x.length,0);if(files.length>65535||offset+size>0xffffffff)throw Error('ZIP 超过当前版本容量');
  const end=header(22);end.view.setUint32(0,0x06054b50,true);end.view.setUint16(8,files.length,true);end.view.setUint16(10,files.length,true);end.view.setUint32(12,size,true);end.view.setUint32(16,offset,true);
  return new Blob([...parts,...directory,end.bytes],{type:'application/zip'});
 }
 root.BridgeZip={zip,crc};if(typeof module!=='undefined')module.exports=root.BridgeZip;
})(globalThis);
