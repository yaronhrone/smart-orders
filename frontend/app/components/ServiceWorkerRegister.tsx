"use client";

import { useEffect } from "react";

/** Registers the PWA shell worker. A no-op on browsers without support or over http. */
export function ServiceWorkerRegister() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
  }, []);

  return null;
}
