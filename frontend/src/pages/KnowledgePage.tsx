/**
 * 知识库管理页面 —— 列表、创建、删除、上传文档
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Card, Button, Table, Modal, Form, Input, message, Popconfirm, Tag, Space, Empty,
  Progress, List,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, DatabaseOutlined, ReloadOutlined,
  UploadOutlined, FileTextOutlined, InboxOutlined, CheckCircleOutlined,
  LoadingOutlined, CloseCircleOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  listKnowledgeBases,
  createKnowledgeBase,
  deleteKnowledgeBase,
  type KnowledgeBase,
} from '../api/knowledgeBase';
import { uploadDocuments, pollTaskUntilDone, type TaskStatus } from '../api/document';
import { listDepartments } from '../api/department';

const STATUS_MAP: Record<string, { color: string; icon: React.ReactNode }> = {
  PENDING: { color: 'default', icon: <LoadingOutlined /> },
  PROCESSING: { color: 'processing', icon: <LoadingOutlined /> },
  COMPLETED: { color: 'success', icon: <CheckCircleOutlined /> },
  FAILURE: { color: 'error', icon: <CloseCircleOutlined /> },
};

interface UploadRecord {
  id: number;
  filename: string;
  status: string;
  progress: number;
  taskId: string;
  errorMsg?: string;
}

export default function KnowledgePage() {
  const [list, setList] = useState<KnowledgeBase[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();
  const [deptMap, setDeptMap] = useState<Record<number, string>>({});

  // ---- 上传 Modal 状态 ----
  const [uploadKb, setUploadKb] = useState<KnowledgeBase | null>(null);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadRecords, setUploadRecords] = useState<UploadRecord[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const dropRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const openUploadModal = (kb: KnowledgeBase) => {
    setUploadKb(kb);
    setUploadFiles([]);
    setUploadRecords([]);
    setUploadModalOpen(true);
  };

  const addFiles = useCallback((newFiles: File[]) => {
    setUploadFiles((prev) => {
      const existing = new Set(prev.map((f) => f.name + f.size));
      return [...prev, ...newFiles.filter((f) => !existing.has(f.name + f.size))];
    });
  }, []);

  const removeFile = (idx: number) => {
    setUploadFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const onUploadSubmit = async () => {
    if (!uploadKb || uploadFiles.length === 0) return;
    setUploading(true);
    try {
      const results = await uploadDocuments(uploadFiles, uploadKb.id);
      const newRecords: UploadRecord[] = results.map((r) => ({
        id: r.doc_id,
        filename: r.filename,
        status: 'PENDING',
        progress: 0,
        taskId: r.celery_task_id,
      }));
      setUploadRecords(newRecords);

      results.forEach((r) => {
        const rec = newRecords.find((nr) => nr.taskId === r.celery_task_id);
        if (!rec) return;
        pollTaskUntilDone(r.celery_task_id, (status: TaskStatus) => {
          setUploadRecords((prev) =>
            prev.map((item) =>
              item.taskId === rec.taskId
                ? { ...item, status: status.status, progress: status.progress || 0, errorMsg: status.error_message }
                : item,
            ),
          );
        }).catch(() => {});
      });

      message.success(`已提交 ${results.length} 个文件`);
      setUploadFiles([]);
      fetchList(); // 刷新文档数
    } catch (err: any) {
      message.error(err.message || '上传失败');
    } finally {
      setUploading(false);
    }
  };

  const fetchList = async (p = page) => {
    setLoading(true);
    try {
      const result = await listKnowledgeBases(p, 20);
      setList(result.items);
      setTotal(result.total);
    } catch (err: any) {
      message.error(err.message || '获取知识库列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchList();
    // 加载部门映射
    listDepartments().then((depts) => {
      const map: Record<number, string> = {};
      depts.forEach((d) => { map[d.id] = d.name; });
      setDeptMap(map);
    }).catch(() => {});
  }, [page]);

  const onCreate = async () => {
    try {
      const values = await form.validateFields();
      setCreating(true);
      await createKnowledgeBase(values);
      message.success('知识库创建成功');
      setModalOpen(false);
      form.resetFields();
      fetchList(1);
      setPage(1);
    } catch (err: any) {
      if (err.message) message.error(err.message);
    } finally {
      setCreating(false);
    }
  };

  const onDelete = async (id: number) => {
    try {
      await deleteKnowledgeBase(id);
      message.success('已删除');
      fetchList();
    } catch (err: any) {
      message.error(err.message || '删除失败');
    }
  };

  const columns: ColumnsType<KnowledgeBase> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      ellipsis: true,
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (v: string) => v || <Tag>无描述</Tag>,
    },
    {
      title: '所属部门',
      dataIndex: 'department_id',
      key: 'department_id',
      width: 150,
      render: (v: number | null) => {
        if (v == null) return <Tag color="default">公共</Tag>;
        return <Tag color="blue">{deptMap[v] || `部门${v}`}</Tag>;
      },
    },
    {
      title: '文档数',
      dataIndex: 'document_count',
      key: 'document_count',
      width: 100,
      render: (v?: number) => v ?? '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (v: string) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_, record) => (
        <Space>
          <Button
            type="link"
            icon={<UploadOutlined />}
            size="small"
            onClick={() => openUploadModal(record)}
          >
            上传文档
          </Button>
          <Popconfirm
            title={`确定删除「${record.name}」？`}
            onConfirm={() => onDelete(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button type="link" danger icon={<DeleteOutlined />} size="small">
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Card
        title={
          <Space>
            <DatabaseOutlined />
            <span>知识库管理</span>
          </Space>
        }
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => fetchList()}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
              创建知识库
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="id"
          columns={columns}
          dataSource={list}
          loading={loading}
          locale={{ emptyText: <Empty description="暂无知识库，点击右上角创建" /> }}
          pagination={{
            current: page,
            total,
            pageSize: 20,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
      </Card>

      <Modal
        title="创建知识库"
        open={modalOpen}
        onOk={onCreate}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[
              { required: true, message: '请输入知识库名称' },
              { max: 100, message: '名称不超过 100 个字符' },
            ]}
          >
            <Input placeholder="例如：产品文档知识库" />
          </Form.Item>
          <Form.Item name="description" label="描述" rules={[{ max: 500 }]}>
            <Input.TextArea rows={3} placeholder="可选，简要描述知识库用途" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 上传文档 Modal */}
      <Modal
        title={
          <Space>
            <UploadOutlined />
            <span>上传文档到「{uploadKb?.name}」</span>
          </Space>
        }
        open={uploadModalOpen}
        onCancel={() => setUploadModalOpen(false)}
        footer={null}
        width={640}
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* 拖拽区域 */}
          <div
            ref={dropRef}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={(e) => { e.preventDefault(); setDragOver(false); }}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              const files = Array.from(e.dataTransfer.files);
              if (files.length > 0) addFiles(files);
            }}
            onClick={() => inputRef.current?.click()}
            style={{
              border: `2px dashed ${dragOver ? '#1677ff' : '#d9d9d9'}`,
              borderRadius: 8,
              padding: '32px 20px',
              textAlign: 'center',
              cursor: 'pointer',
              background: dragOver ? '#e6f4ff' : '#fafafa',
              transition: 'all 0.3s',
            }}
          >
            <InboxOutlined style={{ fontSize: 40, color: dragOver ? '#1677ff' : '#999' }} />
            <p style={{ marginTop: 8, color: '#666' }}>
              {dragOver ? '松开鼠标上传文件' : '点击选择或拖拽文件到此处'}
            </p>
            <p style={{ color: '#999', fontSize: 12 }}>
              支持 PDF / TXT / MD / DOCX / CSV / JSON / HTML，可多选
            </p>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".pdf,.txt,.md,.docx,.doc,.csv,.json,.html"
              onChange={(e) => {
                const files = e.target.files ? Array.from(e.target.files) : [];
                if (files.length > 0) addFiles(files);
                if (inputRef.current) inputRef.current.value = '';
              }}
              style={{ display: 'none' }}
            />
          </div>

          {/* 已选文件列表 */}
          {uploadFiles.length > 0 && (
            <div>
              <div style={{ marginBottom: 8, color: '#666' }}>
                已选择 {uploadFiles.length} 个文件：
              </div>
              <List
                size="small"
                dataSource={uploadFiles}
                style={{ maxHeight: 180, overflowY: 'auto' }}
                renderItem={(file, idx) => (
                  <List.Item
                    style={{ padding: '4px 8px' }}
                    actions={[
                      <Button key="del" type="text" size="small" danger onClick={() => removeFile(idx)}>
                        移除
                      </Button>,
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
                  onClick={onUploadSubmit}
                  size="large"
                >
                  {uploading ? '上传中...' : `上传全部 (${uploadFiles.length})`}
                </Button>
              </div>
            </div>
          )}

          {/* 上传结果 */}
          {uploadRecords.length > 0 && (
            <div>
              <div style={{ marginBottom: 8, color: '#666', fontWeight: 500 }}>上传结果：</div>
              <List
                size="small"
                dataSource={uploadRecords}
                renderItem={(item) => {
                  const s = STATUS_MAP[item.status] || STATUS_MAP.PENDING;
                  return (
                    <List.Item>
                      <Space>
                        <FileTextOutlined />
                        <span>{item.filename}</span>
                        <Tag icon={s.icon} color={s.color}>{item.status}</Tag>
                        {item.status === 'PROCESSING' && (
                          <Progress percent={item.progress} size="small" style={{ width: 120 }} />
                        )}
                        {item.errorMsg && <Tag color="error">{item.errorMsg}</Tag>}
                      </Space>
                    </List.Item>
                  );
                }}
              />
            </div>
          )}
        </Space>
      </Modal>
    </div>
  );
}
