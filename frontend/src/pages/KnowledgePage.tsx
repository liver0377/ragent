/**
 * 知识库管理页面 —— 列表、创建、删除
 */
import { useState, useEffect } from 'react';
import {
  Card, Button, Table, Modal, Form, Input, message, Popconfirm, Tag, Space, Empty,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, DatabaseOutlined, ReloadOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  listKnowledgeBases,
  createKnowledgeBase,
  deleteKnowledgeBase,
  type KnowledgeBase,
} from '../api/knowledgeBase';
import { listDepartments } from '../api/department';

export default function KnowledgePage() {
  const [list, setList] = useState<KnowledgeBase[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();
  const [deptMap, setDeptMap] = useState<Record<number, string>>({});

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
      width: 120,
      render: (_, record) => (
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
    </div>
  );
}
