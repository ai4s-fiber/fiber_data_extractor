import { useState, useEffect, useRef } from 'react';
import {
  Table,
  Button,
  Empty,
  Space,
  Popconfirm,
  Modal,
  App,
} from 'antd';
import {
  UploadOutlined,
  InboxOutlined,
  ReloadOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  CloseCircleOutlined,
  DownloadOutlined,
} from '@ant-design/icons';
import { useProject } from '../stores/project';
import { useExtraction } from '../contexts/ExtractionContext';
import api from '../api/client';
import { downloadBlobResponse } from '../utils/download';
import PaperStatusCell from '../components/papers/PaperStatusCell';
import ExtractionModeModal, {
  type ExtractionMode,
  type ParserStrategy,
} from '../components/papers/ExtractionModeModal';
import {
  type Paper,
  type LlmConfig,
  type ExtractionProgress,
  isActivePaper,
  progressFromPaper,
  modeLabels,
} from '../components/papers/types';

export default function PapersPage() {
  const { message } = App.useApp();
  const { currentProject } = useProject();
  const { state: sseState, startExtraction, cancelExtraction, subscribe, reconnectActive, unsubscribe } = useExtraction();

  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [extractDialogOpen, setExtractDialogOpen] = useState(false);
  const [selectedPaper, setSelectedPaper] = useState<Paper | null>(null);
  const [selectedMode, setSelectedMode] = useState<ExtractionMode>('strong');
  const [selectedParserStrategy, setSelectedParserStrategy] = useState<ParserStrategy>('mineru_cloud');
  const [llmConfig, setLlmConfig] = useState<LlmConfig | null>(null);

  const [progressMap, setProgressMap] = useState<Record<number, ExtractionProgress>>({});

  const projectRef = useRef(currentProject);
  projectRef.current = currentProject;
  const loadAbortRef = useRef<AbortController | null>(null);
  const reconnectRef = useRef<number | null>(null);

  const applyPaperProgress = (paperRows: Paper[]) => {
    const next: Record<number, ExtractionProgress> = {};
    paperRows.forEach((paper) => {
      const prog = progressFromPaper(paper);
      if (prog) next[paper.id] = prog;
    });
    setProgressMap(next);
    return paperRows;
  };

  const load = (silent = false) => {
    const pid = projectRef.current?.id;
    if (!pid) return;
    loadAbortRef.current?.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;

    if (!silent) setLoading(true);
    api.get(`/projects/${pid}/papers`, { signal: controller.signal })
      .then(r => {
        const rows = applyPaperProgress(r.data);
        setPapers(rows);
      })
      .catch((err) => {
        if (err.name !== 'CanceledError') {
          message.error('加载文献列表失败');
        }
      })
      .finally(() => {
        if (!silent) setLoading(false);
      });
  };

  useEffect(() => {
    load();
    const interval = setInterval(() => load(true), 4000);
    return () => {
      clearInterval(interval);
      loadAbortRef.current?.abort();
    };
  }, [currentProject?.id]);

  // After refresh/navigation, restore SSE for the first active extraction job.
  useEffect(() => {
    const pid = currentProject?.id;
    if (!pid || papers.length === 0) return;

    const activePaper = papers.find((paper) => isActivePaper(paper) && paper.latest_job_id);
    if (!activePaper || !activePaper.latest_job_id) return;

    const initial = progressFromPaper(activePaper) || undefined;
    const alreadyTracking = sseState.paperId === activePaper.id
      && (sseState.status === 'streaming' || sseState.status === 'connecting');

    if (!alreadyTracking) {
      reconnectActive(pid, activePaper.id, activePaper.latest_job_id, initial || undefined);
    }

    if (sseState.status === 'reconnecting' && sseState.paperId === activePaper.id) {
      if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
      reconnectRef.current = window.setTimeout(() => {
        reconnectActive(pid, activePaper.id, activePaper.latest_job_id!, initial || undefined);
      }, 3000);
    }

    return () => {
      if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
    };
  }, [papers, currentProject?.id, sseState.status, sseState.paperId, reconnectActive]);

  // Synchronize SSE state changes to show real-time progress & trigger list refreshes
  useEffect(() => {
    if (!sseState.paperId) return;

    if (sseState.status === 'streaming' || sseState.status === 'connecting' || sseState.status === 'reconnecting') {
      setProgressMap(prev => ({
        ...prev,
        [sseState.paperId!]: {
          step: sseState.step || 'starting',
          percent: sseState.percent || 0,
          message: sseState.message,
        },
      }));
    } else if (sseState.status === 'done') {
      message.success(`文献抽取成功！共产生 ${sseState.result?.candidateCount || 0} 条结构化候选记录。`);
      setProgressMap(prev => {
        const copy = { ...prev };
        delete copy[sseState.paperId!];
        return copy;
      });
      unsubscribe();
      load(true);
    } else if (sseState.status === 'error') {
      const errMsg = sseState.error?.message || '未知错误';
      const failedPaperId = sseState.paperId!;

      setProgressMap(prev => {
        const copy = { ...prev };
        delete copy[failedPaperId];
        return copy;
      });
      unsubscribe();
      load(true);

      message.error(`抽取失败: ${errMsg}`);
    } else if (sseState.status === 'cancelled') {
      message.warning('抽取已被用户手动取消');
      setProgressMap(prev => {
        const copy = { ...prev };
        delete copy[sseState.paperId!];
        return copy;
      });
      unsubscribe();
      load(true);
    }
  }, [sseState.status, sseState.step, sseState.percent, sseState.error, sseState.result]);

  // Background/Fallback HTTP Polling for jobs where SSE is disconnected or run in background
  useEffect(() => {
    const pid = projectRef.current?.id;
    if (!pid) return;

    const pollProgress = async () => {
      const activePapers = papers.filter(isActivePaper);
      if (!activePapers.length) return;

      const updates: Record<number, ExtractionProgress> = {};
      await Promise.all(
        activePapers.map(async p => {
          try {
            const res = await api.get(`/projects/${pid}/papers/${p.id}/extraction-status`);
            const {
              extraction_step,
              extraction_percent,
              progress_message,
              error,
            } = res.data;
            updates[p.id] = {
              step: extraction_step || 'starting',
              percent: extraction_percent || 0,
              message: progress_message || undefined,
              error: error || '',
            };
          } catch {
            // ignore failures
          }
        })
      );

      setProgressMap(prev => {
        const filtered: Record<number, ExtractionProgress> = {};
        for (const [id, prog] of Object.entries(prev)) {
          // keep local progress if paper is still in list
          if (papers.some(p => p.id === Number(id))) {
            filtered[Number(id)] = prog;
          }
        }
        return { ...filtered, ...updates };
      });
    };

    pollProgress();
    const progInterval = setInterval(pollProgress, 3000);
    return () => clearInterval(progInterval);
  }, [papers, sseState.paperId]);

  const doUpload = async (file: File) => {
    const pid = projectRef.current?.id;
    if (!pid) return;
    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    try {
      await api.post(`/projects/${pid}/papers`, formData, { timeout: 180000 });
      message.success(`${file.name} 上传成功`);
      load();
    } catch (err: any) {
      message.error(err.response?.data?.detail || `${file.name} 上传失败`);
    } finally {
      setUploading(false);
    }
  };

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

  const openExtractDialog = async (paper: Paper) => {
    setSelectedPaper(paper);
    setSelectedMode((paper.latest_requested_mode as 'auto' | 'weak' | 'strong') || 'auto');
    setSelectedParserStrategy('mineru_cloud');
    setExtractDialogOpen(true);
    setLlmConfig(null);

    if (!currentProject) return;
    try {
      const res = await api.get(`/projects/${currentProject.id}/llm-config`);
      setLlmConfig(res.data);
    } catch {
      setLlmConfig(null);
    }
  };

  const runExtract = async (confirmWipe = false) => {
    if (!selectedPaper || !currentProject) return;
    const { jobId } = await startExtraction(
      currentProject.id,
      selectedPaper.id,
      selectedMode,
      selectedParserStrategy,
      confirmWipe,
    );
    const modeText = modeLabels[selectedMode] || selectedMode;
    message.success(`已加入抽取队列：${modeText}`);
    setExtractDialogOpen(false);
    setSelectedPaper(null);
    subscribe(currentProject.id, selectedPaper.id, jobId);
    setTimeout(() => load(true), 500);
  };

  const triggerExtract = async () => {
    if (!selectedPaper || !currentProject) return;
    setExtracting(true);
    try {
      await runExtract(false);
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      if (err.response?.status === 409 && typeof detail === 'string') {
        Modal.confirm({
          title: '重新抽取将清空已有候选记录',
          content: detail,
          okText: '确认清空并重新抽取',
          okType: 'danger',
          cancelText: '取消',
          onOk: async () => {
            setExtracting(true);
            try {
              await runExtract(true);
            } catch (e: any) {
              message.error(e.response?.data?.detail || '触发失败');
            } finally {
              setExtracting(false);
            }
          },
        });
        return;
      }
      message.error(detail || '触发失败');
    } finally {
      setExtracting(false);
    }
  };

  const doCancelExtract = async (paperId: number) => {
    if (!currentProject) return;
    try {
      await cancelExtraction(currentProject.id, paperId);
      message.success('取消请求已发送，抽取将在数秒内停止');
      load(true);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '取消失败');
    }
  };

  const deletePaper = async (paperId: number) => {
    if (!currentProject) return;
    try {
      await api.delete(`/projects/${currentProject.id}/papers/${paperId}`);
      message.success('文献已删除');
      load();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '删除失败');
    }
  };

  const downloadPaper = async (paper: Paper, e?: React.MouseEvent) => {
    e?.stopPropagation();
    if (!currentProject) return;
    try {
      const res = await api.get(
        `/projects/${currentProject.id}/papers/${paper.id}/download`,
        { responseType: 'blob' },
      );
      downloadBlobResponse(
        res.data,
        paper.original_filename || `paper_${paper.id}.pdf`,
        'application/pdf',
      );
    } catch (err: any) {
      message.error(err.response?.data?.detail || 'PDF 下载失败');
    }
  };

  const columns = [
    {
      title: '文件名',
      dataIndex: 'original_filename',
      key: 'filename',
      ellipsis: true,
      width: 220,
    },
    {
      title: '标题',
      dataIndex: 'paper_title',
      key: 'title',
      ellipsis: true,
      render: (v: string) => v || <span style={{ color: '#aaa', fontStyle: 'italic' }}>暂无标题</span>,
    },
    {
      title: '期刊',
      dataIndex: 'journal',
      key: 'journal',
      width: 140,
      render: (v: string) => v || '-',
    },
    {
      title: '年份',
      dataIndex: 'year',
      key: 'year',
      width: 70,
      render: (v: number) => v || '-',
    },
    {
      title: '页数',
      dataIndex: 'page_count',
      key: 'page_count',
      width: 60,
      render: (v: number) => v || '-',
    },
    {
      title: '状态 / 进度',
      dataIndex: 'status',
      key: 'status',
      width: 180,
      render: (s: string, r: Paper) => (
        <PaperStatusCell paper={r} progress={progressMap[r.id]} />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      render: (_: any, r: Paper) => {
        const isExtracting = r.status === 'extracting' || r.status === 'queued';

        return (
          <Space size="small">
            {isExtracting ? (
              <Popconfirm
                title="确定要取消这次抽取任务吗？已解析的数据将不会保存。"
                onConfirm={() => doCancelExtract(r.id)}
                okText="取消抽取"
                cancelText="继续等待"
              >
                <Button
                  size="small"
                  type="link"
                  danger
                  icon={<CloseCircleOutlined />}
                >
                  取消
                </Button>
              </Popconfirm>
            ) : (
              <Button
                size="small"
                type="link"
                icon={<PlayCircleOutlined />}
                onClick={() => openExtractDialog(r)}
              >
                抽取
              </Button>
            )}
            <Button
              size="small"
              type="link"
              icon={<DownloadOutlined />}
              onClick={(e) => downloadPaper(r, e)}
            >
              下载
            </Button>
            <Popconfirm
              title="删除此文献将同步清空其关联的所有候选提取结果、样品目录和事实记录，确认删除？"
              onConfirm={() => deletePaper(r.id)}
            >
              <Button size="small" type="link" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  if (!currentProject) {
    return <Empty description="请先选择一个项目" style={{ marginTop: 100 }} />;
  }

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

      <Table
        dataSource={papers}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 20 }}
        size="small"
      />

      <ExtractionModeModal
        open={extractDialogOpen}
        paper={selectedPaper}
        llmConfig={llmConfig}
        selectedMode={selectedMode}
        selectedParserStrategy={selectedParserStrategy}
        extracting={extracting}
        onModeChange={setSelectedMode}
        onParserChange={setSelectedParserStrategy}
        onOk={triggerExtract}
        onCancel={() => setExtractDialogOpen(false)}
        afterClose={() => {
          setSelectedPaper(null);
          setLlmConfig(null);
        }}
      />
    </div>
  );
}
