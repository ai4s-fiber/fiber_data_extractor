import { useState } from 'react';
import { Modal, Table, Typography, Card, Space } from 'antd';
import { FileExcelOutlined, RightOutlined } from '@ant-design/icons';
import { EXPORT_SHEET_REFERENCES, type ExportSheetRef } from '../data/exportFieldReference';

const { Text, Paragraph } = Typography;

const helpColumns = [
  { title: '序号', dataIndex: 'no', width: 56 },
  {
    title: '英文字段名',
    dataIndex: 'en',
    width: 200,
    render: (v: string) => <code style={{ fontSize: 12 }}>{v}</code>,
  },
  { title: '中文字段名', dataIndex: 'zh', width: 130 },
  { title: '含义', dataIndex: 'meaning' },
];

interface Props {
  open: boolean;
  onClose: () => void;
}

function SheetFieldTable({ sheet }: { sheet: ExportSheetRef }) {
  return (
    <div>
      <Paragraph type="secondary" style={{ marginBottom: 12 }}>
        {sheet.description}
      </Paragraph>
      <Table
        dataSource={sheet.fields}
        columns={helpColumns}
        rowKey="no"
        size="small"
        pagination={false}
        scroll={{ y: 420 }}
      />
    </div>
  );
}

export default function ExportFieldHelpModal({ open, onClose }: Props) {
  const [activeSheet, setActiveSheet] = useState<string | null>(null);

  const handleClose = () => {
    setActiveSheet(null);
    onClose();
  };

  const selected = EXPORT_SHEET_REFERENCES.find(s => s.key === activeSheet);

  return (
    <Modal
      title={
        selected
          ? `字段说明 · ${selected.title}`
          : '导出表格字段说明'
      }
      open={open}
      onCancel={handleClose}
      footer={null}
      width={selected ? 920 : 640}
      destroyOnClose
    >
      {!selected ? (
        <div>
          <Paragraph type="secondary" style={{ marginBottom: 16 }}>
            导出 Excel 共 {EXPORT_SHEET_REFERENCES.length} 个工作表（Sheet），
            点击下方卡片查看各表的字段定义，与导出文件列名一一对应。
          </Paragraph>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {EXPORT_SHEET_REFERENCES.map(sheet => (
              <Card
                key={sheet.key}
                hoverable
                size="small"
                onClick={() => setActiveSheet(sheet.key)}
                styles={{ body: { padding: '12px 16px' } }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <Space>
                    <FileExcelOutlined style={{ fontSize: 20, color: 'var(--color-primary, #4f6bf6)' }} />
                    <div>
                      <Text strong>{sheet.title}</Text>
                      <div>
                        <Text type="secondary" style={{ fontSize: 13 }}>
                          {sheet.subtitle} · {sheet.fields.length} 个字段
                        </Text>
                      </div>
                    </div>
                  </Space>
                  <RightOutlined style={{ color: 'var(--color-text-secondary)' }} />
                </div>
              </Card>
            ))}
          </Space>
        </div>
      ) : (
        <div>
          <Text
            type="secondary"
            style={{ cursor: 'pointer', display: 'inline-block', marginBottom: 12 }}
            onClick={() => setActiveSheet(null)}
          >
            ← 返回工作表列表
          </Text>
          <div style={{ marginBottom: 8 }}>
            <Text type="secondary">{selected.subtitle}</Text>
          </div>
          <SheetFieldTable sheet={selected} />
        </div>
      )}
    </Modal>
  );
}
