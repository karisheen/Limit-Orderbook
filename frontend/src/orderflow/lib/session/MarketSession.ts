// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.


import { createTerminalStore, type TerminalStore } from "@/lib/store/terminalStore";
import { NativeMarketBridge } from "./NativeMarketBridge";

export class MarketSession {
  readonly store = createTerminalStore();
  readonly bridge = new NativeMarketBridge(this.store);

  dispose(): void {
    this.bridge.reset(null);
  }
}

export type SessionStore = typeof MarketSession.prototype.store;
export type SessionState = TerminalStore;
