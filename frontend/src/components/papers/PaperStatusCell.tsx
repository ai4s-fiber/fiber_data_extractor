import { Progress, Tag } from 'antd';
import type { ExtractionProgress, Paper } from './types';
import { stepLabels, statusMap } from './types';

interface PaperStatusCellProps {
  paper: Paper;
  progress?: ExtractionProgress;
}

export default function PaperStatusCell({ paper, progress }: PaperStatusCellProps) {
  const isExtracting = paper.status === 'extracting' || paper.status === 'queued';

  if (isExtracting && progress) {
    const detail = progress.message || stepLabels[progress.step] || progress.step || '处理中';
    return (
      <div style={{ minWidth: 160 }}>
        <Progress
          percent={progress.percent}
          size="small"
          status={progress.error ? 'exception' : paper.status === 'queued' ? 'normal' : 'active'}
        />
        <div
          style={{
            fontSize: 11,
            color: 'var(--color-text-secondary)',
            lineHeight: 1.35,
            marginTop: 2,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={detail}
        >
          {detail} · {progress.percent}%
        </div>
      </div>
    );
  }

  if (isExtracting) {
    return <Tag color="processing">抽取中</Tag>;
  }

  const m = statusMap[paper.status] || { color: 'default', text: paper.status };
  return <Tag color={m.color}>{m.text}</Tag>;
}
