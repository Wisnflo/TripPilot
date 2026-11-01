import {readFileSync,readdirSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const build=path.join(root,'dist/client'),target=path.resolve(root,'../src/trippilot/web');
function files(folder,prefix=''){return readdirSync(folder,{withFileTypes:true}).flatMap(e=>e.isDirectory()?files(path.join(folder,e.name),path.join(prefix,e.name)):[path.join(prefix,e.name)]);}
const built=files(build);const expected=new Set(built.filter(p=>p.startsWith('assets/')));const extra=files(path.join(target,'assets'),'assets').filter(p=>!expected.has(p));
const mismatches=built.filter(p=>{try{return !readFileSync(path.join(build,p)).equals(readFileSync(path.join(target,p)));}catch{return true;}});
if(mismatches.length||extra.length)throw Error('Bundled client differs from source build: '+[...mismatches,...extra].join(', '));
console.log('Verified bundled client: '+built.length+' files');
