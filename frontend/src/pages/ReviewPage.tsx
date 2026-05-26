import { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Empty, Select, Descriptions, Input,
  Collapse, message, Popconfirm, Divider, Modal,
} from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined, QuestionCircleOutlined,
  ExclamationCircleOutlined, EditOutlined, PlusOutlined, DeleteOutlined,
  QuestionOutlined,
} from '@ant-design/icons';
import { useProject } from '../stores/project';
import api from '../api/client';

const { Option } = Select;
const { Panel } = Collapse;

interface CandidateRow {
  id: number;
  sample_id: string | null;
  performance_metric: string | null;
  performance_value: string | null;
  performance_unit: string | null;
  review_status: string | null;
  ai_confidence: number | null;
  source_location: string | null;
  paper_title: string | null;
  source_paper_id: number | null;
  created_at: string;
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

// 40-column reference table
const COLUMN_REFERENCE = [
  { no: 1, en: 'record_id', zh: '数据记录编号', meaning: '每一行数据的唯一编号' },
  { no: 2, en: 'paper_id', zh: '文献编号', meaning: '每篇文献的内部编号' },
  { no: 3, en: 'paper_title', zh: '文献题名', meaning: '论文标题' },
  { no: 4, en: 'doi_or_url', zh: 'DOI 或链接', meaning: 'DOI、网页链接或数据库链接' },
  { no: 5, en: 'year', zh: '发表年份', meaning: '文献发表年份' },
  { no: 6, en: 'journal', zh: '期刊名称', meaning: '文献发表的期刊或会议名称' },
  { no: 7, en: 'sample_group_id', zh: '样品组编号', meaning: '同一组变量实验的编号，如同一篇文献中的不同配比样品' },
  { no: 8, en: 'sample_id', zh: '样品编号', meaning: '文献中给出的样品名，如 PET-3、S1、PVDF-BT-1.5' },
  { no: 9, en: 'material_system', zh: '材料体系', meaning: '材料组成体系，如 PET/TiO₂、PVDF/BaTiO₃、PAN/CNT' },
  { no: 10, en: 'fiber_type', zh: '纤维类型', meaning: '熔融纺丝长丝、湿法纺丝纤维、电纺纳米纤维、碳纤维等' },
  { no: 11, en: 'variable_name', zh: '变量名称', meaning: '该组样品主要变化的因素，如 TiO₂含量、牵伸倍数、碳化温度' },
  { no: 12, en: 'variable_value', zh: '变量数值', meaning: '当前样品对应的变量值，如 3、4、1200' },
  { no: 13, en: 'variable_unit', zh: '变量单位', meaning: '变量单位，如 wt%、℃、×、min、m/min' },
  { no: 14, en: 'composition_expression', zh: '成分表达式', meaning: '完整成分配方，如 PET=97 wt%; TiO₂=3 wt%' },
  { no: 15, en: 'matrix_name', zh: '基体/前驱体名称', meaning: '主体材料名称，如 PET、PVDF、PAN、PA6' },
  { no: 16, en: 'matrix_content', zh: '基体/前驱体含量', meaning: '主体材料含量，如 97' },
  { no: 17, en: 'matrix_unit', zh: '基体/前驱体单位', meaning: '主体材料含量单位，如 wt%、vol%、mol%' },
  { no: 18, en: 'additive_expression', zh: '填料/改性组分表达式', meaning: '填料、增强相、功能组分信息，如 TiO₂=3 wt%, 50 nm' },
  { no: 19, en: 'solvent_or_aid', zh: '溶剂/助剂', meaning: '溶剂、分散剂、相容剂、加工助剂等，如 DMF、DMSO、PVP' },
  { no: 20, en: 'composition_evidence', zh: '成分证据', meaning: '成分信息在文献中的来源位置，如 Table 1、实验部分、p.3' },
  { no: 21, en: 'process_route', zh: '工艺路线', meaning: '总体制备路线，如 熔融纺丝、湿法纺丝-牵伸-热处理' },
  { no: 22, en: 'spinning_method', zh: '纺丝方法', meaning: '具体纺丝方法，如 melt spinning、wet spinning、electrospinning' },
  { no: 23, en: 'process_parameters', zh: '工艺参数', meaning: '关键工艺参数，如 spinning_temperature=285 ℃; draw_ratio=3.5×' },
  { no: 24, en: 'post_treatment', zh: '后处理条件', meaning: '退火、热牵伸、碳化、稳定化、洗涤、干燥等后处理' },
  { no: 25, en: 'process_evidence', zh: '工艺证据', meaning: '工艺信息在文献中的来源位置' },
  { no: 26, en: 'structure_methods', zh: '结构表征方法', meaning: 'SEM、XRD、DSC、FTIR、Raman、WAXS、SAXS 等' },
  { no: 27, en: 'structure_features', zh: '结构特征', meaning: '结构指标集合，如 crystallinity=36.5%; fiber_diameter=18.5 μm' },
  { no: 28, en: 'structure_evidence', zh: '结构证据', meaning: '结构信息来源位置，如 Fig. 3、Table 2' },
  { no: 29, en: 'performance_category', zh: '性能类别', meaning: '力学性能、热性能、电学性能、压电性能、传感性能等' },
  { no: 30, en: 'performance_metric', zh: '性能指标', meaning: '单一性能指标名称，如 tensile_strength、elongation_at_break' },
  { no: 31, en: 'performance_value', zh: '性能数值', meaning: '性能指标对应的数值，如 520、28.5、0.88' },
  { no: 32, en: 'performance_unit', zh: '性能单位', meaning: '性能单位，如 MPa、%、GPa、S/m、pC/N、V' },
  { no: 33, en: 'performance_method', zh: '性能测试方法', meaning: '测试方法或标准，如 tensile test、GB/T 14344、four-probe method' },
  { no: 34, en: 'performance_condition', zh: '性能测试条件', meaning: '测试条件，如 gauge_length=20 mm; tensile_speed=10 mm/min' },
  { no: 35, en: 'performance_evidence', zh: '性能证据', meaning: '性能数据来源位置，如 Table 3、Fig. 5b' },
  { no: 36, en: 'extraction_method', zh: '提取方式', meaning: '数据来源方式，如手动录入、AI正文提取、AI表格提取、AI图中读取' },
  { no: 37, en: 'evidence_text', zh: '原文证据片段', meaning: '支撑该数据的原文短句或表述' },
  { no: 38, en: 'ai_confidence', zh: 'AI置信度', meaning: 'AI 对该条数据提取结果的可信度，通常为 0–1' },
  { no: 39, en: 'review_status', zh: '审核状态', meaning: '待审核、已修改、通过、存疑、缺失' },
  { no: 40, en: 'reviewer_comment', zh: '审核意见', meaning: '学生或审核人对该条数据的备注' },
];

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
  const [rows, setRows] = useState<CandidateRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [editing, setEditing] = useState(false);
  const [editValues, setEditValues] = useState<Record<string, any>>({});
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [helpOpen, setHelpOpen] = useState(false);
  const [extractionSummary, setExtractionSummary] = useState<Record<string, any> | null>(null);

  const pid = currentProject?.id;

  const loadList = () => {
    if (!pid) return;
    setLoading(true);
    const params: any = {};
    if (statusFilter) params.review_status = statusFilter;
    api.get(`/projects/${pid}/candidates`, { params })
      .then(r => setRows(r.data))
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false));
  };

  const loadDetail = (id: number) => {
    if (!pid) return;
    api.get(`/projects/${pid}/candidates/${id}`)
      .then(r => {
        setDetail(r.data); setEditValues(r.data);
        // Also load extraction report for this candidate's paper
        if (r.data.source_paper_id) {
          api.get(`/projects/${pid}/papers/${r.data.source_paper_id}/extraction-report`)
            .then(rr => setExtractionSummary(rr.data))
            .catch(() => setExtractionSummary(null));
        }
      })
      .catch(() => message.error('加载详情失败'));
  };

  useEffect(loadList, [pid, statusFilter]);

  useEffect(() => {
    if (selectedId) loadDetail(selectedId);
    else { setDetail(null); setEditing(false); setExtractionSummary(null); }
  }, [selectedId]);

  const doReview = async (action: string) => {
    if (!pid || !selectedId) return;
    try {
      await api.post(`/projects/${pid}/candidates/${selectedId}/review`, { action });
      message.success(`操作成功: ${statusLabels[action] || action}`);
      loadList();
      loadDetail(selectedId);
    } catch { message.error('操作失败'); }
  };

  const doDelete = async (id: number) => {
    if (!pid) return;
    try {
      await api.delete(`/projects/${pid}/candidates/${id}`);
      message.success('已永久删除');
      if (selectedId === id) setSelectedId(null);
      loadList();
    } catch { message.error('删除失败'); }
  };

  const saveEdit = async () => {
    if (!pid || !selectedId) return;
    try {
      await api.patch(`/projects/${pid}/candidates/${selectedId}`, editValues);
      message.success('保存成功');
      setEditing(false);
      loadList();
      loadDetail(selectedId);
    } catch { message.error('保存失败'); }
  };

  if (!currentProject) {
    return <Empty description="请先选择项目" style={{ marginTop: 100 }} />;
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 50 },
    { title: '样品编号', dataIndex: 'sample_id', width: 150, ellipsis: true,
      render: (v: string) => v || <span style={{color:'var(--color-text-secondary)'}}>—</span> },
    { title: '性能指标', dataIndex: 'performance_metric', width: 130, ellipsis: true },
    { title: '性能数值', dataIndex: 'performance_value', width: 100 },
    { title: '性能单位', dataIndex: 'performance_unit', width: 90 },
    { title: '审核状态', dataIndex: 'review_status', width: 85,
      render: (s: string) => <Tag color={statusColors[s] || 'default'}>{statusLabels[s] || s}</Tag> },
    { title: 'AI置信度', dataIndex: 'ai_confidence', width: 80,
      render: (v: number) => v != null ? `${(v * 100).toFixed(0)}%` : '—' },
    { title: '来源位置', dataIndex: 'source_location', width: 90, ellipsis: true },
    { title: '操作', width: 80, fixed: 'right' as const,
      render: (_: any, r: CandidateRow) => (
        <Popconfirm title="永久删除此条记录？" onConfirm={() => doDelete(r.id)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ];

  const helpColumns = [
    { title: '序号', dataIndex: 'no', width: 50 },
    { title: '英文字段名', dataIndex: 'en', width: 190, render: (v: string) => <code style={{fontSize:12}}>{v}</code> },
    { title: '中文字段名', dataIndex: 'zh', width: 120 },
    { title: '含义', dataIndex: 'meaning' },
  ];

  return (
    <div>
      <div className="page-header">
        <h1>审核队列</h1>
        <Space>
          <Select
            placeholder="状态筛选" allowClear style={{ width: 140 }}
            value={statusFilter} onChange={setStatusFilter}
          >
            <Option value="pending">待审核</Option>
            <Option value="approved">通过</Option>
            <Option value="modified">已修改</Option>
            <Option value="uncertain">存疑</Option>
            <Option value="missing">缺失</Option>
          </Select>
          <Button icon={<QuestionOutlined />} onClick={() => setHelpOpen(true)}>
            字段说明
          </Button>
        </Space>
      </div>

      <div className="review-layout">
        <div className="review-table-section">
          <Table
            dataSource={rows} columns={columns} rowKey="id" loading={loading}
            size="small" pagination={{ pageSize: 30 }} scroll={{ x: 950 }}
            onRow={(r) => ({
              onClick: () => setSelectedId(r.id),
              style: { cursor: 'pointer', background: r.id === selectedId ? 'rgba(79,107,246,0.1)' : undefined },
            })}
          />
        </div>

        <div className="review-detail-section">
          {detail ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                <h3 style={{ margin: 0 }}>候选详情 #{detail.id}</h3>
                <Space>
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
                <Popconfirm title="标记为已删除？" onConfirm={() => doReview('deleted')}>
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

      <Modal
        title="数据主表 40 列字段说明"
        open={helpOpen}
        onCancel={() => setHelpOpen(false)}
        footer={null}
        width={900}
      >
        <Table
          dataSource={COLUMN_REFERENCE} columns={helpColumns} rowKey="no"
          size="small" pagination={false} scroll={{ y: 500 }}
        />
      </Modal>
    </div>
  );
}
