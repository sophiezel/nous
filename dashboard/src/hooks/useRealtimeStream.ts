"use client";

import { useEffect, useRef, useState } from "react";

interface RealtimeCache {
  breadth?: Record<string, unknown>;
  indices?: Record<string, Record<string, unknown>>;
  northbound?: Record<string, unknown>;
  messages?: { messages: Record<string, unknown>[] };
}

export function useRealtimeStream(topics: string[]) {
  const [cache, setCache] = useState<RealtimeCache>({});
  const [disconnected, setDisconnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const disconnectTimer = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    const url = `/v1/sse/stream?topics=${topics.join(",")}`;
    const es = new EventSource(url);
    esRef.current = es;

    es.addEventListener("connected", () => {
      setDisconnected(false);
      if (disconnectTimer.current) clearTimeout(disconnectTimer.current);
    });

    es.addEventListener("breadth", (e) => {
      setCache((prev) => ({ ...prev, breadth: JSON.parse(e.data) }));
    });

    es.addEventListener("quote", (e) => {
      const data = JSON.parse(e.data);
      setCache((prev) => ({
        ...prev,
        indices: { ...prev.indices, ...data.indices },
      }));
    });

    es.addEventListener("northbound", (e) => {
      setCache((prev) => ({ ...prev, northbound: JSON.parse(e.data) }));
    });

    es.addEventListener("messages", (e) => {
      setCache((prev) => ({ ...prev, messages: JSON.parse(e.data) }));
    });

    es.onerror = () => {
      disconnectTimer.current = setTimeout(() => {
        setDisconnected(true);
      }, 30000); // 30s threshold
    };

    return () => {
      es.close();
      if (disconnectTimer.current) clearTimeout(disconnectTimer.current);
    };
  }, [topics.join(",")]);

  return { cache, disconnected };
}
