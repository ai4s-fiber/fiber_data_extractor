import { useEffect, useMemo, useState } from 'react';
import type { Key } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Steps,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  ApiOutlined,
  CloudServerOutlined,
  DownloadOutlined,
  LoginOutlined,
  LogoutOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import api from '../api/client';
import { useProject } from '../stores/project';
import { downloadBlobResponse } from '../utils/download';

const { Paragraph, Text } = Typography;

interface NasSource {
  id: string;
  label: string;
  path: string;
  available: boolean;
}

interface NasCandidate {
  id: string;
  relative_path: string;
  filename: string;
  size: number;
  modified_ns: string;
}

interface PlatformConfig {
  ready: boolean;
  base_url: string;
  dataset_id?: string;
  template_id?: string;
  dataset_name?: string;
  schema_version?: string;
  delivery_mode?: string;
  batch_template_sha256?: string;
  message?: string;
}

interface PlatformUser {
  username: string;
  display_name: string;
}

interface PreflightResult {
  ready: boolean;
  paper_count: number;
  input_paper_count?: number;
  delivered_paper_ids?: number[];
  sample_count?: number;
  input_sample_count?: number;
  excluded_blocked_sample_count?: number;
  excluded_unverified_sample_count?: number;
  excluded_semantic_sample_count?: number;
  excluded_incomplete_sample_count?: number;
  excluded_fact_count?: number;
  quality_gate?: string;
  record_count: number;
  domain_counts?: Record<string, number>;
  schema_version?: string;
  bytes: number;
  batch_sha256: string;
  filename: string;
  paper_ids: number[];
  previous_delivery?: { status?: string } | null;
}

const errorDetail = (error: any, fallback: string) => (
  error?.response?.data?.detail
  || error?.message
  || fallback
);

const formatBytes = (value: number) => {
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / (1024 ** index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
};

const paperIdsFromNasResult = (result: any): number[] => (
  Array.from(new Set(
    [...(result?.imported || []), ...(result?.duplicates || [])]
      .map((item: any) => Number(item.paper_id))
      .filter((paperId: number) => Number.isInteger(paperId) && paperId > 0),
  ))
);

export default function DataPipelinePage() {
  const { currentProject } = useProject();
  const { message } = App.useApp();
  const projectId = currentProject?.id;

  const [nasSources, setNasSources] = useState<NasSource[]>([]);
  const [sourceId, setSourceId] = useState<string>();
  const [relativeDirectory, setRelativeDirectory] = useState('');
  const [filenameQuery, setFilenameQuery] = useState('');
  const [recursive, setRecursive] = useState(true);
  const [nasMessage, setNasMessage] = useState('');
  const [candidates, setCandidates] = useState<NasCandidate[]>([]);
  const [selectedIds, setSelectedIds] = useState<Key[]>([]);
  const [scanning, setScanning] = useState(false);
  const [importingNas, setImportingNas] = useState(false);
  const [autoExtract, setAutoExtract] = useState(true);
  const [modelMode, setModelMode] = useState('strong');
  const [parserStrategy, setParserStrategy] = useState('mineru_cloud');
  const [nasResult, setNasResult] = useState<any>(null);
  const [currentBatchPaperIds, setCurrentBatchPaperIds] = useState<number[]>([]);

  const [platformConfig, setPlatformConfig] = useState<PlatformConfig | null>(null);
  const [platformUser, setPlatformUser] = useState<PlatformUser | null>(null);
  const [sessionId, setSessionId] = useState('');
  const [loginOpen, setLoginOpen] = useState(false);
  const [captchaImage, setCaptchaImage] = useState('');
  const [captchaUuid, setCaptchaUuid] = useState('');
  const [captchaEnabled, setCaptchaEnabled] = useState(true);
  const [captchaLoading, setCaptchaLoading] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [preflighting, setPreflighting] = useState(false);
  const [exportingBatch, setExportingBatch] = useState(false);
  const [exportingWorkbook, setExportingWorkbook] = useState(false);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [delivering, setDelivering] = useState(false);
  const [deliveryResult, setDeliveryResult] = useState<any>(null);
  const [loginForm] = Form.useForm();

  const sessionStorageKey = projectId
    ? `ai4s_platform_session_${projectId}`
    : '';
  const batchStorageKey = projectId
    ? `ai4s_nas_delivery_batch_${projectId}`
    : '';
  const sessionHeaders = sessionId
    ? { 'X-AI4S-Platform-Session': sessionId }
    : {};

  const selectedCandidates = useMemo(() => {
    const selected = new Set(selectedIds.map(String));
    return candidates.filter(item => selected.has(item.id));
  }, [candidates, selectedIds]);

  const invalidateNasScan = () => {
    setCandidates([]);
    setSelectedIds([]);
    setNasResult(null);
  };

  const loadNasSources = async () => {
    if (!projectId) return;
    try {
      const response = await api.get(`/projects/${projectId}/integrations/nas/sources`);
      const sources: NasSource[] = response.data.sources || [];
      setNasSources(sources);
      setNasMessage(response.data.message || '');
      setSourceId(previous => (
        previous && sources.some(item => item.id === previous)
          ? previous
          : sources.find(item => item.available)?.id
      ));
    } catch (error) {
      message.error(errorDetail(error, '加载 NAS 数据源失败'));
    }
  };

  const loadPlatformConfig = async () => {
    if (!projectId) return;
    try {
      const response = await api.get(`/projects/${projectId}/integrations/platform/config`);
      setPlatformConfig(response.data);
    } catch (error) {
      setPlatformConfig({
        ready: false,
        base_url: '',
        message: errorDetail(error, '加载平台配置失败'),
      });
    }
  };

  const clearPlatformSession = () => {
    if (sessionStorageKey) sessionStorage.removeItem(sessionStorageKey);
    setSessionId('');
    setPlatformUser(null);
  };

  const verifySession = async (handle: string) => {
    if (!projectId || !handle) return;
    try {
      const response = await api.get(
        `/projects/${projectId}/integrations/platform/session`,
        { headers: { 'X-AI4S-Platform-Session': handle } },
      );
      setSessionId(handle);
      setPlatformUser(response.data.user);
    } catch {
      clearPlatformSession();
    }
  };

  useEffect(() => {
    setCandidates([]);
    setSelectedIds([]);
    setNasResult(null);
    setPreflight(null);
    setDeliveryResult(null);
    setPlatformUser(null);
    setSessionId('');
    setCurrentBatchPaperIds([]);
    if (!projectId) return;

    loadNasSources();
    loadPlatformConfig();
    const persisted = sessionStorage.getItem(`ai4s_platform_session_${projectId}`) || '';
    if (persisted) verifySession(persisted);
    try {
      const persistedBatch = JSON.parse(
        sessionStorage.getItem(`ai4s_nas_delivery_batch_${projectId}`) || '[]',
      );
      if (Array.isArray(persistedBatch)) {
        setCurrentBatchPaperIds(
          Array.from(new Set(
            persistedBatch
              .map(Number)
              .filter((paperId: number) => Number.isInteger(paperId) && paperId > 0),
          )),
        );
      }
    } catch {
      sessionStorage.removeItem(`ai4s_nas_delivery_batch_${projectId}`);
    }
  }, [projectId]);

  const scanNas = async () => {
    if (!projectId || !sourceId) {
      message.warning('请先选择可用的 NAS 数据源');
      return;
    }
    setScanning(true);
    setCandidates([]);
    setSelectedIds([]);
    setNasResult(null);
    try {
      const response = await api.post(
        `/projects/${projectId}/integrations/nas/scan`,
        {
          source_id: sourceId,
          relative_directory: relativeDirectory,
          filename_query: filenameQuery,
          recursive,
        },
        { timeout: 180000 },
      );
      const files: NasCandidate[] = response.data.files || [];
      setCandidates(files);
      setSelectedIds([]);
      if (response.data.truncated) {
        message.warning(`扫描结果已达到 ${files.length} 条上限，请缩小目录或文件名筛选范围`);
      } else {
        message.success(`扫描完成，发现 ${files.length} 份 PDF，请勾选本次要导入的文件`);
      }
    } catch (error) {
      message.error(errorDetail(error, 'NAS 扫描失败'));
    } finally {
      setScanning(false);
    }
  };

  const importFromNas = async () => {
    if (!projectId || !sourceId || selectedCandidates.length === 0) {
      message.warning('请至少选择一份 PDF');
      return;
    }
    setImportingNas(true);
    setNasResult(null);
    try {
      const response = await api.post(
        `/projects/${projectId}/integrations/nas/import`,
        {
          source_id: sourceId,
          files: selectedCandidates,
          start_extraction: autoExtract,
          model_mode: modelMode,
          parser_strategy: parserStrategy,
        },
        { timeout: 600000 },
      );
      setNasResult(response.data);
      const importedPaperIds = paperIdsFromNasResult(response.data);
      if (importedPaperIds.length > 0) {
        setCurrentBatchPaperIds(importedPaperIds);
        sessionStorage.setItem(batchStorageKey, JSON.stringify(importedPaperIds));
      }
      const { imported_count, duplicate_count, failed_count } = response.data;
      const summary = `NAS 入库完成：新增 ${imported_count}，重复 ${duplicate_count}，失败 ${failed_count}`;
      if (failed_count) message.warning(summary);
      else message.success(summary);
      setPreflight(null);
    } catch (error) {
      message.error(errorDetail(error, 'NAS 批量入库失败'));
    } finally {
      setImportingNas(false);
    }
  };

  const refreshCaptcha = async () => {
    if (!projectId) return;
    setCaptchaLoading(true);
    try {
      const response = await api.get(
        `/projects/${projectId}/integrations/platform/captcha`,
      );
      setCaptchaImage(response.data.image_base64 || '');
      setCaptchaUuid(response.data.uuid || '');
      setCaptchaEnabled(response.data.captcha_enabled !== false);
      loginForm.setFieldValue('captcha_code', '');
    } catch (error) {
      message.error(errorDetail(error, '验证码加载失败'));
    } finally {
      setCaptchaLoading(false);
    }
  };

  const openPlatformLogin = () => {
    setLoginOpen(true);
    refreshCaptcha();
  };

  const connectPlatform = async () => {
    if (!projectId) return;
    const values = await loginForm.validateFields();
    setConnecting(true);
    try {
      const response = await api.post(
        `/projects/${projectId}/integrations/platform/connect`,
        {
          ...values,
          captcha_uuid: captchaUuid,
        },
        { timeout: 60000 },
      );
      const handle = response.data.session_id as string;
      sessionStorage.setItem(sessionStorageKey, handle);
      setSessionId(handle);
      setPlatformUser(response.data.user);
      setLoginOpen(false);
      loginForm.setFieldsValue({ password: '', captcha_code: '' });
      message.success('已安全连接新材料大数据中心');
    } catch (error) {
      message.error(errorDetail(error, '平台连接失败'));
      refreshCaptcha();
    } finally {
      setConnecting(false);
    }
  };

  const disconnectPlatform = async () => {
    if (!projectId) return;
    try {
      await api.delete(
        `/projects/${projectId}/integrations/platform/session`,
        { headers: sessionHeaders },
      );
    } finally {
      clearPlatformSession();
      message.success('平台临时连接已断开');
    }
  };

  const runPreflight = async () => {
    if (!projectId) return;
    if (currentBatchPaperIds.length === 0) {
      message.warning('请先从 NAS 导入本批文献；预检不会默认包含项目历史数据');
      return;
    }
    setPreflighting(true);
    setDeliveryResult(null);
    try {
      const response = await api.post(
        `/projects/${projectId}/integrations/platform/preflight`,
        {
          paper_ids: currentBatchPaperIds,
          include_unmapped: false,
        },
        { timeout: 180000 },
      );
      setPreflight(response.data);
      message.success(
        `预检通过：${response.data.paper_count} 篇论文，${response.data.record_count} 条平台记录`,
      );
    } catch (error) {
      setPreflight(null);
      message.error(errorDetail(error, '平台批次预检失败'));
    } finally {
      setPreflighting(false);
    }
  };

  const deliverToPlatform = async () => {
    if (!projectId) return;
    if (!preflight?.paper_ids?.length) {
      message.warning('请先运行本批次导入预检');
      return;
    }
    if (!sessionId) {
      openPlatformLogin();
      return;
    }
    setDelivering(true);
    setDeliveryResult(null);
    try {
      const response = await api.post(
        `/projects/${projectId}/integrations/platform/import`,
        {
          paper_ids: preflight.paper_ids,
          include_unmapped: false,
          force: false,
        },
        {
          headers: sessionHeaders,
          timeout: 360000,
        },
      );
      setDeliveryResult(response.data);
      if (response.data.status === 'completed' || response.data.status === 'already_confirmed') {
        message.success('平台已确认上传成功且解析成功');
      } else if (response.data.status === 'processing') {
        message.info('平台仍在解析；再次点击可安全查询，不会重复提交');
      } else {
        message.error('平台返回失败状态，请查看下方详情');
      }
      setPreflight({
        ...(preflight || response.data),
        ...response.data,
        ready: true,
      });
    } catch (error: any) {
      if (error?.response?.status === 401) clearPlatformSession();
      message.error(errorDetail(error, '平台导入失败'));
    } finally {
      setDelivering(false);
    }
  };

  const downloadPlatformBatch = async () => {
    if (!projectId) return;
    if (!preflight?.paper_ids?.length) {
      message.warning('请先运行本批次导入预检');
      return;
    }
    setExportingBatch(true);
    try {
      const response = await api.post(
        `/projects/${projectId}/integrations/platform/export`,
        {
          paper_ids: preflight.paper_ids,
          include_unmapped: false,
        },
        { responseType: 'blob', timeout: 180000 },
      );
      const disposition = String(response.headers['content-disposition'] || '');
      const matched = disposition.match(/filename="([^"]+)"/i);
      const filename = matched?.[1]
        || preflight?.filename
        || `ai4s_material_chain_v032_project_${projectId}.json`;
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      message.success('平台兼容 JSON 已下载，可在平台故障时人工上传');
    } catch (error) {
      message.error(errorDetail(error, '下载平台兼容 JSON 失败'));
    } finally {
      setExportingBatch(false);
    }
  };

  const downloadReadableWorkbook = async () => {
    if (!projectId) return;
    setExportingWorkbook(true);
    try {
      const created = await api.post(
        `/projects/${projectId}/exports`,
        {
          review_status_filter: ['approved', 'pending', 'uncertain', 'modified'],
        },
        { timeout: 180000 },
      );
      const response = await api.get(
        `/projects/${projectId}/exports/${created.data.id}/download`,
        { responseType: 'blob', timeout: 180000 },
      );
      downloadBlobResponse(
        response.data,
        '材料数据_成分工艺结构性能.xlsx',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      );
      message.success('科研主表已生成：文献与样品 → 成分 → 工艺 → 结构 → 性能');
    } catch (error) {
      message.error(errorDetail(error, '生成科研主表失败'));
    } finally {
      setExportingWorkbook(false);
    }
  };

  if (!currentProject) {
    return <Empty description="请先选择一个项目" style={{ marginTop: 100 }} />;
  }

  const completedStep = deliveryResult
    && ['completed', 'already_confirmed'].includes(deliveryResult.status)
    ? 3
    : preflight
      ? 2
      : candidates.length > 0
        ? 1
        : 0;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>NAS → 抽取 → 新材料大数据中心</h1>
          <Text type="secondary">
            受控扫描、内容去重、批量抽取、原子材料事实预检、分片上传和解析回读在同一页面完成。
          </Text>
        </div>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => {
            loadNasSources();
            loadPlatformConfig();
            if (sessionId) verifySession(sessionId);
          }}
        >
          刷新连接
        </Button>
      </div>

      <Card style={{ marginBottom: 20 }}>
        <Steps
          current={completedStep}
          items={[
            { title: '扫描 NAS', content: '只读发现 PDF' },
            { title: '入库与抽取', content: 'SHA-256 去重并排队' },
            { title: '事实预检', content: '成分/工艺/结构/性能' },
            { title: '平台确认', content: '上传成功 + 解析成功' },
          ]}
        />
      </Card>

      <Row gutter={[20, 20]}>
        <Col xs={24} xl={14}>
          <Card
            title={<Space><CloudServerOutlined />NAS 批量获取</Space>}
            extra={nasSources.length > 0 && (
              <Tag color={nasSources.some(item => item.available) ? 'green' : 'red'}>
                {nasSources.filter(item => item.available).length}/{nasSources.length} 可用
              </Tag>
            )}
          >
            {!nasSources.length ? (
              <Alert
                type="warning"
                showIcon
                title="NAS 尚未配置"
                description={nasMessage || '在后端 .env 的 NAS_SOURCE_ROOTS 中配置共享目录或映射盘。'}
              />
            ) : (
              <>
                <Space wrap style={{ marginBottom: 16 }}>
                  <Select
                    value={sourceId}
                    onChange={value => {
                      setSourceId(value);
                      invalidateNasScan();
                    }}
                    style={{ minWidth: 240 }}
                    options={nasSources.map(source => ({
                      value: source.id,
                      label: `${source.label}${source.available ? '' : '（不可用）'}`,
                      disabled: !source.available,
                    }))}
                  />
                  <Input
                    value={relativeDirectory}
                    onChange={event => {
                      setRelativeDirectory(event.target.value);
                      invalidateNasScan();
                    }}
                    placeholder="子目录（可留空）"
                    style={{ width: 220 }}
                  />
                  <Input
                    allowClear
                    value={filenameQuery}
                    onChange={event => {
                      setFilenameQuery(event.target.value);
                      invalidateNasScan();
                    }}
                    placeholder="文件名包含（可留空）"
                    maxLength={200}
                    style={{ width: 220 }}
                  />
                  <Checkbox
                    checked={recursive}
                    onChange={event => {
                      setRecursive(event.target.checked);
                      invalidateNasScan();
                    }}
                  >
                    递归子目录
                  </Checkbox>
                  <Button
                    type="primary"
                    icon={<SearchOutlined />}
                    loading={scanning}
                    onClick={scanNas}
                  >
                    扫描 PDF
                  </Button>
                </Space>
                <Table<NasCandidate>
                  rowKey="id"
                  size="small"
                  loading={scanning}
                  dataSource={candidates}
                  rowSelection={{
                    selectedRowKeys: selectedIds,
                    onChange: setSelectedIds,
                    preserveSelectedRowKeys: false,
                  }}
                  columns={[
                    {
                      title: 'NAS 相对路径',
                      dataIndex: 'relative_path',
                      ellipsis: true,
                    },
                    {
                      title: '大小',
                      dataIndex: 'size',
                      width: 100,
                      render: (value: number) => formatBytes(value),
                    },
                  ]}
                  pagination={{ pageSize: 10, showSizeChanger: false }}
                  locale={{ emptyText: '选择数据源、可选筛选条件并扫描后显示文件' }}
                />
                {candidates.length > 0 && selectedCandidates.length === 0 && (
                  <Alert
                    style={{ marginTop: 12 }}
                    type="info"
                    showIcon
                    title="扫描结果默认不选择"
                    description="请核对 NAS 相对路径后手动勾选本批次文件，避免误选整库。"
                  />
                )}
                <Space wrap style={{ marginTop: 16 }}>
                  <Switch checked={autoExtract} onChange={setAutoExtract} />
                  <Text>入库后自动抽取</Text>
                  <Select
                    value={modelMode}
                    onChange={setModelMode}
                    disabled={!autoExtract}
                    style={{ width: 120 }}
                    options={[
                      { value: 'strong', label: '精准模式' },
                      { value: 'auto', label: '自动模式' },
                      { value: 'weak', label: '经济模式' },
                    ]}
                  />
                  <Select
                    value={parserStrategy}
                    onChange={setParserStrategy}
                    disabled={!autoExtract}
                    style={{ width: 150 }}
                    options={[
                      { value: 'mineru_cloud', label: 'MinerU 云端' },
                      { value: 'mineru_local', label: 'MinerU 本地' },
                      { value: 'mineru_local_sync', label: '本地同步' },
                      { value: 'legacy', label: '兼容解析器' },
                    ]}
                  />
                  <Button
                    type="primary"
                    icon={<ThunderboltOutlined />}
                    loading={importingNas}
                    disabled={selectedCandidates.length === 0}
                    onClick={importFromNas}
                  >
                    导入 {selectedCandidates.length} 份并{autoExtract ? '开始抽取' : '暂存'}
                  </Button>
                </Space>
                {nasResult && (
                  <Alert
                    style={{ marginTop: 16 }}
                    type={nasResult.failed_count ? 'warning' : 'success'}
                    showIcon
                    title={`新增 ${nasResult.imported_count} · 重复 ${nasResult.duplicate_count} · 失败 ${nasResult.failed_count}`}
                    description={(
                      <Space orientation="vertical" size={2}>
                        <Text>
                          {nasResult.imported_count === 0
                            ? '本批次没有新增论文。'
                            : nasResult.extraction_started
                              ? '新增论文已进入现有抽取队列，可在“文献录入”查看进度。'
                              : '新增论文已入库，尚未启动抽取。'}
                        </Text>
                        {(nasResult.failures || []).map((failure: any) => (
                          <Text type="danger" key={`${failure.relative_path}-${failure.reason}`}>
                            {failure.relative_path}：{failure.reason}
                          </Text>
                        ))}
                      </Space>
                    )}
                  />
                )}
              </>
            )}
          </Card>
        </Col>

        <Col xs={24} xl={10}>
          <Card
            title={<Space><ApiOutlined />新材料大数据中心</Space>}
            extra={platformUser
              ? <Tag color="green">已连接：{platformUser.display_name}</Tag>
              : <Tag>未连接</Tag>}
          >
            {!platformConfig?.ready ? (
              <Alert
                type="error"
                showIcon
                title="平台绑定未就绪"
                description={platformConfig?.message || '正在加载平台配置'}
              />
            ) : (
              <>
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="平台">
                    {platformConfig.base_url}
                  </Descriptions.Item>
                  <Descriptions.Item label="目标数据集">
                    {platformConfig.dataset_name}
                  </Descriptions.Item>
                  <Descriptions.Item label="数据结构">
                    <Tag color="blue">{platformConfig.schema_version}</Tag>
                    每条记录对应一个真实材料样品
                  </Descriptions.Item>
                  <Descriptions.Item label="数据集 ID">
                    <Text copyable>{platformConfig.dataset_id}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="模板 ID">
                    <Text copyable>{platformConfig.template_id}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="模板指纹">
                    <Text code>{platformConfig.batch_template_sha256?.slice(0, 16)}…</Text>
                  </Descriptions.Item>
                </Descriptions>

                <Space wrap style={{ marginTop: 16 }}>
                  {platformUser ? (
                    <Button icon={<LogoutOutlined />} onClick={disconnectPlatform}>
                      断开临时连接
                    </Button>
                  ) : (
                    <Button
                      icon={<LoginOutlined />}
                      type="primary"
                      onClick={openPlatformLogin}
                    >
                      登录平台
                    </Button>
                  )}
                  <Button
                    icon={<SafetyCertificateOutlined />}
                    loading={preflighting}
                    onClick={runPreflight}
                  >
                    运行导入预检
                  </Button>
                </Space>
                {currentBatchPaperIds.length > 0 && (
                  <Alert
                    style={{ marginTop: 12 }}
                    type="info"
                    showIcon
                    title={`当前待投递批次：${currentBatchPaperIds.length} 篇论文`}
                    description="批次范围已在本浏览器会话中保留；刷新页面后仍只预检最后一次成功选择的 NAS 批次，不会混入项目历史数据。"
                  />
                )}

                {preflight && (
                  <div style={{ marginTop: 18 }}>
                    <Row gutter={12}>
                      <Col span={6}>
                        <Statistic title="已处理论文" value={preflight.paper_count} />
                      </Col>
                      <Col span={6}>
                        <Statistic title="材料样品" value={preflight.sample_count || 0} />
                      </Col>
                      <Col span={6}>
                        <Statistic title="样品数据链" value={preflight.record_count} />
                      </Col>
                      <Col span={6}>
                        <Statistic title="批次大小" value={formatBytes(preflight.bytes)} />
                      </Col>
                    </Row>
                    {preflight.domain_counts && (
                      <Space wrap style={{ marginTop: 12 }}>
                        {['成分', '工艺', '结构', '性能'].map(domain => (
                          <Tag color="blue" key={domain}>
                            {domain}覆盖 {preflight.domain_counts?.[domain] || 0} 个样品
                          </Tag>
                        ))}
                      </Space>
                    )}
                    {preflight.input_sample_count !== undefined && (
                      <Alert
                        style={{ marginTop: 12 }}
                        type="success"
                        showIcon
                        title={`严格质量闸门保留 ${preflight.sample_count || 0} / ${preflight.input_sample_count} 个样品`}
                        description={[
                          `阻断级质控 ${preflight.excluded_blocked_sample_count || 0}`,
                          `样品身份未核验 ${preflight.excluded_unverified_sample_count || 0}`,
                          `非材料、伪工艺或歧义值 ${preflight.excluded_semantic_sample_count || 0}`,
                          `材料链不完整 ${preflight.excluded_incomplete_sample_count || 0}`,
                          `证据或归属不合格事实 ${preflight.excluded_fact_count || 0}`,
                        ].join('；')}
                      />
                    )}
                    <Paragraph
                      type="secondary"
                      ellipsis={{ rows: 2, expandable: true }}
                      style={{ marginTop: 12 }}
                    >
                      批次指纹：{preflight.batch_sha256}
                    </Paragraph>
                    <Space orientation="vertical" style={{ width: '100%' }}>
                      <Button
                        block
                        icon={<DownloadOutlined />}
                        loading={exportingWorkbook}
                        onClick={downloadReadableWorkbook}
                      >
                        下载科研主表 Excel（按原表结构）
                      </Button>
                      <Button
                        block
                        icon={<DownloadOutlined />}
                        loading={exportingBatch}
                        onClick={downloadPlatformBatch}
                      >
                        下载平台兼容 JSON（应急）
                      </Button>
                      <Button
                        block
                        size="large"
                        type="primary"
                        icon={<ApiOutlined />}
                        disabled={!preflight.ready}
                        loading={delivering}
                        onClick={deliverToPlatform}
                      >
                        {platformUser ? '直接导入并等待平台解析确认' : '登录后直接导入'}
                      </Button>
                    </Space>
                    {delivering && (
                      <div style={{ marginTop: 12 }}>
                        <Progress percent={75} status="active" showInfo={false} />
                        <Text type="secondary">
                          正在分片上传并轮询解析状态，请勿重复点击。
                        </Text>
                      </div>
                    )}
                  </div>
                )}

                {deliveryResult && (
                  <Alert
                    style={{ marginTop: 16 }}
                    type={['completed', 'already_confirmed'].includes(deliveryResult.status)
                      ? 'success'
                      : deliveryResult.status === 'processing'
                        ? 'info'
                        : 'error'}
                    showIcon
                    title={
                      deliveryResult.status === 'already_confirmed'
                        ? '同一批次此前已确认，未重复上传'
                        : deliveryResult.status === 'completed'
                          ? '平台上传与解析均已确认成功'
                          : deliveryResult.status === 'processing'
                            ? '平台仍在解析'
                            : '平台导入失败'
                    }
                    description={`文件：${deliveryResult.filename || preflight?.filename || '-'}`}
                  />
                )}
              </>
            )}
          </Card>
        </Col>
      </Row>

      <Alert
        style={{ marginTop: 20 }}
        type="info"
        showIcon
        title="稳健性约束"
        description="平台正式链路中，每条记录对应一个真实材料样品，并按“文献与样品 → 成分 → 工艺 → 结构 → 性能 → 证据”排列。抽取过程日志、低置信度样品、介质与空白对照、伪工艺、表格脚注污染、无法对齐的重复指标值，以及缺少完整材料链或阻断级质控的样品均不会上传。NAS 内容按 SHA-256 去重，模板 ID、数据集 ID 和模板指纹任一不匹配都会拒绝提交；同一批次使用确定性指纹和本地回执防止重复上传。"
      />

      <Modal
        title="连接新材料大数据中心"
        open={loginOpen}
        onOk={connectPlatform}
        onCancel={() => setLoginOpen(false)}
        confirmLoading={connecting}
        okText="连接"
        destroyOnHidden
      >
        <Alert
          type="info"
          showIcon
          title="账号只用于本次临时会话"
          description="密码不会写入 AI4S 数据库；平台令牌只保存在后端内存，过期或服务重启后需重新连接。"
          style={{ marginBottom: 16 }}
        />
        <Form form={loginForm} layout="vertical">
          <Form.Item
            name="username"
            label="平台账号"
            rules={[{ required: true, message: '请输入平台账号' }]}
          >
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item
            name="password"
            label="平台密码"
            rules={[{ required: true, message: '请输入平台密码' }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          {captchaEnabled && (
            <Form.Item label="验证码" required>
              <Space align="start">
                <Form.Item
                  name="captcha_code"
                  noStyle
                  rules={[{ required: true, message: '请输入验证码' }]}
                >
                  <Input style={{ width: 150 }} maxLength={20} />
                </Form.Item>
                <Button
                  onClick={refreshCaptcha}
                  loading={captchaLoading}
                  style={{ height: 40, padding: 0, overflow: 'hidden' }}
                  title="点击刷新验证码"
                >
                  {captchaImage
                    ? (
                      <img
                        alt="平台验证码"
                        src={`data:image/png;base64,${captchaImage}`}
                        style={{ height: 38, minWidth: 110, objectFit: 'contain' }}
                      />
                    )
                    : <span style={{ padding: '0 12px' }}>刷新验证码</span>}
                </Button>
              </Space>
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  );
}
