import { useState, useEffect } from 'react';
import { Button, Table, Tag, Select, Space, message, Empty, Popconfirm } from 'antd';
import { DownloadOutlined, ExportOutlined, DeleteOutlined } from '@ant-design/icons';
import { useProject } from '../stores/project';
import api from '../api/client';

interface ExportJobRow {
  id: number;
  status: string;
  filter_json: string | null;
  created_at: string;
  finished_at: string | null;
  error_message: string | null;
}

export default function ExportPage() {
  const { currentProject } = useProject();
  const [exports, setExports] = useState<ExportJobRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string[]>(['approved']);

  const pid = currentProject?.id;

  const loadExports = () => {
    if (!pid) return;
    setLoading(true);
    api.get(`/projects/${pid}/exports`)
      .then(r => setExports(r.data))
      .catch(() => message.error('加载导出记录失败'))
      .finally(() => setLoading(false));
  };

  useEffect(loadExports, [pid]);

  const doExport = async () => {
    if (!pid) return;
    setExporting(true);
    try {
      await api.post(`/projects/${pid}/exports`, { review_status_filter: statusFilter });
      message.success('导出成功');
      loadExports();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '导出失败');
    } finally {
      setExporting(false);
    }
  };

  const download = async (id: number) => {
    try {
      const response = await api.get(`/projects/${pid}/exports/${id}/download`, {
        responseType: 'blob',
      });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', '数据主表.xlsx');
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      message.error('下载失败');
    }
  };

  const deleteExport = async (id: number) => {
    if (!pid) return;
    try {
      await api.delete(`/projects/${pid}/exports/${id}`);
      message.success('删除成功');
      loadExports();
    } catch {
      message.error('删除失败');
    }
  };

  if (!currentProject) {
    return <Empty description="请先选择项目" style={{ marginTop: 100 }} />;
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '状态', dataIndex: 'status', width: 100,
      render: (s: string) => <Tag color={s === 'completed' ? 'green' : s === 'failed' ? 'red' : 'blue'}>{s}</Tag> },
    { title: '筛选条件', dataIndex: 'filter_json', ellipsis: true,
      render: (v: string) => v || '-' },
    { title: '创建时间', dataIndex: 'created_at', width: 180,
      render: (v: string) => new Date(v).toLocaleString('zh-CN') },
    { title: '操作', width: 160,
      render: (_: any, r: ExportJobRow) => (
        <Space size="small">
          {r.status === 'completed' && (
            <Button size="small" icon={<DownloadOutlined />} onClick={() => download(r.id)}>下载</Button>
          )}
          <Popconfirm title="确认删除？" onConfirm={() => deleteExport(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="page-header">
        <h1>导出数据主表</h1>
        <Space>
          <Select
            mode="multiple" style={{ width: 280 }}
            placeholder="选择导出的审核状态"
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: 'approved', label: '通过' },
              { value: 'pending', label: '待审核' },
              { value: 'uncertain', label: '存疑' },
              { value: 'modified', label: '已修改' },
            ]}
          />
          <Button type="primary" icon={<ExportOutlined />} onClick={doExport} loading={exporting}>
            生成 Excel
          </Button>
        </Space>
      </div>

      <div style={{ marginBottom: 24, padding: 20, background: 'var(--color-bg-tertiary)', borderRadius: 12, border: '1px solid var(--color-border)' }}>
        <p style={{ color: 'var(--color-text-secondary)', fontSize: 13, margin: 0 }}>
          导出文件名：<strong>数据主表.xlsx</strong>，Sheet 名：<strong>数据主表</strong>，字段顺序固定 40 列。
          默认只导出审核状态为"通过"的候选记录。
        </p>
      </div>

      <Table
        dataSource={exports} columns={columns} rowKey="id"
        size="small" pagination={{ pageSize: 10 }} loading={loading}
        locale={{ emptyText: '暂无导出记录，请选择筛选条件后点击"生成 Excel"' }}
      />
    </div>
  );
}
