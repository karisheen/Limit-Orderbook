// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.
export const CANVAS_FAILURE_EVENT = "canvas-presentation-failure";
export const CANVAS_RETRY_EVENT = "canvas-presentation-retry";

export interface CanvasFailure {
  host: HTMLElement;
  message: string | null;
}

export interface CanvasPresentation {
  invalidate: () => void;
  dispose: () => void;
}

interface Presentation {
  paint: () => void;
  fail: (error: unknown) => void;
  visible: boolean;
  failed: boolean;
  disposed: boolean;
}

const owners = new Set<Presentation>();
const pending = new Set<Presentation>();
let frame: number | null = null;
let stopListening: (() => void) | null = null;
let themeRevision = 0;

export function canvasThemeRevision(): number {
  return themeRevision;
}

function requestPaint(owner: Presentation): void {
  if (owner.disposed || owner.failed || !owner.visible) return;
  pending.add(owner);
  requestFrame();
}

function requestFrame(): void {
  if (document.hidden || frame !== null || pending.size === 0) return;
  frame = requestAnimationFrame(() => {
    frame = null;
    if (document.hidden) return;
    const callbacks = [...pending];
    pending.clear();
    for (const owner of callbacks) {
      if (owner.disposed || owner.failed || !owner.visible) continue;
      try {
        owner.paint();
      } catch (error) {
        owner.failed = true;
        pending.delete(owner);
        // A broken panel, including its error reporter, cannot starve siblings.
        try {
          owner.fail(error);
        } catch (reportError) {
          console.error("Unable to report canvas presentation failure", reportError);
        }
      }
    }
  });
}

function cancelIdleFrame(): void {
  if (pending.size === 0 && frame !== null) {
    cancelAnimationFrame(frame);
    frame = null;
  }
}

function listen(): () => void {
  const invalidate = () => {
    for (const owner of owners) requestPaint(owner);
  };
  const visibility = () => {
    if (document.hidden) {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
    } else {
      invalidate();
    }
  };
  const theme = new MutationObserver(() => {
    themeRevision += 1;
    invalidate();
  });
  // A remount must not reuse palette tokens read before the last teardown.
  themeRevision += 1;
  theme.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme", "style", "class"],
  });
  let ratio = window.devicePixelRatio || 1;
  let media: MediaQueryList | null = null;
  const watchRatio = () => {
    media?.removeEventListener("change", ratioChanged);
    media = window.matchMedia(`(resolution: ${ratio}dppx)`);
    media.addEventListener("change", ratioChanged);
  };
  const ratioChanged = () => {
    const next = window.devicePixelRatio || 1;
    if (next === ratio) return;
    ratio = next;
    watchRatio();
    invalidate();
  };
  watchRatio();
  document.addEventListener("visibilitychange", visibility);
  window.addEventListener("resize", ratioChanged);
  return () => {
    theme.disconnect();
    media?.removeEventListener("change", ratioChanged);
    document.removeEventListener("visibilitychange", visibility);
    window.removeEventListener("resize", ratioChanged);
  };
}

/**
 * Owns presentation only: hidden panels retain their caller's latest data.
 * Failure stays latched until an explicit panel retry, never a redraw loop.
 */
export function mountCanvasPresentation(
  host: HTMLElement,
  canvas: HTMLCanvasElement,
  draw: (ctx: CanvasRenderingContext2D, width: number, height: number) => void,
): CanvasPresentation {
  const panel = host.closest("[data-canvas-panel]");
  const report = (message: string | null) => {
    panel?.dispatchEvent(new CustomEvent<CanvasFailure>(CANVAS_FAILURE_EVENT, {
      detail: { host, message },
    }));
  };
  const owner: Presentation = {
    visible: typeof IntersectionObserver === "undefined",
    failed: false,
    disposed: false,
    paint: () => {
      const width = Math.floor(host.clientWidth);
      const height = Math.floor(host.clientHeight);
      if (width <= 0 || height <= 0) return;
      const dpr = window.devicePixelRatio || 1;
      // Backing dimensions and transform change in the same paint, even when
      // CSS bounds did not change (monitor moves/browser zoom).
      const pixelWidth = Math.max(1, Math.round(width * dpr));
      const pixelHeight = Math.max(1, Math.round(height * dpr));
      if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
      if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("Canvas rendering context is unavailable.");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.save();
      try {
        draw(ctx, width, height);
      } finally {
        ctx.restore();
      }
      if (host.hasAttribute("data-canvas-failed")) {
        host.removeAttribute("data-canvas-failed");
        report(null);
      }
    },
    fail: (error) => {
      // Reset the bitmap and context; partially drawn/stale evidence is invalid.
      canvas.width = 0;
      host.setAttribute("data-canvas-failed", "");
      report(error instanceof Error ? error.message : String(error));
    },
  };
  owners.add(owner);
  if (owners.size === 1) stopListening = listen();
  const invalidate = () => requestPaint(owner);
  const resize = new ResizeObserver(invalidate);
  resize.observe(host);
  const intersection = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver((entries) => {
    if (owner.disposed) return;
    for (const entry of entries) {
      if (entry.target !== host) continue;
      owner.visible = entry.isIntersecting;
      if (owner.visible) invalidate();
      else {
        pending.delete(owner);
        cancelIdleFrame();
      }
    }
  });
  intersection?.observe(host);
  const retry = () => {
    owner.failed = false;
    invalidate();
  };
  host.addEventListener(CANVAS_RETRY_EVENT, retry);
  invalidate();
  return {
    invalidate,
    dispose: () => {
      if (owner.disposed) return;
      owner.disposed = true;
      resize.disconnect();
      intersection?.disconnect();
      host.removeEventListener(CANVAS_RETRY_EVENT, retry);
      pending.delete(owner);
      owners.delete(owner);
      cancelIdleFrame();
      host.removeAttribute("data-canvas-failed");
      report(null);
      if (owners.size === 0) {
        stopListening?.();
        stopListening = null;
      }
    },
  };
}
