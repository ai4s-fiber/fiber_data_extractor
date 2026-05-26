import { useState, useEffect } from 'react';
import { Row, Col, Button, Modal, Form, Input, message, Empty, Card } from 'antd';
import { PlusOutlined, FolderOpenOutlined, FileTextOutlined, ClockCircleOutlined, CheckCircleOutlined } from '@ant-design/icons';
import api from '../api/client';
import { useProject } from '../stores/project';

interface Project {
  id: number;
  name: string;
  description?: string;
  paper_count: number;
  pending_count: number;
  approved_count: number;
  created_at: string;
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const { setCurrentProject } = useProject();

  const load = () => {
    api.get('/projects').then(r => setProjects(r.data)).catch(() => {});
  };
  useEffect(load, []);

  const create = async (values: { name: string; description?: string }) => {
    try {
      const res = await api.post('/projects', values);
      message.success('项目创建成功');
      setModalOpen(false);
      form.resetFields();
      load();
      setCurrentProject(res.data);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建失败');
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1>项目列表</h1>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          新建项目
        </Button>
      </div>

      {projects.length === 0 ? (
        <Empty description="暂无项目，请创建第一个项目" style={{ marginTop: 100 }} />
      ) : (
        <Row gutter={[20, 20]}>
          {projects.map(p => (
            <Col key={p.id} xs={24} sm={12} lg={8} xl={6}>
              <div className="stat-card" style={{ cursor: 'pointer' }} onClick={() => setCurrentProject(p)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                  <FolderOpenOutlined style={{ fontSize: 20, color: 'var(--color-accent)' }} />
                  <span style={{ fontSize: 16, fontWeight: 600 }}>{p.name}</span>
                </div>
                {p.description && (
                  <p style={{ color: 'var(--color-text-secondary)', fontSize: 13, marginBottom: 16 }}>
                    {p.description}
                  </p>
                )}
                <Row gutter={8}>
                  <Col span={8}>
                    <div style={{ textAlign: 'center' }}>
                      <FileTextOutlined style={{ color: '#60A5FA', marginBottom: 4 }} />
                      <div style={{ fontSize: 18, fontWeight: 700 }}>{p.paper_count}</div>
                      <div style={{ fontSize: 11, color: 'var(--color-text-secondary)' }}>文献</div>
                    </div>
                  </Col>
                  <Col span={8}>
                    <div style={{ textAlign: 'center' }}>
                      <ClockCircleOutlined style={{ color: '#FBBF24', marginBottom: 4 }} />
                      <div style={{ fontSize: 18, fontWeight: 700 }}>{p.pending_count}</div>
                      <div style={{ fontSize: 11, color: 'var(--color-text-secondary)' }}>待审核</div>
                    </div>
                  </Col>
                  <Col span={8}>
                    <div style={{ textAlign: 'center' }}>
                      <CheckCircleOutlined style={{ color: '#34D399', marginBottom: 4 }} />
                      <div style={{ fontSize: 18, fontWeight: 700 }}>{p.approved_count}</div>
                      <div style={{ fontSize: 11, color: 'var(--color-text-secondary)' }}>已通过</div>
                    </div>
                  </Col>
                </Row>
              </div>
            </Col>
          ))}
        </Row>
      )}

      <Modal title="新建项目" open={modalOpen} onCancel={() => setModalOpen(false)} onOk={() => form.submit()} okText="创建">
        <Form form={form} layout="vertical" onFinish={create}>
          <Form.Item name="name" label="项目名称" rules={[{ required: true }]}>
            <Input placeholder="例如：PI 气凝胶文献数据集" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="项目描述（可选）" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
