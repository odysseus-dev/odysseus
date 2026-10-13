// Rasterize animated research scenes, then sample into the Fireflies glyph ramp.
const ramp = ' .,:;irsXA253hMHGS#9B&@';
const cols = 56, rows = 20, W = cols * 3, H = rows * 3;
const phaseNames = ['searching', 'reading', 'analyzing', 'writing'];
const captions = ['Discovering sources', 'Extracting evidence', 'Connecting findings', 'Composing report'];
const scenes = [...document.querySelectorAll('article')].map(card => {
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  return { card, kind:card.dataset.scene, pre:card.querySelector('pre'), canvas,
    ctx:canvas.getContext('2d', {willReadFrequently:true}) };
});
const resize = () => scenes.forEach(({card,pre}) => {
  pre.style.fontSize = `${Math.min((card.querySelector('.stage').clientWidth - 20) / (cols * 0.61), 11.5)}px`;
});
const observer = new ResizeObserver(resize);
scenes.forEach(({card}) => observer.observe(card));
resize();
let paused = matchMedia('(prefers-reduced-motion: reduce)').matches;
let time = 0, previous = 0, lastFrame = 0;
const pause = document.querySelector('#pause');
pause.textContent = paused ? 'Play' : 'Pause';
pause.onclick = () => { paused = !paused; pause.textContent = paused ? 'Play' : 'Pause'; };
document.querySelector('#restart').onclick = () => { time = 0; render(); };
document.querySelector('#phase').onchange = () => render();
const frac = x => x - Math.floor(x);
const lerp = (a,b,t) => [a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t];
function dot(ctx,p,r=1.2,light=255) {
  ctx.fillStyle=`rgb(${light},${light},${light})`;
  ctx.beginPath(); ctx.arc(p[0],p[1],r,0,Math.PI*2); ctx.fill();
}
function line(ctx,a,b,light=45) {
  ctx.strokeStyle=`rgb(${light},${light},${light})`; ctx.lineWidth=.6;
  ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
}
function packet(ctx,path,t) {
  for(let k=6;k>=0;k--) dot(ctx,path(frac(t-k*.011)),k===0?1.1:.65,255-k*31);
}
function network(ctx,t,phase) {
  const center=[W/2,H/2];
  const count = 4 + Math.min(4,Math.floor((time%48)/6));
  for(let i=0;i<count;i++) {
    const angle=i*Math.PI*2/count-Math.PI/2;
    const node=[center[0]+Math.cos(angle)*43,center[1]+Math.sin(angle)*17];
    line(ctx,center,node); dot(ctx,node,1.5,170);
    const reverse=phase==='reading'||phase==='analyzing';
    for(let j=0;j<2;j++) packet(ctx,u=>lerp(reverse?node:center,reverse?center:node,u),t*.25+i*.23+j*.5);
    for(let leaf=0;leaf<3;leaf++) {
      const a=angle+(leaf-1)*.37;
      const end=[center[0]+Math.cos(a)*72,center[1]+Math.sin(a)*27];
      line(ctx,node,end,32); dot(ctx,end,.8,130);
      packet(ctx,u=>lerp(node,end,u),t*.32+i*.17+leaf*.33);
    }
  }
  dot(ctx,center,1.8,240);
}
function orbit(ctx,t,phase) {
  const center=[W/2,H/2];
  for(let ring=0;ring<3;ring++) {
    const rx=27+ring*23,ry=9+ring*9;
    ctx.strokeStyle='#242424'; ctx.lineWidth=.55;
    ctx.beginPath(); ctx.ellipse(...center,rx,ry,0,0,Math.PI*2); ctx.stroke();
    for(let i=0;i<5;i++) {
      const theta=t*(.45-ring*.07)*(ring%2?-1:1)+i*Math.PI*2/5;
      for(let tail=10;tail>=0;tail--) {
        const a=theta-tail*.04*(ring%2?-1:1);
        dot(ctx,[center[0]+Math.cos(a)*rx,center[1]+Math.sin(a)*ry],tail===0?1.2:.7,255-tail*21);
      }
    }
  }
  for(let i=0;i<5;i++) packet(ctx,u=>{
    const r=1-u,a=i*Math.PI*2/5+t*.15+u*1.8;
    return [center[0]+Math.cos(a)*r*66,center[1]+Math.sin(a)*r*25];
  },t*(phase==='analyzing'?.25:.14)+i*.2);
  dot(ctx,center,1.6,230);
}
function streams(ctx,t,phase) {
  const hub=[W*.64,H*.5];
  for(let i=0;i<6;i++) {
    const start=[W*.08,7+i*10];
    const path=u=>{
      const smooth=u*u*(3-2*u);
      return [start[0]+(hub[0]-start[0])*u,start[1]+(hub[1]-start[1])*smooth+Math.sin(u*Math.PI*3+t*.7+i)*1.3*Math.sin(Math.PI*u)];
    };
    let last=path(0);
    for(let k=1;k<=40;k++){const p=path(k/40);line(ctx,last,p,32);last=p;}
    dot(ctx,start,1,150);
    for(let j=0;j<3;j++)packet(ctx,path,t*.22+i*.12+j/3);
  }
  dot(ctx,hub,1.7,245);
  const dest=[W*.88,H*.5]; line(ctx,hub,dest,60);
  for(let j=0;j<3;j++)packet(ctx,u=>lerp(hub,dest,u),t*(phase==='writing'?.8:.35)+j/3);
  for(let row=0;row<6;row++) {
    const y=H*.5-10+row*4;
    line(ctx,[W*.87,y],[W*.96-(row%3)*2,y],100+Math.round(80*(.5+.5*Math.sin(t*2-row))));
  }
}
const painters={network,orbit,streams};
function render() {
  const requested=document.querySelector('#phase').value;
  const phase=requested==='auto'?phaseNames[Math.floor(time/12)%4]:requested;
  for(const {card,kind,ctx,pre} of scenes) {
    ctx.fillStyle='#000'; ctx.fillRect(0,0,W,H);
    painters[kind](ctx,time,phase);
    const pixels=ctx.getImageData(0,0,W,H).data, lines=[];
    for(let y=0;y<rows;y++) {
      let text='';
      for(let x=0;x<cols;x++) {
        let value=0;
        for(let dy=0;dy<2;dy++)for(let dx=0;dx<2;dx++)value+=pixels[((y*2+dy)*W+x*2+dx)*4];
        text+=ramp[Math.min(ramp.length-1,Math.floor(value/4/256*ramp.length))];
      }
      lines.push(text);
    }
    pre.textContent=lines.join('\n');
    card.querySelector('.phase').textContent=phase;
    card.querySelector('.status').textContent=captions[phaseNames.indexOf(phase)];
    card.querySelector('.round').textContent=`Round ${1+Math.floor((time%48)/12)}`;
    card.querySelector('.sources').textContent=`${Math.floor(time%48*1.3)} sources`;
    card.querySelector('.fill').style.width=`${time%48/48*100}%`;
  }
}
function frame(now) {
  if(previous && !paused && !document.hidden)time+=Math.min((now-previous)/1000,.1)*Number(document.querySelector('#speed').value);
  previous=now;
  if(now-lastFrame>=66 && !paused && !document.hidden){render();lastFrame=now;}
  requestAnimationFrame(frame);
}
render(); requestAnimationFrame(frame);
