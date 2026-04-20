/**
 * Chat SSE 流式 API
 */
import { getToken } from '../utils/auth';

const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatCallbacks {
  onToken: (token: string) => void;
  onDone: () => void;
  onError: (err: Error) => void;
}

/** SSE 流式聊天 */
export function chatStream(
  question: string,
  conversationId?: number,
  callbacks?: ChatCallbacks,
): AbortController {
  const controller = new AbortController();

  const token = getToken();
  fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      question,
      conversation_id: conversationId,
    }),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail || errBody.message || `HTTP ${res.status}`);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            if (data === '[DONE]') {
              callbacks?.onDone();
              return;
            }
            try {
              const parsed = JSON.parse(data);
              if (parsed.content) {
                callbacks?.onToken(parsed.content);
              }
              if (parsed.type === 'error') {
                callbacks?.onError(new Error(parsed.message || '流式错误'));
                return;
              }
            } catch {
              // 非 JSON 数据直接当文本输出
              if (data) callbacks?.onToken(data);
            }
          }
        }
      }
      callbacks?.onDone();
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        callbacks?.onError(err);
      }
    });

  return controller;
}
