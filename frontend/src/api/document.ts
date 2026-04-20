/**
 * 文档相关 API
 */
import client from './client';

export interface UploadResult {
  doc_id: number | string;
  task_id: number | string;
  celery_task_id: string;
  status: string;
  message: string;
  filename: string;
}

export interface TaskStatus {
  task_id: string;
  status: string;
  stage?: string;
  progress?: number;
  chunk_count?: number;
  text_length?: number;
  elapsed_ms?: number;
  error_message?: string;
}

/** 批量上传文档（multipart/form-data） */
export async function uploadDocuments(
  files: File[],
  knowledgeBaseId: number | string,
): Promise<UploadResult[]> {
  const formData = new FormData();
  files.forEach((f) => formData.append('files', f));
  formData.append('knowledge_base_id', String(knowledgeBaseId));

  const res = await client.post('/documents/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  // 后端返回 { total, success, failed, details: [...] }
  const data = res.data.data;
  return data.details || [];
}

/** 查询摄入任务状态 */
export async function getTaskStatus(taskId: string): Promise<TaskStatus> {
  const res = await client.get(`/ingestion/tasks/${taskId}`);
  return res.data.data;
}

/** 轮询任务直到完成 */
export async function pollTaskUntilDone(
  taskId: string,
  onProgress?: (status: TaskStatus) => void,
  interval = 2000,
  maxAttempts = 60,
): Promise<TaskStatus> {
  for (let i = 0; i < maxAttempts; i++) {
    const status = await getTaskStatus(taskId);
    onProgress?.(status);
    if (
      status.status === 'COMPLETED' ||
      status.status === 'FAILURE'
    ) {
      return status;
    }
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error('任务超时');
}
