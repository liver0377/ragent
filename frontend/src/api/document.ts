/**
 * 文档相关 API
 */
import client from './client';

export interface UploadParams {
  knowledge_base_id: number;
  filename: string;
}

export interface UploadResult {
  doc_id: number;
  task_id: number;
  celery_task_id: string;
  status: string;
  message: string;
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

/** 上传文档 → 提交摄入任务 */
export async function uploadDocument(params: UploadParams): Promise<UploadResult> {
  const res = await client.post('/documents/upload', params);
  return res.data.data;
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
