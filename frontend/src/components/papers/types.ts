export interface Paper {
  id: number;
  original_filename: string;
  paper_title: string | null;
  doi_or_url: string | null;
  year: number | null;
  journal: string | null;
  status: string;
  page_count: number | null;
  created_at: string;
  latest_job_id?: number | null;
  latest_job_status?: string | null;
  latest_job_step?: string | null;
  latest_job_percent?: number | null;
  latest_job_message?: string | null;
  latest_requested_mode?: string | null;
}

export interface LlmConfig {
  llm_provider: string | null;
  llm_base_url: string | null;
  llm_model: string | null;
}

export interface ExtractionProgress {
  step: string;
  percent: number;
  message?: string;
  error?: string;
}

export const ACTIVE_JOB_STATUSES = new Set(['queued', 'running']);

export const stepLabels: Record<string, string> = {
  starting: '启动中',
  inventory: '页面分析',
  extracting: 'AI 抽取',
  saving: '保存结果',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

export const statusMap: Record<string, { color: string; text: string }> = {
  uploaded: { color: 'blue', text: '已上传' },
  queued: { color: 'cyan', text: '已排队' },
  extracting: { color: 'processing', text: '抽取中' },
  review: { color: 'orange', text: '待审核' },
  completed: { color: 'green', text: '已完成' },
  failed: { color: 'red', text: '失败' },
};

export const modeLabels: Record<string, string> = {
  auto: '自动 (Auto)',
  weak: '单轮直接 (Weak)',
  strong: '多阶段高保真 (Strong)',
};

export function isActivePaper(paper: Paper) {
  return (
    paper.status === 'extracting'
    || paper.status === 'queued'
    || (paper.latest_job_status ? ACTIVE_JOB_STATUSES.has(paper.latest_job_status) : false)
  );
}

export function progressFromPaper(paper: Paper): ExtractionProgress | null {
  if (!isActivePaper(paper)) return null;
  return {
    step: paper.latest_job_step || (paper.status === 'queued' ? 'queued' : 'starting'),
    percent: paper.latest_job_percent ?? 0,
    message: paper.latest_job_message || undefined,
  };
}
