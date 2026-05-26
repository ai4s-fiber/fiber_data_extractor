import { useState, useEffect } from 'react';
import { Table, Button, Space, message, Empty, Modal, Form, Input, Switch, Tag, Popconfirm, Select } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, UserOutlined } from '@ant-design/icons';
import { useAuth } from '../stores/auth';
import api from '../api/client';

interface UserAccount {
  id: number;
  email: string;
  name: string;
  is_active: boolean;
  is_superadmin: boolean;
  created_at: string;
}

export default function UserManagementPage() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<UserAccount[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editUser, setEditUser] = useState<UserAccount | null>(null);
  const [createForm] = Form.useForm();
  const [editForm] = Form.useForm();

  const isSuperadmin = currentUser?.is_superadmin === true;

  useEffect(() => {
    if (!isSuperadmin) return;
    load();
  }, [isSuperadmin]);

  const load = () => {
    setLoading(true);
    api.get('/users')
      .then(r => setUsers(r.data))
      .catch(() => message.error('加载用户列表失败'))
      .finally(() => setLoading(false));
  };

  const doCreate = async (values: any) => {
    try {
      await api.post('/users', values);
      message.success('用户创建成功');
      setCreateOpen(false);
      createForm.resetFields();
      load();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建失败');
    }
  };

  const doEdit = async (values: any) => {
    if (!editUser) return;
    const payload: Record<string, any> = {};
    if (values.name !== editUser.name) payload.name = values.name;
    if (values.email !== editUser.email) payload.email = values.email;
    if (values.password) payload.password = values.password;
    if (values.is_active !== editUser.is_active) payload.is_active = values.is_active;
    if (values.is_superadmin !== editUser.is_superadmin) payload.is_superadmin = values.is_superadmin;
    if (Object.keys(payload).length === 0) {
      setEditUser(null);
      return;
    }
    try {
      await api.patch(`/users/${editUser.id}`, payload);
      message.success('用户信息已更新');
      setEditUser(null);
      load();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '更新失败');
    }
  };

  const doDelete = async (userId: number) => {
    if (userId === currentUser?.id) {
      message.error('不能删除自己');
      return;
    }
    try {
      await api.delete(`/users/${userId}`);
      message.success('用户已删除');
      load();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '删除失败');
    }
  };

  if (!isSuperadmin) {
    return <Empty description="仅超级管理员可访问此页面" style={{ marginTop: 100 }} />;
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '姓名', dataIndex: 'name', width: 140 },
    { title: '邮箱', dataIndex: 'email', width: 220 },
    {
      title: '超级管理员', dataIndex: 'is_superadmin', width: 110,
      render: (v: boolean) => v ? <Tag color="red">是</Tag> : <Tag>否</Tag>,
    },
    {
      title: '状态', dataIndex: 'is_active', width: 90,
      render: (v: boolean) => v ? <Tag color="green">正常</Tag> : <Tag color="red">已禁用</Tag>,
    },
    {
      title: '注册时间', dataIndex: 'created_at', width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作', width: 140,
      render: (_: any, r: UserAccount) => (
        <Space size="small">
          <Button size="small" type="link" icon={<EditOutlined />}
            onClick={() => { editForm.setFieldsValue(r); setEditUser(r); }}>
            编辑
          </Button>
          <Popconfirm
            title={r.id === currentUser?.id ? '不能删除自己' : '确认删除此用户？'}
            onConfirm={() => doDelete(r.id)}
            disabled={r.id === currentUser?.id}
          >
            <Button size="small" type="link" danger icon={<DeleteOutlined />}
              disabled={r.id === currentUser?.id} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="page-header">
        <h1>用户管理</h1>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          创建用户
        </Button>
      </div>

      <Table dataSource={users} columns={columns} rowKey="id"
        loading={loading} size="small" pagination={false} />

      <Modal title="创建用户" open={createOpen}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); }}
        onOk={() => createForm.submit()} okText="创建">
        <Form form={createForm} layout="vertical" onFinish={doCreate}
          initialValues={{ is_superadmin: false }}>
          <Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入姓名' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="email" label="邮箱" rules={[{ required: true, type: 'email', message: '请输入有效邮箱' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, min: 6, message: '密码至少6位' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="is_superadmin" label="超级管理员" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="编辑用户" open={!!editUser}
        onCancel={() => setEditUser(null)}
        onOk={() => editForm.submit()} okText="保存">
        <Form form={editForm} layout="vertical" onFinish={doEdit}>
          <Form.Item name="name" label="姓名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="email" label="邮箱" rules={[{ required: true, type: 'email' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="新密码（留空不修改）">
            <Input.Password placeholder="留空则不修改密码" />
          </Form.Item>
          <Form.Item name="is_active" label="账号启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="is_superadmin" label="超级管理员" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
