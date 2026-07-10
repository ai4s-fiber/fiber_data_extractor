import { Alert, Descriptions, Modal, Radio, Space } from 'antd';
import type { LlmConfig, Paper } from './types';
import { modeLabels } from './types';

export type ExtractionMode = 'auto' | 'weak' | 'strong';
export type ParserStrategy = 'mineru_local' | 'mineru_cloud' | 'legacy';

interface ExtractionModeModalProps {
  open: boolean;
  paper: Paper | null;
  llmConfig: LlmConfig | null;
  selectedMode: ExtractionMode;
  selectedParserStrategy: ParserStrategy;
  extracting: boolean;
  onModeChange: (mode: ExtractionMode) => void;
  onParserChange: (strategy: ParserStrategy) => void;
  onOk: () => void;
  onCancel: () => void;
  afterClose?: () => void;
}

export default function ExtractionModeModal({
  open,
  paper,
  llmConfig,
  selectedMode,
  selectedParserStrategy,
  extracting,
  onModeChange,
  onParserChange,
  onOk,
  onCancel,
  afterClose,
}: ExtractionModeModalProps) {
  return (
    <Modal
      title="启动高精度抽取"
      open={open}
      onOk={onOk}
      onCancel={onCancel}
      afterClose={afterClose}
      transitionName=""
      maskTransitionName=""
      okText="加入队列"
      cancelText="取消"
      confirmLoading={extracting}
      width={580}
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {paper && (
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="文献名称">
              {paper.paper_title || paper.original_filename}
            </Descriptions.Item>
            <Descriptions.Item label="所用大模型">
              {llmConfig?.llm_provider || '-'} / {llmConfig?.llm_model || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="Base URL">
              {llmConfig?.llm_base_url || '-'}
            </Descriptions.Item>
          </Descriptions>
        )}

        {(paper?.status === 'review' || paper?.status === 'completed') && (
          <Alert
            type="warning"
            showIcon
            message="注意：重新抽取会替换该文献已有候选记录、样品目录、事实候选、页面清单和证据记录。"
          />
        )}

        <div style={{ fontWeight: 'bold', fontSize: 13, color: 'var(--color-text-primary)' }}>
          1. 选择 AI 提取模式 (Extraction Mode)
        </div>
        <Radio.Group
          value={selectedMode}
          onChange={e => onModeChange(e.target.value)}
          optionType="button"
          buttonStyle="solid"
          style={{ width: '100%' }}
        >
          <Radio.Button value="auto" style={{ width: '33.33%', textAlign: 'center' }}>Auto 模式</Radio.Button>
          <Radio.Button value="weak" style={{ width: '33.33%', textAlign: 'center' }}>Weak 模式</Radio.Button>
          <Radio.Button value="strong" style={{ width: '33.33%', textAlign: 'center' }}>Strong 模式</Radio.Button>
        </Radio.Group>

        <Alert
          type="info"
          showIcon
          message={
            selectedMode === 'strong'
              ? 'Strong：多阶段串行深度抽取。大模型对材料、样品、工艺进行逐步精细研判，质量最高，但速度较慢。'
              : selectedMode === 'weak'
                ? 'Weak：单轮并联直接抽取。适用于结构简单的文献，速度极快，开销极低。'
                : 'Auto：根据当前项目的模型种类，自动选择匹配的 Strong 或 Weak 模式（弱指令小模型自动降级为 Weak）。'
          }
        />

        <div style={{ fontWeight: 'bold', fontSize: 13, color: 'var(--color-text-primary)', marginTop: 4 }}>
          2. 选择 PDF 解析引擎 (PDF Parser Engine Strategy)
        </div>
        <Radio.Group
          value={selectedParserStrategy}
          onChange={e => onParserChange(e.target.value)}
          optionType="button"
          buttonStyle="solid"
          style={{ width: '100%' }}
        >
          <Radio.Button value="mineru_local" style={{ width: '33.33%', textAlign: 'center' }}>本地 MinerU</Radio.Button>
          <Radio.Button value="mineru_cloud" style={{ width: '33.33%', textAlign: 'center' }}>线上精准 (VLM)</Radio.Button>
          <Radio.Button value="legacy" style={{ width: '33.33%', textAlign: 'center' }}>经典纯文本</Radio.Button>
        </Radio.Group>

        <div style={{
          background: 'var(--color-bg-secondary, #fafafa)',
          border: '1px solid var(--color-border, #e8e8e8)',
          borderRadius: 6,
          padding: 12,
          fontSize: 12,
          lineHeight: '1.6',
        }}
        >
          {selectedParserStrategy === 'mineru_local' && (
            <div>
              <div style={{ fontWeight: 'bold', color: '#52c41a', marginBottom: 4 }}>【本地 MinerU 离线抽取】 (默认推荐)</div>
              <div style={{ color: '#52c41a' }}><strong>✓ 优点：</strong>部署于本地/私有服务器，完全免费，无文件大小与页数硬性限制，适合大规模、大批量处理，且数据完全本地化，安全不泄露。</div>
              <div style={{ color: '#ff4d4f', marginTop: 4 }}><strong>✗ 缺点：</strong>依赖本地计算节点的 GPU/CPU 算力与显存，初次解析可能需要数分钟，需要本地一直跑着 MinerU 后台服务。</div>
            </div>
          )}
          {selectedParserStrategy === 'mineru_cloud' && (
            <div>
              <div style={{ fontWeight: 'bold', color: '#1890ff', marginBottom: 4 }}>【线上高精度 VLM 抽取】</div>
              <div style={{ color: '#52c41a' }}><strong>✓ 优点：</strong>使用 MinerU.net 官方线上高精度 VLM 模型，对复杂表格、公式、多栏混合排版的解析精度极高，不占用任何本地计算资源。</div>
              <div style={{ color: '#ff4d4f', marginTop: 4 }}><strong>✗ 缺点：</strong>受云端 API 配额与网络延迟影响，大文件上传较慢，需配置 MINERU_CLOUD_TOKEN。</div>
            </div>
          )}
          {selectedParserStrategy === 'legacy' && (
            <div>
              <div style={{ fontWeight: 'bold', color: '#faad14', marginBottom: 4 }}>【经典纯文本解析】 (降级备选)</div>
              <div style={{ color: '#52c41a' }}><strong>✓ 优点：</strong>无需外部服务，启动极快，适合纯文本 PDF。</div>
              <div style={{ color: '#ff4d4f', marginTop: 4 }}><strong>✗ 缺点：</strong>不支持复杂版式、图像和表格，抽取质量显著下降。</div>
            </div>
          )}
        </div>

        {paper && (
          <div style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
            当前选择：{modeLabels[selectedMode]}
          </div>
        )}
      </Space>
    </Modal>
  );
}
