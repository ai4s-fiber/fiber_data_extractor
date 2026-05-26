import { useState, useEffect } from 'react';
import { Table, Button, Select, Space, message, Empty, Modal, Form, Tag, Input, Switch } from 'antd';
import { PlusOutlined, DeleteOutlined, UserAddOutlined } from '@ant-design/icons';
import { useProject } from '../stores/project';
import { useAuth } from '../stores/auth';
import api from '../api/client';

interface Member {
  id: number;
  project_id: number;
  user_id: number;
  role: string;
  user_name: string | null;
  user_email: string | null;
  created_at: string;
}

interface UserOption {
  id: number;
  name: string;
  email: string;
}

const roleLabels: Record<string, string> = {
  admin: '管理员', reviewer: '审核员', student: '学生',
};

export default function MembersPage() {
  const { currentProject } = useProject();
  const { user: currentUser } = useAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [userOptions, setUserOptions] = useState<UserOption[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [form] = Form.useForm();

  // Create-user sub-modal
  const [createUserOpen, setCreateUserOpen] = useState(false);
  const [createUserForm] = Form.useForm();
  const [creating, setCreating] = useState(false);
  const isSuperadmin = currentUser?.is_superadmin === true;

  const pid = currentProject?.id;

  const load = () => {
    if (!pid) return;
    setLoading(true);
    api.get(`/projects/${pid}/members`)
      .then(r => setMembers(r.data))
      .catch(() => message.error('加载成员失败'))
      .finally(() => setLoading(false));
  };

  const loadUsers = () => {
    setUsersLoading(true);
    api.get('/users/lookup')
      .then(r => setUserOptions(r.data))
      .catch(() => message.error('加载用户列表失败'))
      .finally(() => setUsersLoading(false));
  };

  useEffect(() => { load(); }, [pid]);
  useEffect(() => { if (modalOpen) loadUsers(); }, [modalOpen]);

  const addMember = async (values: { user_id: number; role: string }) => {
    if (!pid) return;
    try {
      await api.post(`/projects/${pid}/members`, values);
      message.success('添加成功');
      setModalOpen(false);
      form.resetFields();
      load();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '添加失败');
    }
  };

  const updateRole = async (memberId: number, role: string) => {
    if (!pid) return;
    try {
      await api.patch(`/projects/${pid}/members/${memberId}`, { role });
      message.success('角色已更新');
      load();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '更新失败');
    }
  };

  const removeMember = async (memberId: number) => {
    if (!pid) return;
    try {
      await api.delete(`/projects/${pid}/members/${memberId}`);
      message.success('成员已移除');
      load();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '移除失败');
    }
  };

  const doCreateUser = async (values: { name: string; email: string; password: string; is_superadmin: boolean }) => {
    setCreating(true);
    try {
      const res = await api.post('/users', values);
      message.success(`用户 ${res.data.name} 创建成功`);
      setCreateUserOpen(false);
      createUserForm.resetFields();
      loadUsers();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建用户失败');
    } finally {
      setCreating(false);
    }
  };

  if (!currentProject) {
    return <Empty description="请先选择项目" style={{ marginTop: 100 }} />;
  }

  const columns = [
    { title: '姓名', dataIndex: 'user_name', width: 120 },
    { title: '邮箱', dataIndex: 'user_email', width: 220 },
    { title: '角色', dataIndex: 'role', width: 160,
      render: (role: string, record: Member) => (
        <Select size="small" value={role} style={{ width: 120 }}
          onChange={(v) => updateRole(record.id, v)}>
          <Select.Option value="admin"><Tag color="red">管理员</Tag></Select.Option>
          <Select.Option value="reviewer"><Tag color="blue">审核员</Tag></Select.Option>
          <Select.Option value="student"><Tag color="green">学生</Tag></Select.Option>
        </Select>
      ),
    },
    { title: '加入时间', dataIndex: 'created_at', width: 180,
      render: (v: string) => new Date(v).toLocaleString('zh-CN') },
    { title: '操作', width: 80,
      render: (_: any, r: Member) => (
        <Button size="small" danger icon={<DeleteOutlined />} onClick={() => removeMember(r.id)} />
      ),
    },
  ];

  return (
    <div>
      <div className="page-header">
        <h1>项目成员</h1>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          添加成员
        </Button>
      </div>

      <Table dataSource={members} columns={columns} rowKey="id"
        loading={loading} size="small" pagination={false} />

      <Modal title="添加成员" open={modalOpen}
        onCancel={() => setModalOpen(false)} onOk={() => form.submit()} okText="添加"
        footer={(_, { OkBtn, CancelBtn }) => (
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <span>
              {isSuperadmin && (
                <Button icon={<UserAddOutlined />} onClick={() => setCreateUserOpen(true)}>
                  创建新用户
                </Button>
              )}
            </span>
            <Space>
              <CancelBtn />
              <OkBtn />
            </Space>
          </Space>
        )}>
        <Form form={form} layout="vertical" onFinish={addMember}
          initialValues={{ role: 'student' }}>
          <Form.Item name="user_id" label="选择用户" rules={[{ required: true, message: '请选择用户' }]}>
            <Select
              showSearch
              placeholder="搜索用户名或邮箱"
              loading={usersLoading}
              filterOption={(input, option) => {
                const label = (option?.label as string || '').toLowerCase();
                return label.includes(input.toLowerCase());
              }}
              options={(() => {
                const addedIds = new Set(members.map(m => m.user_id));
                return userOptions
                  .filter(u => !addedIds.has(u.id))
                  .map(u => ({
                    value: u.id,
                    label: `${u.name} (${u.email})`,
                  }));
              })()}
            />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select>
              <Select.Option value="admin">管理员</Select.Option>
              <Select.Option value="reviewer">审核员</Select.Option>
              <Select.Option value="student">学生</Select.Option>
            </Select>
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="创建新用户" open={createUserOpen}
        onCancel={() => setCreateUserOpen(false)}
        onOk={() => createUserForm.submit()}
        okText="创建" confirmLoading={creating}>
        <Form form={createUserForm} layout="vertical" onFinish={doCreateUser}
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
    </div>
  );
}
