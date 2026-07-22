"""SSE 连接管理 + 广播"""
import asyncio, json

class SSEManager:
    def __init__(self):
        self._queues: dict[str, set[asyncio.Queue]] = {}

    async def connect(self, topics: list[str]) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=200)
        for t in topics:
            self._queues.setdefault(t, set()).add(q)
        try:
            return q
        finally:
            for t in topics:
                self._queues.get(t, set()).discard(q)

    async def broadcast(self, topic: str, data: dict):
        for q in list(self._queues.get(topic, set())):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait({"topic": topic, "data": data})
            except asyncio.QueueFull:
                pass

sse_manager = SSEManager()
