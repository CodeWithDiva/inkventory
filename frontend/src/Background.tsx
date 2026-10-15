// Decorative, slow-moving backdrop for the app and auth pages: a faint data grid, drifting colour
// fields, chart lines that draw themselves, and floating "description" cards and score chips that
// hint at what the product does. Purely visual: hidden from assistive tech, never intercepts clicks,
// and frozen for people who prefer reduced motion.
import { Icon } from "./icons";

const CHIPS = [
  { cls: "c1", icon: "checkCircle" as const, text: "SEO 96" },
  { cls: "c2", icon: "sparkle" as const, text: "Written in Urdu" },
  { cls: "c3", icon: "checkCircle" as const, text: "Approved" },
  { cls: "c4", icon: "layers" as const, text: "2,480 products" },
];

export function AppBackground() {
  return (
    <div className="bg-scene" aria-hidden="true">
      <div className="bg-grid" />
      <div className="bg-blob b1" />
      <div className="bg-blob b2" />
      <div className="bg-blob b3" />
      <svg className="bg-lines" viewBox="0 0 1200 800" preserveAspectRatio="none">
        <defs>
          <linearGradient id="bg-line-a" x1="0" x2="1">
            <stop offset="0" stopColor="#6366f1" stopOpacity="0" />
            <stop offset=".5" stopColor="#6366f1" stopOpacity=".55" />
            <stop offset="1" stopColor="#a78bfa" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="bg-line-b" x1="0" x2="1">
            <stop offset="0" stopColor="#38bdf8" stopOpacity="0" />
            <stop offset=".5" stopColor="#38bdf8" stopOpacity=".45" />
            <stop offset="1" stopColor="#6366f1" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path className="l1" d="M0 610 C 160 560, 260 640, 400 540 S 640 420, 800 470 S 1050 330, 1200 360" stroke="url(#bg-line-a)" />
        <path className="l2" d="M0 700 C 200 690, 300 600, 470 630 S 720 560, 880 590 S 1080 500, 1200 520" stroke="url(#bg-line-b)" />
        <path className="l3" d="M0 240 C 180 260, 320 170, 480 210 S 760 120, 940 160 S 1110 90, 1200 110" stroke="url(#bg-line-b)" />
      </svg>
      <div className="bg-card k1"><b /><i /><i /><i style={{ width: "62%" }} /></div>
      <div className="bg-card k2"><b /><i /><i style={{ width: "70%" }} /></div>
      <div className="bg-card k3"><b /><i /><i /><i style={{ width: "48%" }} /></div>
      {CHIPS.map((c) => (
        <div key={c.cls} className={`bg-chip ${c.cls}`}><Icon name={c.icon} size={13} />{c.text}</div>
      ))}
      <div className="bg-dots">
        {Array.from({ length: 14 }, (_, i) => <span key={i} style={{ ["--i" as string]: i }} />)}
      </div>
    </div>
  );
}
