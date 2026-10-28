"use strict";
const M=window.MMP_MODEL||JSON.parse(document.getElementById("model").textContent);
const $=id=>document.getElementById(id);

/* A control this page may simply not have.

   A model page is a BAKED artifact — its shell is whatever the build wrote that
   day — while viewer.js is served from the package and is this deployment's
   CURRENT code. So a control added since a page was baked is not in the DOM, and
   `ctl("L-body").onchange = fn` on a null is a TypeError thrown at module level:
   the renderer stops before it sizes the canvas and the whole page goes black,
   every panel dead, over one optional toggle. That is what happened to the
   public gallery on 2026-10-26.

   `ctl(id)` is the element, or a sink that swallows writes and reads back empty.
   Anything a page might not have is reached through it; `scripts/
   test_viewer_render.py` renders real committed pages, and one stripped of every
   optional control, against the renderer as it stands. */
const _SINKS = {};
function ctl(id){
  const e = $(id);
  if (e) return e;
  return _SINKS[id] || (_SINKS[id] = {
    style:{}, dataset:{}, options:[], value:"", checked:false, disabled:true,
    textContent:"", innerHTML:"", title:"", parentNode:{style:{},title:""},
    classList:{add(){},remove(){},toggle(){},contains(){return false;}},
    appendChild(){}, insertBefore(){}, removeChild(){}, remove(){},
    addEventListener(){}, removeEventListener(){}, focus(){}, blur(){}, click(){},
    querySelector(){return null;}, querySelectorAll(){return [];},
    getBoundingClientRect(){return {width:0,height:0,top:0,left:0,right:0,bottom:0};}
  });
}
const MOBILE=matchMedia("(max-width:760px)").matches;
const store={get(k,d){try{const v=localStorage.getItem("mmp:"+k);return v===null?d:JSON.parse(v);}catch(e){return d;}},
             set(k,v){try{localStorage.setItem("mmp:"+k,JSON.stringify(v));}catch(e){}}};
function b64(s,T){const b=atob(s),u=new Uint8Array(b.length);for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);return new T(u.buffer);}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function fmtN(v,d){return v==null?"–":Number(v).toLocaleString(undefined,{maximumFractionDigits:d==null?0:d,minimumFractionDigits:d==null?0:d});}
function fmtT(t){if(!t)return"0 t";return t>=1e9?(t/1e9).toFixed(2)+" Bt":t>=1e6?(t/1e6).toFixed(t>=1e8?0:2)+" Mt":t>=1e3?(t/1e3).toFixed(0)+" kt":Math.round(t)+" t";}
function fmtC(v,u){if(v==null)return"–";if(u==="oz"){return v>=1e6?(v/1e6).toFixed(2)+" Moz":v>=1e3?(v/1e3).toFixed(v>=1e5?0:1)+" koz":Math.round(v)+" oz";}
  if(u==="lb"){return v>=1e9?(v/1e9).toFixed(2)+" Blb":v>=1e6?(v/1e6).toFixed(v>=1e8?0:1)+" Mlb":v>=1e3?(v/1e3).toFixed(0)+" klb":Math.round(v)+" lb";}
  return v>=1e6?(v/1e6).toFixed(2)+" Mt":v>=1e3?(v/1e3).toFixed(1)+" kt":Math.round(v)+" t";}
function fmtG(g,u){if(g==null)return"–";const d=g>=100?0:g>=10?1:g>=1?2:3;return g.toFixed(d)+" "+u;}
function toast(msg,ms){const t=$("toast");t.textContent=msg;t.style.opacity=1;clearTimeout(toast._t);toast._t=setTimeout(()=>t.style.opacity=0,ms||2600);}

/* ---------------- header ---------------- */
{const bits=[];if(M.company)bits.push(M.company);if(M.region)bits.push(M.region);
 const k=(M.kinds||[]);const lab=[];if(k.includes("report"))lab.push("43-101 report data");if(k.includes("news"))lab.push("news releases");
 if(k.includes("import"))lab.push("imported data");bits.push(lab.join(" + ")||"drill data");
 if(M.updated)bits.push("to "+String(M.updated).slice(0,10));$("t-sub").textContent=bits.join(" · ");}
$("s-holes").textContent=fmtN(M.counts.holes);$("s-int").textContent=fmtN(M.counts.intervals);
$("s-src").textContent=fmtN((M.sources||[]).length);
{const sv=M.counts.surveyed||0,st=document.createElement("p");st.className="hint";st.style.margin="8px 0 0";
 st.textContent=sv?`${fmtN(sv)} hole${sv>1?"s":""} drawn from downhole surveys (minimum curvature); the rest straight on collar azimuth/dip.`
   :"Holes drawn straight on collar azimuth/dip (no downhole surveys published for this project).";
 document.querySelector("#pModel .stats").after(st);}

/* ---------------- layout repair for pages built before the left column ----------
   Model pages bake in their own markup, so this rebuilds the left column at runtime;
   once a page is rebuilt the shell already has it and this does nothing. */
(function addSizeBox(){
  if(document.getElementById("sizebox"))return;
  const body=document.querySelector("#pModel .pbody");if(!body)return;
  const sec=document.createElement("div");sec.className="sec";
  sec.innerHTML='<p class="lab">Size</p><div id="sizebox"></div>';
  const first=body.querySelector(".sec");
  if(first&&first.nextSibling)body.insertBefore(sec,first.nextSibling);else body.appendChild(sec);
})();

(function fixLayout(){
  const model=document.getElementById("pModel"),data=document.getElementById("pData");
  if(!data||document.getElementById("leftcol"))return;
  const col=document.createElement("div");col.id="leftcol";
  model.parentNode.insertBefore(col,model);col.appendChild(model);col.appendChild(data);
  data.classList.add("min");
  const css=document.createElement("style");
  css.textContent=`#leftcol{position:fixed;z-index:20;top:calc(var(--bar) + 12px);left:12px;width:306px;display:flex;
    flex-direction:column;gap:10px;max-height:calc(100vh - var(--bar) - 86px);pointer-events:none}
   #leftcol>.panel{position:static;width:auto;max-height:none;pointer-events:auto;flex:0 1 auto;min-height:0}
   #leftcol>.panel .pbody{max-height:42vh}
   #holetbl table,table.bd{table-layout:fixed}
   #holetbl td{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
   @media (max-width:760px){#leftcol{display:contents}}
   @media (max-height:560px) and (orientation:landscape){#leftcol{display:contents}}`;
  document.head.appendChild(css);
})();

/* Every page that has `#leftcol` — baked here or by the shell — also has
   `#leftcol>.panel{position:static}`, an id rule that beats the phone rule's
   `.panel{...}` on every property that is not !important, and `position` is not
   one of them. A static box ignores z-index, so on a phone the panel's white
   sheet and all of its label text painted UNDER the fixed canvas and the only
   thing left on screen was a column of unlabelled switch pills: the switches
   are position:relative, so they alone paint above it. Reported 2026-10-27 as
   "how am I supposed to know what each toggle is for".

   This is injected rather than left to the shell because the shell is baked:
   the gallery carries whatever CSS was written that day, so the fix has to ride
   in with the renderer to reach pages that are already out there. */
(function mobilePanels(){const s=document.createElement("style");
  s.textContent=`@media (max-width:760px){#leftcol>.panel{position:fixed;max-height:58vh}}
   @media (max-height:560px) and (orientation:landscape){#leftcol>.panel{position:fixed;max-height:none}}`;
  document.head.appendChild(s);})();

/* ---------------- panels (minimise / mobile sheets) ---------------- */
document.querySelectorAll(".phead").forEach(h=>{const p=h.parentElement,id=p.id;
  if(!MOBILE&&store.get("min:"+id,false))p.classList.add("min");
  const upd=()=>{h.querySelector(".pbtn").textContent=MOBILE?"×":(p.classList.contains("min")?"+":"–");};upd();
  h.addEventListener("click",e=>{if(MOBILE){p.classList.remove("open");document.querySelectorAll("#mtools button").forEach(b=>b.classList.remove("on"));return;}
    p.classList.toggle("min");store.set("min:"+id,p.classList.contains("min"));upd();});});
document.querySelectorAll("#mtools button").forEach(b=>b.addEventListener("click",()=>{const id=b.dataset.open,p=$(id),was=p.classList.contains("open");
  document.querySelectorAll(".panel").forEach(x=>x.classList.remove("open"));document.querySelectorAll("#mtools button").forEach(x=>x.classList.remove("on"));
  if(!was){p.classList.add("open");b.classList.add("on");}}));
if(MOBILE)$("navhint").textContent="One finger to orbit (or Pan mode) · two fingers to pan and pinch-zoom · double-tap anything to pivot around it.";

/* ---------------- three.js scene ---------------- */
const host=$("scene");
const renderer=new THREE.WebGLRenderer({antialias:true,powerPreference:"high-performance"});
renderer.setPixelRatio(Math.min(devicePixelRatio,MOBILE?1.5:2));
host.appendChild(renderer.domElement);
const scene=new THREE.Scene();scene.background=new THREE.Color(0xe9edf2);
const camera=new THREE.PerspectiveCamera(45,1,1,1e6);
scene.add(new THREE.HemisphereLight(0xffffff,0xb9c2cc,0.75));
const sun=new THREE.DirectionalLight(0xffffff,0.62);sun.position.set(-0.55,0.9,-0.35);scene.add(sun);
function W(){return host.clientWidth;} function Hh(){return host.clientHeight;}
function resize(){renderer.setSize(W(),Hh(),false);renderer.domElement.style.width="100%";renderer.domElement.style.height="100%";
  camera.aspect=W()/Hh();camera.updateProjectionMatrix();for(const m of fatMats)m.uniforms.uRes.value.set(W(),Hh());}

/* model frame: x east, y north, z masl  ->  world X=x, Y=z-zRef, Z=-y */
const HO=M.holes.map(r=>({id:r[0],x:r[1],y:r[2],z:r[3],az:r[4],dip:r[5],depth:r[6],p:r[7]||null}));
const zs=HO.map(h=>h.z).sort((a,b)=>a-b);const zRef=zs.length?zs[zs.length>>1]:0;
function V(x,y,z){return new THREE.Vector3(x,z-zRef,-y);}
HO.forEach(h=>{const a=h.az*Math.PI/180,d=h.dip*Math.PI/180;h.d=[Math.cos(d)*Math.sin(a),Math.cos(d)*Math.cos(a),Math.sin(d)];});
function at(h,m){ // position at measured depth m: surveyed (min-curvature) path, else straight
  const p=h.p;if(p&&p.length>=8){const n=p.length/4;let i=0;while(i<n-2&&p[(i+1)*4]<m)i++;
    const m0=p[i*4],m1=p[(i+1)*4],t=m1>m0?(m-m0)/(m1-m0):0;
    return V(h.x+p[i*4+1]+(p[i*4+5]-p[i*4+1])*t,h.y+p[i*4+2]+(p[i*4+6]-p[i*4+2])*t,h.z+p[i*4+3]+(p[i*4+7]-p[i*4+3])*t);}
  return V(h.x+h.d[0]*m,h.y+h.d[1]*m,h.z+h.d[2]*m);}
function holeSegs(h,a,b){ // split [a,b] at survey stations so curved holes bend
  if(!h.p)return[[a,b]];const out=[];let s=a;for(let i=0;i<h.p.length;i+=4){const m=h.p[i];if(m>s&&m<b){out.push([s,m]);s=m;}}out.push([s,b]);return out;}

/* ---- fat (screen-space width) line segments ---- */
const fatMats=[];
function fatMaterial(){const m=new THREE.ShaderMaterial({side:THREE.DoubleSide,uniforms:{uRes:{value:new THREE.Vector2(W(),Hh())},uScale:{value:1}},
  vertexShader:`uniform vec2 uRes;uniform float uScale;attribute vec3 aA;attribute vec3 aB;attribute float aSide;attribute float aEnd;attribute float aW;attribute vec3 aCol;varying vec3 vC;
  void main(){vec4 ca=projectionMatrix*modelViewMatrix*vec4(aA,1.0);vec4 cb=projectionMatrix*modelViewMatrix*vec4(aB,1.0);
   if(ca.w<=0.0||cb.w<=0.0){gl_Position=vec4(2.0,2.0,2.0,1.0);return;}
   vec2 sa=ca.xy/ca.w*uRes,sb=cb.xy/cb.w*uRes;vec2 dir=sb-sa;float L=length(dir);dir=L>1e-4?dir/L:vec2(1.0,0.0);
   vec2 n=vec2(-dir.y,dir.x);vec4 c=mix(ca,cb,aEnd);float w=aW*uScale;
   c.xy+=n*aSide*w/uRes*c.w;c.xy+=dir*(aEnd*2.0-1.0)*w*0.35/uRes*c.w;gl_Position=c;vC=aCol;}`,
  fragmentShader:`varying vec3 vC;void main(){gl_FragColor=vec4(vC,1.0);}`});fatMats.push(m);return m;}
function fatSegments(segs){ // segs: [{a:V3,b:V3,w:px,c:THREE.Color}]
  const n=segs.length,A=new Float32Array(n*12),B=new Float32Array(n*12),S=new Float32Array(n*4),E=new Float32Array(n*4),Wd=new Float32Array(n*4),C=new Float32Array(n*12),I=new Uint32Array(n*6);
  segs.forEach((s,i)=>{for(let k=0;k<4;k++){const o=(i*4+k)*3;A[o]=s.a.x;A[o+1]=s.a.y;A[o+2]=s.a.z;B[o]=s.b.x;B[o+1]=s.b.y;B[o+2]=s.b.z;
      C[o]=s.c.r;C[o+1]=s.c.g;C[o+2]=s.c.b;S[i*4+k]=(k&1)?1:-1;E[i*4+k]=k>>1;Wd[i*4+k]=s.w;}
    const v=i*4;I.set([v,v+1,v+2,v+1,v+3,v+2],i*6);});
  const g=new THREE.BufferGeometry();g.setAttribute("position",new THREE.BufferAttribute(A,3));g.setAttribute("aA",new THREE.BufferAttribute(A,3));
  g.setAttribute("aB",new THREE.BufferAttribute(B,3));g.setAttribute("aSide",new THREE.BufferAttribute(S,1));g.setAttribute("aEnd",new THREE.BufferAttribute(E,1));
  g.setAttribute("aW",new THREE.BufferAttribute(Wd,1));g.setAttribute("aCol",new THREE.BufferAttribute(C,3));g.setIndex(new THREE.BufferAttribute(I,1));
  g.boundingSphere=new THREE.Sphere(new THREE.Vector3(),1e9);
  const mesh=new THREE.Mesh(g,fatMaterial());mesh.frustumCulled=false;return mesh;}

/* ---- drill traces + collars ---- */
const traceGroup=new THREE.Group();scene.add(traceGroup);
{const segs=[],grey=new THREE.Color(0x7d8794);HO.forEach(h=>{if(h.depth>0)holeSegs(h,0,h.depth).forEach(([a,b])=>segs.push({a:at(h,a),b:at(h,b),w:1.4,c:grey}));});
 if(segs.length)traceGroup.add(fatSegments(segs));
 const cg=new THREE.BufferGeometry(),cv=[];HO.forEach(h=>{const p=at(h,0);cv.push(p.x,p.y,p.z);});
 cg.setAttribute("position",new THREE.Float32BufferAttribute(cv,3));
 const dot=document.createElement("canvas");dot.width=dot.height=32;{const x=dot.getContext("2d");x.beginPath();x.arc(16,16,12,0,Math.PI*2);x.fillStyle="#1f2833";x.fill();x.lineWidth=5;x.strokeStyle="#ffffff";x.stroke();}
 traceGroup.add(new THREE.Points(cg,new THREE.PointsMaterial({map:new THREE.CanvasTexture(dot),size:MOBILE?7:8,sizeAttenuation:false,transparent:true,alphaTest:0.3,depthWrite:false})));}

/* ---- per-element state ---- */
const ELS=Object.keys(M.elements);let EL=store.get("el:"+M.slug,M.primary);if(!M.elements[EL])EL=M.primary;
let intMesh=null,blockMesh=null,IV=[],curCut=0;
function binIdx(E,g){let k=-1;for(let i=0;i<E.bins.length;i++)if(g>=E.bins[i])k=i;return k;}
function binCol(E,k){return new THREE.Color(k<0?"#c3cad4":E.colors[k]);}
function buildIntervals(){if(intMesh){scene.remove(intMesh);intMesh.geometry.dispose();}
  const E=M.elements[EL];IV=[];const segs=[];
  E.segments.forEach(s=>{const h=HO[s[0]];if(!h)return;const k=binIdx(E,s[3]);
    holeSegs(h,s[1],s[2]).forEach(([f,t])=>{const a=at(h,f),b=at(h,t);
      IV.push({h,f:s[1],t:s[2],g:s[3],a,b,k});segs.push({a,b,w:k<0?2.6:(5.5+k*0.75),c:binCol(E,k)});});});
  intMesh=segs.length?fatSegments(segs):new THREE.Group();intMesh.visible=$("L-int").checked;scene.add(intMesh);}
/* ---- block model ---- */
let BK=null;
function buildBlocks(){if(blockMesh){scene.remove(blockMesh);blockMesh.geometry.dispose();blockMesh=null;}
  const E=M.elements[EL],B=E.blocks;BK=null;
  $("L-blocks").disabled=!B;$("blk-note").textContent=B?(fmtN(B.n_total)+" blocks"):"too few holes";
  if(!B)return;const ijk=b64(B.ijk,Uint16Array),g=b64(B.g,Float32Array),n=B.n,[bx,by,bz]=B.size,o=B.origin;
  const pos=new Float32Array(n*3);for(let i=0;i<n;i++){const p=V(o[0]+(ijk[i*3]+.5)*bx,o[1]+(ijk[i*3+1]+.5)*by,o[2]+(ijk[i*3+2]+.5)*bz);pos[i*3]=p.x;pos[i*3+1]=p.y;pos[i*3+2]=p.z;}
  // the grid itself is kept, not just the drawn positions: it is what lets a
  // drill interval be asked "is there a block here, and what grade did it get"
  BK={n,g,pos,ijk,o,size:B.size,zone:B.zone?b64(B.zone,Uint8Array):null,cell:null};
  const geo=new THREE.BoxGeometry(bx*0.97,bz*0.97,by*0.97);
  blockMesh=new THREE.InstancedMesh(geo,new THREE.MeshLambertMaterial({transparent:true,opacity:+$("blk-op").value/100,depthWrite:false}),n);
  blockMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);blockMesh.visible=$("L-blocks").checked;scene.add(blockMesh);applyCut();}
/* ---- the body the model imagined ----
   The wireframe decides which blocks may exist, so a hole in the block model has
   two very different explanations: the body never reached there, or it did and
   the estimator found nothing to put in it. Drawn over the blocks, the two are
   told apart by eye. */
/* One colour per body, shared by the envelope, the chips and the labels, so a
   zone is the same thing in the picture and in the table. Zone 0 is the ground
   that belongs to no named body — grey, and still shown. */
const ZCOL=["#8d98a7","#2f7ed8","#d94f3d","#1f9d55","#8e5bd1","#d99a00","#0f9fb0",
            "#c2447a","#6b8e23","#4a5bbd","#b5651d","#2aa198"];
function zoneColor(id){return id?ZCOL[((id-1)%(ZCOL.length-1))+1]:ZCOL[0];}

let bodyMesh=null,BD=null,surfMesh=null,SF=null,contMesh=null,CT=null;
let bodyStyle=store.get("bodystyle","shape");
function buildBody(){
  if(bodyMesh){scene.remove(bodyMesh);bodyMesh.geometry.dispose();bodyMesh.material.dispose();bodyMesh=null;}
  if(surfMesh){scene.remove(surfMesh);surfMesh.geometry.dispose();surfMesh.material.dispose();surfMesh=null;SF=null;}
  if(contMesh){scene.remove(contMesh);contMesh.geometry.dispose();contMesh.material.dispose();contMesh=null;CT=null;}
  buildSurface();
  const E=M.elements[EL],B=E&&E.body;
  const cb=$("L-body"),row=$("body-op-row"),why=$("body-why");
  if(cb)cb.disabled=!B&&!SF;
  bodyNote();
  if(row)row.style.display=(B||SF)?"":"none";
  if(why){
    if(B){why.style.display="";
      const nz=(B.zones||[]).filter(z=>z.id>0).length;
      why.innerHTML="";
      why.textContent=nz+" "+(nz===1?"body":"bodies")+", coloured to match the Bodies panel."
        +(B.reach_m?" Holes linked up to "+B.reach_m+" m apart.":"");
      // A surface needs three intercepts. Where most bodies are single
      // intersections there is almost no shape to draw, and saying so beats
      // showing an all-but-empty layer -- or, as this used to, triangulating one
      // sheet across the whole property to fill it.
      if(B.no_surface&&B.bodies&&B.no_surface>=Math.max(2,B.bodies/2)){
        const d0=document.createElement("div");
        d0.style.cssText="margin-top:4px;color:#6b7684";
        d0.textContent=B.no_surface+" of "+B.bodies+" are single intersections — one or "
          +"two holes cannot define a surface, so they are drawn as the intercepts "
          +"themselves rather than as a sheet invented through them.";
        why.appendChild(d0);}
      why.title="Blocks are only estimated inside this shape, so a gap in it is ground the "
        +"drilling could not justify closing. Grey belongs to no named body.";
      // Is the shape in the estimate? A cell of the shape with no block in it is
      // ground the model drew and then did not fill. Asked repeatedly because the
      // two layers look nothing alike on screen — and they look nothing alike
      // because the BLOCKS are filtered to the cut-off and the SHAPE is not.
      // Counted at build time against EVERY estimated block. The page only
      // carries the blocks above the display threshold, so counting here would
      // compare a whole shape against a cut-off slice — which is how a shape
      // that the estimate fills almost entirely came to read as 31% full.
      if(B.filled!=null&&B.n){
        const pc=Math.round(100*B.filled/B.n);
        const d2=document.createElement("div");
        d2.style.cssText="margin-top:4px;font-weight:700;color:"+(pc<80?"#c0392b":"#1d2530");
        d2.textContent="The estimate fills "+pc+"% of this shape ("+fmtN(B.filled)+" of "
          +fmtN(B.n)+" cells).";
        d2.title="Every estimated block, not just the ones above the cut-off slider: "
          +"the block layer on screen is filtered and this is not, which is why the "
          +"two look different.";
        why.appendChild(d2);}
      setTimeout(()=>{try{
        const R=reconcile();if(!R||!R.ore)return;
        const pc=Math.round(100*R.mod/R.ore);
        const d=document.createElement("div");
        d.style.cssText="margin-top:4px;font-weight:700;color:"+(pc<70?"#c0392b":"#1d2530");
        d.textContent="Covers "+pc+"% of the "+fmtN(Math.round(R.ore))+" m of "+EL+" the assays report.";
        d.title="Drill data shows which holes the rest is in.";
        why.appendChild(d);}catch(e){}},0);
    } else {why.style.display="none";}
  }
  BD=null;
  if(!B){bodyVis();return;}
  const ijk=b64(B.ijk,Uint16Array),[bx,by,bz]=B.size,o=B.origin,n=B.n;
  const pos=new Float32Array(n*3);
  for(let i=0;i<n;i++){const p=V(o[0]+(ijk[i*3]+.5)*bx,o[1]+(ijk[i*3+1]+.5)*by,o[2]+(ijk[i*3+2]+.5)*bz);
    pos[i*3]=p.x;pos[i*3+1]=p.y;pos[i*3+2]=p.z;}
  BD={n,pos,zone:B.zone?b64(B.zone,Uint8Array):null,zones:B.zones||[]};
  // Put each name ON its body, not at its average position. A body that bends
  // round a corner, or is two lobes with a gap, has a mean that lands in empty
  // ground — or inside a different zone — which is why the names looked like
  // they had nothing to do with the shapes they were naming. The medoid (the
  // cell nearest the mean) is always a cell of that body.
  {
    const sum={},cnt={};
    for(let i=0;i<n;i++){const z=BD.zone?BD.zone[i]:0;
      const a=sum[z]||(sum[z]=[0,0,0]);a[0]+=pos[i*3];a[1]+=pos[i*3+1];a[2]+=pos[i*3+2];
      cnt[z]=(cnt[z]||0)+1;}
    const best={};
    for(let i=0;i<n;i++){const z=BD.zone?BD.zone[i]:0,c=cnt[z],a=sum[z];
      const dx=pos[i*3]-a[0]/c,dy=pos[i*3+1]-a[1]/c,dz=pos[i*3+2]-a[2]/c;
      const d=dx*dx+dy*dy+dz*dz;
      if(!best[z]||d<best[z][0])best[z]=[d,i];}
    BD.anchor={};
    for(const z in best){const i=best[z][1];
      BD.anchor[z]=[pos[i*3],pos[i*3+1],pos[i*3+2]];}
  }
  const geo=new THREE.BoxGeometry(bx,bz,by);
  bodyMesh=new THREE.InstancedMesh(geo,new THREE.MeshLambertMaterial({
    transparent:true,opacity:+(($("body-op")||{}).value||18)/100,
    depthWrite:false,side:THREE.DoubleSide}),n);
  bodyMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  scene.add(bodyMesh);applyBody();bodyVis();}

/* ---- the body as geometry ----------------------------------------------
   The cells are the shape on the block lattice; this is the shape itself —
   the surface triangulated through the mineralised intercepts at the
   thickness each one actually shows. A vein that pinches to half a metre
   between two holes and swells to thirty in a third is what a drill section
   is for, and rounding it to the nearest block erased that in both
   directions. Pages built before this carry no surface and keep the cells,
   so nothing here may assume one exists. */
function buildSurface(){
  const E=M.elements[EL],S=E&&E.surface;
  if(!S||!S.tris)return;
  const QV=b64(S.v,Uint16Array),F=b64(S.f,S.wide?Uint32Array:Uint16Array);
  const o=S.origin,sc=S.scale,nv=S.nv;
  const pos=new Float32Array(nv*3),col=new Float32Array(nv*3);
  for(let i=0;i<nv;i++){
    const p=V(o[0]+QV[i*3]*sc[0],o[1]+QV[i*3+1]*sc[1],o[2]+QV[i*3+2]*sc[2]);
    pos[i*3]=p.x;pos[i*3+1]=p.y;pos[i*3+2]=p.z;}
  // one colour per body, matching the Bodies panel and the cell envelope
  const _c=new THREE.Color();
  for(const gp of (S.groups||[])){_c.set(zoneColor(gp.id));
    for(let k=gp.start;k<gp.start+gp.count;k++){const vi=F[k];
      col[vi*3]=_c.r;col[vi*3+1]=_c.g;col[vi*3+2]=_c.b;}}
  let P=pos,C=col,IX=F,groups=S.groups||[],vgroups=S.veins||[],mul=1;
  // Rounded off in the browser rather than in the page: four times the triangles
  // cost nothing to compute here and would quadruple what every reader downloads.
  if(!MOBILE&&S.tris<=60000){const r=refineSurface(P,C,IX);
    if(r){P=r.pos;C=r.col;IX=r.idx;mul=4;}}
  if(mul!==1){groups=groups.map(g=>({id:g.id,start:g.start*mul,count:g.count*mul}));
    vgroups=vgroups.map(g=>({id:g.id,body:g.body,start:g.start*mul,count:g.count*mul}));}
  const geo=new THREE.BufferGeometry();
  geo.setAttribute("position",new THREE.BufferAttribute(P,3));
  geo.setAttribute("color",new THREE.BufferAttribute(C,3));
  geo.setIndex(new THREE.BufferAttribute(IX,1));
  geo.computeVertexNormals();
  geo.computeBoundingSphere();
  surfMesh=new THREE.Mesh(geo,new THREE.MeshLambertMaterial({
    vertexColors:true,transparent:true,opacity:+((ctl("body-op")||{}).value||18)/100,
    depthWrite:false,side:THREE.DoubleSide}));
  surfMesh.renderOrder=2;
  scene.add(surfMesh);
  SF={tris:S.tris,groups:groups,total:IX.length,dropped:S.dropped||0,bodies:S.bodies||0,
      thick:S.thickness_m||null,method:S.method||null,
      /* A body is often several veins. They arrive as their own draw ranges,
         so showing one is a range change rather than a rebuild. A page built
         before this carries none and the vein row never appears. */
      veins:vgroups,vinfo:S.vein_info||[]};
  buildContacts(S.contacts);
  applySurface();}

/* The wall contacts the surface is fitted through: hangingwall and footwall,
   one pair per intercept. A surface on its own says nothing about where it is
   measured and where it is interpolated, and "there's no connection points"
   is exactly that complaint. Older pages carry none, so this does nothing
   rather than assuming. */
function buildContacts(C){
  if(!C||!C.n)return;
  const Q=b64(C.p,Uint16Array),o=C.origin,sc=C.scale,n=C.n;
  const pos=new Float32Array(n*3),col=new Float32Array(n*3),_c=new THREE.Color();
  for(let i=0;i<n;i++){
    const p=V(o[0]+Q[i*3]*sc[0],o[1]+Q[i*3+1]*sc[1],o[2]+Q[i*3+2]*sc[2]);
    pos[i*3]=p.x;pos[i*3+1]=p.y;pos[i*3+2]=p.z;}
  for(const gp of (C.groups||[])){_c.set(zoneColor(gp.id));
    for(let k=gp.start;k<gp.start+gp.count;k++){
      col[k*3]=_c.r;col[k*3+1]=_c.g;col[k*3+2]=_c.b;}}
  const geo=new THREE.BufferGeometry();
  geo.setAttribute("position",new THREE.BufferAttribute(pos,3));
  geo.setAttribute("color",new THREE.BufferAttribute(col,3));
  geo.computeBoundingSphere();
  contMesh=new THREE.Points(geo,new THREE.PointsMaterial({
    vertexColors:true,size:3.2,sizeAttenuation:false,depthWrite:false}));
  contMesh.renderOrder=3;
  scene.add(contMesh);
  CT={groups:C.groups||[],veins:C.veins||[],total:n};}

/* One subdivision, then the inserted points relax toward their neighbours.
   The triangulation is one face per three intercepts, so a body arrives as a
   handful of large flat panels and reads as a crystal rather than as rock.
   Splitting each face and letting only the NEW points move keeps every vertex
   the data put there exactly where it was — the shape is rounded, not
   redrawn. */
function refineSurface(pos,col,idx){
  const nv=pos.length/3,nt=idx.length/3;
  if(nv>1000000||!nt)return null;
  const mid=new Map(),K=1048576;
  const P=[],C=[];                                   // the inserted points
  const em=(a,b)=>{const k=a<b?a*K+b:b*K+a;let m=mid.get(k);
    if(m===undefined){m=nv+P.length/3;
      P.push((pos[a*3]+pos[b*3])/2,(pos[a*3+1]+pos[b*3+1])/2,(pos[a*3+2]+pos[b*3+2])/2);
      C.push((col[a*3]+col[b*3])/2,(col[a*3+1]+col[b*3+1])/2,(col[a*3+2]+col[b*3+2])/2);
      mid.set(k,m);}
    return m;};
  const F2=new Uint32Array(nt*12);
  for(let t=0;t<nt;t++){const a=idx[t*3],b=idx[t*3+1],c=idx[t*3+2];
    const ab=em(a,b),bc=em(b,c),ca=em(c,a);
    F2.set([a,ab,ca, ab,b,bc, ca,bc,c, ab,bc,ca],t*12);}
  const n2=nv+P.length/3;
  const NP=new Float32Array(n2*3),NC=new Float32Array(n2*3);
  NP.set(pos);NC.set(col);
  for(let i=0;i<P.length;i++){NP[nv*3+i]=P[i];NC[nv*3+i]=C[i];}
  const sum=new Float32Array(n2*3),cnt=new Uint32Array(n2);
  for(let it=0;it<2;it++){
    sum.fill(0);cnt.fill(0);
    for(let t=0;t<F2.length;t+=3){
      const i0=F2[t],i1=F2[t+1],i2=F2[t+2];
      const acc=(a,b)=>{sum[a*3]+=NP[b*3];sum[a*3+1]+=NP[b*3+1];sum[a*3+2]+=NP[b*3+2];cnt[a]++;};
      acc(i0,i1);acc(i1,i0);acc(i1,i2);acc(i2,i1);acc(i2,i0);acc(i0,i2);}
    for(let i=nv;i<n2;i++){const c=cnt[i];if(!c)continue;
      NP[i*3]+=0.5*(sum[i*3]/c-NP[i*3]);
      NP[i*3+1]+=0.5*(sum[i*3+1]/c-NP[i*3+1]);
      NP[i*3+2]+=0.5*(sum[i*3+2]/c-NP[i*3+2]);}}
  return {pos:NP,col:NC,idx:F2};}

/* one body on its own, the same rule the blocks and the cells follow. The
   triangles arrive grouped by body, so this is a draw range rather than a
   rebuild. */
/* One body on its own, or one VEIN of that body — the same rule the blocks and
   the cells follow, one level deeper. A vein beats a body when both are picked,
   because the vein is the narrower question. */
function applySurface(){
  const pick=(mesh,gs,vs,total)=>{
    if(!mesh)return;
    const g=(curVein!=null&&vs)?vs.find(x=>x.id===curVein)
           :(curZone?(gs||[]).find(x=>x.id===curZone):null);
    if(!curZone&&curVein==null)mesh.geometry.setDrawRange(0,total);
    else if(g)mesh.geometry.setDrawRange(g.start,g.count);
    else mesh.geometry.setDrawRange(0,0);};
  if(contMesh&&CT)pick(contMesh,CT.groups,CT.veins,CT.total);
  if(surfMesh&&SF)pick(surfMesh,SF.groups,SF.veins,SF.total);}

/* which of the two the envelope toggle shows */
function bodyNote(){
  const B=(M.elements[EL]||{}).body;
  if(!$("body-note"))return;
  // A two-metre vein across four hundred metres of strike is a few pixels, so
  // the thickness the model actually holds is stated rather than left to the eye.
  let t="";
  if(SF&&SF.thick){const k=SF.thick;
    t=" · "+(k.min===k.max?k.median+" m thick"
      :k.min+"–"+k.max+" m thick (median "+k.median+")");}
  // say which it is: a vein surface fitted through the contacts reads very
  // differently from a sheet triangulated through midpoints, and a reader is
  // entitled to know which one is on screen
  const how=(SF&&SF.method==="vein")?" · vein surfaces through the contacts":"";
  ctl("body-note").textContent=
    (SF&&bodyStyle==="shape")?(fmtN(SF.tris)+" triangles"+t+how)
      :(B?(fmtN(B.n)+" cells"):(SF?fmtN(SF.tris)+" triangles"+t+how:"not built"));}

function bodyVis(){
  // The chosen view, where the model has it; the other one where it does not,
  // so the layer is never silently empty because of which button is lit.
  const on=!!(ctl("L-body").checked);
  const shape=bodyStyle==="shape" ? !!SF : !BD;
  if(surfMesh)surfMesh.visible=on&&shape;
  if(contMesh)contMesh.visible=on&&shape&&!!(ctl("L-contacts").checked);
  if(bodyMesh)bodyMesh.visible=on&&!shape;
  const cr=$("cont-row");if(cr)cr.style.display=(on&&shape&&CT)?"":"none";
  const r=$("zlab-row");if(r)r.style.display=on?"":"none";
  // The switch is shown whenever there is an envelope at all — hiding it left a
  // reader unable to tell "this model has only one view" from "this control
  // does not exist". A view the model does not carry is disabled and says why.
  const sw=$("body-style");
  if(sw){
    sw.style.display=(SF||BD)?"":"none";
    const why={shape:SF?"":"This model has no surface: a body needs three "
                 +"intercepts to triangulate one. Showing the cells instead.",
               cells:BD?"":"This model carries no block-lattice envelope."};
    sw.querySelectorAll("[data-bs]").forEach(b=>{
      const have=b.dataset.bs==="shape"?!!SF:!!BD;
      b.disabled=!have;
      b.style.opacity=have?"":"0.45";
      b.style.cursor=have?"":"default";
      if(!have)b.title=why[b.dataset.bs];});
    const note=sw.querySelector(".bs-note");
    if(note)note.textContent=(bodyStyle==="shape"&&!SF)?why.shape
      :((bodyStyle==="cells"&&!BD)?why.cells:"");}}

/* Which of the envelope is drawn: everything, or one body when a zone is picked.
   Same rule as the blocks, so the two always show the same deposit. */
const _bm=new THREE.Matrix4(),_bc=new THREE.Color();
function applyBody(){
  if(!BD||!bodyMesh)return;let j=0;
  for(let i=0;i<BD.n;i++){
    const z=BD.zone?BD.zone[i]:0;
    if(curZone&&z!==curZone)continue;
    _bm.makeTranslation(BD.pos[i*3],BD.pos[i*3+1],BD.pos[i*3+2]);bodyMesh.setMatrixAt(j,_bm);
    _bc.set(zoneColor(z));bodyMesh.setColorAt(j,_bc);j++;}
  bodyMesh.count=j;bodyMesh.instanceMatrix.needsUpdate=true;
  if(bodyMesh.instanceColor)bodyMesh.instanceColor.needsUpdate=true;}

/* the body's name, written in the middle of it */
function zoneLabels(){
  const shown=(bodyMesh&&bodyMesh.visible)||(surfMesh&&surfMesh.visible);
  if(!BD||!shown||!BD.zones.length)return[];
  const cb=$("L-zlabels");
  // Names over a deposit are a reading aid, not a default. Nineteen of them
  // covered the model they were describing, so they are opt-in — and when one
  // body is picked, that body's name is the only one worth drawing anyway.
  if(!curZone&&!(cb&&cb.checked))return[];
  const out=[];
  const cap=curZone?BD.zones.length:(MOBILE?6:14);
  for(const z of BD.zones){
    if(curZone&&z.id!==curZone)continue;
    if(out.length>=cap)break;
    const a=(BD.anchor&&BD.anchor[z.id])||null;   // a point on the body itself
    const p=a?new THREE.Vector3(a[0],a[1],a[2])
             :(z.centre?V(z.centre[0],z.centre[1],z.centre[2]):null);
    if(!p)continue;
    const s=scr(p);
    if(s[2]>1||s[0]<0||s[1]<0||s[0]>W()||s[1]>Hh())continue;
    out.push(`<span class="zl" style="left:${s[0]}px;top:${s[1]}px;background:${zoneColor(z.id)}">`
             +esc(z.name)+`</span>`);}
  return out;}

/* ---- the drilling, against the model built from it ----
   "the drill data says we cut long intervals of mineralisation and the model is
   not reflecting it" is a checkable statement, so it is checked here rather than
   argued about: every assay interval at or above the cut-off is walked down the
   hole and asked whether the block model has a block there, and what grade that
   block carries. The blocks sit on a regular lattice, so that question is a hash
   lookup, not a search. */
function atM(h,m){        // position at depth m, in MODEL space (x east, y north, z elevation)
  const p=h.p;
  if(p&&p.length>=8){const n=p.length/4;let i=0;while(i<n-2&&p[(i+1)*4]<m)i++;
    const m0=p[i*4],m1=p[(i+1)*4],t=m1>m0?(m-m0)/(m1-m0):0;
    return [h.x+p[i*4+1]+(p[i*4+5]-p[i*4+1])*t, h.y+p[i*4+2]+(p[i*4+6]-p[i*4+2])*t,
            h.z+p[i*4+3]+(p[i*4+7]-p[i*4+3])*t];}
  return [h.x+h.d[0]*m, h.y+h.d[1]*m, h.z+h.d[2]*m];}

function blockCells(){
  if(!BK)return null;
  if(BK.cell)return BK.cell;
  const m=new Map();
  for(let i=0;i<BK.n;i++){const a=BK.ijk[i*3],b=BK.ijk[i*3+1],c=BK.ijk[i*3+2];
    m.set((a*65536+b)*65536+c,BK.g[i]);}
  BK.cell=m;return m;}

function blockAt(x,y,z){
  const C=blockCells();if(!C)return undefined;
  const [bx,by,bz]=BK.size,o=BK.o;
  const i=Math.floor((x-o[0])/bx),j=Math.floor((y-o[1])/by),k=Math.floor((z-o[2])/bz);
  if(i<0||j<0||k<0||i>65535||j>65535||k>65535)return undefined;
  return C.get((i*65536+j)*65536+k);}

/* Per interval: its length, and how much of it sits inside an estimated block.
   Sampled at half a block, which is the finest the block model can answer. */
let RECON=null;
function reconcile(){
  const E=M.elements[EL];
  const key=EL+"|"+curCut+"|"+(BK?BK.n:0);
  if(RECON&&RECON.key===key)return RECON;
  if(!BK||!IV.length){RECON={key,rows:[],byHole:{},ore:0,mod:0};return RECON;}
  const step=Math.min(...BK.size)/2,rows=[],byHole={};
  let ore=0,mod=0;
  for(const x of IV){
    if(!(x.g>=curCut))continue;
    const L=Math.max(0,(x.t||0)-(x.f||0));if(!L)continue;
    const nsteps=Math.max(1,Math.min(64,Math.ceil(L/step)));let inside=0,gsum=0,gn=0;
    // the hole is desurveyed once per interval, not once per step: over a metre
    // or two of core a surveyed path is a straight line to well under a block
    const A=atM(x.h,x.f),Bb=atM(x.h,x.t);
    for(let s=0;s<nsteps;s++){
      const u=(s+0.5)/nsteps;
      const bg=blockAt(A[0]+(Bb[0]-A[0])*u,A[1]+(Bb[1]-A[1])*u,A[2]+(Bb[2]-A[2])*u);
      if(bg!==undefined){inside++;if(bg>0){gsum+=bg;gn++;}}}
    const cov=L*inside/nsteps;
    ore+=L;mod+=cov;
    const r={id:x.h.id,f:x.f,t:x.t,len:L,g:x.g,cov,bg:gn?gsum/gn:null};
    rows.push(r);
    const b=byHole[x.h.id]||(byHole[x.h.id]={ore:0,mod:0,n:0,best:0});
    b.ore+=L;b.mod+=cov;b.n++;b.best=Math.max(b.best,x.g);}
  RECON={key,rows,byHole,ore,mod};return RECON;}

function csv(rows){return rows.map(r=>r.map(v=>{
  const s=v==null?"":String(v);
  return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;}).join(",")).join("\n");}
function download(name,text){
  const b=new Blob([text],{type:"text/csv;charset=utf-8"}),u=URL.createObjectURL(b);
  const a=document.createElement("a");a.href=u;a.download=name;document.body.appendChild(a);
  a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),2000);}

/* ---------------- how big is it, and how deep ---------------- */
function drawSize(){
  const box=$("sizebox");if(!box)return;
  const E=M.elements[EL],B=E.blocks;
  const rows=[];
  // the drilling itself (model space is metres: x east, y north, z elevation)
  if(HO.length){
    let x0=1e18,x1=-1e18,y0=1e18,y1=-1e18,z0=1e18,z1=-1e18,deep=0;
    HO.forEach(h=>{const t=toe(h);
      x0=Math.min(x0,h.x,t.ex);x1=Math.max(x1,h.x,t.ex);
      y0=Math.min(y0,h.y,t.ny);y1=Math.max(y1,h.y,t.ny);
      z0=Math.min(z0,h.z,t.ez);z1=Math.max(z1,h.z,t.ez);
      deep=Math.max(deep,h.depth||0);});
    rows.push(["Drilled area",`${m(x1-x0)} E–W × ${m(y1-y0)} N–S`]);
    rows.push(["Deepest hole",m(deep)]);
  }
  if(B&&BK){
    const [bx,by,bz]=B.size,o=B.origin,ijk=b64(B.ijk,Uint16Array);
    const X=[],Y=[],Z=[];
    for(let i=0;i<BK.n;i++){
      if(BK.g[i]<curCut)continue;
      if(curZone&&BK.zone&&BK.zone[i]!==curZone)continue;
      X.push(o[0]+ijk[i*3]*bx);Y.push(o[1]+ijk[i*3+1]*by);Z.push(o[2]+ijk[i*3+2]*bz);}
    if(X.length){
      X.sort((a,b)=>a-b);Y.sort((a,b)=>a-b);Z.sort((a,b)=>a-b);
      const q=(A,p)=>A[Math.min(A.length-1,Math.max(0,Math.round((A.length-1)*p)))];
      const surf=medianCollar();
      // the body itself: the middle 90% of blocks, so one stray outlier 20 km away
      // does not report the deposit as being 20 km wide
      const cx=q(X,.95)-q(X,.05)+bx, cy=q(Y,.95)-q(Y,.05)+by, cz=q(Z,.95)-q(Z,.05)+bz;
      const top=q(Z,.95)+bz, bot=q(Z,.05);
      rows.push(["Main zone", `${m(cx)} E–W × ${m(cy)} N–S`]);
      rows.push(["Vertical extent", m(cz)]);
      if(isFinite(surf))rows.push(["Depth below surface", `${m(Math.max(0,surf-top))} to ${m(Math.max(0,surf-bot))}`]);
      rows.push(["Elevation range", `${Math.round(bot)} to ${Math.round(top)} masl`]);
      const fx=X[X.length-1]-X[0]+bx, fy=Y[Y.length-1]-Y[0]+by;
      if(fx>cx*1.3||fy>cy*1.3)
        rows.push(["Full footprint", `${m(fx)} E–W × ${m(fy)} N–S`]);
      rows.push(["Blocks above cut-off", fmtN(X.length)+" of "+fmtN(B.n_total)
                 +(curZone?" (this zone only)":"")]);
    }
  }
  box.innerHTML=rows.map(([k,v])=>`<div class="kv"><span>${k}</span><b>${v}</b></div>`).join("")
    +`<p class="note">Extent of the blocks above the cut-off you have set — move the cut-off and these follow.</p>`;
}
function m(v){return v>=10000?(v/1000).toFixed(1)+" km":Math.round(v)+" m";}
function toe(h){const d=(h.depth||0),dip=(h.dip||-90)*Math.PI/180,az=(h.az||0)*Math.PI/180,run=Math.cos(dip)*d;
  return {ex:h.x+run*Math.sin(az),ny:h.y+run*Math.cos(az),ez:h.z+Math.sin(dip)*d};}
function medianCollar(){if(!HO.length)return NaN;const z=HO.map(h=>h.z).filter(v=>isFinite(v)).sort((a,b)=>a-b);
  return z.length?z[z.length>>1]:NaN;}

const _m=new THREE.Matrix4(),_c=new THREE.Color();
function applyCut(){if(!BK||!blockMesh)return;const E=M.elements[EL];let j=0;
  for(let i=0;i<BK.n;i++){if(BK.g[i]<curCut)continue;
    if(curZone&&BK.zone&&BK.zone[i]!==curZone)continue;_m.makeTranslation(BK.pos[i*3],BK.pos[i*3+1],BK.pos[i*3+2]);blockMesh.setMatrixAt(j,_m);
    _c.set(E.colors[Math.max(0,binIdx(E,BK.g[i]))]);blockMesh.setColorAt(j,_c);j++;}
  blockMesh.count=j;blockMesh.instanceMatrix.needsUpdate=true;if(blockMesh.instanceColor)blockMesh.instanceColor.needsUpdate=true;drawSize();
  if(typeof drawZoneBox==="function")drawZoneBox();}

/* ---- terrain ---- */
let terrain=null;
(function(){const T=M.terrain;if(!T){$("L-terrain").disabled=true;$("ter-note").textContent="n/a";$("ter-op-row").style.display="none";return;}
  const z=b64(T.z,Float32Array),nx=T.nx,ny=T.ny;const pos=new Float32Array(nx*ny*3),el=new Float32Array(nx*ny);let lo=1e9,hi=-1e9;
  for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){const k=j*nx+i,p=V(T.x0+i*T.dx,T.y0+j*T.dy,z[k]);pos[k*3]=p.x;pos[k*3+1]=p.y;pos[k*3+2]=p.z;el[k]=z[k];lo=Math.min(lo,z[k]);hi=Math.max(hi,z[k]);}
  const idx=[];for(let j=0;j<ny-1;j++)for(let i=0;i<nx-1;i++){const a=j*nx+i,b=a+1,c=a+nx,d=c+1;idx.push(a,b,c,b,d,c);}
  const g=new THREE.BufferGeometry();g.setAttribute("position",new THREE.BufferAttribute(pos,3));g.setAttribute("aEl",new THREE.BufferAttribute(el,1));
  g.setIndex(idx);g.computeVertexNormals();
  const rel=hi-lo;const ci=[5,10,20,25,50,100,200].find(v=>rel/v<=14)||250;
  $("ter-note").textContent=Math.round(rel)+" m relief";
  const mat=new THREE.ShaderMaterial({transparent:true,side:THREE.DoubleSide,depthWrite:false,
    uniforms:{uLo:{value:lo},uHi:{value:hi},uCi:{value:ci},uOp:{value:0.6},uSun:{value:sun.position.clone().normalize()}},
    extensions:{derivatives:true},
    vertexShader:`attribute float aEl;varying float vE;varying vec3 vN;void main(){vE=aEl;vN=normalize(normal);gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`,
    fragmentShader:`uniform float uLo,uHi,uCi,uOp;uniform vec3 uSun;varying float vE;varying vec3 vN;
     vec3 hyps(float t){vec3 c0=vec3(0.36,0.50,0.33),c1=vec3(0.55,0.60,0.38),c2=vec3(0.70,0.62,0.46),c3=vec3(0.62,0.55,0.50),c4=vec3(0.93,0.93,0.94);
      if(t<.3)return mix(c0,c1,t/.3);if(t<.6)return mix(c1,c2,(t-.3)/.3);if(t<.85)return mix(c2,c3,(t-.6)/.25);return mix(c3,c4,(t-.85)/.15);}
     void main(){float t=clamp((vE-uLo)/max(uHi-uLo,1.0),0.0,1.0);vec3 n=normalize(vN);float sh=clamp(dot(n,uSun),0.0,1.0);
      vec3 col=hyps(t)*(0.32+0.95*sh);float f=vE/uCi;float w=fwidth(f);float ln=(1.0-smoothstep(0.0,w*1.2,abs(fract(f-0.5)-0.5)))*(1.0-smoothstep(0.12,0.3,w));
      float f5=f/5.0;float w5=fwidth(f5);float maj=(1.0-smoothstep(0.0,w5*1.4,abs(fract(f5-0.5)-0.5)))*(1.0-smoothstep(0.15,0.35,w5));
      col=mix(col,col*0.6,max(ln*0.35,maj*0.75));
      gl_FragColor=vec4(col,uOp);}`});
  terrain=new THREE.Mesh(g,mat);terrain.renderOrder=5;scene.add(terrain);
  const on=store.get("terrain",true);$("L-terrain").checked=on;terrain.visible=on;
  const op=store.get("terop",45);$("ter-op").value=op;mat.uniforms.uOp.value=op/100;$("ter-op-v").textContent=op+"%";
  const src=(T.from==="collars")?" · from collar elevations":"";
  $("ter-note").textContent=Math.round(rel)+" m relief · "+ci+" m contours"+src;
  if(T.note)$("L-terrain").parentNode.title=T.note+
    " It is the shape of the drilled ground, not of the hills beyond it.";})();

/* ---------------- element chips, legend, grade panel ---------------- */
/* ---------------- zones: one project, body by body ---------------- */
let curZone=0,curVein=null;

/* The Bodies panel, built here when the page does not have one.

   Every page in the gallery today was baked before this panel existed, so on
   all of them the bodies could not be listed and the veins inside them could
   not be reached — the renderer ships ahead of the pages, and waiting for the
   nightly rebuild is how a client's model stayed a day behind for six days.
   Same pattern as the Targets panel: build it, wire it, let a shell that
   already has one win. */
function bodiesPanel(){
  let p=$("pZones");
  if(p&&p.querySelector){
    if(!$("veinrow")){
      const z=p.querySelector("#zones");
      if(z&&z.parentNode){const d=document.createElement("div");
        d.id="veinrow";d.className="sec";d.style.display="none";d.style.marginTop="6px";
        z.parentNode.insertBefore(d,z.nextSibling);}}
    return p;
  }
  p=document.createElement("section");
  p.id="pZones";p.className="panel min";p.style.display="none";
  p.innerHTML='<div class="phead"><h2>Bodies <small id="zone-n" '
    +'style="font-weight:400;color:var(--mut)"></small></h2>'
    +'<button class="pbtn" aria-label="Minimise">+</button></div>'
    +'<div class="pbody"><div class="sec"><div class="chips" id="zones"></div>'
    +'<div class="sec" id="veinrow" style="display:none;margin-top:6px"></div>'
    +'<div id="zonebox"></div></div></div>';
  const col=$("leftcol"),model=$("pModel"),tg=$("pTargets");
  if(col&&col.appendChild){
    if(tg&&tg.parentNode===col)col.insertBefore(p,tg);else col.appendChild(p);}
  else if(model&&model.parentNode)model.parentNode.insertBefore(p,model.nextSibling);
  else document.body.appendChild(p);
  wirePanel(p,"Bodies");
  return p;}

function drawZones(){
  const E=M.elements[EL],Z=(E&&E.zones)||[],sec=bodiesPanel();
  if(!sec||!sec.style)return;
  if(!Z.length){sec.style.display="none";curZone=0;curVein=null;return;}
  sec.style.display="";
  ctl("zone-n").textContent="· "+Z.length;
  const dot=id=>`<i style="display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;vertical-align:1px;background:${zoneColor(id)}"></i>`;
  const chip=(id,label)=>`<button class="chip${curZone===id?" on":""}" data-z="${id}">${id?dot(id):""}${label}</button>`;
  ctl("zones").innerHTML=chip(0,"Whole project")+Z.map(z=>chip(z.id,z.name)).join("");
  [...$("zones").querySelectorAll("button")].forEach(b=>b.onclick=()=>{
    const id=+b.dataset.z;curZone=(curZone===id?0:id);curVein=null;
    drawZones();applyCut();applyBody();applySurface();drawZoneBox();
    if(curZone)flyToZone(Z.find(z=>z.id===curZone));});
  drawVeins();
  drawZoneBox();
}

/* The veins the picked body is made of — or, with no body picked, the biggest
   on the property. A body is a connected lens of rock; a vein is the thing a
   geologist actually reads, and a body of nine veins used to arrive as one
   solid that could only be shown or hidden whole.

   It lists the project's veins when no body is picked because body 0 — real
   mineralisation belonging to no named body — has no chip of its own (chip 0
   is "Whole project"), and on a vein property that is routinely half the veins
   there are. Capped, because a list of every vein is the clutter this panel
   was split off to avoid. */
const VEIN_CHIP_CAP=12;
function drawVeins(){
  const box=ctl("veinrow");if(!box||!box.style)return;
  const vs=(SF&&SF.veins)||[];
  const mine=curZone?vs.filter(v=>v.body===curZone):vs.slice();
  if(mine.length<2){box.style.display="none";curVein=null;return;}
  box.style.display="";
  const info={};for(const q of ((SF&&SF.vinfo)||[]))info[q.id]=q;
  // biggest first: a one-hole lens is real, but it is not what to read first
  const sorted=mine.slice().sort((a,b)=>(b.count-a.count));
  const show=curZone?sorted:sorted.slice(0,VEIN_CHIP_CAP);
  // whichever vein is picked stays on the list even if it is not in the top few
  if(curVein!=null&&!show.some(v=>v.id===curVein)){
    const v=sorted.find(x=>x.id===curVein);if(v)show.push(v);}
  const lab=(v,n)=>{const q=info[v.id]||{};
    const bits=[];
    if(q.holes)bits.push(q.holes+(q.holes===1?" hole":" holes"));
    if(q.span_m>1)bits.push(Math.round(q.span_m)+" m");
    if(q.thick_m)bits.push(fmtN(q.thick_m,q.thick_m<10?1:0)+" m thick");
    // the vein's own attitude, where it was found as a plane through the
    // contacts rather than assumed from the shape of the drilled ground
    if(q.dip!=null)bits.push(Math.round(q.dip)+"° / "+String(Math.round(q.dip_dir)).padStart(3,"0"));
    return `Vein ${n}${bits.length?` <small style="opacity:.72">· ${bits.join(" · ")}</small>`:""}`;};
  const head=curZone?`Veins in this body · ${mine.length}`
    :(mine.length>VEIN_CHIP_CAP?`Veins · ${mine.length}, ${VEIN_CHIP_CAP} largest`
                               :`Veins · ${mine.length}`);
  box.innerHTML=`<p class="lab">${head}</p><div class="chips">`
    +`<button class="chip${curVein==null?" on":""}" data-v="" title="Every vein `
    +`${curZone?"in this body":"on the property"}">All ${mine.length}</button>`
    +show.map((v,i)=>`<button class="chip${curVein===v.id?" on":""}" data-v="${v.id}" `
      +`title="Drawn on its own, with the wall contacts it was fitted through">`
      +`${lab(v,i+1)}</button>`).join("")
    +`</div>`;
  [...box.querySelectorAll("button")].forEach(b=>b.onclick=()=>{
    const raw=b.dataset.v;
    const id=raw===""?null:+raw;
    curVein=(curVein===id?null:id);
    drawVeins();applySurface();});
}
function drawZoneBox(){
  const E=M.elements[EL],Z=(E&&E.zones)||[],box=$("zonebox");if(!box)return;
  const rows=[];
  const at=z=>{const s=(z.series||[]).find(r=>r.cutoff>=curCut)||(z.series||[])[0]||{};return s;};
  for(const z of Z){
    const s=at(z);
    const on=curZone===z.id;
    rows.push(`<div class="kv"${on?' style="font-weight:700"':''}><span><i style="display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;background:${zoneColor(z.id)}"></i>${z.name}
      <small>${z.holes} holes · ${m(z.span_m[0])} × ${m(z.span_m[1])}</small></span>
      <b>${s.tonnes?fmtT(s.tonnes):"–"}${s.grade!=null?" @ "+fmtG(s.grade,E.unit):""}</b></div>`);
  }
  box.innerHTML=rows.join("");
}
function flyToZone(z){
  if(!z||!z.bbox)return;
  const [lo,hi]=z.bbox,cx=(lo[0]+hi[0])/2,cy=(lo[1]+hi[1])/2,cz=(lo[2]+hi[2])/2;
  const span=Math.max(hi[0]-lo[0],hi[1]-lo[1],hi[2]-lo[2],60);
  const p=V(cx,cy,cz);
  fly({x:p.x,y:p.y,z:p.z,dist:span*2.2},600);
}
function drawEls(){const box=$("els");box.innerHTML="";if(ELS.length<2){$("elsec").style.display="none";}
  ELS.forEach(e=>{const b=document.createElement("button");b.className="chip"+(e===EL?" on":"");b.textContent=e;b.onclick=()=>setEl(e);box.appendChild(b);});}
function drawLegend(){const E=M.elements[EL];$("lg-h").textContent=EL+" grade ("+E.unit+")";const L=$("legend");L.innerHTML="";
  for(let k=E.bins.length-1;k>=0;k--){const d=document.createElement("div");const hi=k<E.bins.length-1?E.bins[k+1]:null;
    d.innerHTML=`<i style="background:${E.colors[k]}"></i>${hi==null?"≥ "+E.bins[k]:E.bins[k]+" – "+hi} ${esc(E.unit)}`;L.appendChild(d);}
  const d=document.createElement("div");d.innerHTML=`<i class="lo"></i>&lt; ${E.bins[0]} ${esc(E.unit)} · <span style="color:var(--mut)">grey = no reported intercept</span>`;L.appendChild(d);}
function setEl(e){EL=e;store.set("el:"+M.slug,e);curZone=0;curVein=null;drawEls();buildIntervals();drawLegend();setupGT();buildBlocks();buildBody();drawData();drawZones();drawCard();buildTargets();}

function setupGT(){const E=M.elements[EL];const cut=$("cut");
  if(!E.gt){$("gtsec").hidden=true;$("nogt").hidden=false;
    $("nogt-msg").textContent="Not enough assayed holes with "+EL+" to interpolate a block model yet — the drill intervals are shown, coloured by grade. The model fills in as more holes are reported.";
    curCut=0;drawParams();drawRes();return;}
  $("gtsec").hidden=false;$("nogt").hidden=true;cut.max=E.gt.length-1;
  const def=store.get("cut:"+M.slug+":"+EL,null);let i0=E.gt.findIndex(r=>r[0]>=(E.bins[1]!=null?E.bins[1]:E.bins[0]));if(i0<0)i0=0;
  cut.value=def!=null&&def<E.gt.length?def:i0;updCut();drawParams();drawRes();}
function updCut(){const E=M.elements[EL];if(!E.gt)return;const r=E.gt[+$("cut").value];curCut=r[0];store.set("cut:"+M.slug+":"+EL,+$("cut").value);
  $("cut-v").textContent="≥ "+fmtG(r[0],E.unit);$("g-t").textContent=fmtT(r[1]);$("g-g").textContent=r[1]?fmtG(r[2],E.unit):"–";
  $("g-c").textContent=fmtC(r[3],E.contained_unit);$("g-cu").textContent="Contained "+({oz:"oz",lb:"lb",t:"t"}[E.contained_unit]||"");
  applyCut();drawChart(r);drawCmp(r);drawBreakdown();reconLater();}

/* The reconciliation walks every reported interval, so it follows the cut-off
   slider at rest rather than on every tick of it. */
let _reconT=null;
function reconLater(){clearTimeout(_reconT);_reconT=setTimeout(()=>{
  const f=$("hfilter");if(typeof drawData==="function")drawData();
  if(f&&f.value)f.dispatchEvent(new Event("input"));},260);}
function pubFor(el){el=el||EL;const E=M.elements[el];const cu=E.contained_unit;
  // the statement's grade unit comes from its own arithmetic (tonnes x grade =
  // contained), so a per-cent statement can never be read against a g/t model
  const gu=String(E.unit||"").toLowerCase()==="%"?"%":"g/t";
  return (M.resources||[]).filter(r=>r.contained_unit===cu&&r.contained>0&&
    (!r.grade_unit||r.grade_unit===gu)&&
    (!r.element||r.element===el||r.element===el.replace("Eq","")));}
/* which modelled element a published statement we hold does line up with */
function pubElsewhere(){const out=[];(M.elements?Object.keys(M.elements):[]).forEach(e=>{if(e!==EL&&pubFor(e).length)out.push(e);});return out;}
function drawChart(cur){const E=M.elements[EL],svg=$("gtchart"),G=E.gt;const maxC=Math.max(...G.map(r=>r[3]),...pubFor().map(r=>r.contained||0),1);
  const maxX=G[G.length-1][0]||1;const X=v=>6+v/maxX*288,Y=v=>104-v/maxC*96;
  let p="";G.forEach((r,i)=>{p+=(i?"L":"M")+X(r[0]).toFixed(1)+","+Y(r[3]).toFixed(1);});
  let s=`<path d="${p}" fill="none" stroke="#D71920" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  pubFor().slice(0,1).forEach(r=>{s+=`<line x1="6" x2="294" y1="${Y(r.contained)}" y2="${Y(r.contained)}" stroke="#274b8f" stroke-dasharray="4 3" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;});
  s+=`<line x1="${X(cur[0])}" x2="${X(cur[0])}" y1="4" y2="106" stroke="#111418" stroke-width="1" vector-effect="non-scaling-stroke" opacity=".5"/>`;
  s+=`<circle cx="${X(cur[0])}" cy="${Y(cur[3])}" r="3.5" fill="#D71920"/>`;
  svg.innerHTML=s;svg.setAttribute("aria-label","Contained metal versus cut-off grade");}
/* the cut-off a published statement is reported at, in our units when comparable */
function pubCut(p){const E=M.elements[EL];if(!p||p.cutoff==null)return null;
  const u=String(p.cutoff_unit||"").toLowerCase(),mu=String(E.unit||"").toLowerCase();
  if(u===mu)return{v:+p.cutoff,unit:p.cutoff_unit,same:true};
  if(u==="ppm"&&mu==="g/t")return{v:+p.cutoff,unit:"g/t",same:true};
  if(u==="g/t"&&mu==="ppm")return{v:+p.cutoff,unit:"ppm",same:true};
  return{v:+p.cutoff,unit:p.cutoff_unit,same:false};}      // NSR $/t etc: not a grade
function setCut(v){const E=M.elements[EL];if(!E.gt)return;let i=E.gt.findIndex(r=>r[0]>=v);
  if(i<0)i=E.gt.length-1;$("cut").value=i;updCut();}
function drawCmp(cur){const E=M.elements[EL],box=$("cmp"),pubs=pubFor();
  if(!pubs.length){const other=pubElsewhere();
    box.innerHTML=`<p class="note">Red line: contained ${EL} vs cut-off. `+
      (other.length?`The published statement we extracted from the filings is for <b>${other.map(esc).join(", ")}</b>, not ${esc(EL)} — `+
        other.map(e=>`<button class="mini" data-el="${esc(e)}">compare ${esc(e)}</button>`).join(" ")+
        ` — so there is nothing to compare ${esc(EL)} against here.`
       :`No published resource for ${esc(EL)} has been matched to this project yet${(M.resources||[]).length?"; the statement below is in different units":""}.`)+`</p>`;
    box.querySelectorAll("button[data-el]").forEach(b=>b.onclick=()=>setEl(b.dataset.el));
    return;}
  const p=pubs[0],mx=Math.max(p.contained,cur[3]),ratio=cur[3]/p.contained;
  const pc=pubCut(p);
  let cutline="",verdict="";
  if(pc&&pc.same){
    const match=Math.abs(cur[0]-pc.v)<1e-9||(cur[0]>=pc.v&&E.gt[Math.max(0,+$("cut").value-1)][0]<pc.v);
    cutline=`<div class="kv"><span>Published cut-off</span><b>${fmtG(pc.v,pc.unit)}${match?"":` <button class="mini" id="matchcut">use it</button>`}</b></div>`;
    verdict=match?`Compared <b>at the published cut-off</b> (${fmtG(pc.v,pc.unit)}), so this is like for like.`
                 :`Your cut-off is ${fmtG(cur[0],E.unit)}; the published statement is at ${fmtG(pc.v,pc.unit)} — set them equal before reading anything into the gap.`;
  }else if(pc){
    cutline=`<div class="kv"><span>Published cut-off</span><b>${(+pc.v).toLocaleString()} ${esc(pc.unit)}</b></div>`;
    verdict=`The published statement is reported at a <b>value cut-off</b> (${(+pc.v).toLocaleString()} ${esc(pc.unit)}), which depends on metal prices, recoveries and costs — a grade model cannot reproduce it exactly. Compare the shape and order of magnitude, not the totals.`;
  }else{
    verdict=`The report's cut-off for this statement was not captured, so the two numbers are not necessarily at the same cut-off.`;
  }
  box.innerHTML=`<p class="lab" style="margin-top:8px">Check against published resource</p>
   <div class="kv"><span>MMP at this cut-off</span><b>${fmtC(cur[3],E.contained_unit)}</b></div><div class="cmpbar"><i style="width:${(cur[3]/mx*100).toFixed(1)}%"></i></div>
   <div class="kv"><span>Published M+I+Inf</span><b>${fmtC(p.contained,p.contained_unit)}</b></div><div class="cmpbar pub"><i style="width:${(p.contained/mx*100).toFixed(1)}%"></i></div>
   ${cutline}
   <p class="note">MMP is <b>${(ratio*100).toFixed(0)}%</b> of the published total. ${verdict} ${ratio<0.5?"Below: we may hold only part of the drilling behind the published estimate (see data sources), or interpolation stays close to the holes.":ratio>1.5?"Above: a lower effective cut-off, wider interpolation than the resource wireframes, or composite intercepts reported at a low threshold.":"Broadly consistent in size with the published estimate."} Dashed blue line on the chart = published total.</p>`;
  const mb=$("matchcut");if(mb)mb.onclick=()=>setCut(pc.v);}
/* ---- one deposit, one tonnage -------------------------------------------
   A deposit is one body of rock. Estimating each metal on its own blocks gave
   each metal its own tonnage, which cannot be right: the rock does not change
   between metals, only the grade in it does — and whether that metal was
   assayed there at all. So the table below fixes the tonnage from the primary
   metal's blocks and reports every metal on it. "Assayed over" is the share of
   that tonnage where the metal was actually measured; a metal sampled in a
   third of the holes cannot honestly claim the whole deposit. */
let BMCUT=null,BMUSER=false,BMBASIS=null;
/* which basis the one-tonnage table is cut on. A deposit carrying several metals of
   value is reported in dollars per tonne, not on one metal's grade — cutting Murray
   Brook at 0.1% Cu returned 4.8 Mt where its own NSR cut-off returns 22.7 Mt against
   a published 23.1 Mt, because copper is a third of that rock's value. */
function bmView(){const B=M.by_metal;if(!B)return null;
  const want=BMBASIS||(B.value?"value":"grade");
  return (want==="value"&&B.value)?B.value:B;}
function bmIsValue(){const B=M.by_metal;return !!(B&&B.value&&(BMBASIS||"value")==="value");}
function fmtCut(V,c){return bmIsValue()?`${V.currency||"$"}${fmtN(c,c<10?1:0)}/t`
  :`${fmtG(c,M.by_metal.basis_unit||"g/t")} ${M.by_metal.basis}`;}
/* a published statement MMP can be read against: same cut-off kind, same units */
function pubAtCut(){const V=bmView();if(!V)return null;
  return (M.resources||[]).find(r=>r.cutoff!=null&&r.tonnes>0&&
    (bmIsValue()? /\/t/.test(String(r.cutoff_unit||"")) : String(r.cutoff_unit||"")===(M.by_metal.basis_unit||"")));}
function bmDefault(){const B=bmView();if(!B)return 0;
  if(bmIsValue()){const i=B.cuts.indexOf(B.cutoff);if(i>0)return i;return B.cuts.length>1?1:0;}
  const E=M.elements[M.by_metal.basis]||{},all=B.tonnes[0]||0;
  const near=w=>{let best=-1,bd=Infinity;B.cuts.forEach((c,i)=>{const d=Math.abs(c-w);
    if(c>0&&d<bd){bd=d;best=i;}});return best;};
  // a reporting cut-off the model barely reaches tells the reader nothing, so it is
  // only the default when real tonnage survives it
  for(const w of [E.default_cutoff,(E.bins||[])[1],(E.bins||[])[0]]){
    if(w==null)continue;const i=near(+w);
    if(i>0&&(!all||B.tonnes[i]>=0.02*all))return i;}
  return B.cuts.length>1?1:0;}
function drawBreakdown(){
  const box=$("bdown");if(!box)return;
  const B=M.by_metal,V=bmView();
  if(!V||!V.cuts||!V.cuts.length){return drawBreakdownLegacy(box);}
  if(BMCUT==null||BMCUT>=V.cuts.length)BMCUT=bmDefault();
  const val=bmIsValue();
  // on the basis metal the table follows the cut-off slider, so the number on
  // screen and the number in the table are the same number — until the reader
  // picks a cut-off here, after which this panel stays put
  if(!val&&!BMUSER&&EL===B.basis&&curCut!=null){
    let j=0;V.cuts.forEach((c,i)=>{if(c<=curCut+1e-9)j=i;});BMCUT=j;}
  const i=BMCUT,cut=V.cuts[i],T=V.tonnes[i];
  const order=(V.order||Object.keys(V.metals||{}));
  const rows=order.map(e=>{const r=V.metals[e];if(!r)return"";
    const g=r.grade[i],c=r.contained[i],cov=r.coverage[i]||0,tn=r.tonnes[i];
    const covtxt=cov>=0.995?"all of it":Math.round(cov*100)+"% of it";
    const cls=cov<0.25?"n thin":"n";
    const tip=cov>=0.995?"assayed throughout":fmtT(tn)+" of the "+fmtT(T)+" carries a "+e+" assay close enough to estimate";
    const unpriced=val&&r.priced===false;
    return `<tr data-el="${esc(e)}"${e===EL?' class="on"':''}>`+
      `<td${unpriced?' title="carried in the rock but not paid for in this formula"':''}>${esc(e)}`+
      `${e===B.basis&&!val?' <em>basis</em>':''}${unpriced?' <em>*</em>':''}</td>`+
      `<td class="n">${g==null?"–":fmtG(g,r.unit)}</td>`+
      `<td class="n"><b>${c==null?"–":fmtC(c,r.contained_unit)}</b></td>`+
      `<td class="${cls}" title="${esc(tip)}">${covtxt}</td></tr>`;}).join("");
  const btns=V.cuts.map((c,j)=>`<button class="mini${j===i?" on":""}" data-bm="${j}">`+
    (c>0?"≥ "+fmtCut(V,c):"all blocks")+`</button>`).join(" ");
  const toggle=(B.value?`<div class="bdcuts" style="margin-bottom:2px">`+
      `<button class="mini${val?" on":""}" data-basis="value">cut on value (${esc(B.value.currency||"$")}/t)</button>`+
      `<button class="mini${val?"":" on"}" data-basis="grade">cut on ${esc(B.basis)} grade</button></div>`:"");
  // the tiers, and the published statement when it is cut the same way
  const K=V.classes;
  const tiers=K?`<table class="bd" style="margin-top:6px"><tbody>`+
    Object.keys(K).map(k=>`<tr><td style="cursor:default">${esc(k)}</td><td class="n" style="cursor:default">${fmtT(K[k][i])}</td></tr>`).join("")+
    `</tbody></table><p class="note">MMP's own confidence tiers — how many holes and samples reached each block —
      not CIM categories assigned by a qualified person.</p>`:"";
  const p=pubAtCut();
  let cmp="";
  if(p&&p.cutoff!=null){
    const same=Math.abs(p.cutoff-cut)<Math.max(0.02*Math.max(p.cutoff,1),0.001);
    const pt=p.mi_tonnes||p.tonnes,ratio=pt?T/pt:0;
    const at=/\/t/.test(String(p.cutoff_unit||""))?`${(+p.cutoff).toLocaleString()} ${esc(p.cutoff_unit)}`:fmtG(+p.cutoff,p.cutoff_unit);
    cmp=`<div class="bdhead" style="margin-top:8px">Published at the same cut-off</div>`+
      `<div class="kv"><span>Filed statement · ${esc(at)}</span><b>${fmtT(pt)}</b></div>`+
      `<div class="kv"><span>MMP at ${esc(fmtCut(V,cut))}</span><b>${fmtT(T)}</b></div>`+
      `<p class="note">MMP is <b>${(ratio*100).toFixed(0)}%</b> of the filed tonnage.`+
      (same?"":` <button class="mini" id="matchnsr">match their cut-off</button>`)+
      ` Same rock, same cut-off, independently interpolated from the public drilling.</p>`;
  }
  box.innerHTML=toggle+
    `<div class="bdhead"><b>${fmtT(T)}</b> of rock`+
      (cut>0?` at ≥ ${esc(fmtCut(V,cut))}`:` (every estimated block)`)+
      ` · ${V.density||B.density||"–"} t/m³</div>`+
    `<div class="bdcuts">${btns}</div>`+
    `<table class="bd"><colgroup><col style="width:24%"><col style="width:22%"><col style="width:28%"><col style="width:26%"></colgroup>`+
    `<thead><tr><th>Metal</th><th class="n">Grade</th><th class="n">Contained</th><th class="n">Assayed over</th></tr></thead>`+
    `<tbody>${rows}</tbody></table>`+
    `<p class="note">One deposit, one tonnage. `+
      (val?`The cut-off is a <b>value</b> cut-off: ${esc(V.formula||"")}${V.approximate?
        " — indicative only, from metal prices less typical recovery, payables and treatment charges, because no report for this project states a formula"
        :", the formula this project's technical report states"}. A deposit carrying
        several metals is reported in dollars per tonne, because cutting it on one
        metal's grade throws away rock the others pay for.`
      :`The cut-off above is on <b>${esc(B.basis)}</b>, and every metal is estimated at
        those same blocks, so the rock is counted once.`)+
      ` "Assayed over" says how much of that tonnage each metal was actually measured across.
      Inverse-distance estimates for visualisation, not a resource estimate.</p>`+
    (val&&order.some(e=>V.metals[e]&&V.metals[e].priced===false)
      ? `<p class="note">* in the rock, but paid nothing in this formula — the report prices only
         ${esc(Object.keys(V.factors||{}).join(", "))}.</p>` : "")+
    tiers+methodSplit()+cmp+trackRecord();
  box.querySelectorAll("button[data-bm]").forEach(b=>b.onclick=()=>{BMCUT=+b.dataset.bm;BMUSER=true;drawBreakdown();});
  box.querySelectorAll("button[data-basis]").forEach(b=>b.onclick=()=>{
    BMBASIS=b.dataset.basis;BMCUT=null;BMUSER=false;drawBreakdown();});
  const mn=$("matchnsr");
  if(mn&&p)mn.onclick=()=>{let best=0,bd=Infinity;
    bmView().cuts.forEach((c,j)=>{const d=Math.abs(c-p.cutoff);if(d<bd){bd=d;best=j;}});
    BMCUT=best;BMUSER=true;drawBreakdown();};
  box.querySelectorAll("tr[data-el]").forEach(tr=>tr.onclick=()=>{if(M.elements[tr.dataset.el])setEl(tr.dataset.el);});
}
/* Open pit or underground — how the tonnage would actually be presented.
   A tonne near surface and a tonne at depth are not mined for the same money, so a
   company reports them separately at different cut-offs and so does this. */
function methodSplit(){const B=M.by_metal,S=B&&B.method;if(!S||!S.open_pit)return"";
  const cur=(B.value||{}).currency||"$";
  const row=(k,lbl)=>{const r=S[k];if(!r)return"";
    const cut=S.basis==="value"?`${cur}${fmtN(r.cutoff,r.cutoff<10?1:0)}/t`:fmtG(r.cutoff,B.basis_unit||"g/t");
    const mets=(B.order||[]).filter(e=>r.metals&&r.metals[e]).slice(0,5)
      .map(e=>`${esc(e)} ${fmtG(r.metals[e].grade,r.metals[e].unit)}`).join(" · ");
    return `<tr><td style="cursor:default">${lbl}</td><td class="n" style="cursor:default">≥ ${esc(cut)}</td>`+
      `<td class="n" style="cursor:default"><b>${fmtT(r.tonnes)}</b></td></tr>`+
      (mets?`<tr><td colspan="3" class="note" style="padding:0 4px 6px;cursor:default">${mets}</td></tr>`:"");};
  return `<div class="bdhead" style="margin-top:10px">How it would be mined</div>`+
    `<table class="bd"><colgroup><col style="width:34%"><col style="width:30%"><col style="width:36%"></colgroup>`+
    `<tbody>${row("open_pit","Open pit")}${row("underground","Underground")}</tbody></table>`+
    `<p class="note">${esc(S.note||"")}</p>`;}
/* What the rock is and what you would have to do to it to get the metal out.
   Two deposits of the same grade are not the same asset if one heap leaches and the
   other is refractory and needs an autoclave. */
function metallurgy(){const T=M.metallurgy;if(!T||!T.summary)return"";
  const rec=T.recoveries||{};
  const rows=Object.keys(rec).sort((a,b)=>rec[b]-rec[a])
    .map(e=>`<div class="kv"><span>${esc(e)} recovery</span><b>${rec[e]}%</b></div>`).join("");
  return `<p class="lab" style="margin-top:10px">Ore and processing</p>`+
    `<p class="note" style="margin-top:2px">${esc(T.summary[0].toUpperCase()+T.summary.slice(1))}.</p>`+
    rows+`<p class="note">As stated in this project's technical report. Recoveries are
      what the report assumes, not what a plant has achieved.</p>`;}
/* Anything implausible that survived the repairs, said out loud. A model that is
   wrong and says so is worth more than one that is wrong and does not. */
function integrity(){const F=M.integrity;if(!F||!F.length)return"";
  const bad=F.filter(f=>f.level==="bad"), other=F.filter(f=>f.level!=="bad");
  let h="";
  if(bad.length)h+=`<div class="warn"><b>This model has a problem.</b><ul>`+
    bad.map(f=>`<li>${esc(f.message)}</li>`).join("")+
    `</ul><span>Treat its grades and tonnages as unreliable until this is fixed.</span></div>`;
  if(other.length)h+=`<div class="fixnote"><b>Repaired on the way in:</b><ul>`+
    other.map(f=>`<li>${esc(f.message)}</li>`).join("")+`</ul></div>`;
  return h;}
/* What this method is worth where the answer is known.

   A model of a deposit with no filed resource cannot be checked against anything,
   so the only honest basis for trusting it is how the same pipeline scores on the
   deposits that do have one. That record is measured by scripts/validate.py over
   every project whose filings carry a statement, at each statement's own cut-off,
   and is stamped into every model. It is a small sample and is quoted as one. */
function trackRecord(){const R=M.track_record;if(!R||!R.n)return"";
  const pub=(M.resources||[]).some(r=>r.tonnes>0);
  const t=R.tiers;
  return `<div class="bdhead" style="margin-top:8px">How far off is MMP, where we can check?</div>`+
    `<div class="kv"><span>Deposits with a filed statement</span><b>${R.n}</b></div>`+
    `<div class="kv"><span>Median MMP ÷ filed tonnage</span><b>${R.median_ratio}×</b></div>`+
    `<div class="kv"><span>Within 1.5× of filed</span><b>${R.within_1_5x} of ${R.n}</b></div>`+
    (R.covered_n?`<div class="kv"><span>Where we hold the whole deposit</span><b>${R.covered_median}× · ${R.covered_within_1_5x} of ${R.covered_n} within 1.5×</b></div>`:"")+
    (R.mine?`<div class="kv"><span>Models with ${esc(R.mine.band)}</span><b>${R.mine.median}× · middle half ${R.mine.q1}–${R.mine.q3}×</b></div>`
            +`<div class="kv"><span>…measured on</span><b>${R.mine.n} such project${R.mine.n===1?"":"s"}, ${R.mine.within_2x} within 2×</b></div>`:"")+
    (t?`<div class="kv"><span>Filed measured + indicated</span><b>${Math.round(t.filed_mi_share*100)}% of tonnes</b></div>`+
       `<div class="kv"><span>MMP's top two tiers</span><b>${Math.round(t.mmp_top2_share*100)}% of tonnes</b></div>`:"")+
    `<p class="note">${pub?"This project has a filed statement, compared above."
      :"<b>This deposit has no filed resource</b>, so nothing here can be checked against one directly."}
      The figures above are how the same pipeline scores across every project whose filings do carry a statement,
      each at its own cut-off.
      ${R.covered_n?`The second figure is the same test restricted to the projects whose own reports describe a body
      this model reaches most of. A filed statement rests on a company's whole drill database and a technical report
      publishes a summary of it, so where we hold a fraction of the drilling the gap measures what is missing from the
      data rather than how well the method interpolates.`:""}
      ${t?`Our tiers stay deliberately short of the filed categories — we hold a fraction of the
      assay data a qualified person had — so read the top tier as "well supported by the public drilling", not as measured.`:""}
      ${R.mine?`<b>This model is built on ${R.mine_holes} assayed hole${R.mine_holes===1?"":"s"}</b>, which puts it in the band shown above —
      read that band, not the overall figure. Three things that look as though they should matter were tested and do not:
      the block size against the vein width, the zero-fill margin, and removing the fill altogether all moved the median
      and left the error where it was. What moves it is whether the filings published the drilling.`:""}
      A small sample, and an estimate of an estimate.</p>`;}
/* models built before the one-tonnage pass: each metal on its own blocks */
function drawBreakdownLegacy(box){
  const els=Object.keys(M.elements||{}).filter(e=>M.elements[e].gt&&M.elements[e].gt.length);
  if(els.length<1){box.innerHTML="";return;}
  const rows=els.map(e=>{const E=M.elements[e];
    const saved=store.get("cut:"+M.slug+":"+e,null);
    let i=saved!=null&&saved<E.gt.length?+saved:E.gt.findIndex(r=>r[0]>=(E.bins[1]!=null?E.bins[1]:E.bins[0]));
    if(i<0)i=0;const r=E.gt[i];const p=pubFor(e)[0];
    return `<tr data-el="${esc(e)}"${e===EL?' class="on"':''}><td>${esc(e)}</td><td class="n">≥ ${fmtG(r[0],E.unit)}</td>`+
      `<td class="n">${fmtT(r[1])}</td><td class="n">${fmtG(r[2],E.unit)}</td><td class="n"><b>${fmtC(r[3],E.contained_unit)}</b></td>`+
      `<td class="n">${p?fmtC(p.contained,p.contained_unit):"–"}</td></tr>`;}).join("");
  box.innerHTML=`<table class="bd"><colgroup><col style="width:13%"><col style="width:19%"><col style="width:17%"><col style="width:17%"><col style="width:19%"><col style="width:15%"></colgroup>`+
    `<thead><tr><th>Metal</th><th class="n">Cut-off</th><th class="n">Tonnes</th>`+
    `<th class="n">Grade</th><th class="n">MMP</th><th class="n">Pub.</th></tr></thead><tbody>${rows}</tbody></table>`+
    `<p class="note">Each metal here is estimated on its own blocks, so the tonnages differ between rows. Rebuilt models report one deposit tonnage. Estimates, not a resource.</p>`;
  box.querySelectorAll("tr[data-el]").forEach(tr=>tr.onclick=()=>setEl(tr.dataset.el));
}
function drawRes(){const box=$("res"),R=M.resources||[];if(!R.length){$("ressec").style.display="none";return;}
  box.innerHTML=R.map(r=>{const c=r.categories||{},u=r.contained_unit,gu=r.grade_unit||(u==="oz"?"g/t":"%");
    const rows=Object.entries(c).filter(([k])=>["Measured","Indicated","M+I","Inferred"].includes(k)).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${fmtT(v.tonnes)}</td><td>${v.grade} ${gu}</td><td>${fmtC(v.contained,u)}</td></tr>`).join("");
    const cu2=r.cutoff!=null?`<div class="note">reported at ${String(r.cutoff_unit||"").match(/\/t/)?(+r.cutoff).toLocaleString()+" "+esc(r.cutoff_unit):fmtG(+r.cutoff,r.cutoff_unit)} cut-off</div>`:"";
    const who=r.element?`<div class="note">reported for <b>${esc(r.element)}</b></div>`
      :`<div class="note">the filed table does not name the metal unambiguously — shown as published, and not compared with the model</div>`;
    return `<div class="res"><div class="kv" style="padding:0"><span>${esc(r.title||r.source)}</span></div>${who}${cu2}
      <table>${rows}<tr style="border-top:1px solid var(--line)"><td><b>Total</b></td><td><b>${fmtT(r.tonnes)}</b></td><td></td><td><b>${fmtC(r.contained,u)}</b></td></tr></table>
      ${bestUrl(r)?`<a href="${esc(bestUrl(r))}" target="_blank" rel="noopener">open report ↗</a>`:""}${r.page?` <span class="note">· p.${r.page}</span>`:""}</div>`;}).join("");}
function wireAdopt(){const b=$("adopt");if(!b)return;
  b.onclick=()=>{const u=new URL("../app/adopt.html",location.href);
    u.searchParams.set("slug",M.slug);u.searchParams.set("name",M.project||"");
    if(M.company)u.searchParams.set("company",M.company);location.href=u.toString();};}
function drawParams(){const E=M.elements[EL],B=E.blocks,box=$("params");
  if(!B){box.innerHTML=`<p class="note">No block model for ${EL} (needs ≥4 assayed holes).</p>`;return;}const q=B.params;
  const ST=M.style||{};
  box.innerHTML=[["Deposit style",ST.label?ST.label+(ST.confidence==="report"?"":" *"):"—"],
    ["Block size",B.size.map(v=>+v.toFixed(2)).join(" × ")+" m"],["Block size from",q.block_source],["Estimator","Inverse distance²"],
    ["Composites",q.composite_m+" m (gaps between intercepts = 0)"],["Drill spacing",q.spacing_m+" m"],["Max distance to data",q.max_gap_m+" m"],
    ["Search radius",q.radius_m+" m, ≥"+(q.min_holes||2)+" holes"],["Top-cut",fmtG(q.top_cut,E.unit)],["Density",B.density+" t/m³"],["Density from",q.density_source||"—"],
    ["Mineralisation carried",(ST.halo_m!=null?ST.halo_m+" m past each intercept":"—")
      +(ST.median_intercept_m?" (intercepts median "+ST.median_intercept_m+" m)":"")],
    ["Search shape",q.anisotropy?("along the body "+q.anisotropy[0]+" · across "+q.anisotropy[2]):"isotropic"],
    ["Unsampled hole length",q.gap_fill===false?"not composited":"composited at zero grade"],["Blocks estimated",fmtN(B.n_total)]]
    .map(([k,v])=>`<div class="kv"><span>${k}</span><b>${esc(v)}</b></div>`).join("")
    +(function(){const C=E.check;if(!C)return"";
       const bits=[];
       if(C.bias_pct!=null)bits.push(`<div class="kv"><span>Bias vs nearest-neighbour</span><b>${C.bias_pct>0?"+":""}${C.bias_pct}%</b></div>`);
       if(C.dilution_pct!=null)bits.push(`<div class="kv"><span>Grade lost to compositing</span><b>${C.dilution_pct}%</b></div>`);
       return bits.length?bits.join("")+`<p class="note">Two checks that need no published resource to be useful.
         Inverse distance smooths and nearest neighbour does not, so a large gap between them means this model is
         smoothing rather than interpolating. The second is what filling un-assayed ground at zero grade costs:
         it bounds the deposit, and this is the price.</p>`:"";})()
    +(ST.label?`<p class="note">${ST.why?esc(ST.why[0].toUpperCase()+ST.why.slice(1))+". ":""}${ST.note?esc(ST.note)+" ":""}
       The style sets the density, how far mineralisation is carried from an intercept and how big a block may be —
       a narrow vein and a massive sulphide lens are not the same rock and cannot be modelled as though they were.
       ${ST.confidence==="report"?"":"<b>*</b> read from the drilling, not stated in a report."}</p>`:"")
    +((q.custom&&q.custom.length)?`<p class="note">Set for this project: ${q.custom.map(esc).join(", ")}.</p>`:"")
    +(M.owner_project?"":`<p class="note" style="margin-top:8px"><button class="mini" id="adopt">Adjust these parameters</button> — opens your own private copy of this model in Studio, where block size, search radius, estimator, top-cut, density and cut-off are editable and the model rebuilds.</p>`)
    +(q.coarsened>1?`<p class="note">Display grid coarsened ×${q.coarsened} (very large extent).</p>`:"");
  wireAdopt();}
/* SEDAR+ hands out session-scoped document URLs that expire to its home page, so the
   archived PDF is the link that actually opens the filing. */
function stale(u){return /sedarplus\.ca\/csa-party\/viewInstance|drmKey=|sedar\.com\/GetFile/i.test(String(u||""));}
function bestUrl(o){const u=o&&o.url,a=o&&o.archive;return (!u||stale(u))&&a?a:(u||a||"");}
function drawSources(){const S=M.sources||[],L=$("srclist");$("src-n").textContent="· "+S.length;
  L.innerHTML=S.map(s=>{const href=bestUrl(s),pdf=href&&href===s.archive;
    const extra=pdf&&s.url&&!stale(s.url)?` · <a href="${esc(s.url)}" target="_blank" rel="noopener">listing</a>`:"";
    return `<a class="${s.kind==="news release"?"":"rep"}" href="${esc(href||"#")}" target="_blank" rel="noopener">${esc(s.title||s.kind)}<br><em>${esc(s.kind)}${s.date?" · "+esc(String(s.date).slice(0,10)):""}${s.intervals!=null?" · "+s.collars+" collars, "+s.intervals+" intervals":""}${pdf?" · opens the filed PDF":""}${extra}</em></a>`;}).join("");}
/* ---- how much of the deposit this model reaches -------------------------
   The measured driver of MMP's error is reach, not grade: across the projects
   with a filed statement, the error tracks the share of the deposit's volume the
   model covers at all. A report describes its body in prose — "traced over
   1,200 m of strike, to 450 m depth, true widths of 2 to 15 m" — and that is a
   slab we can compare the model against WITHOUT a filed tonnage, which is the
   case this site exists for. Reported, never applied: it says how much of the
   deposit the estimate is of, and changes no estimate. */
function coverNote(){const C=M.coverage;if(!C||!C.stated_text)return"";
  const mo=C.model||{},r=C.volume_ratio,st=C.stated_tonnes;
  const pct=r!=null?Math.round(r*100):null;
  return `<div class="kv"><span>Body described in the report</span><b>${esc(C.stated_text)}</b></div>`
   +(st?`<div class="kv"><span>That shape would hold</span><b>${fmtN(Math.round(st/1e6))} Mt</b></div>`:"")
   +(mo.strike_m?`<div class="kv"><span>This model spans</span><b>${fmtN(Math.round(mo.strike_m))} m × ${fmtN(Math.round(mo.depth_m))} m deep</b></div>`:"")
   +(pct!=null?`<div class="kv"><span>Share of that volume modelled</span><b>${pct<1?"<1":pct}%</b></div>`:"")
   +`<p class="note">${pct!=null&&pct<25
      ?`The filings we hold place only part of this deposit in 3D, so the block model covers roughly ${pct<1?"under one":pct} per cent of the body the report describes. Read the grades, not the totals.`
      :pct!=null&&pct>160
      ?`The model reaches beyond the body the report describes, so some estimated ground may be outside the mineralised envelope.`
      :`Broadly the extent the report describes.`} The dimensions are read from the report's own words and the implied tonnage is a slab of that shape at this model's density — an upper bound on one lens, not a resource.</p>`;}
/* ---------------- what drill data this model is built on ---------------- */
function drawData(){
  const C=M.counts||{},box=$("datasum");
  const assayed=C.assayed_holes||0,holes=C.holes||0;
  const rows=[["Holes placed in 3D",fmtN(holes)],
              ["Holes with reported assays",fmtN(assayed)+(holes?` · ${Math.round(assayed/holes*100)}%`:"")],
              ["Assay intervals",fmtN(C.intervals)],
              ["Holes with downhole surveys",fmtN(C.surveyed||0)],
              ["Blocks estimated",fmtN(C.blocks)],
              ["Sources",fmtN((M.sources||[]).length)]];
  box.innerHTML=rows.map(([k,v])=>`<div class="kv"><span>${k}</span><b>${v}</b></div>`).join("")
    +coverNote()
    +(holes&&assayed/holes<0.6?`<p class="note">Only ${fmtN(assayed)} of ${fmtN(holes)} holes carry assay intervals in the filings we hold — technical reports usually publish a <em>significant intercepts</em> table rather than the full assay file. The block model is therefore estimated around those holes only; the rest are drawn as traces so you can see where the drilling actually is.</p>`:"");
  // every hole, searchable, click to fly to it
  const byHole={};IV.forEach(x=>{const k=x.h.id;(byHole[k]=byHole[k]||[]).push(x);});
  const draw=(q)=>{const list=HO.filter(h=>!q||h.id.toLowerCase().includes(q));
    $("hole-n").textContent="· "+fmtN(HO.length)+(q?" · "+fmtN(list.length)+" shown":"");
    const unit=M.elements[EL].unit;
    const R=reconcile();
    $("holetbl").innerHTML=`<table><colgroup><col style="width:30%"><col style="width:14%"><col style="width:13%"><col style="width:19%"><col style="width:12%"><col style="width:12%"></colgroup>`+
      `<thead><tr><th>Hole</th><th class="n">Depth</th><th class="n">Ivs</th><th class="n">Best ${esc(EL)}</th>`+
      `<th class="n" title="metres of assay interval at or above the current cut-off">Ore m</th>`+
      `<th class="n" title="how much of that length the block model actually covers">In model</th></tr></thead><tbody>`+
      list.map(h=>{const iv=byHole[h.id]||[];const best=iv.length?Math.max(...iv.map(x=>x.g)):null;
        const b=R.byHole[h.id];
        const pc=b&&b.ore>0?b.mod/b.ore:null;
        // a hole that cut ore and has no model around it is the thing worth seeing
        const bad=b&&b.ore>0&&pc<0.5;
        return `<tr data-h="${esc(h.id)}"${bad?' style="background:#fff5f4"':''}><td title="${esc(h.id)}">${esc(h.id)}</td>`+
               `<td class="n">${h.depth?Math.round(h.depth)+" m":"–"}</td>`+
               `<td class="n">${iv.length||"–"}</td><td class="n">${best!=null?fmtG(best,unit):"–"}</td>`+
               `<td class="n">${b&&b.ore?b.ore.toFixed(1):"–"}</td>`+
               `<td class="n"${bad?' style="color:#c0392b;font-weight:700"':''}>${pc==null?"–":Math.round(pc*100)+"%"}</td></tr>`;}).join("")+
      `</tbody></table>`+(list.length?"":`<p class="note" style="padding:6px 8px">No hole matches “${esc(q)}”.</p>`);
    reconNote(R);
    $("holetbl").querySelectorAll("tr[data-h]").forEach(tr=>tr.onclick=()=>{
      const h=HO.find(x=>x.id===tr.dataset.h);if(!h)return;
      const mid=at(h,(h.depth||0)/2),L=Math.max(h.depth||100,120);
      fly({x:mid.x,y:mid.y,z:mid.z,dist:L*1.8},600);select(h);toast("Showing "+h.id);
      if(MOBILE)document.querySelectorAll(".panel").forEach(x=>x.classList.remove("open"));});};
  draw("");
  const f=$("hfilter");if(f)f.oninput=e=>draw(e.target.value.trim().toLowerCase());

  const unit=M.elements[EL].unit;
  const dl=(id,fn)=>{const b=$(id);if(b)b.onclick=fn;};
  dl("dl-holes",()=>{const R=reconcile();
    const rows=[["hole","x_local_m","y_local_m","elevation_m","latitude","longitude",
                 "azimuth","dip","depth_m",
                 "intervals","best_"+EL+"_"+unit,"ore_m_at_cutoff","modelled_m","modelled_pc"]];
    HO.forEach(h=>{const iv=byHole[h.id]||[],b=R.byHole[h.id];const [la,lo]=ll(h.x,h.y);
      rows.push([h.id,h.x,h.y,h.z,la,lo,h.az,h.dip,h.depth,iv.length,
                 iv.length?Math.max(...iv.map(x=>x.g)):"",
                 b?b.ore.toFixed(2):"",b?b.mod.toFixed(2):"",
                 b&&b.ore>0?(100*b.mod/b.ore).toFixed(1):""]);});
    download(M.slug+"-holes.csv",csv(rows));});
  // the model works in metres recentred on the drilling, so a bare x/y would read
  // like an easting and not be one. Local metres are labelled as such, and where
  // the model is georeferenced the real latitude and longitude go beside them.
  const F=M.frame||{},geo=F.lat0!=null&&F.lon0!=null;
  const ll=(x,y)=>{if(!geo)return["",""];
    const la=F.lat0+y/111320,lo=F.lon0+x/(111320*Math.cos(F.lat0*Math.PI/180)||1);
    return [la.toFixed(6),lo.toFixed(6)];};
  dl("dl-ivs",()=>{
    const rows=[["hole","from_m","to_m","length_m","element","grade","unit",
                 "x_local_m","y_local_m","elevation_m","latitude","longitude",
                 "in_model","block_grade"]];
    const C=blockCells();
    IV.forEach(x=>{const md=(x.f+x.t)/2,P=atM(x.h,md),bg=C?blockAt(P[0],P[1],P[2]):undefined;
      const [la,lo]=ll(P[0],P[1]);
      rows.push([x.h.id,x.f,x.t,(x.t-x.f).toFixed(2),EL,x.g,unit,
                 P[0].toFixed(2),P[1].toFixed(2),P[2].toFixed(2),la,lo,
                 bg===undefined?"no":"yes",bg===undefined?"":(+bg).toFixed(4)]);});
    download(M.slug+"-"+EL+"-intervals.csv",csv(rows));});
  dl("dl-miss",()=>{const R=reconcile();
    const rows=[["hole","from_m","to_m","length_m","element","grade","unit",
                 "modelled_m","modelled_pc","mean_block_grade"]];
    R.rows.filter(r=>r.len>0&&r.cov<r.len*0.5).sort((a,b)=>b.len-a.len).forEach(r=>
      rows.push([r.id,r.f,r.t,r.len.toFixed(2),EL,r.g,unit,r.cov.toFixed(2),
                 (100*r.cov/r.len).toFixed(1),r.bg==null?"":r.bg.toFixed(4)]));
    download(M.slug+"-"+EL+"-unmodelled.csv",csv(rows));});
}

/* ---- the intervals themselves, in the page ----
   A CSV is for taking the data away; this is for looking at it here. Every assay
   interval the model was given, what the block model put at that spot, and which
   ones it put nothing at — filtered, and narrowed to one hole by clicking it in
   the table above. */
let selHole=null,ivFilter="cut";
function drawIvs(){
  const box=$("ivtbl");if(!box)return;
  const E=M.elements[EL],unit=E.unit,R=reconcile();
  const cov={};R.rows.forEach(r=>{(cov[r.id]=cov[r.id]||{})[r.f+"|"+r.t]=r;});
  const C=blockCells();
  let rows=IV;
  if(selHole)rows=rows.filter(x=>x.h.id===selHole);
  if(ivFilter==="cut")rows=rows.filter(x=>x.g>=curCut);
  else if(ivFilter==="out")rows=rows.filter(x=>{const r=(cov[x.h.id]||{})[x.f+"|"+x.t];
    return x.g>=curCut&&r&&r.cov<r.len*0.5;});
  const LIM=400,shown=rows.slice(0,LIM);
  ctl("iv-n").textContent="· "+fmtN(rows.length)+(rows.length>LIM?" — first "+LIM+" shown":"")
    +(selHole?" · "+selHole:"");
  if(!shown.length){box.innerHTML=`<p class="note" style="padding:8px">No interval matches.</p>`;
    ctl("ivnote").textContent="";return;}
  box.innerHTML=`<table><colgroup><col style="width:26%"><col style="width:14%"><col style="width:14%"><col style="width:12%"><col style="width:17%"><col style="width:17%"></colgroup>`+
    `<thead><tr><th>Hole</th><th class="n">From</th><th class="n">To</th><th class="n">Length</th>`+
    `<th class="n">${esc(EL)}</th><th class="n" title="the grade the block model put at this interval">Block</th></tr></thead><tbody>`+
    shown.map(x=>{const md=(x.f+x.t)/2,P=atM(x.h,md),bg=C?blockAt(P[0],P[1],P[2]):undefined;
      const out=x.g>=curCut&&bg===undefined;
      return `<tr data-h="${esc(x.h.id)}"${out?' class="out"':''}><td title="${esc(x.h.id)}">${esc(x.h.id)}</td>`+
             `<td class="n">${(+x.f).toFixed(1)}</td><td class="n">${(+x.t).toFixed(1)}</td>`+
             `<td class="n">${(x.t-x.f).toFixed(2)}</td>`+
             `<td class="n">${fmtG(x.g,unit)}</td>`+
             `<td class="n"${out?' style="color:#c0392b"':''}>${bg===undefined?"none":fmtG(bg,unit)}</td></tr>`;}).join("")+
    `</tbody></table>`;
  box.querySelectorAll("tr[data-h]").forEach(tr=>tr.onclick=()=>{
    const h=HO.find(x=>x.id===tr.dataset.h);if(!h)return;
    const mid=at(h,(h.depth||0)/2),L=Math.max(h.depth||100,120);
    fly({x:mid.x,y:mid.y,z:mid.z,dist:L*1.8},600);select(h);toast("Showing "+h.id);});
  const miss=shown.filter(x=>{const r=(cov[x.h.id]||{})[x.f+"|"+x.t];return r&&r.cov<r.len*0.5;}).length;
  ctl("ivnote").textContent = ivFilter==="all"
    ? "Every interval the model was given, including the ones below the cut-off. “Block” is the grade the block model holds at that interval's own position."
    : (miss? miss+" of these sit mostly outside the estimated blocks (shaded). Click a hole above to narrow this to that hole."
           : "Every interval here sits inside the model. Click a hole above to narrow this to that hole.");}

/* ---- does the model match the drilling? ----
   Three comparisons that need no published resource. The one that matters for a
   narrow vein is the third: a block is as wide as the block, not as wide as the
   vein, so a half-metre of 1,000 g/t becomes five metres of 100 — and it is the
   CONTAINED METAL that survives that, not the grade and not the tonnage. */
function drawCheck(){
  const box=$("checkbox");if(!box)return;
  const E=M.elements[EL],C=E.check,B=E.blocks,unit=E.unit;
  if(!C||!B){box.innerHTML="";ctl("checknote").textContent=
    "This check is reported for the deposit's primary metal.";return;}
  const bs=B.size||[0,0,0],bmin=Math.min(...bs);
  const lens=IV.map(x=>x.t-x.f).filter(v=>v>0).sort((a,b)=>a-b);
  const medw=lens.length?lens[lens.length>>1]:null;
  const ratio=medw?bmin/medw:null;
  const row=(k,v,t)=>`<div class="kv"${t?` title="${esc(t)}"`:""}><span>${k}</span><b>${v}</b></div>`;
  box.innerHTML=
    row("Mean assay grade", fmtG(C.assay_mean,unit), "every assay interval, unweighted")
   +row("After compositing", fmtG(C.composite_mean,unit)
        +(C.dilution_pct!=null?` <span style="color:var(--mut);font-weight:400">(−${C.dilution_pct}%)</span>`:""),
        "the same metal spread over regular composite lengths, including barren ground between intercepts")
   +row("Model vs nearest neighbour",
        (C.bias_pct>0?"+":"")+C.bias_pct+"%",
        "inverse distance smooths, nearest neighbour does not; a large gap means this model is smoothing rather than interpolating")
   +(medw?row("Block vs typical intercept",
        bmin+" m vs "+medw.toFixed(2)+" m"
        +(ratio>1.5?` <span style="color:var(--red);font-weight:700">(${ratio.toFixed(0)}×)</span>`:""),
        "how much wider a block is than the intercepts that fill it"):"");
  ctl("checknote").innerHTML = !medw ? "" :
    (ratio>1.5
      ? `A block is <b>${ratio.toFixed(0)}×</b> wider than a typical intercept here, so the model cannot
         show the body at its true width: ${medw.toFixed(2)} m of ${fmtG(C.assay_mean,unit)} is carried as
         ${bmin} m of something far lower. <b>Contained metal is what survives that trade</b> — grade and
         tonnage separately do not, and neither should be read as what a mine would see. Set
         <code>true_width_m</code> and a smaller block in the model settings to narrow it.`
      : `Blocks are about the size of the intercepts that fill them, so grade and tonnage can be
         read more or less as they stand.`);}

/* The one line that answers "does the model reflect the drilling". */
function reconNote(R){
  const box=$("reconnote");if(!box)return;
  const E=M.elements[EL],unit=E.unit;
  if(!R||!R.ore){box.textContent="";return;}
  const pc=100*R.mod/R.ore;
  const miss=R.rows.filter(r=>r.cov<r.len*0.5);
  const missM=miss.reduce((a,r)=>a+(r.len-r.cov),0);
  box.innerHTML=`At the current cut-off the assays report <b>${fmtN(Math.round(R.ore))} m</b> of `
    +`${esc(EL)} at or above ${fmtG(curCut,unit)}, and the block model covers `
    +`<b>${Math.round(pc)}%</b> of it (${fmtN(Math.round(R.mod))} m). `
    +(miss.length
       ? `<b>${fmtN(miss.length)}</b> interval${miss.length===1?"":"s"} — ${fmtN(Math.round(missM))} m — `
         +`sit mostly outside the estimated blocks; those holes are shaded, and the CSV below lists them longest first. `
         +`An interval outside the model is usually an isolated intercept the wireframe could not link to another hole.`
       : `Every reported interval sits inside the model.`);}
$("cut").addEventListener("input",updCut);

/* ---------------- camera + controls ---------------- */
const target=new THREE.Vector3();let dist=1000,yaw=-0.75,pitch=0.62,R0=500;
function place(){pitch=Math.max(-1.5,Math.min(1.553,pitch));const cp=Math.cos(pitch);
  camera.position.set(target.x+dist*cp*Math.sin(yaw),target.y+dist*Math.sin(pitch),target.z+dist*cp*Math.cos(yaw));
  camera.up.set(0,1,0);camera.lookAt(target);camera.near=Math.max(0.5,dist/2000);camera.far=dist*60+R0*20;camera.updateProjectionMatrix();}
function framing(){ // focus on the densest part of the drilling, not the whole footprint
  let pts=[];IV.forEach(v=>{pts.push(v.a.clone().lerp(v.b,0.5));});if(pts.length<4)HO.forEach(h=>{pts.push(at(h,(h.depth||0)/2));});
  if(!pts.length)return{c:new THREE.Vector3(),r:200};
  const step=Math.max(1,Math.floor(pts.length/500)),S=pts.filter((_,i)=>i%step===0);
  // scale: median nearest-neighbour spacing between samples
  let best=S[0],bn=-1;const rad=(()=>{const d=[];for(let i=0;i<Math.min(S.length,200);i++){let m=1e18;for(let j=0;j<S.length;j++)if(i!==j)m=Math.min(m,S[i].distanceToSquared(S[j]));d.push(Math.sqrt(m));}
    d.sort((a,b)=>a-b);return Math.max(80,(d[d.length>>1]||50)*8);})();
  for(const p of S){let n=0;for(const q of S)if(p.distanceToSquared(q)<rad*rad)n++;if(n>bn){bn=n;best=p;}}
  const near=pts.filter(p=>p.distanceTo(best)<rad*6);const c=new THREE.Vector3();near.forEach(p=>c.add(p));c.multiplyScalar(1/near.length);
  const ds=near.map(p=>p.distanceTo(c)).sort((a,b)=>a-b);const r=Math.max(ds[Math.floor(ds.length*0.9)]||100,70)*1.15;
  const all=pts.map(p=>p.distanceTo(c)).sort((a,b)=>a-b);R0=Math.max(r,all[all.length-1]||r);
  return{c,r};}
let F=null;
function terrainTop(x,z,r){if(!terrain)return -1e9;const P=terrain.geometry.attributes.position.array;let m=-1e9;
  for(let i=0;i<P.length;i+=3){const dx=P[i]-x,dz=P[i+2]-z;if(dx*dx+dz*dz<r*r*0.5&&P[i+1]>m)m=P[i+1];}return m;}
function view(kind,anim){if(!F)F=framing();const fov=camera.fov*Math.PI/180;const fit=F.r/Math.sin(Math.min(fov,fov*camera.aspect)/2)*1.02;
  const to={x:F.c.x,y:F.c.y,z:F.c.z,dist:fit,yaw:-0.75,pitch:0.62};
  if(kind==="plan"){to.pitch=1.553;to.yaw=0;}else if(kind==="ns"){to.pitch=0.0;to.yaw=Math.PI/2;}else if(kind==="ew"){to.pitch=0.0;to.yaw=0;}
  else if(terrain&&terrain.visible){ // keep the 3D camera above the ground surface
    const top=terrainTop(to.x,to.z,to.dist);const need=(top+0.12*to.dist-to.y)/to.dist;
    if(need>Math.sin(to.pitch)){if(need<0.93)to.pitch=Math.asin(need);else{to.pitch=1.2;to.dist=Math.max(to.dist,(top-to.y)/0.8);}}}
  fly(to,anim?600:0);}
let flyAnim=null;
function fly(to,ms){const from={x:target.x,y:target.y,z:target.z,dist,yaw,pitch};
  if(!ms){target.set(to.x,to.y,to.z);dist=to.dist!=null?to.dist:dist;if(to.yaw!=null)yaw=to.yaw;if(to.pitch!=null)pitch=to.pitch;place();return;}
  let dy=(to.yaw!=null?to.yaw:yaw)-from.yaw;dy=((dy+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI;
  const t0=performance.now();flyAnim=()=>{let t=Math.min(1,(performance.now()-t0)/ms);const e=t<.5?2*t*t:1-Math.pow(-2*t+2,2)/2;
    target.set(from.x+(to.x-from.x)*e,from.y+(to.y-from.y)*e,from.z+(to.z-from.z)*e);
    if(to.dist!=null)dist=Math.exp(Math.log(from.dist)+(Math.log(to.dist)-Math.log(from.dist))*e);
    yaw=from.yaw+dy*e;if(to.pitch!=null)pitch=from.pitch+(to.pitch-from.pitch)*e;place();if(t>=1)flyAnim=null;};}
$("v-3d").onclick=()=>view("3d",1);$("v-plan").onclick=()=>view("plan",1);$("v-ns").onclick=()=>view("ns",1);$("v-ew").onclick=()=>view("ew",1);

/* picking: nearest interval / hole / block / terrain under a screen point */
const ray=new THREE.Raycaster();const _v=new THREE.Vector3();
function scr(p){_v.copy(p).project(camera);return[(_v.x+1)/2*W(),(1-_v.y)/2*Hh(),_v.z];}
function segDist(px,py,a,b){const dx=b[0]-a[0],dy=b[1]-a[1];const L=dx*dx+dy*dy;let t=L?((px-a[0])*dx+(py-a[1])*dy)/L:0;t=Math.max(0,Math.min(1,t));
  const x=a[0]+t*dx,y=a[1]+t*dy;return[Math.hypot(px-x,py-y),t];}
function pickInterval(px,py,tol){let best=null,bd=tol;if(!intMesh||!intMesh.visible)return null;
  for(const v of IV){const a=scr(v.a),b=scr(v.b);if(a[2]>1||b[2]>1)continue;const[d,t]=segDist(px,py,a,b);if(d<bd){bd=d;best={v,t};}}return best;}
function pickHole(px,py,tol){let best=null,bd=tol;if(!traceGroup.visible)return null;
  for(const h of HO){const a=scr(at(h,0)),b=scr(at(h,h.depth||0));if(a[2]>1||b[2]>1)continue;const[d,t]=segDist(px,py,a,b);if(d<bd){bd=d;best={h,t};}}return best;}
function pick3D(px,py){const iv=pickInterval(px,py,14);if(iv)return iv.v.a.clone().lerp(iv.v.b,iv.t);
  const hh=pickHole(px,py,12);if(hh)return at(hh.h,(hh.h.depth||0)*hh.t);
  ray.setFromCamera({x:px/W()*2-1,y:-(py/Hh())*2+1},camera);const objs=[];if(blockMesh&&blockMesh.visible)objs.push(blockMesh);if(terrain&&terrain.visible)objs.push(terrain);
  const hit=ray.intersectObjects(objs,false)[0];if(hit)return hit.point;
  const pl=new THREE.Plane(new THREE.Vector3(0,1,0),-target.y),o=new THREE.Vector3();return ray.ray.intersectPlane(pl,o)?o:null;}
function setPivot(px,py){const p=pick3D(px,py);if(!p){toast("Nothing under the cursor");return;}
  const off=camera.position.clone().sub(p);const nd=Math.max(Math.min(off.length(),dist),R0*0.02);
  fly({x:p.x,y:p.y,z:p.z,dist:nd},450);flashPivot(px,py);toast("Pivot set — orbit now turns around this point");}
function flashPivot(px,py){const e=$("pivot");e.style.left=px+"px";e.style.top=(py+host.getBoundingClientRect().top)+"px";e.style.opacity=1;clearTimeout(e._t);e._t=setTimeout(()=>e.style.opacity=0,900);}

const el=renderer.domElement;let drag=null,lx=0,ly=0,auto=false,dn=null;
let navMode=store.get("nav","orbit");                     // left-drag behaviour: orbit | pan
let spaceDown=false, vel={yaw:0,pitch:0,px:0,py:0}, grab=null;
function orbit(dx,dy){yaw-=dx*0.0055;pitch+=dy*0.0055;place();}
/* screen-space pan (fallback and for low camera angles) */
function pan(dx,dy){const s=2*dist*Math.tan(camera.fov*Math.PI/360)/Hh();
  const right=new THREE.Vector3().setFromMatrixColumn(camera.matrix,0);
  const up=new THREE.Vector3().setFromMatrixColumn(camera.matrix,1);
  target.addScaledVector(right,-dx*s).addScaledVector(up,dy*s);place();}
/* "grab the ground": the point you press stays under the cursor while you drag */
function rayAt(px,py){ray.setFromCamera({x:px/W()*2-1,y:-(py/Hh())*2+1},camera);return ray.ray;}
function grabStart(px,py){const p=pick3D(px,py);
  if(!p||Math.abs(Math.sin(pitch))<0.35){grab=null;return;}            // shallow view: screen pan
  grab={p:p.clone(),plane:new THREE.Plane(new THREE.Vector3(0,1,0),-p.y)};}
function grabMove(px,py,dx,dy){
  if(!grab){pan(dx,dy);return;}
  const o=new THREE.Vector3();
  if(!rayAt(px,py).intersectPlane(grab.plane,o)){pan(dx,dy);return;}
  const d=new THREE.Vector3().subVectors(grab.p,o);
  const lim=dist*2;if(d.length()>lim)d.setLength(lim);                  // no wild jumps near the horizon
  target.add(d);place();}
function zoomAt(f,px,py){ // dolly toward the point under the cursor
  const nd=Math.max(R0*0.002,Math.min(R0*40,dist*f));const p=(px!=null)?pickZoomPoint(px,py):null;
  if(p){const k=1-nd/dist;target.lerp(p,k*0.9);}dist=nd;place();}
function pickZoomPoint(px,py){const pl=new THREE.Plane().setFromNormalAndCoplanarPoint(
  camera.getWorldDirection(new THREE.Vector3()).negate(),target);const o=new THREE.Vector3();
  return rayAt(px,py).intersectPlane(pl,o)?o:null;}
function setNav(m){navMode=m;store.set("nav",m);
  document.querySelectorAll("#nav .nb").forEach(b=>b.classList.toggle("on",b.dataset.nav===m));
  el.style.cursor=m==="pan"?"grab":"default";}

/* ---- mouse / pen ---- */
el.addEventListener("pointerdown",e=>{if(e.pointerType==="touch")return;el.setPointerCapture(e.pointerId);
  const wantPan=(e.button===2||e.button===1||e.shiftKey||spaceDown)!==(navMode==="pan");
  drag=wantPan?"pan":"orbit";lx=e.clientX;ly=e.clientY;flyAnim=null;hideTip();vel={yaw:0,pitch:0,px:0,py:0};
  dn=[e.clientX,e.clientY,performance.now()];
  if(drag==="pan"){const r=el.getBoundingClientRect();grabStart(e.clientX-r.left,e.clientY-r.top);el.style.cursor="grabbing";}});
el.addEventListener("pointermove",e=>{if(e.pointerType==="touch")return;const r=el.getBoundingClientRect();
  if(!drag){hoverAt(e.clientX-r.left,e.clientY-r.top,e.clientX,e.clientY);return;}
  const dx=e.clientX-lx,dy=e.clientY-ly;lx=e.clientX;ly=e.clientY;
  if(drag==="orbit"){orbit(dx,dy);vel.yaw=-dx*0.0055;vel.pitch=dy*0.0055;}
  else{const bx=target.x,by=target.y,bz=target.z;grabMove(e.clientX-r.left,e.clientY-r.top,dx,dy);
       vel.px=target.x-bx;vel.py=target.y-by;vel.pz=target.z-bz;}});
function endDrag(){drag=null;grab=null;el.style.cursor=navMode==="pan"?"grab":"default";}
el.addEventListener("pointerup",endDrag);el.addEventListener("pointercancel",endDrag);
/* a press that neither moved nor lingered is a click: select the hole under it */
el.addEventListener("pointerup",e=>{if(e.pointerType==="touch"||!dn)return;
  const moved=Math.hypot(e.clientX-dn[0],e.clientY-dn[1]),held=performance.now()-dn[2];dn=null;
  if(moved>5||held>700||e.button!==0)return;
  const r=el.getBoundingClientRect();clickAt(e.clientX-r.left,e.clientY-r.top);});
el.addEventListener("pointercancel",()=>{dn=null;});
el.addEventListener("wheel",e=>{e.preventDefault();const r=el.getBoundingClientRect();
  if(e.ctrlKey||e.metaKey||!e.shiftKey){const f=Math.exp(Math.sign(e.deltaY)*Math.min(Math.abs(e.deltaY),120)*0.0018);
    zoomAt(f,e.clientX-r.left,e.clientY-r.top);}
  else{pan(-e.deltaX,-e.deltaY);}                                     // shift+wheel / trackpad swipe = pan
  },{passive:false});
el.addEventListener("dblclick",e=>{const r=el.getBoundingClientRect();setPivot(e.clientX-r.left,e.clientY-r.top);});
el.addEventListener("contextmenu",e=>e.preventDefault());

/* ---- keyboard: arrows/WASD pan, Q/E up-down, +/- zoom, R reset, P pan mode ---- */
addEventListener("keydown",e=>{
  if(/input|textarea|select/i.test((e.target.tagName||""))) return;
  if(e.code==="Space"){spaceDown=true;el.style.cursor="grab";e.preventDefault();return;}
  const step=(e.shiftKey?0.22:0.08)*Hh();
  const k=e.key.toLowerCase();let used=true;
  if(k==="arrowleft"||k==="a")pan(-step,0);
  else if(k==="arrowright"||k==="d")pan(step,0);
  else if(k==="arrowup"||k==="w")pan(0,-step);
  else if(k==="arrowdown"||k==="s")pan(0,step);
  else if(k==="q"){target.y+=dist*0.08;place();}
  else if(k==="e"){target.y-=dist*0.08;place();}
  else if(k==="+"||k==="=")zoomAt(0.82);
  else if(k==="-"||k==="_")zoomAt(1.22);
  else if(k==="r")view("3d",1);
  else if(k==="p")setNav(navMode==="pan"?"orbit":"pan");
  else used=false;
  if(used)e.preventDefault();});
addEventListener("keyup",e=>{if(e.code==="Space"){spaceDown=false;el.style.cursor=navMode==="pan"?"grab":"default";}});

/* ---- touch: one finger = current mode, two fingers = pan + pinch ---- */
let T0=null,lastTap=0,tapStart=null;
function tinfo(ts){const r=el.getBoundingClientRect();if(ts.length===1)return{x:ts[0].clientX-r.left,y:ts[0].clientY-r.top,d:0};
  const x=(ts[0].clientX+ts[1].clientX)/2-r.left,y=(ts[0].clientY+ts[1].clientY)/2-r.top;return{x,y,d:Math.hypot(ts[0].clientX-ts[1].clientX,ts[0].clientY-ts[1].clientY)};}
el.addEventListener("touchstart",e=>{e.preventDefault();flyAnim=null;T0=tinfo(e.touches);vel={yaw:0,pitch:0,px:0,py:0};
  if(e.touches.length===1){tapStart={x:T0.x,y:T0.y,t:performance.now()};if(navMode==="pan")grabStart(T0.x,T0.y);}
  else{tapStart=null;grabStart(T0.x,T0.y);}},{passive:false});
el.addEventListener("touchmove",e=>{e.preventDefault();const t=tinfo(e.touches);if(!T0)return;
  if(e.touches.length===1&&T0.d===0){
    if(navMode==="pan")grabMove(t.x,t.y,t.x-T0.x,t.y-T0.y);
    else{orbit(t.x-T0.x,t.y-T0.y);vel.yaw=-(t.x-T0.x)*0.0055;vel.pitch=(t.y-T0.y)*0.0055;}}
  else if(e.touches.length===2&&T0.d>0){grabMove(t.x,t.y,t.x-T0.x,t.y-T0.y);if(t.d>10)zoomAt(T0.d/t.d,t.x,t.y);}
  if(tapStart&&Math.hypot(t.x-tapStart.x,t.y-tapStart.y)>8)tapStart=null;T0=t;},{passive:false});
el.addEventListener("touchend",e=>{e.preventDefault();grab=null;if(e.touches.length){T0=tinfo(e.touches);return;}
  if(tapStart&&performance.now()-tapStart.t<280){const now=performance.now();
    if(now-lastTap<320){setPivot(tapStart.x,tapStart.y);lastTap=0;hideTip();}
    else{lastTap=now;const r=el.getBoundingClientRect();
      if(!clickAt(tapStart.x,tapStart.y))hoverAt(tapStart.x,tapStart.y,tapStart.x+r.left,tapStart.y+r.top,true);}}
  T0=null;tapStart=null;},{passive:false});

/* ---- glide: a flick keeps moving briefly, then settles ---- */
function glide(){if(drag)return;
  const dec=0.90, eps=1e-4;
  if(Math.abs(vel.yaw)>eps||Math.abs(vel.pitch)>eps){yaw+=vel.yaw;pitch+=vel.pitch;vel.yaw*=dec;vel.pitch*=dec;place();}
  if(Math.abs(vel.px||0)+Math.abs(vel.py||0)+Math.abs(vel.pz||0)>dist*1e-4){
    target.x+=vel.px;target.y+=vel.py;target.z+=(vel.pz||0);vel.px*=dec;vel.py*=dec;vel.pz=(vel.pz||0)*dec;place();}}

/* ---- on-screen navigation cluster ---- */
(function(){const n=document.createElement("div");n.id="nav";
  n.innerHTML=`<button class="nb" data-nav="orbit" title="Orbit (drag to turn)"><svg viewBox="0 0 24 24"><ellipse cx="12" cy="12" rx="9" ry="4.2" transform="rotate(-25 12 12)"/><circle cx="12" cy="12" r="2.6" fill="currentColor" stroke="none"/></svg><b>Orbit</b></button>
   <button class="nb" data-nav="pan" title="Pan (drag to move around the model)"><svg viewBox="0 0 24 24"><path d="M12 3v18M3 12h18M12 3l-2.6 2.8M12 3l2.6 2.8M12 21l-2.6-2.8M12 21l2.6-2.8M3 12l2.8-2.6M3 12l2.8 2.6M21 12l-2.8-2.6M21 12l-2.8 2.6"/></svg><b>Pan</b></button>
   <span class="sep"></span>
   <button class="nb sq" id="n-in" title="Zoom in">+</button><button class="nb sq" id="n-out" title="Zoom out">−</button>
   <button class="nb sq" id="n-home" title="Reset view">⌂</button>`;
  document.body.appendChild(n);
  n.querySelectorAll("[data-nav]").forEach(b=>b.onclick=()=>setNav(b.dataset.nav));
  ctl("n-in").onclick=()=>zoomAt(0.72);ctl("n-out").onclick=()=>zoomAt(1.38);ctl("n-home").onclick=()=>view("3d",1);
  setNav(navMode);})();

/* tooltip */
const tip=$("tip");function hideTip(){tip.style.display="none";hovPending=null;}
let hovPending=null;
function hoverAt(px,py,cx,cy,touch){hovPending=[px,py,cx,cy,touch];}
function doHover(){if(!hovPending)return;const[px,py,cx,cy,touch]=hovPending;hovPending=null;const E=M.elements[EL];
  const iv=pickInterval(px,py,touch?18:8);let html=null;
  if(iv){const v=iv.v;const incl=IV.filter(x=>x.h===v.h);
    html=`<b>${esc(v.h.id)}</b> · ${v.f.toFixed(1)}–${v.t.toFixed(1)} m (${(v.t-v.f).toFixed(1)} m)<br><span class="g" style="color:${v.k<0?"#5f6670":E.colors[v.k]}">${fmtG(v.g,E.unit)} ${EL}</span>`+
      `<br><span style="color:var(--mut)">${incl.length} interval${incl.length>1?"s":""} on this hole · az ${v.h.az}° dip ${v.h.dip}°</span>`;}
  else{const hh=pickHole(px,py,touch?16:7);if(hh){const h=hh.h;const n=IV.filter(x=>x.h===h).length;
    html=`<b>${esc(h.id)}</b><br>${h.depth?h.depth.toFixed(0)+" m":"depth n/a"} · az ${h.az}° · dip ${h.dip}°<br><span style="color:var(--mut)">${n?n+" "+EL+" interval"+(n>1?"s":""):"no "+EL+" intervals reported"} · collar ${h.z.toFixed(0)} masl</span>`;}}
  if(!html){hideTip();return;}tip.innerHTML=html;tip.style.display="block";
  const w=tip.offsetWidth,h=tip.offsetHeight;tip.style.left=Math.min(cx+14,innerWidth-w-8)+"px";tip.style.top=Math.max(8,Math.min(cy+14,innerHeight-h-8))+"px";}

/* find hole */
{const dl=$("holelist");HO.slice(0,3000).forEach(h=>{const o=document.createElement("option");o.value=h.id;dl.appendChild(o);});
 const go=()=>{const q=$("find").value.trim().toLowerCase();if(!q)return;const h=HO.find(x=>x.id.toLowerCase()===q)||HO.find(x=>x.id.toLowerCase().includes(q));
   if(!h){toast("No hole matching “"+$("find").value+"”");return;}const mid=at(h,(h.depth||0)/2);const L=Math.max(h.depth||100,120);
   fly({x:mid.x,y:mid.y,z:mid.z,dist:L*1.8},600);select(h);toast("Showing "+h.id);if(MOBILE){document.querySelectorAll(".panel").forEach(x=>x.classList.remove("open"));}};
 $("find-go").onclick=go;$("find").addEventListener("keydown",e=>{if(e.key==="Enter")go();});}

/* layers */
$("L-int").onchange=e=>{if(intMesh)intMesh.visible=e.target.checked;};
$("L-trace").onchange=e=>{traceGroup.visible=e.target.checked;};
$("L-blocks").onchange=e=>{if(blockMesh)blockMesh.visible=e.target.checked;store.set("blocks",e.target.checked);};
$("L-terrain").onchange=e=>{if(terrain)terrain.visible=e.target.checked;store.set("terrain",e.target.checked);};
$("ter-op").oninput=e=>{const v=+e.target.value;$("ter-op-v").textContent=v+"%";if(terrain){terrain.material.uniforms.uOp.value=v/100;terrain.material.depthWrite=v>=100;}store.set("terop",v);};
$("blk-op").oninput=e=>{const v=+e.target.value;$("blk-op-v").textContent=v+"%";if(blockMesh){blockMesh.material.opacity=v/100;blockMesh.material.transparent=v<100;blockMesh.material.depthWrite=v>=100;blockMesh.material.needsUpdate=true;}store.set("blkop",v);};
{const v=store.get("blkop",35);$("blk-op").value=v;$("blk-op-v").textContent=v+"%";$("L-blocks").checked=store.get("blocks",true);}
ctl("L-body").onchange=e=>{store.set("body",e.target.checked);bodyVis();drawLabels();};
/* Shape or cells. Built here rather than in the page shell, like the hole card:
   every model page bakes its own markup and only the renderer is shared. */
{const cb=$("L-body");
 const row=(cb&&cb.closest)?cb.closest("label"):null;   // the toggle's own row
 if(row&&row.parentNode&&row.parentNode.insertBefore){
   const d=document.createElement("div");d.id="body-style";
   d.style.cssText="display:none;margin:4px 0 2px;font-size:11px";
   d.innerHTML='<button class="chip" data-bs="shape" title="The surface through the '
     +'intercepts, at the thickness each one shows">Shape</button>'
     +'<button class="chip" data-bs="cells" title="The same body on the block '
     +'lattice, cell by cell">Cells</button>'
     +'<div class="bs-note" style="color:var(--mut,#6b7684);font-size:10.5px;'
     +'margin:3px 0 0;line-height:1.3"></div>';
   d.style.cssText+=";padding-left:18px";
   row.parentNode.insertBefore(d,row.nextSibling);
   const mark=()=>d.querySelectorAll("[data-bs]").forEach(
     x=>x.classList.toggle("on",x.dataset.bs===bodyStyle));
   d.querySelectorAll("[data-bs]").forEach(b=>b.onclick=()=>{
     if(b.disabled)return;
     bodyStyle=b.dataset.bs;store.set("bodystyle",bodyStyle);mark();bodyVis();bodyNote();});
   mark();}}
/* "Show the contacts" — built here for the same reason as the switch above:
   the page shell is baked and the renderer is not, so a control added today
   has to reach the pages that are already out there. */
{const cb=$("L-body");
 const row=(cb&&cb.closest)?cb.closest("label"):null;
 if(row&&row.parentNode&&row.parentNode.insertBefore&&!$("cont-row")){
   const l=document.createElement("label");
   l.className="tg";l.id="cont-row";l.style.cssText="display:none;padding-left:18px";
   l.title="The hangingwall and footwall the surface is fitted through: two "
     +"points per intercept. Where there are none, the surface is interpolated.";
   l.innerHTML='<input type="checkbox" id="L-contacts">Contact points';
   const anchor=$("body-style")||row;
   row.parentNode.insertBefore(l,anchor.nextSibling);}}
ctl("L-contacts").onchange=e=>{store.set("contacts",e.target.checked);bodyVis();};
{const c=$("L-contacts");if(c)c.checked=store.get("contacts",true);}
ctl("L-zlabels").onchange=e=>{store.set("zlabels",e.target.checked);drawLabels();};
{const v=store.get("zlabels",false);const c=$("L-zlabels");if(c)c.checked=v;}
ctl("body-op").oninput=e=>{const v=+e.target.value;ctl("body-op-v").textContent=v+"%";
  for(const m of [bodyMesh,surfMesh])if(m){m.material.opacity=v/100;m.material.needsUpdate=true;}
  store.set("bodyop",v);};
{const v=store.get("bodyop",18);ctl("body-op").value=v;ctl("body-op-v").textContent=v+"%";
 ctl("L-body").checked=store.get("body",false);}
/* hole labels (nearest ~80 to screen centre) */
const LBL=$("labels");let lblOn=false;$("L-labels").onchange=e=>{lblOn=e.target.checked;LBL.innerHTML="";drawLabels();};
function drawLabels(){
  const parts=[];
  if(lblOn){const cands=[];for(const h of HO){const s=scr(at(h,0));if(s[2]>1||s[0]<0||s[1]<0||s[0]>W()||s[1]>Hh())continue;cands.push([Math.hypot(s[0]-W()/2,s[1]-Hh()/2),h,s]);}
    cands.sort((a,b)=>a[0]-b[0]);
    parts.push(...cands.slice(0,MOBILE?40:90).map(c=>`<span style="left:${c[2][0]}px;top:${c[2][1]}px">${esc(c[1].id)}</span>`));}
  parts.push(...zoneLabels());parts.push(...selLabels());parts.push(...targetLabels());
  LBL.innerHTML=parts.join("");placeSel();}

/* ---------------- selected hole: highlight, data card, hole-to-hole distance ----
   Click a hole and its assays come up in a card beside it; the hole itself gets an
   orange centreline, a pin above the collar and a ring on the collar, so it stays
   identifiable in a crowd and when something else flies the camera to it.
   The markup and CSS are built here, not in model3d_viewer.html: every model page
   bakes its own shell, so a template-only addition reaches just the pages rebuilt
   afterwards, while the renderer is shared and a runtime build works everywhere. */
let SEL=null,selMesh=null;
let measFrom=null,measWant=false,MEAS=null,measMesh=null,measBtn=null;
HO.forEach((h,i)=>{h.i=i;});

(function(){const s=document.createElement("style");s.textContent=
`#hcard{position:fixed;z-index:39;width:272px;max-width:calc(100vw - 16px);background:#fff;color:#1d2530;
  border:1px solid #d6dbe2;border-radius:10px;box-shadow:0 10px 30px rgba(16,22,30,.20);display:none;
  font-size:12px;line-height:1.35}
#hcard.on{display:block}
#hcard .hh{display:flex;align-items:flex-start;gap:6px;padding:8px 9px 6px;border-bottom:1px solid #eceff3}
#hcard .hh b{font-size:13px;font-weight:700;word-break:break-all;flex:1}
#hcard .x{cursor:pointer;border:0;background:#f1f3f6;border-radius:6px;width:20px;height:20px;
  line-height:18px;font-size:14px;color:#5f6670;padding:0;flex:none}
#hcard .hs{padding:5px 9px 6px;color:#5f6670;font-size:11px}
#hcard .iw{max-height:38vh;overflow:auto;border-top:1px solid #eceff3}
#hcard table{width:100%;border-collapse:collapse;font-size:11.5px;margin:0}
#hcard th{position:sticky;top:0;background:#f7f8fa;color:#5f6670;font-weight:600;text-align:right;
  padding:3px 9px;border-bottom:1px solid #eceff3}
#hcard th:first-child{text-align:left}
#hcard td{padding:2px 9px;text-align:right;border-bottom:1px solid #f4f6f8;font-variant-numeric:tabular-nums}
#hcard td:first-child{text-align:left}
#hcard .hf{padding:6px 9px;border-top:1px solid #eceff3;color:#5f6670;font-size:11px}
#hcard .hb{display:flex;gap:6px;padding:7px 9px 9px}
#hcard .hb button{flex:1;cursor:pointer;font:inherit;font-size:11px;padding:4px 6px;border:1px solid #d6dbe2;
  background:#fff;border-radius:6px;color:#1d2530}
#hcard .hb button:hover{background:#f4f6f8}
#hpin{position:fixed;z-index:13;width:17px;height:17px;margin:-8.5px 0 0 -8.5px;border-radius:50%;
  border:2.5px solid #ff7a18;background:rgba(255,122,24,.20);pointer-events:none;display:none}
#mline{position:fixed;z-index:39;left:50%;transform:translateX(-50%);bottom:70px;display:none;
  background:#fff;border:1px solid #d6dbe2;border-radius:9px;box-shadow:0 8px 24px rgba(16,22,30,.18);
  padding:7px 10px;font-size:11.5px;color:#1d2530;max-width:calc(100vw - 24px)}
#mline.on{display:flex;gap:10px;align-items:center}
#mline button{cursor:pointer;font:inherit;font-size:11px;border:1px solid #d6dbe2;background:#fff;
  border-radius:6px;padding:3px 7px;flex:none}`;
 document.head.appendChild(s);})();

const HC=document.createElement("div");HC.id="hcard";document.body.appendChild(HC);
const HPIN=document.createElement("div");HPIN.id="hpin";document.body.appendChild(HPIN);
const MLINE=document.createElement("div");MLINE.id="mline";document.body.appendChild(MLINE);

function fmtM(m){if(m==null)return"–";const a=Math.abs(m);
  return a>=1000?(m/1000).toFixed(2)+" km":a>=100?m.toFixed(0)+" m":m.toFixed(1)+" m";}
/* every interval the model holds for one hole in one element, deepening */
function holeIv(h,el){const E=M.elements[el];if(!E||!E.segments)return[];const out=[];
  for(const s of E.segments)if(s[0]===h.i)out.push({f:+s[1],t:+s[2],g:+s[3]});
  out.sort((a,b)=>a.f-b.f);return out;}

function select(h){SEL=h||null;hideTip();drawSel();drawCard();drawLabels();}
function clearSel(){SEL=null;drawSel();drawCard();drawLabels();}
function centre(h){const mid=at(h,(h.depth||0)/2),L=Math.max(h.depth||100,120);
  fly({x:mid.x,y:mid.y,z:mid.z,dist:L*1.8},600);}

function clickAt(px,py){
  const iv=pickInterval(px,py,MOBILE?18:10);
  const hit=iv?{h:iv.v.h}:pickHole(px,py,MOBILE?16:9);
  if(!hit){if(measWant){measWant=false;measFrom=null;syncMeas();toast("Measuring off");}else clearSel();return false;}
  select(hit.h);
  if(measWant){
    if(!measFrom||measFrom===hit.h){measFrom=hit.h;syncMeas();toast("Now click the hole to measure to");}
    else{setMeas(measFrom,hit.h);measFrom=null;measWant=false;}}
  return true;}

/* ---- distance between two holes ----
   Collar to collar is the number people quote; the one that decides whether two
   holes actually test the same ground is how close the traces come at depth, so
   both are reported, with where on each hole the closest approach falls. */
function holePath(h,n){const d=h.depth||0,k=Math.max(2,Math.min(n,Math.round(d/2)+2)),out=[];
  for(let i=0;i<k;i++)out.push(at(h,d*i/(k-1)));return out;}
function nearestApproach(a,b){const A=holePath(a,300),B=holePath(b,300);
  let bd=Infinity,bi=0,bj=0;
  for(let i=0;i<A.length;i++)for(let j=0;j<B.length;j++){const d=A[i].distanceToSquared(B[j]);
    if(d<bd){bd=d;bi=i;bj=j;}}
  return{d:Math.sqrt(bd),pa:A[bi],pb:B[bj],
         ma:(a.depth||0)*bi/(A.length-1),mb:(b.depth||0)*bj/(B.length-1)};}
function setMeas(a,b){const dx=b.x-a.x,dy=b.y-a.y,dz=b.z-a.z;
  const hz=Math.hypot(dx,dy),d3=Math.hypot(hz,dz),az=(Math.atan2(dx,dy)*180/Math.PI+360)%360;
  const c=nearestApproach(a,b);
  MEAS={a,b,collar:d3,horiz:hz,vert:dz,az,d:c.d,pa:c.pa,pb:c.pb,ma:c.ma,mb:c.mb};
  drawMeas();drawMline();syncMeas();drawLabels();
  toast(a.id+" → "+b.id+": "+fmtM(d3)+" collar to collar");}
function clearMeas(){MEAS=null;measFrom=null;measWant=false;drawMeas();drawMline();syncMeas();drawLabels();}
function syncMeas(){if(measBtn&&measBtn.classList)measBtn.classList.toggle("on",!!(measWant||MEAS));}

function drawMline(){
  if(!MEAS){MLINE.className="";MLINE.innerHTML="";return;}
  const m=MEAS,up=m.vert>=0?"higher":"lower";
  /* the closest approach is only worth a clause when the holes converge: two
     parallel holes come no closer at depth than their collars already are */
  const conv=m.d<m.collar-Math.max(1,m.collar*0.02);
  MLINE.innerHTML=`<span><b>${esc(m.a.id)} → ${esc(m.b.id)}</b> · collars ${fmtM(m.collar)} apart`+
    ` (${fmtM(m.horiz)} horizontal, second collar ${fmtM(Math.abs(m.vert))} ${up}, bearing ${m.az.toFixed(0)}°)`+
    (conv?` · the traces close to ${fmtM(m.d)}, at ${m.ma.toFixed(0)} m down ${esc(m.a.id)}`+
      ` and ${m.mb.toFixed(0)} m down ${esc(m.b.id)}`
      :` · they never come closer than that`)+`</span><button id="m-clr">Clear</button>`;
  MLINE.className="on";
  const b=MLINE.querySelector("#m-clr");if(b)b.onclick=clearMeas;}

function drawCard(){
  if(!SEL){HC.className="";HC.innerHTML="";HPIN.style.display="none";return;}
  const h=SEL,E=M.elements[EL],unit=E.unit,iv=holeIv(h,EL);
  const len=iv.reduce((s,x)=>s+(x.t-x.f),0);
  const wavg=len?iv.reduce((s,x)=>s+x.g*(x.t-x.f),0)/len:null;
  const best=iv.length?iv.reduce((p,x)=>x.g>p.g?x:p):null;
  const rows=iv.map(x=>{const k=binIdx(E,x.g);
    return `<tr><td>${x.f.toFixed(1)}</td><td>${x.t.toFixed(1)}</td><td>${(x.t-x.f).toFixed(2)}</td>`+
      `<td style="color:${k<0?"#5f6670":E.colors[k]};font-weight:700">${fmtG(x.g,unit)}</td></tr>`;}).join("");
  const others=ELS.filter(e=>e!==EL).map(e=>{const n=holeIv(h,e);if(!n.length)return null;
    const bb=n.reduce((p,x)=>x.g>p.g?x:p);
    return esc(e)+" "+n.length+"× (best "+fmtG(bb.g,M.elements[e].unit)+")";}).filter(Boolean);
  HC.innerHTML=
    `<div class="hh"><b>${esc(h.id)}</b><button class="x" title="Close">×</button></div>`+
    `<div class="hs">${h.depth?h.depth.toFixed(0)+" m":"depth n/a"} · az ${h.az}° · dip ${h.dip}°`+
    ` · collar ${h.z.toFixed(0)} masl<br>`+
    `${h.p&&h.p.length>=8?"path from the downhole survey":"drawn straight on collar azimuth and dip"}</div>`+
    (iv.length
      ?`<div class="iw"><table><thead><tr><th>From</th><th>To</th><th>Len</th><th>${esc(EL)}</th></tr></thead>`+
       `<tbody>${rows}</tbody></table></div>`+
       `<div class="hf">${iv.length} ${esc(EL)} interval${iv.length>1?"s":""} · ${len.toFixed(1)} m assayed`+
       ` · ${fmtG(wavg,unit)} over that length · best ${fmtG(best.g,unit)} at ${best.f.toFixed(1)} m</div>`
      :`<div class="hf">No ${esc(EL)} intervals reported on this hole.</div>`)+
    (others.length?`<div class="hf">Also assayed: ${others.join(" · ")}</div>`:"")+
    `<div class="hb"><button id="hc-m">Measure to…</button><button id="hc-c">Centre view</button></div>`;
  HC.className="on";
  const bx=HC.querySelector(".x");if(bx)bx.onclick=clearSel;
  const bm=HC.querySelector("#hc-m");
  if(bm)bm.onclick=()=>{measFrom=h;measWant=true;syncMeas();toast("Click the hole to measure to");};
  const bc=HC.querySelector("#hc-c");if(bc)bc.onclick=()=>centre(h);
  placeSel();}

/* Where to hang the ring, the chip and the card. The collar when it is in view —
   but a default view often frames the drilling at depth with every collar off
   screen, and a marker you cannot see marks nothing, so fall back to the first
   point down the hole that is actually on screen. */
function selAnchor(h){
  if(!h)return null;const d=h.depth||0,n=16;let any=null;
  for(let i=0;i<=n;i++){const s=scr(at(h,d*i/n));if(s[2]>1)continue;
    if(s[0]>=0&&s[1]>=0&&s[0]<=W()&&s[1]<=Hh())return{s,m:d*i/n,on:true};
    if(!any)any={s,m:d*i/n,on:false};}
  return any;}

/* the card and the ring follow the hole as the camera moves */
function placeSel(){
  if(!SEL){HPIN.style.display="none";return;}
  const r=host.getBoundingClientRect(),A=selAnchor(SEL);
  const vis=!!(A&&A.on);
  HPIN.style.display=vis?"block":"none";
  if(vis){HPIN.style.left=(A.s[0]+r.left)+"px";HPIN.style.top=(A.s[1]+r.top)+"px";}
  const s=A?A.s:[0,0,2];
  if(HC.className!=="on")return;
  const w=HC.offsetWidth||272,ch=HC.offsetHeight||220;
  let L,T;
  if(vis){L=s[0]+r.left+24;T=s[1]+r.top-20;
    if(L+w>innerWidth-8)L=s[0]+r.left-w-24;}                 // flip to the other side
  else{L=innerWidth-w-12;T=r.top+12;}                         // off screen: park it
  HC.style.left=Math.max(8,Math.min(L,innerWidth-w-8))+"px";
  HC.style.top=Math.max(r.top+6,Math.min(T,innerHeight-ch-8))+"px";}

function drawSel(){
  if(selMesh){scene.remove(selMesh);selMesh.geometry.dispose();selMesh=null;}
  if(!SEL)return;
  const segs=[],c=new THREE.Color(0xff7a18),d=SEL.depth||0;
  /* a thin centreline, not a fat one: it has to read through terrain and blocks
     without burying the grade colours it is drawn over */
  if(d>0)holeSegs(SEL,0,d).forEach(([a,b])=>segs.push({a:at(SEL,a),b:at(SEL,b),w:2.0,c}));
  const p0=at(SEL,0);
  segs.push({a:p0,b:p0.clone().add(new THREE.Vector3(0,Math.max(R0*0.015,6),0)),w:3.0,c});
  selMesh=fatSegments(segs);selMesh.material.depthTest=false;selMesh.material.depthWrite=false;
  selMesh.renderOrder=997;scene.add(selMesh);}

function drawMeas(){
  if(measMesh){scene.remove(measMesh);measMesh.geometry.dispose();measMesh=null;}
  if(!MEAS)return;
  const segs=[],red=new THREE.Color(0xd71920),blu=new THREE.Color(0x1f6feb);
  const A=at(MEAS.a,0),B=at(MEAS.b,0),nd=30;
  for(let i=0;i<nd;i++){const t=i/nd;
    segs.push({a:A.clone().lerp(B,t),b:A.clone().lerp(B,t+0.62/nd),w:2.4,c:red});}
  if(MEAS.pa&&MEAS.pb&&MEAS.d<MEAS.collar-Math.max(1,MEAS.collar*0.02))
    segs.push({a:MEAS.pa,b:MEAS.pb,w:3.2,c:blu});
  measMesh=fatSegments(segs);measMesh.material.depthTest=false;measMesh.material.depthWrite=false;
  measMesh.renderOrder=996;scene.add(measMesh);}

function selLabels(){const out=[];
  /* a chip whose anchor is off screen is clipped to a coloured sliver at the
     edge, which reads as a glitch — leave it out */
  const chip=(x,y,bg,t)=>{if(!(x>=40&&y>=10&&x<=W()-40&&y<=Hh()-10))return;
    out.push(`<span style="left:${x.toFixed(0)}px;top:${y.toFixed(0)}px;background:${bg};`+
      `color:#fff;font-weight:700;border:0">${t}</span>`);};
  if(SEL){const A=selAnchor(SEL);if(A&&A.on)chip(A.s[0],A.s[1]-24,"#ff7a18",esc(SEL.id));}
  if(MEAS){const A=at(MEAS.a,0),B=at(MEAS.b,0),m=scr(A.clone().lerp(B,0.5));
    if(m[2]<=1)chip(m[0],m[1],"#d71920",fmtM(MEAS.collar));
    if(MEAS.pa&&MEAS.d<MEAS.collar-Math.max(1,MEAS.collar*0.02)){
      const q=scr(MEAS.pa.clone().lerp(MEAS.pb,0.5));
      if(q[2]<=1)chip(q[0],q[1],"#1f6feb",fmtM(MEAS.d)+" closest");}}
  return out;}

/* measure button in the navigation cluster, so the feature is findable */
{const n=document.getElementById("nav");
 if(n){const b=document.createElement("button");b.className="nb sq";b.id="n-meas";
   b.title="Measure from one hole to another";b.innerHTML="↔";
   b.onclick=()=>{if(MEAS||measWant){clearMeas();toast("Measuring off");return;}
     measWant=true;measFrom=SEL||null;syncMeas();
     toast(measFrom?"Click the hole to measure to":"Click the first hole");};
   n.appendChild(b);measBtn=b;}}

addEventListener("keydown",e=>{
  if(/input|textarea|select/i.test((e.target.tagName||"")))return;
  if(e.key==="Escape"){if(MEAS||measWant)clearMeas();else if(SEL)clearSel();return;}
  if(e.key.toLowerCase()==="m"){measWant=true;measFrom=SEL||null;syncMeas();
    toast(measFrom?"Click the hole to measure to":"Click the first hole");}});

/* scale bar + compass */
function niceNum(x){const p=Math.pow(10,Math.floor(Math.log10(x)));const f=x/p;return(f<1.5?1:f<3.5?2:f<7.5?5:10)*p;}
function updHud(){const mpp=2*dist*Math.tan(camera.fov*Math.PI/360)/Hh();const n=niceNum(90*mpp);
  document.querySelector("#scalebar .sb").style.width=Math.max(20,Math.min(200,n/mpp))+"px";$("sb-l").textContent=n>=1000?(n/1000)+" km":Math.round(n)+" m";
  $("needle").setAttribute("transform",`rotate(${(-yaw*180/Math.PI+180).toFixed(1)})`);}

/* ---- what the report says about the ground between the holes --------------
   Geophysics covers the ground drilling cannot: it is why a target was drilled
   where it was, and it is the report's own statement of where the body might
   continue past the last hole. None of it is an assay, so none of it estimates
   anything here — it is shown as the sentences it is, with the page they came
   from, beside the shape MMP drew from the drilling alone. The rock properties
   are different: the density IS the tonnage factor, and where the report states
   its own, the model carries that rather than an assumption.
   Built at runtime like the hole card: model pages bake their own markup. */
function drawGround(){
  const G=M.geophysics,R=M.rock;
  if(!G&&!R)return;
  const host2=$("pModel");if(!host2||!host2.appendChild)return;
  const box=document.createElement("div");
  box.id="groundbox";box.style.cssText="margin-top:14px;border-top:1px solid var(--line,#e6e9ee);padding-top:10px";
  const H=[];
  const sv=(G&&G.surveys)||[],tg=(G&&G.targets)||[];
  if(sv.length){
    const by={};for(const s of sv){const k=s.method;(by[k]=by[k]||[]).push(s);}
    H.push(`<h3 style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut,#6b7684);margin:0 0 6px">Geophysics the report describes</h3>`);
    H.push(`<table style="width:100%;border-collapse:collapse;font-size:11.5px">`+
      Object.keys(by).map(k=>{const rows=by[k];
        const km=rows.reduce((a,b)=>a+(b.line_km||0),0);
        const st=rows.reduce((a,b)=>a+(b.stations||0),0);
        const plats=[...new Set(rows.map(r=>r.platform).filter(Boolean))];
        const yrs=rows.map(r=>r.year).filter(Boolean).sort();
        const bits=[];
        if(km)bits.push(fmtN(km,km<10?1:0)+" line-km");
        if(st)bits.push(fmtN(st)+" stations");
        if(plats.length)bits.push(plats.join(" + "));
        if(yrs.length)bits.push(yrs[0]===yrs[yrs.length-1]?yrs[0]:yrs[0]+"–"+yrs[yrs.length-1]);
        // a company's own announcement is not a qualified person's report, and
        // the difference is worth a reader's eye
        if(rows.every(r=>r.from_release))bits.push("from a release");
        else if(rows.some(r=>r.from_release))bits.push("report + release");
        return `<tr><td style="padding:2px 0">${esc(k)}</td>`+
               `<td style="padding:2px 0;text-align:right;color:var(--mut,#6b7684)">${esc(bits.join(" · ")||rows.length+"×")}</td></tr>`;}).join("")
      +`</table>`);
  }
  if(tg.length){
    const open=tg.filter(t=>t.untested),exp=tg.filter(t=>t.explained&&!t.untested);
    const pick=open.concat(exp).slice(0,3);
    H.push(`<p class="note" style="margin:8px 0 4px">`+
      (open.length?open.length+" of these anomalies the report calls untested or recommends drilling. ":"")+
      `Quoted as written — a sentence is not a sample, and nothing here estimates grade.</p>`);
    H.push(pick.map(t=>`<blockquote style="margin:6px 0;padding:5px 8px;border-left:3px solid `
      +(t.untested?"#ff7a18":"#9aa4b2")+`;background:#f7f8fa;font-size:11px;color:#3a434f">`
      +`${esc(t.text)} <span style="color:var(--mut,#6b7684)">— `
      +(t.from_release?"a news release":"p"+t.page)+`</span></blockquote>`).join(""));
  }
  if(R){
    const row=(lbl,v,ttl)=>`<tr title="${esc(ttl||"")}"><td style="padding:2px 0">${esc(lbl)}</td>`+
      `<td style="padding:2px 0;text-align:right;font-weight:600">${esc(v)}</td></tr>`;
    const rs=[];
    if(R.density_low&&R.density_high)
      rs.push(row("Density (stated range)",R.density_low.median+"–"+R.density_high.median+" t/m³",R.density_low.text));
    else if(R.density)rs.push(row("Density",R.density.median+" t/m³",R.density.text));
    if(R.density_samples)rs.push(row("Density measurements",fmtN(R.density_samples.median),R.density_samples.text));
    if(R.overburden_m)rs.push(row("Overburden",R.overburden_m.median+" m",R.overburden_m.text));
    if(R.weathering_m)rs.push(row("Weathering to",R.weathering_m.median+" m",R.weathering_m.text));
    if(R.rqd)rs.push(row("RQD",R.rqd.median+"%",R.rqd.text));
    if(R.gsi)rs.push(row("GSI",R.gsi.median,R.gsi.text));
    if(R.rmr)rs.push(row("Rock mass rating",R.rmr.median,R.rmr.text));
    if(R.ucs_mpa)rs.push(row("Unconfined strength",R.ucs_mpa.median+" MPa",R.ucs_mpa.text));
    if(R.slope_angle_deg)rs.push(row("Pit slope angle",R.slope_angle_deg.median+"°",R.slope_angle_deg.text));
    if(rs.length){
      H.push(`<h3 style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut,#6b7684);margin:12px 0 6px">The rock itself</h3>`);
      H.push(`<table style="width:100%;border-collapse:collapse;font-size:11.5px">`+rs.join("")+`</table>`);
      const used=(M.settings||{}).density;
      H.push(`<p class="note" style="margin:6px 0 0">`+
        (used?`The model is using ${used} t/m³.`:"")+
        ` Everything else here is reported, not applied: it says how the rock would be mined, not where it is.</p>`);
    }
  }
  if(H.length<2)return;
  box.innerHTML=H.join("");
  host2.appendChild(box);}

/* ---- where the next hole goes -------------------------------------------
   The block model answers what is here. The rim of it — where the body is
   still carrying grade when the drilling stops — answers where to drill, and
   that is computed at build time from the same alpha criterion that stops the
   wireframe. Drawn as a marker on the ground it points at, and listed with the
   hole each one steps out from, so a reader can check it against the drilling
   rather than take it on trust. */
let tgtMesh=null,TG=null;
function buildTargets(){
  if(tgtMesh){scene.remove(tgtMesh);tgtMesh.geometry.dispose();tgtMesh.material.dispose();tgtMesh=null;}
  TG=null;
  const T=(M.elements[EL]||{}).targets;
  if(!T||!T.rows||!T.rows.length)return;
  TG=T;
  const segs=[],c=new THREE.Color(0xd71920),up=Math.max(R0*0.02,12);
  for(const t of T.rows){
    const p=V(t.x,t.y,t.z);
    segs.push({a:p,b:p.clone().add(new THREE.Vector3(0,up,0)),w:3.0,c});
    // a cross on the spot itself, so it reads as a target and not as a hole
    const r=up*0.25;
    segs.push({a:p.clone().add(new THREE.Vector3(-r,0,0)),b:p.clone().add(new THREE.Vector3(r,0,0)),w:2.4,c});
    segs.push({a:p.clone().add(new THREE.Vector3(0,0,-r)),b:p.clone().add(new THREE.Vector3(0,0,r)),w:2.4,c});
    if(t.collar){const q=V(t.collar.x,t.collar.y,t.collar.z);
      segs.push({a:q,b:p,w:1.2,c});}        // the hole that would cut it
  }
  tgtMesh=fatSegments(segs);
  tgtMesh.material.depthTest=false;tgtMesh.material.depthWrite=false;
  tgtMesh.renderOrder=995;
  tgtMesh.visible=store.get("targets",true);
  scene.add(tgtMesh);
  drawTargetList();}

function targetLabels(){
  if(!TG||!tgtMesh||!tgtMesh.visible)return[];
  const out=[];
  TG.rows.forEach((t,i)=>{
    if(out.length>=(MOBILE?4:10))return;
    const s=scr(V(t.x,t.y,t.z));
    if(s[2]>1||s[0]<40||s[1]<16||s[0]>W()-40||s[1]>Hh()-16)return;
    out.push(`<span style="left:${s[0].toFixed(0)}px;top:${(s[1]-26).toFixed(0)}px;`
      +`background:#d71920;color:#fff;font-weight:700;border:0">T${i+1}</span>`);});
  return out;}

/* Where to drill is a drill plan, not a line in a layer panel. It needs the
   collar, the azimuth, the dip and the planned length, and a driller needs it as
   a file — so it gets a panel of its own, like Bodies, built at runtime because
   every model page bakes its own markup and only the renderer is shared.

   What it deliberately does NOT lead with is a grade. The intercept beside each
   target is the EVIDENCE for ranking it, not a prediction of what the hole will
   cut, and putting it in the same row as the hole to drill read as a forecast.
   It sits under "why here", in words that say whose assay it is. */
function wirePanel(p,label){
  const h=p.querySelector(".phead");if(!h)return;
  const btn=h.querySelector(".pbtn");
  const upd=()=>{if(btn)btn.textContent=MOBILE?"×":(p.classList.contains("min")?"+":"–");};
  upd();
  h.addEventListener("click",()=>{
    if(MOBILE){p.classList.remove("open");
      document.querySelectorAll("#mtools button").forEach(b=>b.classList.remove("on"));return;}
    p.classList.toggle("min");store.set("min:"+p.id,p.classList.contains("min"));upd();});
  const tools=$("mtools");
  if(tools&&tools.appendChild&&!tools.querySelector('[data-open="'+p.id+'"]')){
    const b=document.createElement("button");b.dataset.open=p.id;
    b.textContent=label||"Targets";
    b.addEventListener("click",()=>{const was=p.classList.contains("open");
      document.querySelectorAll(".panel").forEach(x=>x.classList.remove("open"));
      document.querySelectorAll("#mtools button").forEach(x=>x.classList.remove("on"));
      if(!was){p.classList.add("open");b.classList.add("on");}});
    const after=tools.querySelector('[data-open="'+(p.id==="pZones"?"pModel":"pZones")+'"]');
    if(after&&after.nextSibling)tools.insertBefore(b,after.nextSibling);else tools.appendChild(b);}}

function targetPanel(){
  let p=$("pTargets");
  if(p&&p.remove)return p;
  p=document.createElement("section");
  p.id="pTargets";p.className="panel min";p.style.display="none";
  p.innerHTML='<div class="phead"><h2>Drill targets <small id="tgt-n" '
    +'style="font-weight:400;color:var(--mut)"></small></h2>'
    +'<button class="pbtn" aria-label="Minimise">+</button></div>'
    +'<div class="pbody"><div class="sec" id="tgt-body"></div></div>';
  const col=$("leftcol"),zones=$("pZones"),model=$("pModel");
  if(col&&col.appendChild)col.appendChild(p);
  else if(zones&&zones.parentNode)zones.parentNode.insertBefore(p,zones.nextSibling);
  else if(model&&model.parentNode)model.parentNode.insertBefore(p,model.nextSibling);
  else document.body.appendChild(p);
  wirePanel(p,"Targets");
  return p;}

function fmtXY(v){return Math.round(v).toLocaleString();}

function drawTargetList(){
  const old=$("tgtbox");if(old&&old.remove)old.remove();   // it used to live in Model
  const p=targetPanel();
  const box=$("tgt-body");
  if(!TG||!box||!box.appendChild){if(p&&p.style)p.style.display="none";return;}
  p.style.display="";
  const unit=M.elements[EL].unit;
  const F=M.frame||{},geo=F.lat0!=null&&F.lon0!=null;
  const ll=(x,y)=>{if(!geo)return null;
    return [F.lat0+y/111320,F.lon0+x/(111320*Math.cos(F.lat0*Math.PI/180)||1)];};
  const attitude=c=>!c?"no collar placed"
    :(Math.abs(c.dip)>85?"vertical":`${Math.round(c.az)}° / ${Math.round(c.dip)}°`);
  const n=$("tgt-n");if(n)n.textContent="· "+TG.rows.length;
  box.innerHTML=
    `<label class="tg" style="margin:0 0 8px"><input type="checkbox" id="L-targets"`
    +(tgtMesh&&tgtMesh.visible?" checked":"")+`>Show on the model</label>`
    +`<div id="tgt-rows"></div>`
    +`<button id="tgt-csv" class="chip" style="margin-top:8px">Download drill plan (CSV)</button>`
    +`<p class="note" style="margin:8px 0 0">Each is one ${TG.step_m} m step-out — the `
    +`spacing this pattern already drills — from a hole that cut mineralisation at the `
    +`edge of the model, onto ground no existing hole passes through. `
    +(TG.oriented?"Angled to the attitude the report states."
      :"Angled square to the body's own plane; no report states its attitude.")
    +` The model stops where the drilling stops, and so does this: nothing here `
    +`estimates what a hole would cut.</p>`;
  const rows=$("tgt-rows");
  rows.innerHTML=TG.rows.map((t,i)=>{
    const c=t.collar;
    const q=ll(t.x,t.y);
    return `<div class="tgt" data-t="${i}" style="padding:6px 0;border-top:1px solid var(--line,#eceff3);cursor:pointer">`
      +`<div style="display:flex;gap:8px;align-items:baseline">`
      +`<b style="color:#d71920;flex:none">T${i+1}</b>`
      +`<span style="flex:1">${c?Math.round(c.depth_m)+" m":"—"} · ${esc(attitude(c))}</span>`
      +`<span style="color:var(--mut,#6b7684);flex:none">${t.step_m} m out</span></div>`
      +(c?`<div style="color:var(--mut,#6b7684);font-size:11px;margin-top:1px">`
          +(q?`collar ${q[0].toFixed(5)}, ${q[1].toFixed(5)} · ${Math.round(c.z)} m`
             :`collar ${fmtXY(c.x)}, ${fmtXY(c.y)} local · ${Math.round(c.z)} m`)
          +`</div>`:"")
      +`<div class="tgt-more" style="display:none;font-size:11px;margin-top:5px;`
      +`padding:6px 8px;background:#f7f8fa;border-radius:6px;color:#3a434f">`
      +(c?`<div><b>Collar</b> `+(q?`${q[0].toFixed(5)}, ${q[1].toFixed(5)}`:"")
          +`${q?" · ":""}${fmtXY(c.x)}, ${fmtXY(c.y)} in the model's own grid`
          +` · ${Math.round(c.z)} m RL</div>`
         +`<div><b>Azimuth</b> ${Math.round(c.az)}° · <b>Dip</b> ${Math.round(c.dip)}°`
         +` · <b>Length</b> ${Math.round(c.depth_m)} m</div>`:"")
      +`<div><b>Cuts the body at</b> ${fmtXY(t.x)}, ${fmtXY(t.y)} `
      +`(model grid) · ${Math.round(t.z)} m RL</div>`
      +`<div style="margin-top:4px"><b>Why here</b> — it steps ${t.step_m} m `
      +`${Math.round(t.bearing)}° from hole ${esc(t.from_hole)}, which cut `
      +`${t.width_m} m at ${fmtG(t.grade,unit)} ${esc(EL)} where the body reaches its edge. `
      +`That is <i>that hole's assay</i>, not a forecast for this one.</div>`
      +(t.nearest_drilling_m!=null?`<div style="margin-top:3px">Nearest existing `
        +`drilling ${Math.round(t.nearest_drilling_m)} m.</div>`:"")
      +`</div></div>`;}).join("");
  ctl("L-targets").onchange=e=>{if(tgtMesh)tgtMesh.visible=e.target.checked;
    store.set("targets",e.target.checked);drawLabels();};
  rows.querySelectorAll(".tgt").forEach(d=>d.onclick=()=>{
    const i=+d.dataset.t,t=TG.rows[i];if(!t)return;
    const m=d.querySelector(".tgt-more");
    const open=m.style.display!=="none";
    rows.querySelectorAll(".tgt-more").forEach(x=>{x.style.display="none";});
    if(!open){m.style.display="";
      const v=V(t.x,t.y,t.z);
      fly({x:v.x,y:v.y,z:v.z,dist:Math.max(TG.step_m*6,200)},600);}});
  const b=$("tgt-csv");
  if(b)b.onclick=()=>{
    const out=[["target","collar_x_local_m","collar_y_local_m","collar_elevation_m",
                "collar_latitude","collar_longitude","azimuth","dip","planned_length_m",
                "target_x_local_m","target_y_local_m","target_elevation_m",
                "step_out_m","step_out_bearing","nearest_drilling_m",
                "from_hole","from_hole_interval_m","from_hole_grade","element","unit"]];
    TG.rows.forEach((t,i)=>{const c=t.collar||{},q=ll(c.x,c.y)||["",""];
      out.push(["T"+(i+1),c.x??"",c.y??"",c.z??"",q[0],q[1],c.az??"",c.dip??"",c.depth_m??"",
                t.x,t.y,t.z,t.step_m,t.bearing,t.nearest_drilling_m??"",
                t.from_hole,t.width_m,t.grade,EL,unit]);});
    download(M.slug+"-drill-targets.csv",csv(out));};}

/* ---------------- start ---------------- */
drawEls();buildIntervals();drawLegend();drawSources();drawGround();buildTargets();buildBlocks();buildBody();setupGT();drawData();drawBreakdown();drawSize();drawZones();
{const ib=$("intbox");if(ib)ib.innerHTML=integrity();
 const mb=$("metbox");if(mb)mb.innerHTML=metallurgy();}
resize();F=framing();view("3d",0);
addEventListener("resize",()=>{resize();place();});
if(!store.get("seen-pivot",false)){setTimeout(()=>toast(MOBILE?"Double-tap any hole to pivot around it":"Tip: double-click any hole or block to pivot around it",4200),900);store.set("seen-pivot",true);}
let frames=0;
function loop(){requestAnimationFrame(loop);if(flyAnim)flyAnim();else glide();doHover();updHud();if((frames++&1)===0)drawLabels();renderer.render(scene,camera);}
loop();

