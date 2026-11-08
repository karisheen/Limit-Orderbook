// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.


import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useStore } from "zustand";
import { MarketSession, type SessionStore } from "./MarketSession";

const SessionContext = createContext<MarketSession | null>(null);

export function MarketSessionProvider({ children }: { children: ReactNode }) {
  const [session] = useState(() => new MarketSession());

  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.hidden) session.pause();
      else session.resume();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      session.dispose();
    };
  }, [session]);

  return (
    <SessionContext.Provider value={session}>
      {children}
    </SessionContext.Provider>
  );
}

export function useMarketSession(): MarketSession {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useMarketSession must be used inside MarketSessionProvider");
  return session;
}

export function useSessionStore<T>(selector: (state: ReturnType<SessionStore["getState"]>) => T): T {
  return useStore(useMarketSession().store, selector);
}

export function useSessionStoreApi(): SessionStore {
  return useMarketSession().store;
}
