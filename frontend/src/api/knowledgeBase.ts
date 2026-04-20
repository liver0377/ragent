/**
 * 知识库相关 API
 */
import client from './client';

export interface KnowledgeBase {
  id: number | string;
  name: string;
  description: string;
  embedding_model: string;
  collection_name: string;
  department_id: number | string | null;
  document_count?: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface KbListResult {
  items: KnowledgeBase[];
  total: number;
  page: number;
  page_size: number;
}

export interface KbCreateParams {
  name: string;
  description?: string;
}

/** 知识库列表（分页） */
export async function listKnowledgeBases(
  page = 1,
  pageSize = 20,
): Promise<KbListResult> {
  const res = await client.get('/knowledge-bases', {
    params: { page, page_size: pageSize },
  });
  return res.data.data;
}

/** 知识库详情 */
export async function getKnowledgeBase(kbId: number | string): Promise<KnowledgeBase> {
  const res = await client.get(`/knowledge-bases/${kbId}`);
  return res.data.data;
}

/** 创建知识库 */
export async function createKnowledgeBase(
  params: KbCreateParams,
): Promise<KnowledgeBase> {
  const res = await client.post('/knowledge-bases', params);
  return res.data.data;
}

/** 删除知识库 */
export async function deleteKnowledgeBase(kbId: number | string): Promise<void> {
  await client.delete(`/knowledge-bases/${kbId}`);
}
