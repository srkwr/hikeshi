// matoi_v2.jsx — refined 纏 that reads unmistakably as a matoi (not a candle).
// Key moves vs v1:
//  • 馬簾 (baren fringe) is the hero — bold strips, clearly WIDER than the pole,
//    hung from an explicit collar bar so it reads as "fringe", not candle wax.
//  • The pole is a short handle BELOW the fringe (a long thin stick = candle).
//  • The head is a brigade EMBLEM; fire lives as a flame knocked OUT of a disc,
//    so it never reads as a candle flame standing on a stick.
//  • Works as a single-color silhouette.
const M2 = window.HIKESHI_PAL;

function r2Pole(ink, foot) {
  const els = [<rect key="p" x="47" y="60" width="6" height={foot ? 22 : 26} rx="3" fill={ink} />];
  if (foot) els.push(<rect key="f" x="39" y="83" width="22" height="6" rx="3" fill={ink} />);
  return els;
}

// 馬簾 — fanned fringe (the "H" style you liked): 9 strips, wide spread
function r2Baren(ink) {
  const n = 9, spread = 42, L = 23;
  return Array.from({ length: n }, (_, i) => {
    const a = -spread + i * ((2 * spread) / (n - 1));
    return (
      <rect key={i} x="48.6" y="37" width="2.8" height={L} rx="1.4" fill={ink}
        transform={`rotate(${a} 50 37)`} />
    );
  });
}

// flame silhouettes for the disc knockout (local coords around the disc center 50,20)
const FLAMES = {
  // clean teardrop flame: pointed top, rounded bottom — simple, reads as fire
  simple: { outer: 'M50 6 C53 13 57 17.5 57 23 C57 28.6 53.8 32 50 32 C46.2 32 43 28.6 43 23 C43 17.5 47 13 50 6 Z' },
  // leaf/almond (the original) — kept only for reference
  leaf: { outer: 'M50 11 C57.5 18 57.5 24 50 29.5 C42.5 24 42.5 18 50 11 Z' },
};

const HEAD_FLAME = 'simple'; // default flame used by the ember head

function emberHead(a, key) {
  const f = FLAMES[key] || FLAMES.lick;
  return (
    <g>
      <circle cx="50" cy="20" r="16" fill={a} />
      <path d={f.outer} fill={M2.paper} />
      {f.extra && <path d={f.extra} fill={M2.paper} />}
      {f.inner && <path d={f.inner} fill={a} />}
    </g>
  );
}

const HEADS2 = {
  // simple single-shape heads
  disc: (ink, a) => <circle cx="50" cy="20" r="16" fill={a} />,
  ring: (ink, a) => <circle cx="50" cy="20" r="13" fill="none" stroke={a} strokeWidth="6" />,
  diamond: (ink, a) => <rect x="37" y="7" width="26" height="26" rx="3" transform="rotate(45 50 20)" fill={a} />,
  // D(ring) × E(diamond) — a hollowed-out diamond frame
  hishi: (ink, a) => <rect x="38.5" y="8.5" width="23" height="23" rx="3" transform="rotate(45 50 20)" fill="none" stroke={a} strokeWidth="6" strokeLinejoin="round" />,
  triangle: (ink, a) => <path d="M50 6 L62.5 33 L37.5 33 Z" fill={a} strokeLinejoin="round" />,
  drop: (ink, a) => <path d="M50 6 C56 14 57 19 57 24 C57 29.5 54 33 50 33 C46 33 43 29.5 43 24 C43 19 44 14 50 6 Z" fill={a} />,
  // fire as negative space inside a disc (a touch more detail)
  ember: (ink, a) => emberHead(a, HEAD_FLAME),
};

function RefinedMatoi({ head = 'ember', ink = M2.ink, accent = M2.accent, s = 120, mono, foot = false }) {
  const headColor = mono ? ink : accent;
  return (
    <svg viewBox="0 0 100 100" width={s} height={s} aria-label={'Hikeshi matoi ' + head}>
      {r2Pole(ink, foot)}
      {r2Baren(ink)}
      <rect x="40" y="33" width="20" height="5" rx="2.5" fill={ink} />
      {HEADS2[head](ink, headColor)}
    </svg>
  );
}

// large disc + flame, centered — for comparing flame shapes
function FlameDisc({ flame = 'lick', s = 120, accent = M2.accent }) {
  const f = FLAMES[flame] || FLAMES.lick;
  return (
    <svg viewBox="0 0 100 100" width={s} height={s} aria-label={'flame ' + flame}>
      <g transform="translate(50 50) scale(2.1) translate(-50 -20)">
        <circle cx="50" cy="20" r="16" fill={accent} />
        <path d={f.outer} fill={M2.paper} />
        {f.extra && <path d={f.extra} fill={M2.paper} />}
        {f.inner && <path d={f.inner} fill={accent} />}
      </g>
    </svg>
  );
}

// Parameterized 菱環 (diamond-ring) head + full matoi for fine-tuning.
function hishiHead(a, { side = 23, sw = 6, nested } = {}) {
  const o = side, x = 50 - o / 2, y = 20 - o / 2;
  const els = [
    <rect key="o" x={x} y={y} width={o} height={o} rx="3" transform="rotate(45 50 20)"
      fill="none" stroke={a} strokeWidth={sw} strokeLinejoin="round" />,
  ];
  const diamond = (k, ssz, fill, stroke, ssw) => {
    const ix = 50 - ssz / 2, iy = 20 - ssz / 2;
    return <rect key={k} x={ix} y={iy} width={ssz} height={ssz} rx="2" transform="rotate(45 50 20)"
      fill={fill} stroke={stroke} strokeWidth={ssw} strokeLinejoin="round" />;
  };
  if (nested === 'solid') els.push(diamond('i', side * 0.42, a, 'none', 0));
  else if (nested === 'frame') els.push(diamond('i', side * 0.56, 'none', a, Math.max(2.4, sw * 0.55)));
  else if (nested === 'triple') {
    els.push(diamond('i', side * 0.6, 'none', a, Math.max(2.2, sw * 0.5)));
    els.push(diamond('c', side * 0.22, a, 'none', 0));
  }
  return <g>{els}</g>;
}

function HishiMark({ side = 23, sw = 6, nested, s = 120, ink = M2.ink, accent = M2.accent, mono }) {
  const a = mono ? ink : accent;
  return (
    <svg viewBox="0 0 100 100" width={s} height={s} aria-label="Hikeshi hishi matoi">
      {r2Pole(ink, false)}
      {r2Baren(ink)}
      <rect x="40" y="33" width="20" height="5" rx="2.5" fill={ink} />
      {hishiHead(a, { side, sw, nested })}
    </svg>
  );
}

Object.assign(window, { RefinedMatoi, FlameDisc, HishiMark });
