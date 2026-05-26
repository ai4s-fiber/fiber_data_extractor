import { useState, useEffect, useRef } from 'react';
import { Table, Button, message, Tag, Empty, Space, Popconfirm, Progress } from 'antd';
import { UploadOutlined, InboxOutlined, ReloadOutlined, DeleteOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { useProject } from '../stores/project';
import api from '../api/client';

interface Paper {
  id: number;
  original_filename: string;
  paper_title: string | null;
  doi_or_url: string | null;
  year: number | null;
  journal: string | null;
  status: string;
  page_count: number | null;
  created_at: string;
}

interface ExtractionProgress {
  step: string;
  percent: number;
  error?: string;
}

const stepLabels: Record<string, string> = {
  starting: '启动中',
  inventory: '页面分析',
  extracting: 'AI 抽取',
  saving: '保存结果',
  completed: '已完成',
  failed: '失败',
};

const statusMap: Record<string, { color: string; text: string }> = {
  uploaded: { color: 'blue', text: '已上传' },
  extracting: { color: 'processing', text: '抽取中' },
  review: { color: 'orange', text: '待审核' },
  completed: { color: 'green', text: '已完成' },
  failed: { color: 'red', text: '失败' },
};

export default function PapersPage() {
  const { currentProject } = useProject();
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progressMap, setProgressMap] = useState<Record<number, ExtractionProgress>>({});
  const projectRef = useRef(currentProject);
  projectRef.current = currentProject;

  const load = (silent = false) => {
    const pid = projectRef.current?.id;
    if (!pid) return;
    if (!silent) setLoading(true);
    api.get(`/projects/${pid}/papers`)
      .then(r => setPapers(r.data))
      .catch(() => message.error('加载文献列表失败'))
      .finally(() => { if (!silent) setLoading(false); });
  };

  useEffect(() => {
    load();
    const interval = setInterval(() => load(true), 3000);
    return () => clearInterval(interval);
  }, [currentProject?.id]);

  useEffect(() => {
    const pid = projectRef.current?.id;
    if (!pid) return;
    const pollProgress = async () => {
      const extracting = papers.filter(p => p.status === 'extracting');
      if (!extracting.length) return;
      const updates: Record<number, ExtractionProgress> = {};
      await Promise.all(extracting.map(async (p) => {
        try {
          const res = await api.get(`/projects/${pid}/papers/${p.id}/extraction-status`);
          const { extraction_step, extraction_percent, error } = res.data;
          updates[p.id] = { step: extraction_step || '', percent: extraction_percent || 0, error: error || '' };
        } catch { /* ignore */ }
      }));
      setProgressMap(prev => {
        const filtered: Record<number, ExtractionProgress> = {};
        for (const [id, prog] of Object.entries(prev)) {
          if (papers.some(p => p.id === Number(id))) filtered[Number(id)] = prog;
        }
        return { ...filtered, ...updates };
      });
    };
    pollProgress();
    const progInterval = setInterval(pollProgress, 2000);
    return () => clearInterval(progInterval);
  }, [papers.length, currentProject?.id]);

  const doUpload = async (file: File) => {
    const pid = projectRef.current?.id;
    if (!pid) return;
    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    try {
      await api.post(`/projects/${pid}/papers`, formData, { timeout: 120000 });
      message.success(`${file.name} 上传成功`);
      load();
    } catch (err: any) {
      message.error(err.response?.data?.detail || `${file.name} 上传失败`);
    } finally {
      setUploading(false);
    }
  };

  if (!currentProject) {
    return <Empty description="请先选择一个项目" style={{ marginTop: 100 }} />;
  }

  const triggerFileSelect = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.pdf';
    input.onchange = (ev: Event) => {
      const target = ev.target as HTMLInputElement;
      const file = target.files?.[0];
      if (file) doUpload(file);
    };
    input.click();
  };

  const triggerExtract = async (paperId: number) => {
    try {
      await api.post(`/projects/${currentProject.id}/papers/${paperId}/extract`);
      message.success('已触发高精准抽取流水线');
      load();
    } catch {
      message.error('触发失败');
    }
  };

  const deletePaper = async (paperId: number) => {
    try {
      await api.delete(`/projects/${currentProject.id}/papers/${paperId}`);
      message.success('文献已删除');
      load();
    } catch {
      message.error('删除失败');
    }
  };

  const columns = [
    { title: '文件名', dataIndex: 'original_filename', key: 'filename', ellipsis: true, width: 200 },
    { title: '标题', dataIndex: 'paper_title', key: 'title', ellipsis: true },
    { title: '期刊', dataIndex: 'journal', key: 'journal', width: 140, render: (v: string) => v || '-' },
    { title: '年份', dataIndex: 'year', key: 'year', width: 70, render: (v: number) => v || '-' },
    { title: '页数', dataIndex: 'page_count', key: 'page_count', width: 60, render: (v: number) => v || '-' },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 160,
      render: (s: string, r: Paper) => {
        const prog = progressMap[r.id];
        if (s === 'extracting' && prog) {
          const label = stepLabels[prog.step] || prog.step || '处理中';
          return (
            <div style={{ minWidth: 130 }}>
              <Progress percent={prog.percent} size="small" status={prog.error ? 'exception' : 'active'} />
              <span style={{ fontSize: 11, color: 'var(--color-text-secondary)' }}>{label}</span>
            </div>
          );
        }
        const m = statusMap[s] || { color: 'default', text: s };
        return <Tag color={m.color}>{m.text}</Tag>;
      },
    },
    {
      title: '操作', key: 'actions', width: 150,
      render: (_: any, r: Paper) => (
        <Space size="small">
          <Button size="small" type="link" icon={<PlayCircleOutlined />}
            onClick={() => triggerExtract(r.id)} disabled={r.status === 'extracting'}>
            抽取
          </Button>
          <Popconfirm title="删除此文献将同步清空其关联的所有候选提取结果，确认删除？" onConfirm={() => deletePaper(r.id)}>
            <Button size="small" type="link" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="page-header">
        <h1>文献库</h1>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => load()}>刷新</Button>
              <Button type="primary" icon={<UploadOutlined />} loading={uploading} onClick={triggerFileSelect}>
            上传 PDF
          </Button>
        </Space>
      </div>

      <div className="upload-area" style={{ marginBottom: 24 }} onClick={triggerFileSelect}>
        <p style={{ fontSize: 40, color: 'var(--color-accent)' }}><InboxOutlined /></p>
        <p style={{ fontSize: 14, color: 'var(--color-text-secondary)' }}>
          点击选择 PDF 文件上传
        </p>
        {uploading && <p style={{ color: 'var(--color-accent)', marginTop: 8 }}>上传中...</p>}
      </div>

      <Table dataSource={papers} columns={columns} rowKey="id" loading={loading}
        pagination={{ pageSize: 20 }} size="small" />
    </div>
  );
}

