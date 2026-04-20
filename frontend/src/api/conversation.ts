/**
 * 会话管理 API
 */
import client from './client';

export interface Conversation {
  id: number;
  title: string;
  last_message_time: string | null;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  user_id: number;
  messages: ConversationMessage[];
}

export interface ConversationMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface ConversationListResult {
  items: Conversation[];
  total: number;
  page: number;
  page_size: number;
}

/** 创建会话 */
export async function createConversation(title: string = '新对话'): Promise<Conversation> {
  const res = await client.post('/conversations', { title });
  return res.data.data;
}

/** 获取会话列表 */
export async function listConversations(
  page: number = 1,
  pageSize: number = 50,
): Promise<ConversationListResult> {
  const res = await client.get('/conversations', { params: { page, page_size: pageSize } });
  return res.data.data;
}

/** 获取会话详情（含消息） */
export async function getConversation(convId: number): Promise<ConversationDetail> {
  const res = await client.get(`/conversations/${convId}`);
  return res.data.data;
}

/** 删除会话 */
export async function deleteConversation(convId: number): Promise<void> {
  await client.delete(`/conversations/${convId}`);
}
