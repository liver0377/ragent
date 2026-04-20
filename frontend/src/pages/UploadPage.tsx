/**
 * 文档上传页面
 */
import { useState, useEffect } from 'react';
import {
  Card, Select, Button, Input, message, Progress, List, Tag, Space, Empty, Alert,
} from 'antd';
import {
  UploadOutlined, FileTextOutlined, CheckCircleOutlined,
  LoadingOutlined, CloseCircleOutlined,
} from '@ant-design/icons';
import { listKnowledgeBases, type KnowledgeBase } from '../api/knowledgeBase';
import { uploadDocument, pollTaskUntilDone, type TaskStatus } from '../api/document';

const STATUS_MAP: Record<string, { color: string; icon: React.ReactNode }> = {
  PENDING: { color: 'default', icon: <LoadingOutlined /> },
  PROCESSING: { color: 'processing', icon: <LoadingOutlined /> },
  COMPLETED: { color: 'success', icon: <CheckCircleOutlined /> },
  FAILURE: { color: 'error', icon: <CloseCircleOutlined /> },
};

interface UploadRecord {
  id: number;
  filename: string;
  kbName: string;
  status: string;
  progress: number;
  taskId: string;
  errorMsg?: string;
}

export default function UploadPage() {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selectedKb, setSelectedKb] = useState<number | null>(null);
  const [filename, setFilename] = useState('');
  const [uploading, setUploading] = useState(false);
  const [records, setRecords] = useState<UploadRecord[]>([]);

  useEffect(() => {
    listKnowledgeBases(1, 100).then((res) => setKbs(res.items)).catch(() => {});
  }, []);

  const onUpload = async () => {
    if (!selectedKb) {
      message.warning('请选择知识库');
      return;
    }
    if (!filename.trim()) {
      message.warning('请输入文件名');
      return;
    }

    setUploading(true);
    try {
      const result = await uploadDocument({
        knowledge_base_id: selectedKb,
        filename: filename.trim(),
      });

      const kbName = kbs.find((kb) => kb.id === selectedKb)?.name || '';

      const record: UploadRecord = {
        id: result.doc_id,
        filename: filename.trim(),
        kbName,
        status: 'PENDING',
        progress: 0,
        taskId: String(result.task_id),
      };
      setRecords((prev) => [record, ...prev]);

      // 后台轮询任务状态
      pollTaskUntilDone(
        result.celery_task_id,
        (status: TaskStatus) => {
          setRecords((prev) =>
            prev.map((r) =>
              r.id === record.id
                ? {
                    ...r,
                    status: status.status,
                    progress: status.progress || 0,
                    errorMsg: status.error_message,
                  }
                : r,
            ),
          );
        },
      ).catch(() => {});

      message.success('文档已提交摄入队列');
      setFilename('');
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
          <Alert
            type="info"
            message="文档需预先放置在服务器 /data/pdfs/ 目录下，此处填写文件名即可触发摄入任务。"
            showIcon
          />

          <Space wrap>
            <Select
              style={{ width: 280 }}
              placeholder="选择知识库"
              value={selectedKb}
              onChange={setSelectedKb}
              options={kbs.map((kb) => ({ label: kb.name, value: kb.id }))}
              notFoundContent="暂无知识库，请先创建"
            />
            <Input
              style={{ width: 300 }}
              placeholder="文件名，例如 paper.pdf"
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              onPressEnter={onUpload}
            />
            <Button type="primary" icon={<UploadOutlined />} loading={uploading} onClick={onUpload}>
              提交摄入
            </Button>
          </Space>
        </Space>
      </Card>

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
