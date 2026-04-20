/**
 * 知识库文档列表页面
 */
import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Card, Table, Button, message, Popconfirm, Space, Tag, Empty,
} from 'antd';
import {
  ArrowLeftOutlined, DeleteOutlined, ReloadOutlined, FileTextOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  listDocuments,
  deleteDocument,
  type DocumentInfo,
} from '../api/document';

export default function DocumentsPage() {
  const { kbId } = useParams<{ kbId: string }>();
  const navigate = useNavigate();

  const [list, setList] = useState<DocumentInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const pageSize = 20;

  const fetchList = async (p = page) => {
    if (!kbId) return;
    setLoading(true);
    try {
      const result = await listDocuments(kbId, p, pageSize);
      setList(result.items);
      setTotal(result.total);
    } catch (err: any) {
      message.error(err.message || '获取文档列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchList();
  }, [page, kbId]);

  const onDelete = async (id: number | string) => {
    try {
      await deleteDocument(id);
      message.success('文档已删除');
      fetchList();
    } catch (err: any) {
      message.error(err.message || '删除失败');
    }
  };

  const columns: ColumnsType<DocumentInfo> = [
    {
      title: '文档名称',
      dataIndex: 'doc_name',
      key: 'doc_name',
      ellipsis: true,
      render: (v: string) => (
        <Space>
          <FileTextOutlined />
          <span>{v}</span>
        </Space>
      ),
    },
    {
      title: '文件类型',
      dataIndex: 'file_type',
      key: 'file_type',
      width: 100,
      render: (v: string) => <Tag>{v?.toUpperCase()}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 80,
      render: (v: boolean) =>
        v ? <Tag color="success">启用</Tag> : <Tag color="default">禁用</Tag>,
    },
    {
      title: '分块数',
      dataIndex: 'chunk_count',
      key: 'chunk_count',
      width: 100,
      render: (v: number) => v ?? 0,
    },
    {
      title: '处理方式',
      dataIndex: 'process_mode',
      key: 'process_mode',
      width: 100,
      render: (v: string) => v || '-',
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
      width: 80,
      render: (_, record) => (
        <Popconfirm
          title={`确定删除「${record.doc_name}」？`}
          onConfirm={() => onDelete(record.id)}
          okText="确定"
          cancelText="取消"
        >
          <Button type="link" danger icon={<DeleteOutlined />} size="small">
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <Card
        title={
          <Space>
            <Button
              type="text"
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate('/knowledge')}
            />
            <span>知识库文档</span>
          </Space>
        }
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => fetchList()}>
            刷新
          </Button>
        }
      >
        <Table
          rowKey="id"
          columns={columns}
          dataSource={list}
          loading={loading}
          locale={{ emptyText: <Empty description="暂无文档，请通过知识库列表上传" /> }}
          pagination={{
            current: page,
            total,
            pageSize,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
      </Card>
    </div>
  );
}
