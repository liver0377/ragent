/**
 * 认证相关 API
 */
import client from './client';

export interface LoginParams {
  username: string;
  password: string;
}

export interface RegisterParams extends LoginParams {}

export interface UserInfo {
  id: number;
  username: string;
  role: string;
  avatar: string | null;
}

export interface AuthResult {
  user: UserInfo;
  access_token: string;
  token_type: string;
}

/** 用户注册 */
export async function register(params: RegisterParams): Promise<AuthResult> {
  const res = await client.post('/auth/register', params);
  return res.data.data;
}

/** 用户登录 */
export async function login(params: LoginParams): Promise<AuthResult> {
  const res = await client.post('/auth/login', params);
  return res.data.data;
}

/** 获取当前用户信息 */
export async function getMe(): Promise<UserInfo> {
  const res = await client.get('/auth/me');
  return res.data.data;
}
