import { useState, useEffect, useRef } from "react";

export function useAutoError(ms = 5000): [string, (msg: string) => void] {
  const [error, setErrorRaw] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function setError(msg: string) {
    setErrorRaw(msg);
    if (timer.current) clearTimeout(timer.current);
    if (msg) {
      timer.current = setTimeout(() => setErrorRaw(""), ms);
    }
  }

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  return [error, setError];
}
