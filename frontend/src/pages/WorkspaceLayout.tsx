import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Layout, Menu, Select } from 'antd';
import {
  AuditOutlined,
  ExportOutlined,
  FileTextOutlined,
  ProjectOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import api from '../api/client';
import { useProject } from '../stores/project';

const { Sider, Header, Content } = Layout;

interface ProjectOption {
  id: number;
  name: string;
}

export default function WorkspaceLayout() {
  const { currentProject, setCurrentProject } = useProject();
  const navigate = useNavigate();
  const location = useLocation();
  const [projects, setProjects] = useState<ProjectOption[]>([]);

  useEffect(() => {
    api.get('/projects').then(res => {
      setProjects(res.data);
      if (res.data.length > 0) {
        const persisted = currentProject;
        const match = persisted ? res.data.find((p: ProjectOption) => p.id === persisted.id) : null;
        setCurrentProject(match || res.data[0]);
      }
    }).catch(() => {});
  }, []);

  const menuItems = [
    { key: '/projects', icon: <ProjectOutlined />, label: '项目库' },
    { key: '/papers', icon: <FileTextOutlined />, label: '文献录入' },
    { key: '/review', icon: <AuditOutlined />, label: '数据复核' },
    { key: '/export', icon: <ExportOutlined />, label: '导出' },
    { key: '/settings', icon: <SettingOutlined />, label: '项目配置' },
  ];

  return (
    <Layout className="app-layout">
      <Sider width={220} className="app-sider">
        <div className="sider-logo"><h2>Fiber V6</h2></div>
        <div style={{ padding: '16px 12px' }}>
          <Select
            style={{ width: '100%' }}
            placeholder="选择项目库"
            value={currentProject?.id}
            onChange={(id) => {
              const project = projects.find(item => item.id === id);
              if (project) setCurrentProject(project);
            }}
            options={projects.map(project => ({ value: project.id, label: project.name }))}
            size="small"
          />
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderRight: 'none' }}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
          <span className="project-selector">{currentProject?.name || '请选择项目库'}</span>
        </Header>
        <Content className="app-content"><Outlet /></Content>
      </Layout>
    </Layout>
  );
}
