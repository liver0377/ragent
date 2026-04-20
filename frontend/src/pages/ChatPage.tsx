/**
 * Chat 对话页面 —— SSE 流式问答
 */
import { useState, useRef, useEffect } from 'react';
import { Card, Input, Button, Space, Avatar, Typography, Select, Empty } from 'antd';
import { SendOutlined, UserOutlined, RobotOutlined, ClearOutlined } from '@ant-design/icons';
import { chatStream } from '../api/chat';
import { listKnowledgeBases, type KnowledgeBase } from '../api/knowledgeBase';

const { Text } = Typography;

interface Message {
  id: number;
  role: 'user' | 'assistant';
  content: string;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selectedKb, setSelectedKb] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    listKnowledgeBases(1, 100).then((res) => setKbs(res.items)).catch(() => {});
  }, []);

  // 自动滚动到底部
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  const onSend = () => {
    const question = input.trim();
    if (!question || sending) return;

    const userMsg: Message = {
      id: Date.now(),
      role: 'user',
      content: question,
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setSending(true);

    const assistantMsg: Message = {
      id: Date.now() + 1,
      role: 'assistant',
      content: '',
    };
    setMessages((prev) => [...prev, assistantMsg]);

    const controller = chatStream(question, selectedKb || undefined, {
      onToken: (token) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsg.id ? { ...m, content: m.content + token } : m,
          ),
        );
      },
      onDone: () => {
        setSending(false);
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
    <Card
      title={
        <Space>
          <RobotOutlined />
          <span>智能问答</span>
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
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: 24,
        }}
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
  );
}
