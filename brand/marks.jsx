// marks.jsx — Hikeshi logo mark library
// All marks are geometric, built to read at favicon size.
// Palette is passed in so each mark can be recolored (ink / accent / paper).

const PAL = { paper: '#f4efe6', ink: '#1c1c1c', accent: '#b8392c' };

// 1 — 纏 / Matoi. The Edo firefighter's standard: head, hanging fringe (馬簾), pole.
function MatoiMark({ s = 120, ink = PAL.ink, accent = PAL.accent }) {
  const strips = Array.from({ length: 9 }, (_, i) => 30 + i * 5);
  return (
    <svg viewBox="0 0 100 100" width={s} height={s} aria-label="Hikeshi matoi mark">
      <rect x="47" y="40" width="6" height="48" rx="3" fill={ink} />
      <rect x="38" y="84" width="24" height="6" rx="3" fill={ink} />
      <circle cx="50" cy="23" r="15" fill={accent} />
      {strips.map((x) => (
        <rect key={x} x={x - 1.35} y="40" width="2.7" height="17" rx="1.35" fill={ink} />
      ))}
    </svg>
  );
}

// 2 — 円相 + 炎 / Ensō ring containing a flame. Containment = HITL control valve.
function EnsoMark({ s = 120, ink = PAL.ink, accent = PAL.accent }) {
  return (
    <svg viewBox="0 0 100 100" width={s} height={s} aria-label="Hikeshi ensō mark">
      <circle
        cx="50" cy="50" r="35" fill="none" stroke={ink} strokeWidth="8"
        strokeLinecap="round" strokeDasharray="186 40" transform="rotate(-52 50 50)"
      />
      <path d="M50 32 C61 46 62 57 50 68 C38 57 39 46 50 32 Z" fill={accent} />
    </svg>
  );
}

// 3 — 消火＋防火 / Two mirrored flames. Up = proactive (防火), down = reactive (消火).
function DualMark({ s = 120, ink = PAL.ink, accent = PAL.accent }) {
  return (
    <svg viewBox="0 0 100 100" width={s} height={s} aria-label="Hikeshi dual mark">
      <path d="M50 20 C62 34 62 46 50 50 C38 46 38 34 50 20 Z" fill={accent} />
      <path d="M50 80 C62 66 62 54 50 50 C38 54 38 66 50 80 Z" fill={ink} />
    </svg>
  );
}

// 4 — 制御弁 / Control valve. Engineering gate-valve glyph with a flame core.
function ValveMark({ s = 120, ink = PAL.ink, accent = PAL.accent }) {
  return (
    <svg viewBox="0 0 100 100" width={s} height={s} aria-label="Hikeshi valve mark">
      <circle cx="50" cy="50" r="34" fill="none" stroke={ink} strokeWidth="6" />
      <path d="M27 33 L27 67 L50 50 Z" fill={ink} />
      <path d="M73 33 L73 67 L50 50 Z" fill={ink} />
      <circle cx="50" cy="50" r="7.5" fill={accent} />
    </svg>
  );
}

// 5 — H モノグラム + 炎 / The H with a flame for its crossbar.
function HMark({ s = 120, ink = PAL.ink, accent = PAL.accent }) {
  return (
    <svg viewBox="0 0 100 100" width={s} height={s} aria-label="Hikeshi H monogram">
      <rect x="22" y="22" width="11" height="56" rx="3" fill={ink} />
      <rect x="67" y="22" width="11" height="56" rx="3" fill={ink} />
      <rect x="33" y="46" width="34" height="9" rx="2.5" fill={accent} />
      <path d="M50 27 C58 37 58 45 50 46 C42 45 42 37 50 27 Z" fill={accent} />
    </svg>
  );
}

// 6 — 刺子の炎 / Sashiko (running-stitch) flame, nodding to the fireman's quilted coat.
function SashikoMark({ s = 120, ink = PAL.ink, accent = PAL.accent }) {
  return (
    <svg viewBox="0 0 100 100" width={s} height={s} aria-label="Hikeshi sashiko mark">
      <path
        d="M50 24 C67 43 68 60 50 78 C32 60 33 43 50 24 Z" fill="none"
        stroke={accent} strokeWidth="3.4" strokeLinecap="round" strokeDasharray="0.1 6"
      />
      <path
        d="M50 40 C59 50 59 58 50 67 C41 58 41 50 50 40 Z" fill="none"
        stroke={ink} strokeWidth="2.6" strokeLinecap="round" strokeDasharray="0.1 6" opacity="0.85"
      />
    </svg>
  );
}

// Seal / 印 treatment — vermilion stamp with a knocked-out glyph. Great app icon.
function SealMark({ s = 120, accent = PAL.accent, paper = PAL.paper, glyph = 'flame' }) {
  return (
    <svg viewBox="0 0 100 100" width={s} height={s} aria-label="Hikeshi seal">
      <rect x="3" y="3" width="94" height="94" rx="24" fill={accent} />
      {glyph === 'flame' && (
        <path d="M50 28 C66 47 67 61 50 76 C33 61 34 47 50 28 Z" fill={paper} />
      )}
      {glyph === 'hi' && (
        <text x="50" y="53" textAnchor="middle" dominantBaseline="central"
          fontFamily="'Shippori Mincho B1', serif" fontWeight="700" fontSize="60" fill={paper}>火</text>
      )}
      {glyph === 'matoi' && (
        <g>
          <rect x="47" y="42" width="6" height="40" rx="3" fill={paper} />
          <rect x="40" y="79" width="20" height="5" rx="2.5" fill={paper} />
          <circle cx="50" cy="28" r="12" fill={paper} />
          {Array.from({ length: 7 }, (_, i) => 34 + i * 5.3).map((x) => (
            <rect key={x} x={x - 1.1} y="41" width="2.2" height="14" rx="1.1" fill={paper} />
          ))}
        </g>
      )}
    </svg>
  );
}

// Wordmark — "Hikeshi" set in the chosen type.
function Wordmark({
  color = PAL.ink, size = 40, weight = 600,
  font = "'Space Grotesk', sans-serif", spacing = '0.005em', lower = true,
}) {
  return (
    <span style={{
      fontFamily: font, fontWeight: weight, fontSize: size,
      letterSpacing: spacing, color, lineHeight: 1, whiteSpace: 'nowrap',
    }}>{lower ? 'Hikeshi' : 'HIKESHI'}</span>
  );
}

Object.assign(window, {
  HIKESHI_PAL: PAL,
  MatoiMark, EnsoMark, DualMark, ValveMark, HMark, SashikoMark, SealMark, Wordmark,
});
