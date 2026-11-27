// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.
// ─── Theme Tokens ─────────────────────────────────────────────────────────────
// Applied directly via style.setProperty on document.documentElement so canvas
// renderers and CSS read the same values without cascade surprises.

export type Theme = "dark" | "light" | "forest" | "futuristic" | "dark-dull" | "light-dull";

export interface ThemeTokens {
  "--bg-base": string;
  "--bg-chart": string;
  "--bg-surface": string;
  "--bg-panel": string;
  "--bg-panel-alt": string;
  "--bg-hover": string;
  "--bg-active": string;
  "--border": string;
  "--border-muted": string;
  "--focus": string;
  "--control-edge": string;
  "--text-primary": string;
  "--text-secondary": string;
  "--text-muted": string;
  "--text-accent": string;
  "--buy": string;
  "--buy-dim": string;
  "--buy-bg": string;
  "--buy-border": string;
  "--sell": string;
  "--sell-dim": string;
  "--sell-bg": string;
  "--sell-border": string;
  "--neutral": string;
  "--highlight": string;
  "--highlight-dim": string;
  "--poc": string;
  "--vwap": string;
  "--warn": string;
  "--critical": string;
  "--oi": string;
  "--funding": string;
  "--grid": string;
  "--shadow": string;
  "--accent": string;
  "--on-accent": string;
}

const THEMES: Record<Theme, ThemeTokens> = {
  dark: {
    "--bg-base": "#08090b",
    "--bg-chart": "#08090b",
    "--bg-surface": "#0c0d10",
    "--bg-panel": "#111215",
    "--bg-panel-alt": "#16171b",
    "--bg-hover": "#1b1c21",
    "--bg-active": "#24252b",
    "--border": "#202127",
    "--border-muted": "#18191e",
    "--focus": "#f2f3f5",
    "--control-edge": "#858993",
    "--text-primary": "#f2f3f5",
    "--text-secondary": "#a2a5ae",
    "--text-muted": "#92959f",
    "--text-accent": "#e4fb8c",
    "--buy": "#2fd897",
    "--buy-dim": "#1d8a61",
    "--buy-bg": "rgba(47,216,151,0.10)",
    "--buy-border": "rgba(47,216,151,0.35)",
    "--sell": "#ff5c74",
    "--sell-dim": "#a33a4b",
    "--sell-bg": "rgba(255,92,116,0.10)",
    "--sell-border": "rgba(255,92,116,0.35)",
    "--neutral": "#92959f",
    "--highlight": "#d7f75b",
    "--highlight-dim": "rgba(215,247,91,0.13)",
    "--poc": "#ffc857",
    "--vwap": "#a68bff",
    "--warn": "#ff9f43",
    "--critical": "#ff9696",
    "--oi": "#d2c092",
    "--funding": "#a2a5ae",
    "--grid": "rgba(255,255,255,0.045)",
    "--shadow": "rgba(0,0,0,0.55)",
    "--accent": "#d7f75b",
    "--on-accent": "#0a0b0d",
  },
  light: {
    "--bg-base": "#eef1f6",
    "--bg-chart": "#eef1f6",
    "--bg-surface": "#ffffff",
    "--bg-panel": "#fbfcfe",
    "--bg-panel-alt": "#f3f5f9",
    "--bg-hover": "#e9edf4",
    "--bg-active": "#dfe5ef",
    "--border": "#d5dbe6",
    "--border-muted": "#e5e9f0",
    "--focus": "#141a26",
    "--control-edge": "#707b8f",
    "--text-primary": "#141a26",
    "--text-secondary": "#4b566b",
    "--text-muted": "#5f687a",
    "--text-accent": "#2756c7",
    "--buy": "#086648",
    "--buy-dim": "#064f38",
    "--buy-bg": "rgba(8,102,72,0.09)",
    "--buy-border": "rgba(8,102,72,0.35)",
    "--sell": "#a1262d",
    "--sell-dim": "#781d23",
    "--sell-bg": "rgba(161,38,45,0.09)",
    "--sell-border": "rgba(161,38,45,0.35)",
    "--neutral": "#5f687a",
    "--highlight": "#2f6fec",
    "--highlight-dim": "rgba(47,111,236,0.12)",
    "--poc": "#b8860b",
    "--vwap": "#8a3ffc",
    "--warn": "#825000",
    "--critical": "#a0222b",
    "--oi": "#6b581f",
    "--funding": "#4b566b",
    "--grid": "rgba(20,30,60,0.06)",
    "--shadow": "rgba(20,30,60,0.12)",
    "--accent": "#2f6fec",
    "--on-accent": "#ffffff",
  },
  forest: {
    "--bg-base": "#060d09",
    "--bg-chart": "#060d09",
    "--bg-surface": "#09140d",
    "--bg-panel": "#0c1a11",
    "--bg-panel-alt": "#0f2015",
    "--bg-hover": "#142b1c",
    "--bg-active": "#1a3724",
    "--border": "#1c3826",
    "--border-muted": "#12271a",
    "--focus": "#9fe0b6",
    "--control-edge": "#799e86",
    "--text-primary": "#d3ecdc",
    "--text-secondary": "#8bb89b",
    "--text-muted": "#8bb89b",
    "--text-accent": "#9fe0b6",
    "--buy": "#4cd07d",
    "--buy-dim": "#2f8a51",
    "--buy-bg": "rgba(76,208,125,0.10)",
    "--buy-border": "rgba(76,208,125,0.35)",
    "--sell": "#ff6b5e",
    "--sell-dim": "#b0443b",
    "--sell-bg": "rgba(255,107,94,0.10)",
    "--sell-border": "rgba(255,107,94,0.35)",
    "--neutral": "#8bb89b",
    "--highlight": "#9be46a",
    "--highlight-dim": "rgba(116,211,154,0.16)",
    "--poc": "#ffd54f",
    "--vwap": "#7fd1ff",
    "--warn": "#ffb74d",
    "--critical": "#ff9696",
    "--oi": "#d2c092",
    "--funding": "#8bb89b",
    "--grid": "rgba(120,200,150,0.06)",
    "--shadow": "rgba(0,0,0,0.5)",
    "--accent": "#9be46a",
    "--on-accent": "#06140b",
  },
  futuristic: {
    "--bg-base": "#04040f",
    "--bg-chart": "#04040f",
    "--bg-surface": "#08081c",
    "--bg-panel": "#0b0b24",
    "--bg-panel-alt": "#0f0f2e",
    "--bg-hover": "#171744",
    "--bg-active": "#1f1f58",
    "--border": "#25256a",
    "--border-muted": "#171744",
    "--focus": "#b4b4ff",
    "--control-edge": "#8b8bce",
    "--text-primary": "#e1e2ff",
    "--text-secondary": "#9a9ae6",
    "--text-muted": "#9a9ae6",
    "--text-accent": "#b4b4ff",
    "--buy": "#00e5ff",
    "--buy-dim": "#00a0cc",
    "--buy-bg": "rgba(0,229,255,0.09)",
    "--buy-border": "rgba(0,229,255,0.35)",
    "--sell": "#ff3df5",
    "--sell-dim": "#aa22a4",
    "--sell-bg": "rgba(255,61,245,0.09)",
    "--sell-border": "rgba(255,61,245,0.35)",
    "--neutral": "#9a9ae6",
    "--highlight": "#9b6bff",
    "--highlight-dim": "rgba(155,107,255,0.2)",
    "--poc": "#ffea00",
    "--vwap": "#7dffb3",
    "--warn": "#ffaa33",
    "--critical": "#ff9696",
    "--oi": "#d2c092",
    "--funding": "#9a9ae6",
    "--grid": "rgba(140,140,255,0.07)",
    "--shadow": "rgba(0,0,0,0.6)",
    "--accent": "#9b6bff",
    "--on-accent": "#ffffff",
  },
  "dark-dull": {
    "--bg-base": "#090909",
    "--bg-chart": "#0c0c0c",
    "--bg-surface": "#141414",
    "--bg-panel": "#111111",
    "--bg-panel-alt": "#191919",
    "--bg-hover": "#242424",
    "--bg-active": "#242424",
    "--border": "#383838",
    "--border-muted": "#292929",
    "--focus": "#f4f4f4",
    "--control-edge": "#858585",
    "--text-primary": "#ededed",
    "--text-secondary": "#bdbdbd",
    "--text-muted": "#a3a3a3",
    "--text-accent": "#ededed",
    "--buy": "#76c9a6",
    "--buy-dim": "#57977c",
    "--buy-bg": "rgb(118 201 166 / 12%)",
    "--buy-border": "rgba(118,201,166,0.32)",
    "--sell": "#f19595",
    "--sell-dim": "#b56f6f",
    "--sell-bg": "rgb(241 149 149 / 12%)",
    "--sell-border": "rgba(241,149,149,0.32)",
    "--neutral": "#bdbdbd",
    "--highlight": "#ededed",
    "--highlight-dim": "#242424",
    "--poc": "#c9ad4b",
    "--vwap": "#b08cd0",
    "--warn": "#e3bd73",
    "--critical": "#ff9696",
    "--oi": "#d2c092",
    "--funding": "#bdbdbd",
    "--grid": "#292929",
    "--shadow": "rgba(0,0,0,0.4)",
    "--accent": "#ededed",
    "--on-accent": "#181818",
  },
  "light-dull": {
    "--bg-base": "#e8e8e8",
    "--bg-chart": "#fafafa",
    "--bg-surface": "#eeeeee",
    "--bg-panel": "#f4f4f4",
    "--bg-panel-alt": "#ffffff",
    "--bg-hover": "#dedede",
    "--bg-active": "#dedede",
    "--border": "#c4c4c4",
    "--border-muted": "#e0e0e0",
    "--focus": "#202020",
    "--control-edge": "#737373",
    "--text-primary": "#202020",
    "--text-secondary": "#454545",
    "--text-muted": "#555555",
    "--text-accent": "#252525",
    "--buy": "#126044",
    "--buy-dim": "#43856c",
    "--buy-bg": "rgb(18 96 68 / 9%)",
    "--buy-border": "rgba(18,96,68,0.32)",
    "--sell": "#962f37",
    "--sell-dim": "#b7646a",
    "--sell-bg": "rgb(150 47 55 / 9%)",
    "--sell-border": "rgba(150,47,55,0.32)",
    "--neutral": "#454545",
    "--highlight": "#252525",
    "--highlight-dim": "#dedede",
    "--poc": "#8f7419",
    "--vwap": "#7a4aa0",
    "--warn": "#765000",
    "--critical": "#a0222b",
    "--oi": "#6b581f",
    "--funding": "#454545",
    "--grid": "#e0e0e0",
    "--shadow": "rgba(0,0,0,0.12)",
    "--accent": "#252525",
    "--on-accent": "#ffffff",
  },
};

export const THEME_OPTIONS: { value: Theme; label: string }[] = [
  { value: "dark", label: "Midnight" },
  { value: "light", label: "Daylight" },
  { value: "forest", label: "Forest" },
  { value: "futuristic", label: "Neon" },
  { value: "dark-dull", label: "Graphite" },
  { value: "light-dull", label: "Paper" },
];

export function isLightTheme(theme: Theme): boolean {
  return theme === "light" || theme === "light-dull";
}

export function applyTheme(theme: Theme): void {
  const tokens = THEMES[theme] ?? THEMES.dark;
  const root = document.documentElement;
  for (const [prop, value] of Object.entries(tokens)) {
    root.style.setProperty(prop, value);
  }
  root.setAttribute("data-theme", theme);
  root.style.colorScheme = isLightTheme(theme) ? "light" : "dark";
}

export { THEMES };
