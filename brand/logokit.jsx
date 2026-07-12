// logokit.jsx — Hikeshi brand & logo kit (confirmed mark: 菱環・入れ子実芯)
const { HishiMark, Wordmark, HIKESHI_PAL } = window;

const C = {
  kinari: '#F4EFE6', kinariDeep: '#ECE4D6', line: '#E3DCCD',
  sumi: '#1C1C1C', shu: '#B8392C', muted: '#8A7F70', mutedDark: '#9A8F80',
};
const SANS = "'Space Grotesk', sans-serif";
const MONO = "'IBM Plex Mono', monospace";
const MINCHO = "'Shippori Mincho B1', serif";

// confirmed mark spec
const SPEC = { side: 26, sw: 5, nested: 'solid' };
function Logo({ s = 120, ink = C.sumi, accent = C.shu, mono }) {
  return <HishiMark side={SPEC.side} sw={SPEC.sw} nested={SPEC.nested} s={s} ink={ink} accent={accent} mono={mono} />;
}

// ── shared layout bits ────────────────────────────────────────────
function Eyebrow({ children, color = C.muted }) {
  return <div style={{ fontFamily: MONO, fontSize: 12, letterSpacing: '0.26em', textTransform: 'uppercase', color }}>{children}</div>;
}
function SectionHead({ no, title, note }) {
  return (
    <div style={{ marginBottom: 36, borderTop: `1px solid ${C.line}`, paddingTop: 26 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
        <span style={{ fontFamily: MONO, fontSize: 13, color: C.shu, letterSpacing: '0.1em', lineHeight: '34px' }}>{no}</span>
        <h2 style={{ fontFamily: SANS, fontSize: 26, fontWeight: 600, color: C.sumi, margin: 0, letterSpacing: '-0.01em', lineHeight: 1.25 }}>{title}</h2>
      </div>
      {note && <p style={{ fontFamily: SANS, fontSize: 15, color: '#6b6253', margin: '14px 0 0', maxWidth: 640, lineHeight: 1.6 }}>{note}</p>}
    </div>
  );
}
const Section = ({ children, ...p }) => (
  <section style={{ marginBottom: 72 }}>
    <SectionHead {...p} />
    {children}
  </section>
);

// ── sections ──────────────────────────────────────────────────────
function Hero() {
  return (
    <header style={{ paddingTop: 64, paddingBottom: 64 }}>
      <Eyebrow color={C.shu}>HIKESHI · 火消し</Eyebrow>
      <h1 style={{ fontFamily: SANS, fontSize: 52, fontWeight: 600, color: C.sumi, margin: '18px 0 0', letterSpacing: '-0.02em' }}>ロゴ & カラー キット</h1>
      <p style={{ fontFamily: SANS, fontSize: 17, color: '#6b6253', margin: '14px 0 0', maxWidth: 560, lineHeight: 1.6 }}>
        少人数SREのための、信頼（HITL）内蔵のAI時代の運用エージェント。プロダクト／スライドで使う各種ロゴと、3色のプロダクトカラー。
      </p>
      <div style={{ marginTop: 56, background: C.kinari, border: `1px solid ${C.line}`, borderRadius: 6, padding: '64px 0', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 26 }}>
        <Logo s={188} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <Wordmark color={C.sumi} size={44} weight={600} />
        </div>
        <Eyebrow>纏 × 火の紋（入れ子・実芯）</Eyebrow>
      </div>
    </header>
  );
}

function Swatch({ name, jp, hex, role, dark }) {
  return (
    <div style={{ border: `1px solid ${C.line}`, borderRadius: 6, overflow: 'hidden', background: '#fff' }}>
      <div style={{ background: hex, height: 150 }} />
      <div style={{ padding: '18px 18px 20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span style={{ fontFamily: SANS, fontWeight: 600, fontSize: 17, color: C.sumi }}>{name}</span>
          <span style={{ fontFamily: MINCHO, fontSize: 17, color: C.sumi }}>{jp}</span>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 14, color: C.shu, marginTop: 8, letterSpacing: '0.04em' }}>{hex}</div>
        <div style={{ fontFamily: SANS, fontSize: 13.5, color: '#6b6253', marginTop: 10, lineHeight: 1.5 }}>{role}</div>
      </div>
    </div>
  );
}

function Colors() {
  return (
    <Section no="01" title="プロダクトカラー" note="ロゴの2色＋背景の生成りで三色。背景は純白ではなく、わずかに温かい生成り（#F4EFE6）。これがブランドのベースになります。">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
        <Swatch name="Kinari" jp="生成り" hex="#F4EFE6" role="背景／ベース。純白を使わず、紙のような温かい下地に。" />
        <Swatch name="Sumi" jp="墨" hex="#1C1C1C" role="文字・支柱・房。主たる前景色。" />
        <Swatch name="Shu" jp="朱" hex="#B8392C" role="アクセント／火。頭の紋と強調に限定して効かせる。" />
      </div>
    </Section>
  );
}

function LockupCard({ label, children, h = 200, bg = C.kinari }) {
  return (
    <div style={{ border: `1px solid ${C.line}`, borderRadius: 6, overflow: 'hidden' }}>
      <div style={{ background: bg, height: h, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{children}</div>
      <div style={{ padding: '12px 16px', background: '#fff', borderTop: `1px solid ${C.line}` }}>
        <Eyebrow>{label}</Eyebrow>
      </div>
    </div>
  );
}

function Lockups() {
  return (
    <Section no="02" title="ロゴ構成（ロックアップ）" note="横組みを基本に、縦積み・シンボル単体を用意。タグラインは「消火 ＋ 防火」。">
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 20, marginBottom: 20 }}>
        <LockupCard label="横組み · PRIMARY">
          <div style={{ display: 'flex', alignItems: 'center', gap: 22 }}>
            <Logo s={76} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <Wordmark color={C.sumi} size={46} weight={600} />
              <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: '0.24em', color: C.muted }}>消火 ＋ 防火</div>
            </div>
          </div>
        </LockupCard>
        <LockupCard label="縦積み · STACKED">
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14 }}>
            <Logo s={84} />
            <Wordmark color={C.sumi} size={34} weight={600} />
          </div>
        </LockupCard>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
        <LockupCard label="シンボル単体" h={170}><Logo s={96} /></LockupCard>
        <LockupCard label="ワードマーク単体" h={170}><Wordmark color={C.sumi} size={40} weight={600} /></LockupCard>
        <LockupCard label="モノ · 墨" h={170}><Logo s={96} mono /></LockupCard>
      </div>
    </Section>
  );
}

function Variants() {
  const tiles = [
    { label: 'PRIMARY · 生成り地', bg: C.kinari, node: <Logo s={104} /> },
    { label: 'REVERSED · 墨地', bg: C.sumi, node: <Logo s={104} ink={C.kinari} accent={C.shu} />, eb: C.mutedDark },
    { label: 'MONO · 墨（生成り地）', bg: C.kinari, node: <Logo s={104} mono /> },
    { label: 'KNOCKOUT · 朱地', bg: C.shu, node: <Logo s={104} ink={C.kinari} accent={C.kinari} />, eb: '#f4d9d2' },
    { label: 'MONO · 生成り（墨地）', bg: C.sumi, node: <Logo s={104} mono ink={C.kinari} />, eb: C.mutedDark },
    { label: 'MONO · 朱（生成り地）', bg: C.kinari, node: <Logo s={104} mono ink={C.shu} /> },
  ];
  return (
    <Section no="03" title="配色バリエーション" note="地の色に応じて使い分け。朱は前景にも背景にも置けるが、墨と朱を同時に弱い地で重ねない。">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
        {tiles.map((t, i) => (
          <div key={i} style={{ border: `1px solid ${C.line}`, borderRadius: 6, overflow: 'hidden' }}>
            <div style={{ background: t.bg, height: 190, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{t.node}</div>
            <div style={{ padding: '12px 16px', background: '#fff', borderTop: `1px solid ${C.line}` }}>
              <Eyebrow>{t.label}</Eyebrow>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function IconTile({ bg, border, node, label }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
      <div style={{ width: 128, height: 128, borderRadius: 28, background: bg, border: border || 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 8px 22px rgba(28,20,12,0.14)' }}>{node}</div>
      <Eyebrow>{label}</Eyebrow>
    </div>
  );
}

function AppIcons() {
  const sizes = [16, 24, 32, 48, 64];
  return (
    <Section no="04" title="アプリアイコン" note="favicon を含む。角丸タイルに収める。極小サイズでは線の細部が潰れるため、最小は 24px を目安に。">
      <div style={{ display: 'flex', gap: 40, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <IconTile bg={C.shu} node={<Logo s={92} ink={C.kinari} accent={C.kinari} />} label="朱地 · 白" />
        <IconTile bg={C.sumi} node={<Logo s={92} ink={C.kinari} accent={C.shu} />} label="墨地 · 朱" />
        <IconTile bg={C.kinari} border={`1px solid ${C.line}`} node={<Logo s={92} />} label="生成り地" />
      </div>
      <div style={{ marginTop: 40, border: `1px solid ${C.line}`, borderRadius: 6, background: '#fff', padding: '28px 30px' }}>
        <Eyebrow>favicon · 実寸</Eyebrow>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 30, marginTop: 22 }}>
          {sizes.map((px) => (
            <div key={px} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
              <Logo s={px} />
              <span style={{ fontFamily: MONO, fontSize: 10, color: C.muted }}>{px}px</span>
            </div>
          ))}
        </div>
      </div>
    </Section>
  );
}

function ClearSpace() {
  return (
    <Section no="05" title="アイソレーション & 最小サイズ" note="ロゴの周囲には、頭（菱環）の高さ分の余白を確保。最小サイズはデジタルで 24px、印刷で 8mm。">
      <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 20 }}>
        <div style={{ border: `1px solid ${C.line}`, borderRadius: 6, background: C.kinari, padding: 40, position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ position: 'relative', padding: 38, border: `1px dashed ${C.shu}`, borderRadius: 4 }}>
            <Logo s={150} />
            <span style={{ position: 'absolute', top: 8, left: 10, fontFamily: MONO, fontSize: 10, color: C.shu, letterSpacing: '0.1em' }}>CLEAR SPACE = X</span>
          </div>
        </div>
        <div style={{ border: `1px solid ${C.line}`, borderRadius: 6, background: '#fff', padding: 30, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 26 }}>
          <div>
            <Eyebrow>最小サイズ · デジタル</Eyebrow>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 16, marginTop: 16 }}>
              <Logo s={24} />
              <span style={{ fontFamily: MONO, fontSize: 12, color: C.muted }}>24px ↑</span>
            </div>
          </div>
          <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 22 }}>
            <Eyebrow>余白の基準 X</Eyebrow>
            <p style={{ fontFamily: SANS, fontSize: 14, color: '#6b6253', margin: '10px 0 0', lineHeight: 1.6 }}>X＝頭（菱環）の高さ。周囲4辺にXを確保し、他要素と干渉させない。</p>
          </div>
        </div>
      </div>
    </Section>
  );
}

function InUse() {
  return (
    <Section no="06" title="使用例" note="プロダクトとスライドでの見え方。">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {/* product nav (dark) */}
        <div style={{ border: `1px solid ${C.line}`, borderRadius: 6, overflow: 'hidden' }}>
          <div style={{ background: '#16130f' }}>
            <div style={{ height: 60, display: 'flex', alignItems: 'center', padding: '0 22px', borderBottom: '1px solid rgba(244,239,230,0.08)', gap: 12 }}>
              <span style={{ flexShrink: 0, display: 'inline-flex' }}><Logo s={30} ink={C.kinari} accent={C.shu} /></span>
              <span style={{ flexShrink: 0, display: 'inline-flex' }}><Wordmark color={C.kinari} size={20} weight={600} /></span>
              <div style={{ flex: 1 }} />
              {['Incidents', 'Runbooks', 'Eval'].map((t) => (
                <span key={t} style={{ fontFamily: MONO, fontSize: 11.5, letterSpacing: '0.06em', color: '#a89e8e', marginLeft: 16, flexShrink: 0 }}>{t}</span>
              ))}
              <div style={{ width: 28, height: 28, borderRadius: '50%', background: C.shu, marginLeft: 18, flexShrink: 0 }} />
            </div>
            <div style={{ padding: 24 }}>
              <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: '0.16em', color: '#6f6757' }}>● LIVE · 0 OPEN INCIDENTS</div>
              <div style={{ fontFamily: SANS, fontSize: 23, color: C.kinari, marginTop: 12, fontWeight: 500 }}>夜は、当番1人でいい。</div>
            </div>
          </div>
          <div style={{ padding: '12px 16px', background: '#fff', borderTop: `1px solid ${C.line}` }}><Eyebrow>プロダクト · トップナビ</Eyebrow></div>
        </div>
        {/* slide header (kinari) */}
        <div style={{ border: `1px solid ${C.line}`, borderRadius: 6, overflow: 'hidden' }}>
          <div style={{ background: C.kinari, height: 218, padding: '26px 30px', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <Logo s={26} />
              <span style={{ fontFamily: MONO, fontSize: 11, letterSpacing: '0.3em', color: C.sumi }}>HIKESHI</span>
              <span style={{ fontFamily: MONO, fontSize: 11, letterSpacing: '0.3em', color: '#b3a796' }}>｜ DESIGN SHARE</span>
            </div>
            <div style={{ flex: 1 }} />
            <div style={{ fontFamily: SANS, fontSize: 30, fontWeight: 600, color: C.sumi, letterSpacing: '-0.01em', lineHeight: 1.2 }}>消火＋防火を、<span style={{ color: C.shu }}>HITL内蔵</span>で回す</div>
          </div>
          <div style={{ padding: '12px 16px', background: '#fff', borderTop: `1px solid ${C.line}` }}><Eyebrow>スライド · ヘッダ</Eyebrow></div>
        </div>
      </div>
    </Section>
  );
}

function App() {
  return (
    <div style={{ background: C.kinari, minHeight: '100vh', paddingBottom: 90 }}>
      <div style={{ maxWidth: 1080, margin: '0 auto', padding: '0 40px' }}>
        <Hero />
        <Colors />
        <Lockups />
        <Variants />
        <AppIcons />
        <ClearSpace />
        <InUse />
        <footer style={{ borderTop: `1px solid ${C.line}`, paddingTop: 24 }}>
          <Eyebrow>HIKESHI · BRAND KIT · 2026 — 生成り #F4EFE6 / 墨 #1C1C1C / 朱 #B8392C</Eyebrow>
        </footer>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
