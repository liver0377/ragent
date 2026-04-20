/**
 * 部门相关 API
 */
import client from './client';

export interface Department {
  id: number;
  name: string;
  description: string | null;
}

/** 获取所有部门列表 */
export async function listDepartments(): Promise<Department[]> {
  const res = await client.get('/departments');
  return res.data.data;
}
