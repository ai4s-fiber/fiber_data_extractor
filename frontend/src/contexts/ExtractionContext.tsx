import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import api from '../api/client';

interface ExtractionState {
  paperId: number | null;
  projectId: number | null;
  status: 'idle' | 'connecting' | 'streaming' | 'reconnecting' | 'done' | 'error' | 'cancelled';
  step: string;
  percent: number;
  message: string;
  error: { code: string; message: string } | null;
  result: { candidateCount: number; jobId: number } | null;
  jobId: number | null;
  mode: string | null;
}

interface ExtractionContextValue {
  state: ExtractionState;
  startExtraction: (
    projectId: number,
    paperId: number,
    mode: string,
    parserStrategy?: string,
    confirmWipe?: boolean,
  ) => Promise<{ jobId: number }>;
  cancelExtraction: (projectId: number, paperId: number) => Promise<void>;
  subscribe: (projectId: number, paperId: number, jobId: number, initial?: Partial<Pick<ExtractionState, 'step' | 'percent' | 'message'>>) => void;
  reconnectActive: (projectId: number, paperId: number, jobId: number, initial?: Partial<Pick<ExtractionState, 'step' | 'percent' | 'message'>>) => void;
  unsubscribe: () => void;
}

const DEFAULT_STATE: ExtractionState = {
  paperId: null,
  projectId: null,
  status: 'idle',
  step: '',
  percent: 0,
  message: '',
  error: null,
  result: null,
  jobId: null,
  mode: null,
};

const MAX_SSE_RETRIES = 8;
const SSE_RETRY_DELAY_MS = 3000;

const ExtractionContext = createContext<ExtractionContextValue | null>(null);

function sleep(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export function ExtractionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ExtractionState>(DEFAULT_STATE);
  const eventSourceRef = useRef<{ close: () => void } | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  const pollTerminalStatus = useCallback(async (
    projectId: number,
    paperId: number,
    jobId: number,
  ) => {
    try {
      const res = await api.get(`/projects/${projectId}/papers/${paperId}/extraction-status`);
      const data = res.data;
      if (data.status === 'completed') {
        const candidateCount =
          data.candidate_count ??
          data.extraction_summary?.生成记录数 ??
          (() => {
            const match = String(data.progress_message || '').match(/(\d+)\s*条/);
            return match ? Number(match[1]) : 0;
          })();
        setState(prev => ({
          ...prev,
          paperId,
          projectId,
          jobId,
          status: 'done',
          step: 'completed',
          percent: 100,
          message: data.progress_message || '抽取完成',
          result: { candidateCount, jobId },
        }));
        return true;
      }
      if (data.status === 'failed') {
        setState(prev => ({
          ...prev,
          paperId,
          projectId,
          jobId,
          status: 'error',
          error: { code: data.error_code || '', message: data.error_message || '抽取失败' },
          message: data.error_message || '抽取失败',
        }));
        return true;
      }
      if (data.status === 'cancelled') {
        setState(prev => ({
          ...prev,
          paperId,
          projectId,
          jobId,
          status: 'cancelled',
          message: '抽取已取消',
        }));
        return true;
      }
      if (data.status === 'running' || data.status === 'queued') {
        setState(prev => ({
          ...prev,
          paperId,
          projectId,
          jobId,
          status: 'reconnecting',
          step: data.step || prev.step,
          percent: data.percent ?? prev.percent,
          message: data.progress_message || prev.message,
        }));
      }
    } catch {
      // polling fallback failed
    }
    return false;
  }, []);

  const startExtraction = useCallback(async (
    projectId: number,
    paperId: number,
    mode: string,
    parserStrategy?: string,
    confirmWipe = false,
  ) => {
    const res = await api.post(`/projects/${projectId}/papers/${paperId}/extract`, {
      model_mode: mode,
      parser_strategy: parserStrategy || 'mineru_cloud',
      confirm_wipe: confirmWipe,
    });
    const { job_id, requested_mode } = res.data;
    setState({
      paperId,
      projectId,
      status: 'connecting',
      step: 'queued',
      percent: 0,
      message: '已加入队列...',
      error: null,
      result: null,
      jobId: job_id,
      mode: requested_mode,
    });
    return { jobId: job_id };
  }, []);

  const cancelExtraction = useCallback(async (projectId: number, paperId: number) => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    const res = await api.post(`/projects/${projectId}/papers/${paperId}/extract/cancel`);
    const data = res.data as { success?: boolean; message?: string };
    if (data?.success === false) {
      throw new Error(data.message || '无法取消该任务');
    }
    setState(prev => ({
      ...prev,
      paperId,
      projectId,
      status: 'cancelled' as const,
      message: data?.message || '正在停止抽取...',
    }));
  }, []);

  const subscribe = useCallback((
    projectId: number,
    paperId: number,
    jobId: number,
    initial?: Partial<Pick<ExtractionState, 'step' | 'percent' | 'message'>>,
  ) => {
    eventSourceRef.current?.close();

    const url = `/api/projects/${projectId}/papers/${paperId}/extraction-progress-stream`;
    const controller = new AbortController();
    let closed = false;

    setState(prev => ({
      ...prev,
      paperId,
      projectId,
      jobId,
      status: 'connecting',
      step: initial?.step ?? prev.step,
      percent: initial?.percent ?? prev.percent,
      message: initial?.message ?? prev.message,
    }));

    const handleSSEEvent = (event: string, data: any) => {
      switch (event) {
        case 'progress':
          setState(prev => ({
            ...prev,
            paperId,
            projectId,
            status: 'streaming',
            step: data.step || '',
            percent: data.percent || 0,
            message: data.message || data.step || '',
          }));
          break;
        case 'done':
          setState(prev => ({
            ...prev,
            paperId,
            projectId,
            status: 'done',
            step: 'completed',
            percent: 100,
            message: data.message || data.progress_message || '抽取完成',
            result: {
              candidateCount:
                data.candidate_count ??
                (() => {
                  const match = String(data.message || '').match(/(\d+)\s*条/);
                  return match ? Number(match[1]) : 0;
                })(),
              jobId: data.job_id || jobId,
            },
          }));
          closed = true;
          break;
        case 'error':
          setState(prev => ({
            ...prev,
            paperId,
            projectId,
            status: 'error',
            error: { code: data.error_code || '', message: data.error_message || '' },
            message: data.error_message || '抽取失败',
          }));
          closed = true;
          break;
        case 'cancelled':
          setState(prev => ({
            ...prev,
            paperId,
            projectId,
            status: 'cancelled',
            message: data.message || '抽取已取消',
          }));
          closed = true;
          break;
      }
    };

    const connectOnce = async (): Promise<boolean> => {
      const response = await fetch(url, {
        headers: {
          'Cache-Control': 'no-cache',
        },
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error(`SSE connection failed: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      setState(prev => ({ ...prev, status: 'streaming' }));

      while (!closed) {
        let readResult: ReadableStreamReadResult<Uint8Array>;
        let timer: ReturnType<typeof setTimeout> | null = null;
        try {
          readResult = await Promise.race([
            reader.read(),
            new Promise<never>((_, reject) => {
              timer = setTimeout(() => reject(new Error('read_timeout')), 60000);
            }),
          ]);
        } catch (raceErr: any) {
          if (raceErr.message === 'read_timeout') {
            throw new Error('SSE stream stalled');
          }
          throw raceErr;
        } finally {
          if (timer) clearTimeout(timer);
        }

        const { done, value } = readResult;
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        let currentEvent = '';
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            const data = line.slice(6);
            try {
              const parsed = JSON.parse(data);
              handleSSEEvent(currentEvent, parsed);
            } catch {
              // skip malformed data
            }
            currentEvent = '';
          }
        }
      }
      return closed;
    };

    const connect = async () => {
      for (let attempt = 1; attempt <= MAX_SSE_RETRIES && !closed; attempt += 1) {
        try {
          const terminal = await connectOnce();
          if (terminal) return;
          if (closed) return;
        } catch (err: any) {
          if (err.name === 'AbortError' || closed) return;
          const terminal = await pollTerminalStatus(projectId, paperId, jobId);
          if (terminal) return;
          setState(prev => ({
            ...prev,
            paperId,
            projectId,
            jobId,
            status: 'reconnecting',
            message: `进度连接断开，正在重连 (${attempt}/${MAX_SSE_RETRIES})...`,
          }));
        }
        if (attempt < MAX_SSE_RETRIES && !closed) {
          await sleep(SSE_RETRY_DELAY_MS);
        }
      }
      if (!closed) {
        await pollTerminalStatus(projectId, paperId, jobId);
      }
    };

    eventSourceRef.current = { close: () => { closed = true; controller.abort(); } };
    connect();
  }, [pollTerminalStatus]);

  const reconnectActive = useCallback((
    projectId: number,
    paperId: number,
    jobId: number,
    initial?: Partial<Pick<ExtractionState, 'step' | 'percent' | 'message'>>,
  ) => {
    subscribe(projectId, paperId, jobId, initial);
  }, [subscribe]);

  const unsubscribe = useCallback(() => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    setState(DEFAULT_STATE);
  }, []);

  return (
    <ExtractionContext.Provider value={{ state, startExtraction, cancelExtraction, subscribe, reconnectActive, unsubscribe }}>
      {children}
    </ExtractionContext.Provider>
  );
}

export function useExtraction() {
  const ctx = useContext(ExtractionContext);
  if (!ctx) throw new Error('useExtraction must be used within ExtractionProvider');
  return ctx;
}
