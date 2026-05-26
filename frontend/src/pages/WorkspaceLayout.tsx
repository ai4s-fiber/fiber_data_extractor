import { useEffect, useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Select, Button, Avatar, Dropdown, Space, message } from 'antd';
import {
  ProjectOutlined, FileTextOutlined, AuditOutlined,
  ExportOutlined, TeamOutlined, LogoutOutlined, UserOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import { useAuth } from '../stores/auth';
import { useProject } from '../stores/project';
import api from '../api/client';

const { Sider, Header, Content } = Layout;

interface ProjectOption {
  id: number;
  name: string;
  description?: string;
  paper_count?: number;
  pending_count?: number;
  approved_count?: number;
}

const baseMenuItems = [
  { key: '/projects', icon: <ProjectOutlined />, label: '项目列表' },
  { key: '/papers', icon: <FileTextOutlined />, label: '文献库' },
  { key: '/review', icon: <AuditOutlined />, label: '审核队列' },
  { key: '/export', icon: <ExportOutlined />, label: '导出' },
  { key: '/members', icon: <TeamOutlined />, label: '成员' },
  { key: '/settings', icon: <SettingOutlined />, label: '大模型配置' },
];


export default function WorkspaceLayout() {
  const { user, logout } = useAuth();
  const { currentProject, setCurrentProject } = useProject();
  const navigate = useNavigate();
  const location = useLocation();
  const [projects, setProjects] = useState<ProjectOption[]>([]);

  useEffect(() => {
    api.get('/projects').then(res => {
      setProjects(res.data);
      if (res.data.length > 0 && !currentProject) {
        setCurrentProject(res.data[0]);
      }
    }).catch(() => {});
  }, []);

  const handleProjectChange = (id: number) => {
    const p = projects.find(p => p.id === id);
    if (p) setCurrentProject(p);
  };

  const userMenuItems = [
    { key: 'info', label: `${user?.name} (${user?.email})`, disabled: true },
    { type: 'divider' as const },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
  ];

  return (
    <Layout className="app-layout">
      <Sider width={220} className="app-sider">
        <div className="sider-logo">
          <h2>Fiber V6</h2>
        </div>
        <div style={{ padding: '16px 12px' }}>
          <Select
            style={{ width: '100%' }}
            placeholder="选择项目"
            value={currentProject?.id}
            onChange={handleProjectChange}
            options={projects.map(p => ({ value: p.id, label: p.name }))}
            size="small"
          />
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={user?.is_superadmin
            ? [...baseMenuItems, { key: '/users', icon: <UserOutlined />, label: '用户管理' }]
            : baseMenuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderRight: 'none' }}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
          <span className="project-selector">
            {currentProject ? currentProject.name : '请选择项目'}
          </span>
          <Dropdown menu={{
            items: userMenuItems,
            onClick: ({ key }) => {
              if (key === 'logout') { logout(); navigate('/login'); }
            },
          }}>
            <Space style={{ cursor: 'pointer' }}>
              <Avatar icon={<UserOutlined />} style={{ background: 'var(--color-accent)' }} />
              <span style={{ color: 'var(--color-text-secondary)' }}>{user?.name}</span>
            </Space>
          </Dropdown>
        </Header>
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
