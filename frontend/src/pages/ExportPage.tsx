import { useState, useEffect } from 'react';
import { App, Button, Table, Tag, Select, Space, Empty, Popconfirm, Typography } from 'antd';
import { DownloadOutlined, ExportOutlined, DeleteOutlined } from '@ant-design/icons';
import { useProject } from '../stores/project';
import api from '../api/client';
import { downloadBlobResponse } from '../utils/download';

const { Text } = Typography;

const EXPORT_STATUS_OPTIONS = [
  { value: 'approved', label: '通过' },
  { value: 'pending', label: '待审核' },
  { value: 'uncertain', label: '存疑' },
  { value: 'modified', label: '已修改' },
] as const;

type ExportStatus = (typeof EXPORT_STATUS_OPTIONS)[number]['value'];

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
  const { message } = App.useApp();
  const [exports, setExports] = useState<ExportJobRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string[]>(['approved']);
  const [statusCounts, setStatusCounts] = useState<Record<ExportStatus, number>>({
    approved: 0,
    pending: 0,
    uncertain: 0,
    modified: 0,
  });

  const pid = currentProject?.id;

  const loadStatusCounts = () => {
    if (!pid) return;
    Promise.all(
      EXPORT_STATUS_OPTIONS.map(({ value }) =>
        api.get(`/projects/${pid}/candidates/count`, { params: { review_status: value } })
          .then(r => [value, r.data.count as number] as const),
      ),
    )
      .then(results => setStatusCounts(Object.fromEntries(results) as Record<ExportStatus, number>))
      .catch(() => {});
  };

  const loadExports = () => {
    if (!pid) return;
    setLoading(true);
    api.get(`/projects/${pid}/exports`)
      .then(r => setExports(r.data))
      .catch(() => message.error('加载导出记录失败'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadExports();
    loadStatusCounts();
  }, [pid]);

  const exportableCount = statusFilter.reduce(
    (sum, status) => sum + (statusCounts[status as ExportStatus] ?? 0),
    0,
  );

  const statusOptions = EXPORT_STATUS_OPTIONS.map(({ value, label }) => ({
    value,
    label: `${label} (${statusCounts[value] ?? 0})`,
  }));

  const doExport = async () => {
    if (!pid) return;
    setExporting(true);
    try {
      const res = await api.post(`/projects/${pid}/exports`, { review_status_filter: statusFilter });
      message.success(`导出成功，共写入 ${res.data?.exported_record_count ?? 0} 条记录`);
      loadExports();
      loadStatusCounts();
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
      downloadBlobResponse(
        response.data,
        '数据主表.xlsx',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      );
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
        <h1>导出结构化工作簿</h1>
        <Space direction="vertical" size={4} align="end">
          <Space>
            <Select
              mode="multiple" style={{ width: 320 }}
              placeholder="选择导出的审核状态"
              value={statusFilter}
              onChange={setStatusFilter}
              options={statusOptions}
            />
            <Button
              type="primary"
              icon={<ExportOutlined />}
              onClick={doExport}
              loading={exporting}
              disabled={exportableCount === 0}
            >
              生成 Excel
            </Button>
          </Space>
          <Text type={exportableCount > 0 ? 'secondary' : 'danger'} style={{ fontSize: 12 }}>
            {exportableCount > 0
              ? `当前所选状态共 ${exportableCount} 条可导出`
              : '当前所选状态没有可导出记录，请先在数据审核中处理或调整筛选'}
          </Text>
        </Space>
      </div>

      <div style={{ marginBottom: 24, padding: 20, background: 'var(--color-bg-tertiary)', borderRadius: 12, border: '1px solid var(--color-border)' }}>
        <p style={{ color: 'var(--color-text-secondary)', fontSize: 13, margin: 0 }}>
          导出文件名：<strong>数据主表.xlsx</strong>；工作簿包含 <strong>Main_Data</strong>、<strong>Papers</strong>、<strong>Evidence</strong>、<strong>Parse_Blocks</strong>、<strong>Quality_Report</strong>。
          <strong>Main_Data</strong> 是可独立交付的 40 列主数据表；文献信息、证据明细和解析块同时保留在独立 Sheet 中。
          默认只导出审核状态为"通过"的候选记录，导出不会删除数据库中的原始结果。
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
