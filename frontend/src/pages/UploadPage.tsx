/**
 * 文档上传页面 —— 拖拽 + 批量文件上传
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Card, Select, Button, message, Progress, List, Tag, Space, Empty,
} from 'antd';
import {
  UploadOutlined, FileTextOutlined, CheckCircleOutlined,
  LoadingOutlined, CloseCircleOutlined, InboxOutlined, DeleteOutlined,
} from '@ant-design/icons';
import { listKnowledgeBases, type KnowledgeBase } from '../api/knowledgeBase';
import { uploadDocuments, pollTaskUntilDone, type TaskStatus } from '../api/document';

const STATUS_MAP: Record<string, { color: string; icon: React.ReactNode }> = {
  PENDING: { color: 'default', icon: <LoadingOutlined /> },
  PROCESSING: { color: 'processing', icon: <LoadingOutlined /> },
  COMPLETED: { color: 'success', icon: <CheckCircleOutlined /> },
  FAILURE: { color: 'error', icon: <CloseCircleOutlined /> },
};

const ALLOWED_EXTS = ['.pdf', '.txt', '.md', '.docx', '.doc', '.csv', '.json', '.html'];

interface UploadRecord {
  id: number;
  filename: string;
  kbName: string;
  status: string;
  progress: number;
  taskId: string;
  errorMsg?: string;
}

function isAllowedFile(name: string): boolean {
  const ext = '.' + name.split('.').pop()?.toLowerCase();
  return ALLOWED_EXTS.includes(ext);
}

export default function UploadPage() {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selectedKb, setSelectedKb] = useState<number | null>(null);
  const [fileList, setFileList] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [records, setRecords] = useState<UploadRecord[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const dropRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listKnowledgeBases(1, 100).then((res) => setKbs(res.items)).catch(() => {});
  }, []);

  // ---- 添加文件（去重） ----
  const addFiles = useCallback((newFiles: File[]) => {
    setFileList((prev) => {
      const existing = new Set(prev.map((f) => f.name + f.size));
      const unique = newFiles.filter((f) => !existing.has(f.name + f.size) && isAllowedFile(f.name));
      return [...prev, ...unique];
    });
  }, []);

  // ---- 移除文件 ----
  const removeFile = (idx: number) => {
    setFileList((prev) => prev.filter((_, i) => i !== idx));
  };

  // ---- 拖拽事件 ----
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  };
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    const allowed = files.filter((f) => isAllowedFile(f.name));
    if (allowed.length < files.length) {
      message.warning(`${files.length - allowed.length} 个文件格式不支持，已跳过`);
    }
    if (allowed.length > 0) addFiles(allowed);
  };

  // ---- 选择文件 ----
  const onFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files ? Array.from(e.target.files) : [];
    if (files.length > 0) addFiles(files);
    // reset input
    if (inputRef.current) inputRef.current.value = '';
  };

  // ---- 上传 ----
  const onUpload = async () => {
    if (!selectedKb) {
      message.warning('请先选择知识库');
      return;
    }
    if (fileList.length === 0) {
      message.warning('请先添加文件');
      return;
    }

    setUploading(true);
    const kbName = kbs.find((kb) => kb.id === selectedKb)?.name || '';

    try {
      const results = await uploadDocuments(fileList, selectedKb);

      // 为每个文件创建上传记录
      const newRecords: UploadRecord[] = results.map((r) => ({
        id: r.doc_id,
        filename: r.filename,
        kbName,
        status: 'PENDING',
        progress: 0,
        taskId: r.celery_task_id,
      }));
      setRecords((prev) => [...newRecords, ...prev]);

      // 并行轮询所有任务状态
      results.forEach((r) => {
        const record = newRecords.find((nr) => nr.taskId === r.celery_task_id);
        if (!record) return;
        pollTaskUntilDone(
          r.celery_task_id,
          (status: TaskStatus) => {
            setRecords((prev) =>
              prev.map((rec) =>
                rec.taskId === record.taskId
                  ? { ...rec, status: status.status, progress: status.progress || 0, errorMsg: status.error_message }
                  : rec,
              ),
            );
          },
        ).catch(() => {});
      });

      message.success(`已提交 ${results.length} 个文件到摄入队列`);
      setFileList([]);
    } catch (err: any) {
      message.error(err.message || '上传失败');
    } finally {
      setUploading(false);
    }
  };

  return (
    <div>
      <Card
        title={
          <Space>
            <UploadOutlined />
            <span>文档上传</span>
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* 知识库选择 */}
          <Select
            style={{ width: '100%' }}
            placeholder="选择目标知识库"
            value={selectedKb}
            onChange={setSelectedKb}
            options={kbs.map((kb) => ({ label: kb.name, value: kb.id }))}
            notFoundContent="暂无知识库，请先创建"
          />

          {/* 拖拽区域 */}
          <div
            ref={dropRef}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
            style={{
              border: `2px dashed ${dragOver ? '#1677ff' : '#d9d9d9'}`,
              borderRadius: 8,
              padding: '40px 20px',
              textAlign: 'center',
              cursor: 'pointer',
              background: dragOver ? '#e6f4ff' : '#fafafa',
              transition: 'all 0.3s',
            }}
          >
            <InboxOutlined style={{ fontSize: 48, color: dragOver ? '#1677ff' : '#999' }} />
            <p style={{ marginTop: 12, color: '#666', fontSize: 16 }}>
              {dragOver ? '松开鼠标上传文件' : '点击或拖拽文件到此区域'}
            </p>
            <p style={{ color: '#999', fontSize: 13 }}>
              支持 PDF / TXT / MD / DOCX / CSV / JSON / HTML，可同时选择多个文件
            </p>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept={ALLOWED_EXTS.join(',')}
              onChange={onFileSelect}
              style={{ display: 'none' }}
            />
          </div>

          {/* 待上传文件列表 */}
          {fileList.length > 0 && (
            <div>
              <div style={{ marginBottom: 8, color: '#666' }}>
                已选择 {fileList.length} 个文件：
              </div>
              <List
                size="small"
                dataSource={fileList}
                style={{ maxHeight: 200, overflowY: 'auto' }}
                renderItem={(file, idx) => (
                  <List.Item
                    style={{ padding: '4px 8px' }}
                    actions={[
                      <Button
                        key="del"
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={() => removeFile(idx)}
                      />,
                    ]}
                  >
                    <Space>
                      <FileTextOutlined />
                      <span style={{ fontSize: 13 }}>{file.name}</span>
                      <Tag>{(file.size / 1024).toFixed(1)} KB</Tag>
                    </Space>
                  </List.Item>
                )}
              />
              <div style={{ marginTop: 12, textAlign: 'right' }}>
                <Button
                  type="primary"
                  icon={<UploadOutlined />}
                  loading={uploading}
                  onClick={onUpload}
                  size="large"
                >
                  {uploading ? '上传中...' : `上传全部 (${fileList.length})`}
                </Button>
              </div>
            </div>
          )}
        </Space>
      </Card>

      {/* 上传记录 */}
      <Card title="上传记录">
        {records.length === 0 ? (
          <Empty description="暂无上传记录" />
        ) : (
          <List
            dataSource={records}
            renderItem={(item) => {
              const s = STATUS_MAP[item.status] || STATUS_MAP.PENDING;
              return (
                <List.Item>
                  <List.Item.Meta
                    avatar={<FileTextOutlined style={{ fontSize: 24, color: '#666' }} />}
                    title={
                      <Space>
                        <span>{item.filename}</span>
                        <Tag icon={s.icon} color={s.color}>
                          {item.status}
                        </Tag>
                      </Space>
                    }
                    description={`知识库: ${item.kbName} | 任务ID: ${item.taskId}`}
                  />
                  {item.status === 'PROCESSING' && (
                    <Progress percent={item.progress} style={{ width: 200 }} />
                  )}
                  {item.errorMsg && (
                    <Tag color="error">{item.errorMsg}</Tag>
                  )}
                </List.Item>
              );
            }}
          />
        )}
      </Card>
    </div>
  );
}
