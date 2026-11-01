<template>
<div class="px-sky" aria-hidden="true">
    <div class="aSky"></div>
    <svg class="aArt" viewBox="0 0 320 150" preserveAspectRatio="xMidYMax slice" shape-rendering="crispEdges">
      <defs>
        <radialGradient id="sunGlow">
          <stop offset="0" stop-color="#FFE9A8" stop-opacity=".62"/>
          <stop offset=".42" stop-color="#FFD98A" stop-opacity=".2"/>
          <stop offset="1" stop-color="#FFC46A" stop-opacity="0"/>
        </radialGradient>
        <radialGradient id="moonGlow">
          <stop offset="0" stop-color="#FFE9A8" stop-opacity=".26"/>
          <stop offset=".55" stop-color="#FFE9A8" stop-opacity=".07"/>
          <stop offset="1" stop-color="#FFE9A8" stop-opacity="0"/>
        </radialGradient>
      </defs>
      <g class="stars">
        <g opacity=".85">
          <rect x="52" y="10" width="2" height="2"/><rect x="74" y="22" width="2" height="2"/>
          <rect x="96" y="8" width="2" height="2"/><rect x="118" y="18" width="2" height="2"/>
          <rect x="150" y="6" width="2" height="2"/><rect x="172" y="16" width="2" height="2"/>
          <rect x="206" y="8" width="2" height="2"/><rect x="222" y="20" width="2" height="2"/>
          <rect x="284" y="28" width="2" height="2"/><rect x="272" y="48" width="2" height="2"/>
        </g>
        <g opacity=".32">
          <rect x="40" y="26" width="1.5" height="1.5"/><rect x="62" y="34" width="1.5" height="1.5"/>
          <rect x="86" y="16" width="1.5" height="1.5"/><rect x="108" y="30" width="1.5" height="1.5"/>
          <rect x="132" y="12" width="1.5" height="1.5"/><rect x="160" y="28" width="1.5" height="1.5"/>
          <rect x="184" y="10" width="1.5" height="1.5"/><rect x="214" y="32" width="1.5" height="1.5"/>
          <rect x="262" y="62" width="1.5" height="1.5"/><rect x="292" y="40" width="1.5" height="1.5"/>
        </g>
        <g opacity=".9" transform="translate(84,26)">
          <rect x="0" y="0" width="3" height="3"/>
          <rect x="1" y="-3" width="1" height="2"/><rect x="1" y="4" width="1" height="2"/>
          <rect x="-3" y="1" width="2" height="1"/><rect x="4" y="1" width="2" height="1"/>
        </g>
      </g>
      <g class="moon" transform="translate(238,12)">
        <circle cx="9" cy="10" r="34" fill="url(#moonGlow)"/>
        <g fill="#FFE9A8">
          <rect x="5" y="0" width="8" height="2"/><rect x="3" y="2" width="12" height="2"/>
          <rect x="1" y="4" width="16" height="4"/><rect x="0" y="8" width="18" height="4"/>
          <rect x="1" y="12" width="16" height="4"/><rect x="3" y="16" width="12" height="2"/>
          <rect x="5" y="18" width="8" height="2"/>
        </g>
        <g fill="#E7CB86"><rect x="3" y="12" width="5" height="4"/><rect x="12" y="16" width="5" height="2"/></g>
        <g fill="#D8B96F"><rect x="7" y="6" width="4" height="3"/><rect x="13" y="9" width="3" height="3"/></g>
      </g>
      <g class="sun">
        <circle class="sunHalo" cx="9" cy="10" r="56" fill="url(#sunGlow)"/>
        <g class="sunCore">
          <rect x="5" y="0" width="8" height="2"/><rect x="3" y="2" width="12" height="2"/>
          <rect x="1" y="4" width="16" height="4"/><rect x="0" y="8" width="18" height="4"/>
          <rect x="1" y="12" width="16" height="4"/><rect x="3" y="16" width="12" height="2"/>
          <rect x="5" y="18" width="8" height="2"/>
        </g>
        <g class="sunRim" opacity=".7">
          <rect x="1" y="12" width="16" height="4"/><rect x="3" y="16" width="12" height="2"/>
          <rect x="5" y="18" width="8" height="2"/>
        </g>
      </g>
      <g class="clouds">
        <g transform="translate(38,13)">
          <rect x="0" y="10" width="34" height="4"/><rect x="4" y="5" width="8" height="5"/>
          <rect x="5" y="3" width="5" height="2"/><rect x="12" y="2" width="12" height="8"/>
          <rect x="14" y="0" width="8" height="2"/><rect x="24" y="5" width="8" height="5"/>
          <rect x="26" y="3" width="5" height="2"/>
        </g>
        <g transform="translate(196,22)" opacity=".95">
          <rect x="0" y="10" width="34" height="4"/><rect x="4" y="5" width="8" height="5"/>
          <rect x="5" y="3" width="5" height="2"/><rect x="12" y="2" width="12" height="8"/>
          <rect x="14" y="0" width="8" height="2"/><rect x="24" y="5" width="8" height="5"/>
          <rect x="26" y="3" width="5" height="2"/>
        </g>
        <g transform="translate(94,28)" opacity=".92">
          <rect x="0" y="7" width="22" height="3"/><rect x="2" y="4" width="6" height="3"/>
          <rect x="7" y="2" width="9" height="5"/><rect x="9" y="1" width="5" height="1"/>
          <rect x="16" y="4" width="6" height="3"/>
        </g>
        <g transform="translate(140,9)" opacity=".9">
          <rect x="0" y="7" width="22" height="3"/><rect x="2" y="4" width="6" height="3"/>
          <rect x="7" y="2" width="9" height="5"/><rect x="9" y="1" width="5" height="1"/>
          <rect x="16" y="4" width="6" height="3"/>
        </g>
        <g transform="translate(268,14)" opacity=".92">
          <rect x="0" y="7" width="22" height="3"/><rect x="2" y="4" width="6" height="3"/>
          <rect x="7" y="2" width="9" height="5"/><rect x="9" y="1" width="5" height="1"/>
          <rect x="16" y="4" width="6" height="3"/>
        </g>
        <g transform="translate(30,45)" opacity=".88">
          <rect x="0" y="7" width="22" height="3"/><rect x="2" y="4" width="6" height="3"/>
          <rect x="7" y="2" width="9" height="5"/><rect x="9" y="1" width="5" height="1"/>
          <rect x="16" y="4" width="6" height="3"/>
        </g>
        <g transform="translate(36,62)" opacity=".86">
          <rect x="0" y="7" width="22" height="3"/><rect x="2" y="4" width="6" height="3"/>
          <rect x="7" y="2" width="9" height="5"/><rect x="9" y="1" width="5" height="1"/>
          <rect x="16" y="4" width="6" height="3"/>
        </g>
      </g>
      <path class="hillFar" d="M140,150 L140,138 L146,138 L146,134 L153,134 L153,131 L160,131 L160,134 L167,134 L167,138 L170,138 L170,150 Z"/>
      <g transform="translate(10,124)">
        <path class="hillA" d="M0,26 L0,19 L7,19 L7,14 L14,14 L14,10 L22,10 L22,7 L28,7 L28,10 L34,10 L34,14 L41,14 L41,19 L48,19 L48,26 Z"/>
        <path class="hillB" d="M28,7 L32,7 L32,10 L38,10 L38,14 L45,14 L45,19 L52,19 L52,26 L28,26 Z"/>
      </g>
      <g class="castle" transform="translate(250,116)">
        <rect x="4" y="16" width="26" height="18"/><rect x="10" y="4" width="14" height="12"/>
        <rect x="0" y="10" width="10" height="24"/><rect x="24" y="10" width="10" height="24"/>
        <rect x="10" y="1" width="3" height="3"/><rect x="14" y="1" width="3" height="3"/>
        <rect x="18" y="1" width="3" height="3"/><rect x="0" y="7" width="3" height="3"/>
        <rect x="4" y="7" width="3" height="3"/><rect x="27" y="7" width="3" height="3"/>
        <rect x="31" y="7" width="3" height="3"/>
      </g>
      <g transform="translate(250,116)">
        <rect class="castleDoor" x="14" y="26" width="8" height="8"/>
        <g class="castleWin">
          <rect x="12" y="6" width="3" height="4"/><rect x="19" y="6" width="3" height="4"/>
          <rect x="4" y="16" width="3" height="4"/><rect x="27" y="16" width="3" height="4"/>
        </g>
      </g>
      <g class="bushes">
        <g transform="translate(66,142)"><rect x="0" y="5" width="18" height="3"/><rect x="2" y="3" width="5" height="2"/><rect x="7" y="1" width="6" height="4"/><rect x="14" y="3" width="4" height="2"/></g>
        <g transform="translate(104,142)"><rect x="0" y="5" width="18" height="3"/><rect x="2" y="3" width="5" height="2"/><rect x="7" y="1" width="6" height="4"/><rect x="14" y="3" width="4" height="2"/></g>
        <g transform="translate(168,142)"><rect x="0" y="5" width="18" height="3"/><rect x="2" y="3" width="5" height="2"/><rect x="7" y="1" width="6" height="4"/><rect x="14" y="3" width="4" height="2"/></g>
        <g transform="translate(214,142)"><rect x="0" y="5" width="18" height="3"/><rect x="2" y="3" width="5" height="2"/><rect x="7" y="1" width="6" height="4"/><rect x="14" y="3" width="4" height="2"/></g>
      </g>
    </svg>
    <div class="aHalo"></div>
    <div class="aGround"></div>
    <div class="aLight"></div>
    <div class="aClouds">
      <i class="c c1"/><i class="c c2"/><i class="c c3"/><i class="c c4"/><i class="c c5"/><i class="c c6"/>
    </div>
    <div class="aiDust"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
    <div class="aVig"></div>
  </div>
</template>

<style scoped>
/* ---------- 关卡背景（时间驱动） ---------- */
.px-sky{position:absolute;inset:0;z-index:0;overflow:hidden;pointer-events:none}
.px-sky>div{position:absolute}
.aSky{inset:0;background:var(--sky)}
.aArt{position:absolute;left:0;top:0;height:91%;width:100%;display:block;z-index:1}
.px-sky .clouds{fill:var(--cloud);opacity:0}
/* HTML 云层：像素云形状，单向从左漂到右穿过屏幕 */
.aClouds{position:absolute;inset:0;z-index:1;overflow:hidden;pointer-events:none}
.c{position:absolute;left:-160px;background:var(--cloud);image-rendering:pixelated;opacity:.92}
.c::before,.c::after{content:'';position:absolute;background:inherit}
.c1{width:110px;height:28px;top:7%;animation:cloud-cross 58s linear infinite}
.c1::before{width:30px;height:16px;left:18px;top:-16px}
.c1::after{width:42px;height:20px;right:22px;top:-20px}
.c2{width:80px;height:22px;top:18%;animation:cloud-cross 82s linear infinite;animation-delay:-30s;opacity:.85}
.c2::before{width:22px;height:12px;left:12px;top:-12px}
.c2::after{width:30px;height:15px;right:14px;top:-15px}
.c3{width:130px;height:30px;top:30%;animation:cloud-cross 72s linear infinite;animation-delay:-55s;opacity:.9}
.c3::before{width:36px;height:18px;left:22px;top:-18px}
.c3::after{width:48px;height:22px;right:26px;top:-22px}
.c4{width:70px;height:20px;top:45%;animation:cloud-cross 95s linear infinite;animation-delay:-15s;opacity:.8}
.c4::before{width:20px;height:11px;left:10px;top:-11px}
.c4::after{width:26px;height:14px;right:12px;top:-14px}
.c5{width:95px;height:26px;top:58%;animation:cloud-cross 66s linear infinite;animation-delay:-45s;opacity:.88}
.c5::before{width:26px;height:14px;left:16px;top:-14px}
.c5::after{width:36px;height:18px;right:18px;top:-18px}
.c6{width:60px;height:18px;top:72%;animation:cloud-cross 110s linear infinite;animation-delay:-70s;opacity:.75}
.c6::before{width:18px;height:10px;left:8px;top:-10px}
.c6::after{width:22px;height:12px;right:10px;top:-12px}
@keyframes cloud-cross{from{transform:translateX(0)}to{transform:translateX(calc(105vw + 160px))}}
.px-sky .hillFar{fill:var(--hillFar)}
.px-sky .hillA{fill:var(--hillA)}
.px-sky .hillB{fill:var(--hillB)}
.px-sky .bushes{fill:var(--bush)}
.px-sky .castle{fill:var(--castle)}
.px-sky .castleDoor{fill:var(--castleDoor)}
.px-sky .castleWin{fill:var(--castleWin);opacity:var(--castleWinOp)}
.px-sky .sun{transform:translate(var(--sunX),var(--sunY));opacity:var(--sunOp);
  transition:transform .6s steps(3,end),opacity .4s}
.px-sky .sunCore{fill:var(--sunCore)}
.px-sky .sunRim{fill:var(--sunRim)}
.px-sky .sunHalo{opacity:var(--sunHaloOp)}
.px-sky .moon{opacity:var(--moonOp);transition:opacity .4s}
.px-sky .stars{fill:#FFF8D8;opacity:var(--starOp);transition:opacity .4s}
.aGround{left:0;right:0;bottom:0;height:9%;z-index:3;
  background:
    repeating-linear-gradient(90deg,var(--gLine) 0 2px,transparent 2px 32px),
    repeating-linear-gradient(180deg,var(--gLine) 0 2px,transparent 2px 32px),
    repeating-linear-gradient(90deg,transparent 0 30px,var(--g2) 30px 32px),
    repeating-linear-gradient(180deg,var(--g1) 0 30px,var(--g2) 30px 32px);
  box-shadow:inset 0 3px 0 var(--gTop),inset 0 -28px 46px var(--gShadow)}
.aGround::before{content:'';position:absolute;left:0;right:0;top:0;height:3px;background:var(--gSeam)}
.aHalo{left:50%;top:34%;width:min(1500px,124vw);aspect-ratio:1;transform:translate(-50%,-50%);
  z-index:2;background:var(--halo)}
.aLight{inset:0;z-index:4;background:var(--mist)}
.aiDust{inset:0;z-index:5;opacity:.5}
.aiDust i{position:absolute;width:3px;height:3px;background:var(--dust);animation:drift 12s linear infinite}
.aiDust i:nth-child(1){left:7%;top:70%;animation-delay:-1s}
.aiDust i:nth-child(2){left:17%;top:38%;animation-delay:-4s;opacity:.5}
.aiDust i:nth-child(3){left:28%;top:82%;animation-delay:-7s}
.aiDust i:nth-child(4){left:41%;top:62%;animation-delay:-2.5s;opacity:.4}
.aiDust i:nth-child(5){left:59%;top:30%;animation-delay:-6s}
.aiDust i:nth-child(6){left:71%;top:76%;animation-delay:-9s;opacity:.45}
.aiDust i:nth-child(7){left:84%;top:46%;animation-delay:-3s;opacity:.35}
.aiDust i:nth-child(8){left:93%;top:84%;animation-delay:-8s}
@keyframes drift{0%{transform:translateY(0);opacity:0}18%{opacity:.7}80%{opacity:.45}100%{transform:translateY(-90px);opacity:0}}
.aVig{inset:0;z-index:6;background:var(--vig)}

/* ---------- 时段调色板 ---------- */
.px-sky{
  --sky:linear-gradient(180deg,#3E9AD6 0%,#5FB2E2 22%,#7FC7EE 48%,#9AD6F4 74%,#ADE1F7 100%);
  --cloud:#FFFFFF;
  --hillA:#4F9B52; --hillB:#3E8443; --hillFar:#6FBE74; --bush:#3E8443;
  --castle:#8FBFD8; --castleDoor:#79AECC; --castleWin:#FFF3C0; --castleWinOp:.2;
  --sunCore:#FFF6CE; --sunRim:#FFDE96; --sunOp:.9; --sunX:250px; --sunY:16px; --sunHaloOp:.4;
  --moonOp:0; --starOp:0;
  --g1:var(--c-brick1); --g2:var(--c-brick2); --gLine:rgba(255,226,170,.3); --gTop:rgba(255,214,150,.5);
  --gShadow:rgba(84,32,4,.42); --gSeam:rgba(42,18,6,.5);
  --halo:radial-gradient(closest-side,rgba(255,255,255,.16),rgba(255,255,255,.055) 40%,transparent 80%);
  --mist:linear-gradient(180deg,rgba(255,255,255,.14),transparent 20%);
  --vig:radial-gradient(122% 92% at 50% 44%,transparent 50%,rgba(22,64,102,.1) 88%,rgba(22,64,102,.2) 100%);
  --dust:rgba(255,255,255,.85);
  --scanOp:.05;
  --frame:rgba(255,251,240,.94);
  --frameInk:#6B4A22;
  /* --- 组件调色板（白天） --- */
  --paperHi:#FFFEFA; --paperLo:#E8DEC4;
  --c-iron1:#4A3320; --c-iron2:#33220F; --c-iron3:#241608;
  --c-nav1:#422D1C; --c-nav2:#2B1C0E; --c-nav3:#1C1006;
  --c-gold1:#FFD24A; --c-gold2:#E0A010; --c-gold3:#E9A912; --c-goldD2:#C9900A;
  --c-green1:#3E9B3E; --c-green2:#2F7D32; --c-green3:#1E5C1E; --c-green3b:#1B4A1E; --c-greenS:#143C0D;
  --c-tile1:#D08B22; --c-tile2:#A9610F; --c-tile3:#6E3E06;
  --c-skip1:#7A5330; --c-skip2:#4E331A; --c-skip3:#2C1A0A;
  --c-hud1:#FFFBF0; --c-hud2:#F3ECDC;
  --c-brick1:#C9762E; --c-brick2:#96501A;
}
:global(html[data-tod="dawn"] .px-sky){
  /* --- 组件调色板（清晨：暖粉奶油） --- */
  --paper:#FFF8F1; --paper2:#FBEEDF; --paper3:#EFDFCB; --paperHi:#FFFDF9; --paperLo:#F0DFCE;
  --gold:#FFD25A; --goldL:#FFE7A0; --goldD:#9A6014;
  --brick:#D2711E; --brickL:#EE8A33;
  --green:#4C9E4C; --greenL:#86D286; --greenD:#1F5A1F;
  --c-iron1:#5A3B2E; --c-iron2:#3E271C; --c-iron3:#2A1811;
  --c-nav1:#4E3327; --c-nav2:#342017; --c-nav3:#22120C;
  --c-gold1:#FFD25A; --c-gold2:#E4A61A; --c-gold3:#EDAF1C; --c-goldD2:#C9900A;
  --c-green1:#4C9E4C; --c-green2:#357C36; --c-green3:#1F5A1F; --c-green3b:#1C4A1E; --c-greenS:#17400F;
  --c-tile1:#D69029; --c-tile2:#AE6512; --c-tile3:#713F07;
  --c-skip1:#7E5433; --c-skip2:#52351B; --c-skip3:#2E1B0B;
  --c-hud1:#FFF8F1; --c-hud2:#F7E8D9;
  --c-brick1:#CF7A34; --c-brick2:#9A531C;
  --sky:linear-gradient(180deg,#DE6E58 0%,#EE9A6C 24%,#F6C08A 50%,#FBE0B4 74%,#FEF3DC 100%);
  --cloud:#FFEDE0;
  --hillA:#7FA576; --hillB:#6B8F68; --hillFar:#9FBE94; --bush:#6B8F68;
  --castle:#DDB79F; --castleDoor:#C49C86; --castleWin:#FFF0C0; --castleWinOp:.35;
  --sunCore:#FFEFC0; --sunRim:#FFA85C; --sunOp:1; --sunX:232px; --sunY:98px; --sunHaloOp:.7;
  --moonOp:0; --starOp:0;
  --g1:#B0672A; --g2:#7E4212; --gLine:rgba(255,232,190,.34); --gTop:rgba(255,226,180,.55);
  --gShadow:rgba(96,42,8,.4); --gSeam:rgba(84,38,10,.45);
  --halo:radial-gradient(closest-side,rgba(255,226,180,.28),rgba(255,206,150,.1) 42%,transparent 78%);
  --mist:linear-gradient(180deg,rgba(255,228,196,.32),rgba(255,216,176,.09) 28%,transparent 46%);
  --vig:radial-gradient(120% 90% at 50% 46%,transparent 48%,rgba(120,52,30,.12) 86%,rgba(120,52,30,.24) 100%);
  --dust:rgba(255,242,222,.9);
  --frame:rgba(255,250,240,.95); --frameInk:#6B4A22;
}
:global(html[data-tod="dusk"] .px-sky){
  /* --- 组件调色板（傍晚：琥珀） --- */
  --paper:#FFF4E2; --paper2:#FAE8CE; --paper3:#EDD6B8; --paperHi:#FFFCF4; --paperLo:#EFD9BE;
  --gold:#FFC83C; --goldL:#FFDD7A; --goldD:#7E4E0E;
  --brick:#C05E22; --brickL:#E67C36;
  --green:#438F45; --greenL:#7CC47C; --greenD:#1A4C1A;
  --c-iron1:#4E3122; --c-iron2:#34200F; --c-iron3:#241306;
  --c-nav1:#452A1B; --c-nav2:#2C1A0D; --c-nav3:#1B0F05;
  --c-gold1:#FFC83C; --c-gold2:#DE9410; --c-gold3:#E39E12; --c-goldD2:#B9820C;
  --c-green1:#438F45; --c-green2:#2C6E2F; --c-green3:#1A4C1A; --c-green3b:#173F1A; --c-greenS:#12380C;
  --c-tile1:#C46B26; --c-tile2:#8E4613; --c-tile3:#5E2C06;
  --c-skip1:#74472C; --c-skip2:#4A2A16; --c-skip3:#281407;
  --c-hud1:#FFF4E2; --c-hud2:#F4E2CC;
  --c-brick1:#C05E22; --c-brick2:#8A3F10;
  --sky:linear-gradient(180deg,#2B4674 0%,#5C5588 20%,#A9638A 40%,#D97A6C 58%,#F0995C 76%,#FBC47E 92%,#FFE0A8 100%);
  --cloud:#F7B0A4;
  --hillA:#5A3B62; --hillB:#4A2E52; --hillFar:#6A4874; --bush:#43304F;
  --castle:#5A3C62; --castleDoor:#422848; --castleWin:#FFCF7A; --castleWinOp:.9;
  --sunCore:#FFE9A8; --sunRim:#FFC46A; --sunOp:1; --sunX:236px; --sunY:92px; --sunHaloOp:.9;
  --moonOp:0; --starOp:.25;
  --g1:#8A4A26; --g2:#3A1E10; --gLine:rgba(255,200,140,.28); --gTop:rgba(255,200,150,.45);
  --gShadow:rgba(40,16,30,.5); --gSeam:rgba(28,12,26,.45);
  --halo:radial-gradient(closest-side,rgba(255,216,150,.3),rgba(255,190,120,.1) 40%,transparent 80%);
  --mist:linear-gradient(180deg,rgba(255,214,170,.2),transparent 18%);
  --vig:radial-gradient(118% 90% at 50% 46%,transparent 48%,rgba(58,26,52,.12) 88%,rgba(58,26,52,.24) 100%);
  --dust:rgba(255,226,180,.9);
  --frame:rgba(255,248,238,.95); --frameInk:#6B3A2E;
}
:global(html[data-tod="night"] .px-sky){
  /* --- 组件调色板（夜晚：月光冷调） --- */
  --paper:#E7EDF7; --paper2:#D9E2F0; --paper3:#C4D0E2; --paperHi:#F6FAFF; --paperLo:#C9D5E6;
  --gold:#F4DC96; --goldL:#FFF0C4; --goldD:#6E5A32;
  --brick:#5E6B84; --brickL:#808EA8;
  --green:#3E8F72; --greenL:#74B79B; --greenD:#17453A;
  --frame:rgba(11,21,35,.96); --frameInk:#93AAC8;
  --c-iron1:#2C3B50; --c-iron2:#1C2738; --c-iron3:#111A26;
  --c-nav1:#263346; --c-nav2:#17212E; --c-nav3:#0E1520;
  --c-gold1:#F4DC96; --c-gold2:#CBAC5C; --c-gold3:#D4B566; --c-goldD2:#8A7440;
  --c-green1:#3E8F72; --c-green2:#2A6B57; --c-green3:#17453A; --c-green3b:#153B31; --c-greenS:#0F2E26;
  --c-tile1:#A2894E; --c-tile2:#7A6537; --c-tile3:#4A3C1E;
  --c-skip1:#3A4458; --c-skip2:#232C3C; --c-skip3:#131A26;
  --c-hud1:#16263C; --c-hud2:#0C1726;
  --c-brick1:#5E6B84; --c-brick2:#37425A;
  --sky:linear-gradient(180deg,#050B1E 0%,#0A1730 28%,#12294A 56%,#0E2038 80%,#071322 100%);
  --cloud:#233A5E;
  --hillA:#17304A; --hillB:#101F33; --hillFar:#1D3C58; --bush:#101F33;
  --castle:#16283E; --castleDoor:#0B1622; --castleWin:#FFC85A; --castleWinOp:1;
  --sunOp:0; --sunX:250px; --sunY:60px; --sunHaloOp:0;
  --moonOp:1; --starOp:1;
  --g1:#3A2416; --g2:#1A0E06; --gLine:rgba(255,190,120,.16); --gTop:rgba(255,190,120,.28);
  --gShadow:rgba(0,0,0,.62); --gSeam:rgba(0,0,0,.6);
  --halo:radial-gradient(closest-side,rgba(255,196,90,.2),rgba(255,176,70,.07) 40%,transparent 78%);
  --mist:linear-gradient(180deg,rgba(24,64,110,.26),transparent 24%);
  --vig:radial-gradient(106% 82% at 50% 46%,transparent 38%,rgba(2,6,14,.5) 82%,rgba(2,6,14,.78) 100%);
  --dust:rgba(255,226,120,.75);
  --scanOp:.1;
}



.px-sky { position: fixed; }
@media (prefers-reduced-motion: reduce) {
  .aiDust i, .c { animation: none; }
  .c1{left:5%}.c2{left:35%}.c3{left:70%}.c4{left:15%}.c5{left:50%}.c6{left:80%}
}
</style>
