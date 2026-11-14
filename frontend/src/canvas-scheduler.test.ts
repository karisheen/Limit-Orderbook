import assert from 'node:assert/strict';
import { test, type TestContext } from 'node:test';
import {
  CANVAS_FAILURE_EVENT, CANVAS_RETRY_EVENT, canvasThemeRevision, mountCanvasPresentation,
  type CanvasFailure, type CanvasPresentation,
} from './orderflow/lib/session/canvasScheduler.ts';

class ListenerTarget extends EventTarget {
  listeners = new Map<string, Set<EventListenerOrEventListenerObject>>();
  override addEventListener(type: string, callback: EventListenerOrEventListenerObject | null) {
    super.addEventListener(type, callback);
    if (callback) {
      const listeners = this.listeners.get(type) ?? new Set();
      listeners.add(callback);
      this.listeners.set(type, listeners);
    }
  }
  override removeEventListener(type: string, callback: EventListenerOrEventListenerObject | null) {
    super.removeEventListener(type, callback);
    if (callback) this.listeners.get(type)?.delete(callback);
  }
  get listenerCount() {
    return [...this.listeners.values()].reduce((sum, listeners) => sum + listeners.size, 0);
  }
}

function environment(t: TestContext) {
  const frames = new Map<number, FrameRequestCallback>();
  let frameId = 0;
  const document = Object.assign(new ListenerTarget(), { hidden: false, documentElement: {} });
  const media: ListenerTarget[] = [];
  const window = Object.assign(new ListenerTarget(), {
    devicePixelRatio: 1,
    matchMedia: () => {
      const query = new ListenerTarget();
      media.push(query);
      return query;
    },
  });
  const mutations: Mutation[] = [];
  class Mutation {
    connected = false;
    options?: MutationObserverInit;
    constructor(readonly callback: () => void) { mutations.push(this); }
    observe(_target: unknown, options: MutationObserverInit) { this.connected = true; this.options = options; }
    disconnect() { this.connected = false; }
  }
  const resizes: Resize[] = [];
  class Resize {
    target?: Element;
    connected = false;
    constructor(readonly callback: () => void) { resizes.push(this); }
    observe(target: Element) { this.target = target; this.connected = true; }
    disconnect() { this.connected = false; }
  }
  const intersections: Intersection[] = [];
  class Intersection {
    target?: Element;
    connected = false;
    constructor(readonly callback: (entries: Partial<IntersectionObserverEntry>[]) => void) { intersections.push(this); }
    observe(target: Element) { this.target = target; this.connected = true; }
    disconnect() { this.connected = false; }
  }
  const globals = {
    document, window, MutationObserver: Mutation, ResizeObserver: Resize, IntersectionObserver: Intersection,
    requestAnimationFrame: (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame: (id: number) => { frames.delete(id); },
  };
  const originals = new Map(Object.keys(globals).map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  const presentations: CanvasPresentation[] = [];
  t.after(() => {
    for (const presentation of presentations) presentation.dispose();
    for (const [key, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
  });

  function surface(draw: (ctx: CanvasRenderingContext2D, width: number, height: number) => void) {
    const panel = new ListenerTarget();
    const reports: CanvasFailure[] = [];
    panel.addEventListener(CANVAS_FAILURE_EVENT, (event) => reports.push((event as CustomEvent<CanvasFailure>).detail));
    const attributes = new Set<string>();
    const host = Object.assign(new ListenerTarget(), {
      clientWidth: 120, clientHeight: 60,
      closest: () => panel,
      hasAttribute: (name: string) => attributes.has(name),
      setAttribute: (name: string) => { attributes.add(name); },
      removeAttribute: (name: string) => { attributes.delete(name); },
    });
    const operations: unknown[][] = [];
    const ctx = {
      setTransform: (...args: number[]) => { operations.push(['transform', ...args]); },
      clearRect: (...args: number[]) => { operations.push(['clear', ...args]); },
      save: () => { operations.push(['save']); },
      restore: () => { operations.push(['restore']); },
    } as unknown as CanvasRenderingContext2D;
    const canvas = { width: 0, height: 0, style: {}, getContext: () => ctx } as unknown as HTMLCanvasElement;
    const presentation = mountCanvasPresentation(host as unknown as HTMLElement, canvas, draw);
    presentations.push(presentation);
    const intersection = intersections.at(-1)!;
    return {
      host, canvas, reports, operations, presentation,
      visible: (isIntersecting: boolean) => intersection.callback([{ target: intersection.target, isIntersecting }]),
      retry: () => host.dispatchEvent(new Event(CANVAS_RETRY_EVENT)),
    };
  }
  return {
    frames, document, window, media, mutations, resizes, intersections, surface,
    flush: () => {
      const current = [...frames.values()];
      frames.clear();
      for (const callback of current) callback(0);
    },
    theme: () => { for (const mutation of mutations) if (mutation.connected) mutation.callback(); },
    hidden: (hidden: boolean) => { document.hidden = hidden; document.dispatchEvent(new Event('visibilitychange')); },
  };
}

test('a failed canvas clears invalid pixels, reports its panel and does not starve a sibling', (t) => {
  const env = environment(t);
  let broken = true;
  let attempts = 0;
  let healthyPaints = 0;
  const failed = env.surface(() => { attempts += 1; if (broken) throw new Error('Bad panel geometry'); });
  const healthy = env.surface(() => { healthyPaints += 1; });
  failed.visible(true);
  healthy.visible(true);
  assert.equal(env.frames.size, 1);
  env.flush();
  assert.equal(healthyPaints, 1);
  assert.equal(failed.canvas.width, 0);
  assert.equal(failed.host.hasAttribute('data-canvas-failed'), true);
  assert.equal(failed.reports.at(-1)?.message, 'Bad panel geometry');
  assert.equal(failed.reports.at(-1)?.host, failed.host);
  failed.presentation.invalidate();
  env.theme();
  env.flush();
  assert.equal(attempts, 1, 'failure remains latched across data/theme invalidation');
  assert.equal(env.frames.size, 0, 'no automatic retry loop');
  broken = false;
  failed.retry();
  env.flush();
  assert.equal(attempts, 2);
  assert.equal(failed.canvas.width, 120);
  assert.equal(failed.host.hasAttribute('data-canvas-failed'), false);
  assert.equal(failed.reports.at(-1)?.message, null);
});

test('offscreen and hidden panels suspend work then paint the latest retained data once', (t) => {
  const env = environment(t);
  let latest = 1;
  const painted: number[] = [];
  const surface = env.surface(() => painted.push(latest));
  assert.equal(env.frames.size, 0, 'wait for initial intersection evidence');
  surface.visible(true);
  env.flush();
  surface.visible(false);
  latest = 2;
  surface.presentation.invalidate();
  latest = 3;
  surface.presentation.invalidate();
  env.theme();
  assert.equal(env.frames.size, 0);
  surface.visible(true);
  assert.equal(env.frames.size, 1);
  env.hidden(true);
  assert.equal(env.frames.size, 0, 'hide cancels an already queued frame');
  latest = 4;
  surface.presentation.invalidate();
  env.hidden(false);
  env.flush();
  assert.deepEqual(painted, [1, 4]);
  assert.equal(env.frames.size, 0);
  surface.presentation.invalidate();
  surface.visible(false);
  assert.equal(env.frames.size, 0, 'leaving view cancels unnecessary RAF');
});

test('static panels share event-driven theme invalidation and dispose every listener', (t) => {
  const env = environment(t);
  const revisions: number[] = [];
  const first = env.surface(() => revisions.push(canvasThemeRevision()));
  const second = env.surface(() => revisions.push(canvasThemeRevision()));
  first.visible(true);
  second.visible(true);
  env.flush();
  assert.equal(env.mutations.length, 1);
  assert.deepEqual(env.mutations[0]!.options?.attributeFilter, ['data-theme', 'style', 'class']);
  const initial = revisions[0]!;
  env.theme();
  env.theme();
  assert.equal(env.frames.size, 1);
  env.flush();
  assert.deepEqual(revisions, [initial, initial, initial + 2, initial + 2]);
  assert.equal(env.frames.size, 0);
  first.presentation.invalidate();
  second.presentation.invalidate();
  first.presentation.dispose();
  second.presentation.dispose();
  assert.equal(env.frames.size, 0);
  assert.equal(env.document.listenerCount, 0);
  assert.equal(env.window.listenerCount, 0);
  assert.ok(env.media.every((query) => query.listenerCount === 0));
  assert.ok(env.mutations.every((observer) => !observer.connected));
  assert.ok(env.resizes.every((observer) => !observer.connected));
  assert.ok(env.intersections.every((observer) => !observer.connected));
  assert.equal(first.host.listenerCount, 0);
  first.presentation.invalidate();
  first.visible(true); // A queued observer notification after disposal is inert.
  env.resizes[0]!.callback();
  env.hidden(false);
  assert.equal(env.frames.size, 0);
  const remounted = env.surface(() => revisions.push(canvasThemeRevision()));
  remounted.visible(true);
  env.flush();
  assert.ok(revisions.at(-1)! > initial + 2, 'remount invalidates old palette tokens');
});

test('DPR changes resize the backing bitmap and transform atomically without a CSS resize', (t) => {
  const env = environment(t);
  const paints: unknown[][] = [];
  const surface = env.surface((_ctx, width, height) => {
    paints.push([surface.canvas.width, surface.canvas.height, width, height, surface.operations.at(-3)]);
  });
  surface.visible(true);
  env.flush();
  env.window.devicePixelRatio = 2;
  env.media[0]!.dispatchEvent(new Event('change'));
  assert.equal(env.media[0]!.listenerCount, 0, 'old DPR query is released');
  assert.equal(surface.canvas.width, 120, 'dimensions remain coherent with the old frame until paint');
  env.flush();
  assert.deepEqual(paints, [
    [120, 60, 120, 60, ['transform', 1, 0, 0, 1, 0, 0]],
    [240, 120, 120, 60, ['transform', 2, 0, 0, 2, 0, 0]],
  ]);
  assert.equal(env.frames.size, 0);
  env.window.dispatchEvent(new Event('resize'));
  assert.equal(env.frames.size, 0, 'same ratio resize is not an idle repaint loop');
  env.window.devicePixelRatio = 1.5;
  env.window.dispatchEvent(new Event('resize'));
  env.flush();
  assert.equal(surface.canvas.width, 180);
  assert.equal(surface.canvas.height, 90);
});

test('disposing a queued sibling during a paint prevents its callback from executing', (t) => {
  const env = environment(t);
  const first = env.surface(() => sibling.dispose());
  let siblingPaints = 0;
  const second = env.surface(() => { siblingPaints += 1; });
  const sibling = second.presentation;
  first.visible(true);
  second.visible(true);
  env.flush();
  assert.equal(siblingPaints, 0);
});

test('missing rendering context surfaces a panel failure rather than silently retaining evidence', (t) => {
  const env = environment(t);
  const surface = env.surface(() => assert.fail('cannot draw without a context'));
  surface.canvas.getContext = (() => null) as HTMLCanvasElement['getContext'];
  surface.visible(true);
  env.flush();
  assert.equal(surface.reports.at(-1)?.message, 'Canvas rendering context is unavailable.');
  assert.equal(surface.canvas.width, 0);
  assert.equal(env.frames.size, 0);
});
