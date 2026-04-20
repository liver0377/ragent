/**
 * Chat 对话页面 —— SSE 流式问答 + 会话列表侧边栏
 */
import { useState, useRef, useEffect, useCallback } from 'react';
import { Card, Input, Button, Space, Avatar, Typography, Select, Empty, List, Popconfirm, message } from 'antd';
import {
  SendOutlined, UserOutlined, RobotOutlined, ClearOutlined,
  PlusOutlined, DeleteOutlined, MessageOutlined,
} from '@ant-design/icons';
import { chatStream } from '../api/chat';
import { listKnowledgeBases, type KnowledgeBase } from '../api/knowledgeBase';
import {
  listConversations, createConversation as _createConversation, deleteConversation,
  getConversation, type Conversation, type ConversationMessage,
} from '../api/conversation';

const { Text } = Typography;

interface ChatMessage {
  id: number | string;
  role: 'user' | 'assistant';
  content: string;
}

export default function ChatPage() {
  // ---- 会话列表状态 ----
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<number | string | null>(null);
  const [convLoading, setConvLoading] = useState(false);

  // ---- 聊天状态 ----
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selectedKb, setSelectedKb] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // ---- 加载会话列表 ----
  const loadConversations = useCallback(async () => {
    setConvLoading(true);
    try {
      const res = await listConversations(1, 100);
      setConversations(res.items);
    } catch { /* ignore */ }
    setConvLoading(false);
  }, []);

  useEffect(() => {
    loadConversations();
    listKnowledgeBases(1, 100).then((res) => setKbs(res.items)).catch(() => {});
  }, [loadConversations]);

  // ---- 自动滚动到底部 ----
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  // ---- 切换会话 ----
  const switchConversation = async (convId: number | string) => {
    // 停止当前流
    abortRef.current?.abort();
    setSending(false);
    setActiveConvId(convId);

    try {
      const detail = await getConversation(convId);
      const loaded: ChatMessage[] = detail.messages.map((m: ConversationMessage) => ({
        id: m.id,
        role: m.role,
        content: m.content,
      }));
      setMessages(loaded);
    } catch {
      setMessages([]);
    }
  };

  // ---- 新建会话 ----
  const onNewConversation = async () => {
    abortRef.current?.abort();
    setSending(false);
    setActiveConvId(null);
    setMessages([]);
  };

  // ---- 删除会话 ----
  const onDeleteConversation = async (convId: number | string) => {
    try {
      await deleteConversation(convId);
      message.success('会话已删除');
      if (activeConvId === convId) {
        setActiveConvId(null);
        setMessages([]);
      }
      loadConversations();
    } catch (err: any) {
      message.error(err.message || '删除失败');
    }
  };

  // ---- 发送消息 ----
  const onSend = () => {
    const question = input.trim();
    if (!question || sending) return;

    const userMsg: ChatMessage = {
      id: Date.now(),
      role: 'user',
      content: question,
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setSending(true);

    const assistantMsg: ChatMessage = {
      id: Date.now() + 1,
      role: 'assistant',
      content: '',
    };
    setMessages((prev) => [...prev, assistantMsg]);

    const controller = chatStream(question, activeConvId || undefined, selectedKb || undefined, {
      onToken: (token) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsg.id ? { ...m, content: m.content + token } : m,
          ),
        );
      },
      onDone: () => {
        setSending(false);
        // 刷新会话列表（首次发消息会自动创建会话）
        loadConversations();
      },
      onError: (err) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, content: m.content + `\n\n❌ 错误: ${err.message}` }
              : m,
          ),
        );
        setSending(false);
      },
    });

    abortRef.current = controller;
  };

  const onClear = () => {
    abortRef.current?.abort();
    setMessages([]);
    setSending(false);
  };

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 140px)', gap: 0 }}>
      {/* ====== 左侧：会话列表 ====== */}
      <Card
        style={{ width: 260, flexShrink: 0, borderRadius: 0, borderRight: '1px solid #f0f0f0' }}
        styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column', height: '100%' } }}
      >
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0' }}>
          <Button type="primary" icon={<PlusOutlined />} block onClick={onNewConversation}>
            新建对话
          </Button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          <List
            loading={convLoading}
            dataSource={conversations}
            locale={{ emptyText: <Empty description="暂无会话" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
            renderItem={(conv) => (
              <List.Item
                key={conv.id}
                onClick={() => switchConversation(conv.id)}
                style={{
                  padding: '10px 16px',
                  cursor: 'pointer',
                  background: activeConvId === conv.id ? '#e6f4ff' : 'transparent',
                  borderLeft: activeConvId === conv.id ? '3px solid #1677ff' : '3px solid transparent',
                }}
                actions={[
                  <Popconfirm
                    key="del"
                    title="确定删除此会话？"
                    onConfirm={(e) => {
                      e?.stopPropagation();
                      onDeleteConversation(conv.id);
                    }}
                    onCancel={(e) => e?.stopPropagation()}
                  >
                    <DeleteOutlined
                      onClick={(e) => e.stopPropagation()}
                      style={{ color: '#999', fontSize: 12 }}
                    />
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  avatar={<MessageOutlined style={{ color: '#1677ff', fontSize: 16, marginTop: 4 }} />}
                  title={
                    <Text ellipsis style={{ fontSize: 13, maxWidth: 140 }} title={conv.title}>
                      {conv.title}
                    </Text>
                  }
                  description={
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {conv.last_message_time
                        ? new Date(conv.last_message_time).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
                        : new Date(conv.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                    </Text>
                  }
                />
              </List.Item>
            )}
          />
        </div>
      </Card>

      {/* ====== 右侧：聊天区域 ====== */}
      <Card
        style={{ flex: 1, borderRadius: 0 }}
        title={
          <Space>
            <RobotOutlined />
            <span>{activeConvId ? conversations.find(c => c.id === activeConvId)?.title || '智能问答' : '智能问答'}</span>
          </Space>
        }
        extra={
          <Space>
            <Select
              style={{ width: 200 }}
              placeholder="选择知识库（可选）"
              allowClear
              value={selectedKb}
              onChange={setSelectedKb}
              options={kbs.map((kb) => ({ label: kb.name, value: kb.id }))}
            />
            <Button icon={<ClearOutlined />} onClick={onClear}>
              清空对话
            </Button>
          </Space>
        }
        styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column', height: 'calc(100vh - 200px)' } }}
      >
        {/* 消息列表 */}
        <div
          ref={scrollRef}
          style={{ flex: 1, overflowY: 'auto', padding: 24 }}
        >
          {messages.length === 0 ? (
            <Empty
              description="向 RAgent 提问吧"
              style={{ marginTop: 100 }}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ) : (
            messages.map((msg) => (
              <div
                key={msg.id}
                style={{
                  display: 'flex',
                  gap: 12,
                  marginBottom: 16,
                  flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
                }}
              >
                <Avatar
                  icon={msg.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                  style={{
                    backgroundColor: msg.role === 'user' ? '#1677ff' : '#52c41a',
                    flexShrink: 0,
                  }}
                />
                <div
                  style={{
                    maxWidth: '70%',
                    padding: '10px 16px',
                    borderRadius: 12,
                    background: msg.role === 'user' ? '#e6f4ff' : '#f6ffed',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    lineHeight: 1.6,
                  }}
                >
                  <Text>
                    {msg.content || (
                      <span style={{ color: '#999' }}>
                        思考中<span className="dotting">...</span>
                      </span>
                    )}
                  </Text>
                </div>
              </div>
            ))
          )}
        </div>

        {/* 输入区 */}
        <div style={{ padding: 16, borderTop: '1px solid #f0f0f0' }}>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              size="large"
              placeholder="输入你的问题..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPressEnter={onSend}
              disabled={sending}
            />
            <Button
              type="primary"
              size="large"
              icon={<SendOutlined />}
              onClick={onSend}
              loading={sending}
            >
              发送
            </Button>
          </Space.Compact>
        </div>
      </Card>
    </div>
  );
}
