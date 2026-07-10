import { useState, useEffect, useRef } from 'react';
import {
  App, Table, Tag, Button, Space, Empty, Select, Input,
  Collapse, Popconfirm, Divider, Modal, Segmented, Tooltip, Alert,
} from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined, QuestionCircleOutlined,
  ExclamationCircleOutlined, EditOutlined, PlusOutlined, DeleteOutlined,
  QuestionOutlined, DownloadOutlined,
} from '@ant-design/icons';
import { useProject } from '../stores/project';
import api from '../api/client';
import { downloadBlobResponse } from '../utils/download';
import ExportFieldHelpModal from '../components/ExportFieldHelpModal';

const { Option } = Select;
const { Panel } = Collapse;

interface CandidateRow {
  id: number;
  sample_id: string | null;
  performance_category: string | null;
  performance_metric: string | null;
  performance_value: string | null;
  performance_unit: string | null;
  review_status: string | null;
  ai_confidence: number | null;
  evidence_text: string | null;
  reviewer_comment: string | null;
  candidate_status: string | null;
  source_location: string | null;
  paper_title: string | null;
  source_paper_id: number | null;
  created_at: string;
  updated_at?: string | null;
}

interface CandidateDetail {
  id: number;
  [key: string]: any;
}

const statusColors: Record<string, string> = {
  pending: 'gold', approved: 'green', modified: 'blue',
  uncertain: 'orange', missing: 'red', deleted: 'default',
};
const statusLabels: Record<string, string> = {
  pending: '待审核', approved: '通过', modified: '已修改',
  uncertain: '存疑', missing: '缺失', deleted: '已删除',
};
const statusAlias: Record<string, string> = {
  待审核: 'pending', 通过: 'approved', 已修改: 'modified',
  存疑: 'uncertain', 缺失: 'missing', 已删除: 'deleted',
};

function normalizeReviewStatus(status?: string | null) {
  if (!status) return '';
  return statusAlias[status] || status;
}

const coreMetricHints = [
  'density', 'porosity', 'shrinkage', 'fiber_diameter', 'fiber_length',
  'thermal_conductivity', 'surface_temperature', 'water_contact_angle',
  'dielectric_constant', 'dielectric_loss', 'electrical_conductivity',
  'tensile_strength', 'compressive_strength', 'compressive_stress',
  'permittivity', 'loss tangent', 'conductivity', 'contact angle',
  'thermal conductivity', 'surface temperature',
];

const secondaryMetricHints = [
  'xps', 'ftir', 'binding energy', 'reaction pathway', 'imidization',
  'fractional free volume', 'ffv', 'simulation', 'peak',
];

function metricPriority(row: Pick<CandidateRow, 'performance_metric' | 'reviewer_comment'>): 'Core' | 'Secondary' | 'Narrative' {
  const comment = row.reviewer_comment || '';
  const explicit = comment.match(/metric_priority=([^;]+)/);
  if (explicit?.[1]) return explicit[1].trim() as 'Core' | 'Secondary' | 'Narrative';
  const metric = (row.performance_metric || '').toLowerCase();
  if (secondaryMetricHints.some(k => metric.includes(k))) return 'Secondary';
  if (coreMetricHints.some(k => metric.includes(k))) return 'Core';
  return 'Secondary';
}

function hasRoughSource(source?: string | null) {
  const s = (source || '').trim().toLowerCase();
  if (!s) return true;
  if (['results_text', 'experimental', 'figure_caption', 'table_text', 'results', 'figure', 'table', 'unknown'].includes(s)) return true;
  return !/(p\.|page|fig\.|figure|table|section|sec\.|scheme)/i.test(s);
}

function apiErrorMessage(err: any, fallback: string) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail?.message) return detail.message;
  return fallback;
}

function issueTags(row: CandidateRow) {
  const tags: string[] = [];
  if (!row.sample_id) tags.push('样品缺失');
  if (!row.evidence_text) tags.push('证据缺失');
  if (hasRoughSource(row.source_location)) tags.push('来源过粗');
  if ((row.reviewer_comment || '').includes('value_operator=<')) tags.push('不等号');
  if ((row.reviewer_comment || '').includes('range_')) tags.push('范围值');
  return tags;
}

// Detail panel groups
const detailGroups = [
  { title: '基础信息', fields: [
    'record_id', 'paper_title', 'doi_or_url', 'year', 'journal',
  ]},
  { title: '样品与成分', fields: [
    'sample_group_id', 'sample_id', 'material_system', 'fiber_type',
    'variable_name', 'variable_value', 'variable_unit',
    'composition_expression', 'matrix_name', 'matrix_content', 'matrix_unit',
    'additive_expression', 'solvent_or_aid',
  ]},
  { title: '工艺', fields: [
    'process_route', 'spinning_method', 'process_parameters', 'post_treatment',
  ]},
  { title: '结构', fields: [
    'structure_methods', 'structure_features',
  ]},
  { title: '性能', fields: [
    'performance_category', 'performance_metric', 'performance_value',
    'performance_unit', 'performance_method', 'performance_condition',
  ]},
  { title: '证据与审核', fields: [
    'extraction_method', 'evidence_text', 'composition_evidence',
    'process_evidence', 'structure_evidence', 'performance_evidence',
    'ai_confidence', 'review_status', 'reviewer_comment', 'source_location',
  ]},
];

export default function ReviewPage() {
  const { currentProject } = useProject();
  const { message } = App.useApp();
  const [rows, setRows] = useState<CandidateRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [editing, setEditing] = useState(false);
  const [editValues, setEditValues] = useState<Record<string, any>>({});
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [priorityFilter, setPriorityFilter] = useState<string>('Core');
  const [issueFilter, setIssueFilter] = useState<string | undefined>(undefined);
  const [helpOpen, setHelpOpen] = useState(false);
  const [extractionSummary, setExtractionSummary] = useState<Record<string, any> | null>(null);
  const [paperFilter, setPaperFilter] = useState<number | undefined>(undefined);
  const [papers, setPapers] = useState<Array<{ id: number; label: string }>>([]);
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const detailRequestRef = useRef(0);

  const pid = currentProject?.id;

  const loadPapers = () => {
    if (!pid) return;
    api.get(`/projects/${pid}/papers`, { params: { page_size: 200 } })
      .then(r => {
        const items = (r.data || []).map((p: any) => ({
          id: p.id,
          label: p.paper_title || p.original_filename || `文献 #${p.id}`,
        }));
        setPapers(items);
      })
      .catch(() => {});
  };

  const loadList = () => {
    if (!pid) return;
    setLoading(true);
    const params: any = { page_size: 200 };
    if (statusFilter) params.review_status = statusFilter;
    if (paperFilter) params.paper_id = paperFilter;
    api.get(`/projects/${pid}/candidates`, { params })
      .then(r => {
        setRows(r.data);
        setSelectedRowKeys(prev => prev.filter(id => r.data.some((row: CandidateRow) => row.id === id)));
      })
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false));
  };

  const clearDetail = () => {
    detailRequestRef.current += 1;
    setSelectedId(null);
    setDetail(null);
    setEditing(false);
    setExtractionSummary(null);
  };

  const loadDetail = (id: number) => {
    if (!pid) return;
    const reqId = ++detailRequestRef.current;
    api.get(`/projects/${pid}/candidates/${id}`)
      .then(r => {
        if (detailRequestRef.current !== reqId) return;
        setDetail(r.data);
        setEditValues(r.data);
        if (r.data.source_paper_id) {
          api.get(`/projects/${pid}/papers/${r.data.source_paper_id}/extraction-report`)
            .then(rr => {
              if (detailRequestRef.current === reqId) setExtractionSummary(rr.data);
            })
            .catch(() => {
              if (detailRequestRef.current === reqId) setExtractionSummary(null);
            });
        } else {
          setExtractionSummary(null);
        }
      })
      .catch((err) => {
        if (detailRequestRef.current !== reqId) return;
        if (err?.response?.status === 404) {
          setSelectedId(null);
          setDetail(null);
          setEditing(false);
          return;
        }
        message.error(apiErrorMessage(err, '加载详情失败'));
      });
  };

  useEffect(loadList, [pid, statusFilter, paperFilter]);
  useEffect(loadPapers, [pid]);

  useEffect(() => {
    if (selectedId) loadDetail(selectedId);
    else { setDetail(null); setEditing(false); setExtractionSummary(null); }
  }, [selectedId]);

  const handleConflict = (err: any) => {
    if (err?.response?.status !== 409) return false;
    const detail = err.response?.data?.detail;
    const text = typeof detail === 'string'
      ? detail
      : detail?.message || '该记录已被他人修改或审核，请刷新后重试';
    Modal.warning({
      title: '记录已被他人修改',
      content: text,
      onOk: () => {
        loadList();
        if (selectedId) loadDetail(selectedId);
      },
    });
    return true;
  };

  const doReview = async (action: string) => {
    if (!pid || !selectedId || !detail) return;
    try {
      const res = await api.post(`/projects/${pid}/candidates/${selectedId}/review`, {
        action,
        expected_updated_at: detail.updated_at,
      });
      message.success(`操作成功: ${statusLabels[action] || action}`);
      setDetail(res.data);
      setEditValues(res.data);
      loadList();
    } catch (err: any) {
      if (!handleConflict(err)) message.error(apiErrorMessage(err, '操作失败'));
    }
  };

  const doDelete = async (id: number) => {
    if (!pid) return;
    if (selectedId === id) clearDetail();
    setSelectedRowKeys(prev => prev.filter(key => key !== id));
    try {
      await api.delete(`/projects/${pid}/candidates/${id}`);
      message.success('已永久删除');
      loadList();
    } catch (err: any) {
      message.error(apiErrorMessage(err, '删除失败'));
    }
  };

  const doBatchDelete = async (ids: number[]) => {
    if (!pid || ids.length === 0) return;
    setBatchLoading(true);
    try {
      const res = await api.post(`/projects/${pid}/candidates/batch-delete`, { ids });
      message.success(`已删除 ${res.data.deleted_count} 条记录`);
      if (selectedId && ids.includes(selectedId)) clearDetail();
      setSelectedRowKeys([]);
      loadList();
    } catch (err: any) {
      message.error(apiErrorMessage(err, '批量删除失败'));
    } finally {
      setBatchLoading(false);
    }
  };

  const doDeleteByPaper = async () => {
    if (!pid || !paperFilter) return;
    setBatchLoading(true);
    try {
      const res = await api.post(
        `/projects/${pid}/candidates/batch-delete-by-paper`,
        null,
        { params: { paper_id: paperFilter } },
      );
      message.success(`已删除该文献下 ${res.data.deleted_count} 条记录`);
      clearDetail();
      setSelectedRowKeys([]);
      loadList();
    } catch (err: any) {
      message.error(apiErrorMessage(err, '删除失败'));
    } finally {
      setBatchLoading(false);
    }
  };

  const downloadPaperPdf = async (paperId: number, filename?: string) => {
    if (!pid) return;
    try {
      const res = await api.get(`/projects/${pid}/papers/${paperId}/download`, {
        responseType: 'blob',
      });
      downloadBlobResponse(
        res.data,
        filename || `paper_${paperId}.pdf`,
        'application/pdf',
      );
    } catch (err: any) {
      message.error(err.response?.data?.detail || 'PDF 下载失败');
    }
  };

  const saveEdit = async () => {
    if (!pid || !selectedId || !detail) return;
    try {
      await api.patch(`/projects/${pid}/candidates/${selectedId}`, {
        ...editValues,
        expected_updated_at: detail.updated_at,
      });
      message.success('保存成功');
      setEditing(false);
      loadList();
      loadDetail(selectedId);
    } catch (err: any) {
      if (!handleConflict(err)) message.error('保存失败');
    }
  };

  if (!currentProject) {
    return <Empty description="请先选择项目" style={{ marginTop: 100 }} />;
  }

  const filteredRows = rows.filter(row => {
    if (priorityFilter !== 'All' && metricPriority(row) !== priorityFilter) return false;
    if (issueFilter && !issueTags(row).includes(issueFilter)) return false;
    return true;
  });

  const summary = {
    core: rows.filter(r => metricPriority(r) === 'Core').length,
    secondary: rows.filter(r => metricPriority(r) === 'Secondary').length,
    pending: rows.filter(r => normalizeReviewStatus(r.review_status) === 'pending').length,
    uncertain: rows.filter(r => normalizeReviewStatus(r.review_status) === 'uncertain').length,
    missingEvidence: rows.filter(r => !r.evidence_text).length,
    roughSource: rows.filter(r => hasRoughSource(r.source_location)).length,
  };

  const columns = [
    { title: '样品', dataIndex: 'sample_id', width: 150, ellipsis: true,
      render: (v: string) => v || <span style={{color:'var(--color-text-secondary)'}}>—</span> },
    { title: '指标', dataIndex: 'performance_metric', width: 180, ellipsis: true,
      render: (v: string, r: CandidateRow) => (
        <Space size={4} wrap>
          <span>{v || '—'}</span>
          <Tag color={metricPriority(r) === 'Core' ? 'blue' : 'purple'}>{metricPriority(r)}</Tag>
        </Space>
      ) },
    { title: '数值', dataIndex: 'performance_value', width: 92,
      render: (v: string, r: CandidateRow) => <span>{v || '—'} {r.performance_unit || ''}</span> },
    { title: '证据', dataIndex: 'evidence_text', width: 240, ellipsis: true,
      render: (v: string | null) => v ? <Tooltip title={v}><span>{v}</span></Tooltip> : <Tag color="red">缺失</Tag> },
    { title: '来源', dataIndex: 'source_location', width: 150, ellipsis: true,
      render: (v: string | null, r: CandidateRow) => (
        <Space size={4} wrap>
          <span>{v || '—'}</span>
          {hasRoughSource(v) && <Tag color="orange">过粗</Tag>}
          {issueTags(r).filter(t => t !== '来源过粗').map(t => <Tag key={t} color="gold">{t}</Tag>)}
        </Space>
      ) },
    { title: '状态', dataIndex: 'review_status', width: 85,
      render: (s: string) => {
        const normalized = normalizeReviewStatus(s);
        return <Tag color={statusColors[normalized] || 'default'}>{statusLabels[normalized] || s}</Tag>;
      } },
    { title: '置信度', dataIndex: 'ai_confidence', width: 80,
      render: (v: number) => v != null ? `${(v * 100).toFixed(0)}%` : '—' },
    { title: '操作', width: 80, fixed: 'right' as const,
      render: (_: any, r: CandidateRow) => (
        <Popconfirm title="永久删除此条记录？" onConfirm={() => doDelete(r.id)}>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={e => e.stopPropagation()} />
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <div className="page-header">
        <h1>核心数据审核</h1>
        <Space>
          <Segmented
            value={priorityFilter}
            onChange={v => setPriorityFilter(String(v))}
            options={[
              { label: 'Core', value: 'Core' },
              { label: 'Secondary', value: 'Secondary' },
              { label: '全部', value: 'All' },
            ]}
          />
          <Select
            placeholder="按文献筛选" allowClear style={{ width: 220 }}
            value={paperFilter} onChange={setPaperFilter}
            showSearch
            optionFilterProp="label"
            options={papers.map(p => ({ value: p.id, label: p.label }))}
          />
          <Select
            placeholder="状态筛选" allowClear style={{ width: 140 }}
            value={statusFilter} onChange={setStatusFilter}
          >
            <Option value="pending">待审核</Option>
            <Option value="approved">通过</Option>
            <Option value="modified">已修改</Option>
            <Option value="uncertain">存疑</Option>
            <Option value="missing">缺失</Option>
            <Option value="deleted">已删除</Option>
          </Select>
          <Select
            placeholder="问题筛选" allowClear style={{ width: 140 }}
            value={issueFilter} onChange={setIssueFilter}
          >
            <Option value="样品缺失">样品缺失</Option>
            <Option value="证据缺失">证据缺失</Option>
            <Option value="来源过粗">来源过粗</Option>
            <Option value="范围值">范围值</Option>
            <Option value="不等号">不等号</Option>
          </Select>
          <Button icon={<QuestionOutlined />} onClick={() => setHelpOpen(true)}>
            字段说明
          </Button>
        </Space>
      </div>

      <div className="review-quality-strip">
        <div className="quality-item"><span>Core</span><strong>{summary.core}</strong></div>
        <div className="quality-item"><span>Secondary</span><strong>{summary.secondary}</strong></div>
        <div className="quality-item"><span>待审核</span><strong>{summary.pending}</strong></div>
        <div className="quality-item warning"><span>存疑</span><strong>{summary.uncertain}</strong></div>
        <div className="quality-item danger"><span>证据缺失</span><strong>{summary.missingEvidence}</strong></div>
        <div className="quality-item warning"><span>来源过粗</span><strong>{summary.roughSource}</strong></div>
      </div>

      <div className="review-layout">
        <div className="review-table-section">
          {paperFilter && (
            <div style={{ marginBottom: 12 }}>
              <Button
                icon={<DownloadOutlined />}
                onClick={() => {
                  const paper = papers.find(p => p.id === paperFilter);
                  downloadPaperPdf(paperFilter, paper?.label);
                }}
              >
                下载原文 PDF
              </Button>
            </div>
          )}
          <div style={{ marginBottom: 12 }}>
            <Alert
              type="info"
              showIcon
              message="批量操作"
              description="勾选表格左侧复选框后点击「批量删除」；也可先按文献筛选，再删除该文献下的全部记录。"
              style={{ marginBottom: 8 }}
            />
            <Space wrap>
              <Popconfirm
                title={`永久删除选中的 ${selectedRowKeys.length} 条记录？`}
                onConfirm={() => doBatchDelete(selectedRowKeys)}
                disabled={selectedRowKeys.length === 0}
              >
                <Button
                  danger
                  loading={batchLoading}
                  icon={<DeleteOutlined />}
                  disabled={selectedRowKeys.length === 0}
                >
                  批量删除{selectedRowKeys.length > 0 ? ` (${selectedRowKeys.length})` : ''}
                </Button>
              </Popconfirm>
              {paperFilter && (
                <Popconfirm
                  title="永久删除当前文献下的全部候选记录？不可恢复！"
                  onConfirm={doDeleteByPaper}
                >
                  <Button danger loading={batchLoading} icon={<DeleteOutlined />}>
                    删除本篇文献全部记录
                  </Button>
                </Popconfirm>
              )}
              {selectedRowKeys.length > 0 && (
                <Button onClick={() => setSelectedRowKeys([])}>清空选择</Button>
              )}
            </Space>
          </div>
          <div className="review-table-scroll">
          <Table
            dataSource={filteredRows} columns={columns} rowKey="id" loading={loading}
            size="small" pagination={{ pageSize: 30 }}
            scroll={{ x: 'max-content', y: 'calc(100vh - 380px)' }}
            rowSelection={{
              selectedRowKeys,
              onChange: keys => setSelectedRowKeys(keys as number[]),
              preserveSelectedRowKeys: true,
            }}
            onRow={(r) => ({
              onClick: () => setSelectedId(r.id),
              style: { cursor: 'pointer', background: r.id === selectedId ? 'rgba(79,107,246,0.1)' : undefined },
            })}
          />
          </div>
        </div>

        <div className="review-detail-section">
          {detail ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                <div>
                  <h3 style={{ margin: 0 }}>候选详情 #{detail.id}</h3>
                </div>
                <Space>
                  {detail.source_paper_id && (
                    <Button
                      size="small"
                      icon={<DownloadOutlined />}
                      onClick={() => downloadPaperPdf(
                        detail.source_paper_id,
                        detail.paper_title || detail.original_filename,
                      )}
                    >
                      下载原文
                    </Button>
                  )}
                  {!editing ? (
                    <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(true)}>编辑</Button>
                  ) : (
                    <>
                      <Button size="small" type="primary" onClick={saveEdit}>保存</Button>
                      <Button size="small" onClick={() => { setEditing(false); setEditValues(detail); }}>取消</Button>
                    </>
                  )}
                </Space>
              </div>

              <Divider style={{ margin: '12px 0' }} />

              <Space style={{ marginBottom: 16 }} wrap>
                <Button size="small" type="primary" icon={<CheckCircleOutlined />}
                  style={{ background: '#059669' }} onClick={() => doReview('approved')}>通过</Button>
                <Button size="small" icon={<QuestionCircleOutlined />}
                  onClick={() => doReview('uncertain')}>存疑</Button>
                <Button size="small" icon={<ExclamationCircleOutlined />}
                  onClick={() => doReview('missing')}>缺失</Button>
                <Popconfirm title="标记为已删除？记录仍保留，导出时不会包含。" onConfirm={() => doReview('deleted')}>
                  <Button size="small" danger icon={<DeleteOutlined />}>标记删除</Button>
                </Popconfirm>
                <Popconfirm title="永久删除此记录？不可恢复！" onConfirm={() => doDelete(detail.id)}>
                  <Button size="small" danger type="primary" icon={<DeleteOutlined />}>永久删除</Button>
                </Popconfirm>
              </Space>

              <Collapse defaultActiveKey={['基础信息', '性能']} ghost size="small">
                {detailGroups.map(g => (
                  <Panel header={<span className="detail-group-title">{g.title}</span>} key={g.title}>
                    {g.fields.map(f => (
                      <div className="detail-field" key={f}>
                        <label>{f}</label>
                        {editing ? (
                          <Input
                            size="small" value={editValues[f] ?? ''}
                            onChange={e => setEditValues({ ...editValues, [f]: e.target.value })}
                          />
                        ) : (
                          <div style={{ fontSize: 13, color: detail[f] ? 'var(--color-text-primary)' : 'var(--color-text-secondary)' }}>
                            {detail[f] ?? '—'}
                          </div>
                        )}
                      </div>
                    ))}
                  </Panel>
                ))}
                {extractionSummary && extractionSummary.文献标题 && (
                  <Panel header={<span className="detail-group-title">AI 抽取摘要</span>} key="extraction-summary">
                    <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', lineHeight: 2 }}>
                      <div>样品识别：<strong>{extractionSummary.识别样品数}</strong> 个 |
                        提取事实：<strong>{extractionSummary.提取事实总数}</strong> 条 |
                        生成记录：<strong>{extractionSummary.生成记录数}</strong> 条</div>
                      <div>成功归属：<strong>{extractionSummary.成功归属数}</strong> 条 |
                        未归属：<strong style={{color: extractionSummary.未归属事实数 > 0 ? 'var(--color-danger)' : 'inherit'}}>{extractionSummary.未归属事实数}</strong> 条</div>
                      <div>状态分布：待审核 {extractionSummary.待审核数} | 存疑 {extractionSummary.存疑数} | 缺失 {extractionSummary.缺失数} | 通过 {extractionSummary.通过数}</div>
                      {extractionSummary.推荐人工复核项?.length > 0 && (
                        <div style={{ marginTop: 4 }}>
                          {extractionSummary.推荐人工复核项.map((item: string, i: number) => (
                            <Tag key={i} color="orange" style={{ marginBottom: 4 }}>{item}</Tag>
                          ))}
                        </div>
                      )}
                    </div>
                  </Panel>
                )}
              </Collapse>
            </>
          ) : (
            <Empty description="点击左侧表格行查看详情" style={{ marginTop: 80 }} />
          )}
        </div>
      </div>

      <ExportFieldHelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}
